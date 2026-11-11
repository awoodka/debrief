"""The scoring call (docs/CRITERIA.md "The model call"): one call (triage.yml scoring.model), through
Claude Code headless on the Max subscription, over every candidate and the most-discussed unattached
threads (Hacker News, LessWrong and the Alignment Forum).

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
repo (settings, commands, agents) leaks in. Fetched text goes in as JSON data inside tags, with "</" escaped.

Answers are cached in <data>/cache/score/<key>.json, keyed by a hash of everything the call sees
(system prompt, user message, model, effort, schema). Re-running triage on the same data replays
the answer, so the index is byte-identical. Only an answer for every candidate is cached, so a
re-run after a gap asks again.

There are at most two calls, and the second asks only about what the first left without a usable
answer (see trusted()). A story still without one is decided by the rules, alone (apply()). If no
call worked, or too few stories have an answer (scoring.min_answered), ScoringError, and run.py
falls back to the deterministic keep for the whole day.
"""
import hashlib
import json
import os
import subprocess
import tempfile
from collections import Counter
from datetime import datetime, timezone

from .. import sanitize
from ..config import REPO
from .candidates import Group, published, thread_id, thread_mention
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
            th = g.story["extra"].get("thread") if sec == "discussion" else None
            if th:
                it["link"] = th["link"]
                it["comments"] = th["comments"]
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


def argv_for(model, system, effort, schema=None):
    """schema: the answer's JSON schema (default: triage's; jobs passes its own)."""
    argv = ["claude", "-p", "--model", model, "--tools", "", "--system-prompt", system,
            "--json-schema", json.dumps(schema or SCHEMA), "--output-format", "json", "--setting-sources", "",
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


def trusted(answer, ids):
    """({id: item} of the answers that can be used, notes on the rest). On a long list the model
    sometimes can't copy one hex id, and the same input makes it slip the same way again. It leaves
    the story out, writes why "placeholder" under a made-up or real id, or answers under another
    story's id. So an id answered twice is trusted nowhere: either answer may be another story's.
    The notes name ids only, because a why can belong to a dropped story and they reach the report.
    A failed call's text can quote a dropped story too, so ask() sends that only to the log."""
    items = [it for it in (answer or {}).get("items") or [] if isinstance(it, dict) and isinstance(it.get("id"), str)]
    counts = Counter(it["id"] for it in items)
    got, notes = {}, []
    for it in items:
        i = it["id"]
        if i not in ids:
            notes.append(f"{i} isn't a story being asked about")
        elif counts[i] > 1:
            notes.append(f"{i} answered twice")
        elif str(it.get("why")).strip().lower() == "placeholder":
            notes.append(f"{i} answered with a placeholder")
        else:
            got[i] = it
    return got, list(dict.fromkeys(notes))


def validate(got, cand_ids, ref_ids, why_max):
    """{id: {same_as, why, relevant, score}} for the candidates in `got`. Lenient where the meaning
    is clear: a same_as that names nothing known (or itself) becomes null, a relevant answer without
    a score becomes 3, an irrelevant one loses its score."""
    out, notes = {}, []
    known = set(cand_ids) | set(ref_ids)
    for cid in cand_ids:
        if cid not in got:
            continue
        it = got[cid]
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


def add_usage(a, b):
    if a is None:
        return b
    return {k: sorted({*a[k], *b[k]}) if k == "served_by" else round(a[k] + b[k], 4) for k in a}


def ask(t, model, system, data, runner, log):
    """Up to two calls. Returns ({id: item}, notes, usage, calls). ScoringError if no call worked or
    under scoring.min_answered of the stories have an answer: the rules can stand in for a few stories,
    not for most of a day. The second call lists the answered stories under <reported>, so its same_as
    can still reach them."""
    every = data["candidates"]
    got, notes, errors, usage = {}, [], [], None
    for attempt in (1, 2):
        todo = [c for c in every if c["id"] not in got]
        answered = [{"id": c["id"], "title": c["title"]} for c in every if c["id"] in got]
        user = render({"candidates": todo, "reported": data["reported"] + answered})
        try:
            answer, used = parse_response(*runner(argv_for(model, system, t.get("effort")), user, t["timeout_s"]))
        except ScoringError as e:
            errors.append(str(e))
            notes.append(f"call {attempt} failed")
            log(f"scoring attempt {attempt} failed: {e}")
            continue
        usage = add_usage(usage, used)
        found, slips = trusted(answer, {c["id"] for c in todo})
        got |= found
        left = [c["id"] for c in every if c["id"] not in got]
        if left:
            slips.append(f"call {attempt} left {len(left)} of {len(every)} without a usable answer: {', '.join(left[:5])}")
        for slip in slips:
            log(f"scoring: {slip}")
        notes += slips
        if not left:
            break
    if usage is None or len(got) < t["min_answered"] * len(every):
        raise ScoringError("; ".join(errors + [f"{len(got)} of {len(every)} candidates got a usable answer"]))
    return got, notes, usage, attempt


def call(cache_dir, t, data, model=None, runner=run_claude, fresh=False, log=print):
    """Score `data` (from payload()). Returns {"answers", "unanswered", "notes", "usage", "cached",
    "attempts", "model", "prompt_version"}; raises ScoringError."""
    model = model or t["model"]
    system = system_prompt(t)
    key = cache_key(model, t.get("effort"), system, render(data))
    cand_ids = [x["id"] for x in data["candidates"]]
    ref_ids = [x["id"] for x in data["reported"]]
    meta = {"model": model, "prompt_version": prompt_version(t, system), "key": key[:16]}
    path = cache_dir / f"{key}.json"
    cached = path.exists() and not fresh
    if cached:
        hit = json.loads(path.read_text())
        got = {it["id"]: it for it in hit["answer"]["items"]}
        slips, usage, attempts = hit.get("slips", []), hit["usage"], hit["attempts"]
    else:
        got, slips, usage, attempts = ask(t, model, system, data, runner, log)
        if len(got) == len(cand_ids):
            cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"key": key, **meta, "answer": {"items": list(got.values())}, "slips": slips, "usage": usage,
                                       "attempts": attempts, "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
                                      ensure_ascii=False, indent=1))
            os.replace(tmp, path)
    answers, notes = validate(got, cand_ids, ref_ids, t["why_max_chars"])
    return {**meta, "answers": answers, "unanswered": [i for i in cand_ids if i not in got], "notes": slips + notes,
            "usage": usage, "cached": cached, "attempts": attempts}


