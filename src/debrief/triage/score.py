"""The scoring call (docs/v2/CRITERIA.md "The model call"): one call to Haiku, through Claude Code
headless on the Max subscription, over every candidate and the most-discussed unattached threads.

The model answers four things per candidate, in this order: same_as (another candidate, or a
reported story, that is the same story), why (one line), relevant (yes or no, by
config/relevance.md), and score (5, 4 or 3, only when relevant). Then code, not the model:
  - merges each same_as group into one story. A group that reaches a reported story brings that
    story back with status "update"; otherwise the merged story lives in the first section of
    merge.section_precedence among its members, and its representative is the earliest published
  - drops every group with no relevant member (logged with its why, never shown to the skill)
  - gives the rest the best score and why among their members

The call has no tools, an explicit system prompt (prompts/<name>.md with relevance.md appended),
no settings, MCP servers or slash commands, and runs in an empty directory, so nothing from the
repo's v1 .claude/ leaks in. Fetched text goes in as JSON data inside tags, with "</" escaped.

Answers are cached in <data>/cache/score/<key>.json, keyed by a hash of everything the call sees
(system prompt, user message, model, effort, schema). Re-running triage on the same data replays
the answer, so the index is byte-identical. A failed call (an error, a timeout, an unusable
answer) is retried once; then ScoringError, and run.py falls back to the deterministic keep.
"""
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone

from .. import sanitize
from ..config import REPO
from .candidates import Group, published
from .merge import Union

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "same_as", "why", "relevant", "score"],
        "properties": {
            "id": {"type": "string"},
            "same_as": {"type": ["string", "null"]},
            "why": {"type": "string"},
            "relevant": {"type": "boolean"},
            "score": {"type": ["integer", "null"], "enum": [3, 4, 5, None]},
        }}}},
}


class ScoringError(Exception):
    pass


# ------------------------------------------------------------------------------------- the input

def teaser(ctx, g):
    """What the model reads about a candidate: its summary, plus the start of its page when the
    summary is short, about text_max_chars in all."""
    from .output import _pages, summary
    o = ctx.t["scoring"]
    text = summary(ctx, g) or ""
    if len(text) < o["short_summary_chars"]:
        pages = _pages(ctx, g)
        if pages:
            text = (text + "\n\n" + pages[0]["text"]).strip()
    return sanitize.text(text, o["text_max_chars"])


def payload(ctx, groups, reported):
    """{"candidates": [...], "reported": [...]}, in the deterministic candidate order."""
    from .output import _compact_signals
    items = []
    for sec in ctx.t["sections"]:
        for g in sorted((g for g in groups.get(sec, []) if g.alive), key=lambda g: (g.rank, g.id)):
            it = {"id": g.id, "section": sec, "source": g.story["sources"][0], "org": g.story["org"],
                  "title": g.story["title"], "text": teaser(ctx, g)}
            hn = g.story["extra"].get("hn") if sec == "discussion" else None
            if hn:
                it["link"] = hn["link"]
                it["comments"] = hn["comments"]
            others = [g.stories[sid]["title"] for sid in g.members[1:]][:3]
            if others:
                it["also"] = others
            sig = _compact_signals(g)
            if sig:
                it["signals"] = sig
            items.append(it)
    return {"candidates": items, "reported": reported}


def reported_list(ctx, ledger_doc, run_date, candidate_ids):
    """Stories in a brief in the last scoring.reported_days days: [{id, title, reported_on}]."""
    days = ctx.t["scoring"]["reported_days"]
    from datetime import date, timedelta
    since = (date.fromisoformat(run_date) - timedelta(days=days)).isoformat()
    out = []
    for sid, dates in sorted(ledger_doc["dates"].items()):
        last = max(dates)
        if last < since or sid in candidate_ids:
            continue
        s = story_or_thread(ctx, sid)
        if s:
            out.append({"id": sid, "title": s["title"], "reported_on": last})
    return out


def _line(obj):
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def render(data):
    lines = ["<reported>", *(_line(x) for x in data["reported"]), "</reported>",
             "<candidates>", *(_line(x) for x in data["candidates"]), "</candidates>"]
    return "\n".join(lines) + "\n"


def system_prompt(t):
    prompt = (REPO / t["prompt"]).read_text()
    relevance = (REPO / t["relevance"]).read_text()
    return prompt.replace("{relevance}", relevance.strip())


def prompt_version(t, system):
    name = t["prompt"].rsplit("/", 1)[-1].removesuffix(".md")
    return f"{name}@{hashlib.sha256(system.encode()).hexdigest()[:8]}"


# -------------------------------------------------------------------------------------- the call