# ------------------------------------------------------------------------------ applying answers

def story_or_thread(ctx, sid):
    """A story row, or a thread dressed as one (id <source>:<item>, candidates.thread_id)."""
    mid = thread_mention(sid, ctx.settings)
    if mid:
        m = ctx.store.mention(mid)
        return thread_story(ctx.store, ctx.settings, m) if m else None
    return ctx.store.story(sid)


def thread_story(store, settings, m, points=None, comments=None):
    """A thread that attached to nothing, as a story of the discussion section. Its summary is the
    post's own text (Ask HN, Tell HN, a LessWrong post's body), if it has one; for HN, the page it
    links is stored as its page (adapters/article_pages.py ThreadPages), under the same id. Its link
    is the first of its links: what an HN submission links to; for a LessWrong post, the page a link
    post links to, or the post itself."""
    links = json.loads(m["links"] or "[]")
    link = links[0] if links else m["url"]
    sid = thread_id(m)
    return {"story_id": sid, "kind": "discussion", "section": "discussion", "title": m["title"] or link,
            "org": None, "authors": [], "published_at": m["posted_at"] or m["first_seen_at"],
            "first_seen": m["first_seen_at"][:10], "first_seen_at": m["first_seen_at"],
            "last_changed_at": m["first_seen_at"], "tier": 3, "summary": m["text"] or "",
            "artifact_urls": [link] if link else [], "code_urls": [], "sources": [m["source_id"]],
            "extra": {"thread": {"points": points, "comments": comments, "link": link, "thread": m["url"]}},
            "content_hash": "", "merged_into": None}


def apply(ctx, groups, result, ledger_doc):
    """Merge same_as groups, drop the irrelevant, and record each answer (see the module doc)."""
    answers = result["answers"]
    precedence = ctx.t["merge"]["section_precedence"]
    every = {g.id: g for gs in groups.values() for g in gs}
    unanswered = set(result["unanswered"])
    for cid in unanswered:
        g = every[cid]
        kept = g.rank <= ctx.t["fallback_keep"].get(g.section, 0)
        answers[cid] = {"same_as": None, "why": "The scoring call gave no answer for this one, so the rules decided.",
                        "relevant": kept, "score": 3 if kept else None}
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
                if g.id in unanswered:
                    g.drop("keep", g.info["why"])
                else:
                    g.drop("score", f"not relevant: {g.info['why']}")
            continue
        best = max(relevant, key=lambda g: (g.info["score"], g.id not in unanswered, -(g.rank or 0)))
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