def run_claude(argv, stdin, timeout):
    """Claude Code in an empty directory, with no API key in its environment (so it uses the
    subscription token). Returns (exit code, stdout, stderr)."""
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    with tempfile.TemporaryDirectory() as cwd:
        try:
            p = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
        except subprocess.TimeoutExpired:
            return 124, "", f"timed out after {timeout} s"
        except FileNotFoundError:
            return 127, "", "the claude CLI isn't installed here"
    return p.returncode, p.stdout, p.stderr


def argv_for(model, system, effort):
    argv = ["claude", "-p", "--model", model, "--tools", "", "--system-prompt", system,
            "--json-schema", json.dumps(SCHEMA), "--output-format", "json", "--setting-sources", "",
            "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"]
    return argv + (["--effort", effort] if effort else [])


def parse_response(code, stdout, stderr):
    """(structured output, usage) from Claude Code's JSON result, or ScoringError."""
    lines = [x for x in (stdout or "").splitlines() if x.strip()]
    try:
        d = json.loads(lines[-1]) if lines else {}
    except ValueError:
        d = {}
    if code != 0 or d.get("is_error") or d.get("subtype") != "success" or not isinstance(d.get("structured_output"), dict):
        why = d.get("result") or d.get("subtype") or (stderr or "").strip().splitlines()[-1:] or f"exit {code}"
        raise ScoringError(f"claude call failed (exit {code}): {str(why)[:300]}")
    u = d.get("usage") or {}
    usage = {
        "input_tokens": int((u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
                            + (u.get("cache_read_input_tokens") or 0)),
        "output_tokens": int(u.get("output_tokens") or 0),
        "thinking_tokens": int((u.get("output_tokens_details") or {}).get("thinking_tokens") or 0),
        "duration_s": round((d.get("duration_ms") or 0) / 1000, 1),
        "cost_usd_list_price": round(float(d.get("total_cost_usd") or 0), 4),
        "served_by": sorted((d.get("modelUsage") or {}).keys()),
    }
    return d["structured_output"], usage


def validate(answer, cand_ids, ref_ids, why_max):
    """{id: {same_as, why, relevant, score}} for every candidate, or ScoringError. Lenient where the
    meaning is clear: a same_as that names nothing known (or itself) becomes null, a relevant answer
    without a score becomes 3, an irrelevant one loses its score."""
    seen, notes = {}, []
    for it in (answer or {}).get("items") or []:
        if isinstance(it, dict) and it.get("id") in cand_ids and it["id"] not in seen:
            seen[it["id"]] = it
    missing = [i for i in cand_ids if i not in seen]
    if missing:
        raise ScoringError(f"{len(missing)} of {len(cand_ids)} candidates unanswered, e.g. {missing[0]}")
    out = {}
    known = set(cand_ids) | set(ref_ids)
    for cid in cand_ids:
        it = seen[cid]
        same = it.get("same_as")
        if same is not None and (same == cid or same not in known):
            notes.append(f"{cid}: same_as {same!r} names nothing known; ignored")
            same = None
        rel = bool(it.get("relevant"))
        score = it.get("score") if rel else None
        if rel and score not in (3, 4, 5):
            notes.append(f"{cid}: relevant without a score; scored 3")
            score = 3
        out[cid] = {"same_as": same, "why": sanitize.text(it.get("why"), why_max), "relevant": rel, "score": score}
    return out, notes


def cache_key(model, effort, system, user):
    blob = json.dumps({"model": model, "effort": effort, "system": system, "user": user, "schema": SCHEMA},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def call(cache_dir, t, data, model=None, runner=run_claude, fresh=False, log=print):
    """Score `data` (from payload()). Returns {"answers", "notes", "usage", "cached", "attempts",
    "model", "prompt_version"}; raises ScoringError."""
    model = model or t["model"]
    system = system_prompt(t)
    user = render(data)
    key = cache_key(model, t.get("effort"), system, user)
    cand_ids = [x["id"] for x in data["candidates"]]
    ref_ids = [x["id"] for x in data["reported"]]
    meta = {"model": model, "prompt_version": prompt_version(t, system), "key": key[:16]}
    path = cache_dir / f"{key}.json"
    if path.exists() and not fresh:
        hit = json.loads(path.read_text())
        answers, notes = validate(hit["answer"], cand_ids, ref_ids, t["why_max_chars"])
        return {**meta, "answers": answers, "notes": notes, "usage": hit["usage"], "cached": True,
                "attempts": hit["attempts"]}
    errors = []
    for attempt in (1, 2):
        try:
            answer, usage = parse_response(*runner(argv_for(model, system, t.get("effort")), user, t["timeout_s"]))
            answers, notes = validate(answer, cand_ids, ref_ids, t["why_max_chars"])
        except ScoringError as e:
            errors.append(str(e))
            log(f"scoring attempt {attempt} failed: {e}")
            continue
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"key": key, **meta, "answer": answer, "usage": usage, "attempts": attempt,
                                   "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
                                  ensure_ascii=False, indent=1))
        os.replace(tmp, path)
        return {**meta, "answers": answers, "notes": notes, "usage": usage, "cached": False, "attempts": attempt}
    raise ScoringError("; ".join(errors))


# ------------------------------------------------------------------------------ applying answers

def story_or_thread(ctx, sid):
    """A story row, or a Hacker News thread dressed as one (id hn:<item>)."""
    if sid.startswith("hn:"):
        m = ctx.store.mention(f"hn|{sid.split(':', 1)[1]}")
        return thread_story(ctx.store, ctx.settings, m) if m else None
    return ctx.store.story(sid)


def thread_story(store, settings, m, points=None, comments=None):
    """A thread that attached to nothing, as a story of the discussion section. Its summary is the
    post's own text (Ask HN, Tell HN), if it has one; the page it links is stored as its page
    (adapters/article_pages.py ThreadPages), under the same id."""
    links = json.loads(m["links"] or "[]")
    link = links[0] if links else m["url"]
    sid = "hn:" + m["item_key"]
    return {"story_id": sid, "kind": "discussion", "section": "discussion", "title": m["title"] or link,
            "org": None, "authors": [], "published_at": m["posted_at"] or m["first_seen_at"],
            "first_seen": m["first_seen_at"][:10], "first_seen_at": m["first_seen_at"],
            "last_changed_at": m["first_seen_at"], "tier": 3, "summary": m["text"] or "",
            "artifact_urls": [link] if link else [], "code_urls": [], "sources": [m["source_id"]],
            "extra": {"hn": {"points": points, "comments": comments, "link": link, "thread": m["url"]}},
            "content_hash": "", "merged_into": None}


def apply(ctx, groups, result, ledger_doc):
    """Merge same_as groups, drop the irrelevant, and record each answer (see the module doc)."""
    answers = result["answers"]
    precedence = ctx.t["merge"]["section_precedence"]
    every = {g.id: g for gs in groups.values() for g in gs}
    cands = {cid: every[cid] for cid in answers}
    u = Union()
    for cid, a in answers.items():
        u.find(cid)
        if a["same_as"]:
            u.join(cid, a["same_as"])
    for g in cands.values():
        a = answers[g.id]
        g.info.update({"why": a["why"], "relevant": a["relevant"], "score": a["score"]})
    for comp in u.groups():
        members = [cands[i] for i in comp if i in cands]
        targets = sorted(i for i in comp if i not in cands)          # reported stories
        relevant = [g for g in members if g.info["relevant"]]
        if not relevant:
            for g in members:
                g.drop("score", f"not relevant: {g.info['why']}")
            continue
        best = max(relevant, key=lambda g: (g.info["score"], -(g.rank or 0)))
        if targets:
            keeper = _revive(ctx, groups, every, targets[0], ledger_doc, rank=min(g.rank or 1 for g in members))
            why = f"same story as {keeper.id}, reported {keeper.prev_reported} (the model's same_as)"
        else:
            section = min({g.section for g in members}, key=lambda s: precedence.index(s) if s in precedence else 99)
            keeper = min((g for g in members if g.section == section), key=lambda g: (published(g.story), g.id))
            why = f"same story as {keeper.id} (the model's same_as)"
        for g in sorted(members, key=lambda g: g.id):
            if g is not keeper:
                keeper.absorb(g)
                g.drop("merge", why)
                g.info["merged_into"] = keeper.id
        keeper.info.update({"score": best.info["score"], "why": best.info["why"], "relevant": True})
        if len(keeper.members) > 1:
            keeper.info["merged"] = keeper.members[1:]


def _revive(ctx, groups, every, sid, ledger_doc, rank):
    """A reported story back in the index as an update, whether or not it was in today's pools."""
    g = every.get(sid)
    if g is None:
        s = story_or_thread(ctx, sid)
        g = Group(id=sid, section=s["section"], members=[sid], stories={sid: s})
        groups.setdefault(g.section, []).append(g)
        every[sid] = g
    g.stage, g.reason = None, ""
    g.status, g.prev_reported = "update", max(ledger_doc["dates"].get(sid, ["?"]))
    g.rank = g.rank or rank
    g.info["candidate"] = True
    return g
