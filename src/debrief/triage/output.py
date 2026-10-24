"""The output contract (brief 8; schemas/): what triage writes to runs/<date>/.

  stories_index.jsonl   one line per kept story, grouped by section, then by the model's score,
                        then newest first (never by volume). What the skill reads first.
  stories/<id>.json     everything about one kept story: members, signals, discussion, coverage,
                        full text, raw refs
  triage_log.jsonl      every story that entered a pool: where it stopped and why. The only place
                        the model's drops are written, for auditing; the skill doesn't read it.
  run_report.md         a JSON header (schemas/report_header.schema.json), then tables for people.
                        Model drops appear only as a count.

Every document is validated against its schema before anything is written, and the files are
swapped in together, so a failed run leaves the previous output whole. Output is byte-for-byte
the same for the same input: sorted keys, and every list in a fixed order.
"""
import json
import os
import re
import shutil
from datetime import datetime, timezone

import jsonschema

from .. import sanitize, times
from ..config import REPO, ConfigError
from ..store import loads
from ..stories import signals
from .candidates import published
from .ledger import days_discussed

SCHEMAS = REPO / "schemas"
STAGES = ("rules", "merge", "ledger", "use_bar", "org_cap", "cap", "score", "keep")


class Validator:
    def __init__(self, directory=SCHEMAS):
        self.v = {}
        for name in ("index_line", "story", "triage_log", "report_header", "reported", "brief_draft", "brief"):
            schema = json.loads((directory / f"{name}.schema.json").read_text())
            self.v[name] = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())

    def validate(self, name, doc):
        err = next(iter(sorted(self.v[name].iter_errors(doc), key=lambda e: list(e.path))), None)
        if err:
            where = "/".join(str(p) for p in err.path) or "(top)"
            raise ValueError(f"{name} schema: {where}: {err.message[:300]}")


def dumps(doc):
    return json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def detail_path(story_id):
    return "stories/" + re.sub(r"[^A-Za-z0-9._-]+", "-", story_id).strip("-") + ".json"


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(])")


def first_sentence(text, cap):
    text = " ".join((text or "").split())
    for m in _SENTENCE_END.finditer(text):
        if m.start() >= 40:
            text = text[: m.start()]
            break
    return sanitize.text(text, cap)


_HUB_ADAPTERS = ("hf_org_watch", "hf_trending")      # their summary is repo metadata, not prose
_SENTENCE = re.compile(r"[.!?](\s|$)")


def _words(text):
    return re.findall(r"\w+", (text or "").lower())


def prose_paragraph(text, title=None):
    """The first paragraph of a page or model card that reads as prose. Skipped: headings, quotes,
    lists, code, rules, tables and link bars (any "|"), Hub widgets ("•"), the title repeated, and
    title, date and byline lines (fewer than 10 words, or no sentence end)."""
    for p in (text or "").split("\n\n"):
        p = p.strip()
        if not p or p.startswith(("#", ">", "- ", "|", "```", "---")) or any(c in p for c in "|｜•"):
            continue
        p = " ".join(p.replace("**", "").replace("__", "").replace("`", "").replace("*", "").split())
        if title and _words(p) == _words(title):
            continue
        if len(p.split()) >= 10 and _SENTENCE.search(p):
            return p
    return None


def summary(ctx, g):
    """The story's summary, except for a Hugging Face model, whose stored summary is repo
    metadata ("text-generation · transformers · license mit"): its model card's first prose
    paragraph instead, when the card has one."""
    s = g.story
    try:
        adapter = ctx.cfg.source(s["sources"][0]).adapter
    except ConfigError:
        adapter = None
    if adapter in _HUB_ADAPTERS:
        for p in _pages(ctx, g):
            para = prose_paragraph(p["text"]) if "huggingface.co/" in p["url"] else None
            if para:
                return sanitize.text(para, ctx.settings["collect"]["summary_max_chars"])
    return s["summary"]


def reads_as_prose(text):
    """A summary that says something: at least 6 words with a sentence end, or 15 words, and no Hub
    widgets. Not a feed's category label ("Generative AI") or an image's alt text ("Collage showing
    five builds with Gemini 4 Argon")."""
    words = (text or "").split()
    return "•" not in text and ((len(words) >= 6 and bool(_SENTENCE.search(text))) or len(words) >= 15)


def gist(ctx, g, cap):
    """One line on what the story is: Hugging Face's AI summary for a paper; else the first sentence
    of the summary, when it reads as prose; else of the first prose paragraph of the story's page;
    else of whatever the summary has."""
    s = g.story
    ai = (s["extra"].get("hf-daily-papers") or {}).get("ai_summary")
    if s["section"] == "papers" and ai:
        return sanitize.text(ai, cap)
    text = summary(ctx, g) or ""
    if reads_as_prose(text):
        return first_sentence(text, cap)
    for p in _pages(ctx, g):
        para = prose_paragraph(p["text"], s["title"])
        if para:
            return first_sentence(para, cap)
    th = s["extra"].get("thread")
    if not text and th:             # a thread whose page couldn't be read: say what it is
        what, unit = _THREAD_WORDS.get(s["sources"][0], ("A discussion", "points"))
        stats = f"{th['points']} {unit}, {th['comments']} comments, " if th.get("points") is not None else ""
        return sanitize.text(f"{what} ({stats}linking {th['link']}).", cap)
    return first_sentence(text, cap) if text else ""


_THREAD_WORDS = {"hn": ("A Hacker News discussion", "points"), "lesswrong": ("A LessWrong post", "karma"),
                 "alignment-forum": ("An Alignment Forum post", "karma")}


def _pages(ctx, g):
    out = []
    for sid in g.members:
        out += [p for p in ctx.store.pages(sid) if 200 <= p["status"] < 300 and p["text"]]
    return out


def _coverage(ctx, ids):
    marks = ",".join("?" * len(ids))
    rows = ctx.store.q(f"""SELECT c.source_id, c.covered_at, c.url FROM coverage c
                           JOIN mentions m ON m.mention_id = c.mention_id
                           WHERE c.story_id IN ({marks}) AND m.first_seen_at <= ?
                           ORDER BY c.covered_at, c.source_id, c.url""", (*ids, ctx.at))
    seen, out = set(), []
    for src, at, url in rows:
        if (src, url) not in seen:
            seen.add((src, url))
            out.append({"source": src, "date": at[:10] if at else None, "url": url})
    return out


def _threads(ctx, ids, mentions=()):
    o = ctx.t["output"]
    store, at = ctx.store, ctx.at
    marks = ",".join("?" * len(ids))
    extra = list(mentions) or [""]
    emarks = ",".join("?" * len(extra))
    rows = store.q(f"""SELECT mention_id, source_id, url, title, posted_at FROM mentions
                       WHERE (story_id IN ({marks}) OR mention_id IN ({emarks})) AND kind IN ('thread', 'post')
                       AND first_seen_at <= ?""", (*ids, *extra, at))
    threads = []
    for mid, src, url, title, posted in rows:
        e = signals._latest(store, mid, at)
        threads.append((-(e.get("points") or 0), mid, {"src": src, "url": url, "title": title, "points": e.get("points"),
                                                         "comments": e.get("comments"), "posted_at": posted, "_id": mid}))
    # HF paper comments hang off the paper, not a thread: one pseudo-thread per paper.
    for sid in ids:
        n = store.one("""SELECT COUNT(*) FROM mentions WHERE story_id=? AND kind='comment' AND parent_id IS NULL
                         AND first_seen_at <= ?""", (sid, at))[0]
        if n:
            paper = sid.split(":", 1)[1] if sid.startswith("arxiv:") else None
            threads.append((0, f"~{sid}", {"src": "hf-paper-comments", "title": None, "points": None, "comments": n,
                                           "url": f"https://huggingface.co/papers/{paper}" if paper else None,
                                           "posted_at": None, "_paper": sid}))
    threads.sort(key=lambda x: (x[0], x[1]))
    out = []
    for _, _, t in threads[: o["max_threads"]]:
        if "_paper" in t:
            q, args, first = "story_id=? AND kind='comment' AND parent_id IS NULL", (t.pop("_paper"),), "first"
        else:
            q, args, first = "parent_id=?", (t.pop("_id"),), "top-ranked"
        stored = store.q(f"""SELECT mention_id, author, text, rank, replies FROM mentions WHERE {q} AND first_seen_at <= ?
                             ORDER BY rank IS NULL, rank, mention_id""", (*args, at))
        top = []
        for c, picked in pick_comments(stored, o):
            entry = {"author": c["author"], "text": sanitize.text(c["text"], o["comment_max_chars"]), "rank": c["rank"],
                     "replies": c["replies"], "picked": picked if picked != "top-ranked" else first}
            e = signals._latest(store, c["mention_id"], at)
            for key in ("likes", "points"):          # HF reactions; LessWrong karma
                if e.get(key) is not None:
                    entry[key] = e[key]
            top.append(entry)
        out.append({**t, "top_comments": top, "more_comments": max(0, len(stored) - len(top))})
    return {"threads": out, "more_threads": max(0, len(threads) - len(out))}


def pick_comments(stored, o):
    """[(comment, why picked)]: of the comments that say enough (output.comment_min_chars without
    their links), the comments_top first in the source's ranked order, then the comments_debated
    most-replied of the rest, where the disagreement usually is."""
    from ..adapters.hn import substantive
    good = [c for c in stored if substantive(c["text"] or "", o["comment_min_chars"])]
    top = good[: o["comments_top"]]
    debated = sorted((c for c in good[o["comments_top"]:] if (c["replies"] or 0) > 0),
                     key=lambda c: (-(c["replies"] or 0), c["rank"] or 0, c["mention_id"]))[: o["comments_debated"]]
    return [(c, "top-ranked") for c in top] + [(c, "most replies") for c in debated]


def _num(v):
    """Snapshots are stored as REAL; whole numbers read better as ints (242, not 242.0)."""
    return int(v) if isinstance(v, float) and v.is_integer() else v


def _artifact_signals(ctx, ids):
    out = {}
    for sid in ids:
        for k, v in ctx.store.latest_signals(sid, ctx.at).items():
            out[k] = _num(max(out.get(k, v), v))
    return dict(sorted(out.items()))


def _compact_signals(g):
    if g.section == "papers":
        v = g.info.get("signals") or {}
        out = {k: _num(v[k]) for k in ("upvotes", "stars", "adoption", "hn_points", "trending_rank") if v.get(k)}
        if "fusion" in g.info:
            out["fusion"] = g.info["fusion"]
        return out
    if g.section == "releases":
        return {"likes": _num(g.info.get("likes", 0))}
    if g.section == "press_policy":
        return {"techmeme": bool(g.info.get("techmeme"))}
    if g.section == "discussion" and "points" in g.info:
        return {"points": g.info["points"]}
    return {}


def index_line(ctx, g):
    d = ctx.discussion(g.members, g.mentions)
    s = g.story
    cov = _coverage(ctx, g.members)
    line = {
        "id": g.id, "section": g.section, "rank": g.rank, "kind": s["kind"], "title": s["title"], "org": s["org"],
        "published_at": published(s), "tier": g.tier, "status": g.status, "sources": g.sources,
        "gist": gist(ctx, g, ctx.t["output"]["gist_max_chars"]),
        "discussion": {k: d[k] for k in ("band", "where", "spread", "trend")},
        "mainstream": bool(cov), "score": g.info.get("score"), "why": g.info.get("why"), "detail": detail_path(g.id),
    }
    if len(g.members) > 1:
        line["merged"] = g.members[1:]
    sig = _compact_signals(g)
    if sig:
        line["signals"] = sig
    if g.section == "press_policy":
        line["outlets"] = g.info.get("outlets", [])
    if g.status == "update":
        line["prev_reported"] = g.prev_reported
        line["days_discussed"] = g.info["days_discussed"]
    return line


def detail(ctx, g):
    o = ctx.t["output"]
    s = g.story
    d = ctx.discussion(g.members, g.mentions)
    cov = _coverage(ctx, g.members)
    members = [{"story_id": sid, "section": m["section"], "title": m["title"], "sources": m["sources"],
                "url": m["artifact_urls"][0] if m["artifact_urls"] else None, "published_at": published(m)}
               for sid, m in ((sid, g.stories[sid]) for sid in g.members)]
    union = lambda key: list(dict.fromkeys(u for sid in g.members for u in g.stories[sid][key]))  # noqa: E731
    if g.section == "papers":
        full = [{"story_id": g.id, "kind": "abstract", "url": s["artifact_urls"][0] if s["artifact_urls"] else None,
                 "text": sanitize.text(s["summary"], o["abstract_max_chars"]), "chars": len(s["summary"])}]
    else:
        # A thread's own post (Ask HN, Tell HN, a LessWrong post) first, then each member's page, the
        # representative's first.
        full = [{"story_id": sid, "kind": "post", "url": g.stories[sid]["extra"]["thread"]["thread"],
                 "text": sanitize.text(g.stories[sid]["summary"], o["full_text_max_chars"], keep_paragraphs=True),
                 "chars": len(g.stories[sid]["summary"])}
                for sid in g.members if "thread" in g.stories[sid]["extra"] and g.stories[sid]["summary"]]
        full += [{"story_id": p["story_id"], "kind": "page", "url": p["url"], "fetched_at": p["fetched_at"],
                  "text": sanitize.text(p["text"], o["full_text_max_chars"], keep_paragraphs=True), "chars": len(p["text"])}
                 for p in _pages(ctx, g)]
    sig = {"band": d["band"], "spread": d["spread"], "trend": d["trend"], "where": d["where"],
           "by_source": d["by_source"], "artifact": _artifact_signals(ctx, g.members)}
    if g.section == "press_policy":
        sig.update({"outlets": g.info.get("outlets", []), "techmeme": bool(g.info.get("techmeme"))})
    marks = ",".join("?" * len(g.members))
    refs = [r[0] for r in ctx.store.q(f"""SELECT raw_ref FROM items WHERE story_id IN ({marks}) AND role='anchor'
                                         ORDER BY source_id, item_key""", tuple(g.members))]
    refs += [r[0] for r in ctx.store.q(f"""SELECT raw_ref FROM story_pages WHERE story_id IN ({marks})
                                          ORDER BY story_id, url""", tuple(g.members))]
    doc = {
        "story_id": g.id, "section": g.section, "kind": s["kind"], "title": s["title"], "org": s["org"],
        "authors": s["authors"], "artifact_urls": union("artifact_urls"), "code_urls": union("code_urls"),
        "published_at": published(s), "first_seen": s["first_seen"], "tier": g.tier, "status": g.status,
        "sources": g.sources, "summary": summary(ctx, g), "members": members,
        "triage": {"mode": ctx.mode, "rank": g.rank, "score": g.info.get("score"), "relevant": g.info.get("relevant"),
                   "why": g.info.get("why"), "same_as": None,
                   "prompt_version": (ctx.scoring or {}).get("prompt_version"), "model": (ctx.scoring or {}).get("model")},
        "signals": sig, "discussion": _threads(ctx, g.members, g.mentions), "covered_by": cov,
        "mainstream_at": cov[0]["date"] if cov else None, "full_text": full, "raw_refs": list(dict.fromkeys(refs)),
    }
    if doc["summary"] != s["summary"]:
        doc["model_info"] = s["summary"]          # the Hub metadata the card's paragraph replaced
    if "fusion" in g.info:
        doc["triage"]["fusion"] = g.info["fusion"]
    for key in ("press", "about"):
        if g.info.get(key):
            doc[key] = list(g.info[key])
    if g.status == "update":
        doc["prev_reported"] = g.prev_reported
        doc["days_discussed"] = g.info["days_discussed"]
    return doc


def log_line(g):
    kept = g.alive
    line = {"id": g.id, "section": g.section, "title": g.story["title"], "tier": g.tier, "sources": g.sources,
            "outcome": "kept" if kept else ("merged" if g.stage == "merge" else "dropped"),
            "stage": "kept" if kept else g.stage, "reason": "" if kept else g.reason, "rank": g.rank,
            "candidate": bool(g.info.get("candidate")), "status": g.status}
    if g.info.get("merged_into"):
        line["merged_into"] = g.info["merged_into"]
    for key in ("why", "relevant", "score"):
        if key in g.info:
            line[key] = g.info[key]
    if g.alive and len(g.members) > 1:
        line["merged"] = g.members[1:]
    sig = _compact_signals(g)
    if sig:
        line["signals"] = sig
    return line


def section_order(ctx, groups):
    """Log order: section, then candidates by rank, then the rest by story_id."""
    out = []
    for sec in ctx.t["sections"]:
        gs = groups.get(sec, [])
        out += sorted(gs, key=lambda g: (g.rank is None, g.rank or 0, g.id))
    return out


def collect_summary(ctx, run_date):
    """Each source's status in the latest of the day's collect runs that included it (a re-run of
    one source, or the 6-hour poll, updates only its own sources)."""
    rows = ctx.store.q("SELECT run_id, report FROM runs WHERE kind='collect' AND run_date=? ORDER BY run_id",
                       (run_date,))
    by_id = {}
    for row in rows:
        for x in (loads(row["report"], {}) or {}).get("sources", []):
            e = {"id": x["id"], "status": x["status"]}
            if "counts" in x:
                e.update({"requests": x.get("requests", 0), "items": x["counts"].get("items", 0),
                          "new": x["counts"].get("new", 0)})
            if x.get("errors"):
                e["error"] = x["errors"][0][:300]
            if "counts" in x or x["id"] not in by_id:      # "disabled" in a one-source run says nothing
                by_id[x["id"]] = e
    order = {s.id: i for i, s in enumerate(ctx.cfg.sources)}
    srcs = sorted(by_id.values(), key=lambda e: (order.get(e["id"], 1e9), e["id"]))
    bad = any(e["status"] in ("failed", "partial") for e in srcs)
    return {"runs": [r["run_id"] for r in rows], "status": ("degraded" if bad else "ok") if rows else None,
            "sources": srcs}


def header(ctx, groups, run_date, ledger_doc, degraded):
    stages = {}
    for sec in ctx.t["sections"]:
        gs = groups.get(sec, [])
        st = {"pool": len(gs)}
        for stage in STAGES:
            st[stage] = sum(1 for g in gs if g.stage == stage)
        st["candidates"] = sum(1 for g in gs if g.info.get("candidate"))
        st["kept"] = sum(1 for g in gs if g.alive)
        stages[sec] = st
    coll = collect_summary(ctx, run_date)
    failed = sorted(x["id"] for x in coll["sources"] if x["status"] in ("failed", "partial"))
    reasons = list(degraded)
    if failed:
        reasons.append(f"sources failed or partial in the day's collect runs: {', '.join(failed)}")
    # Papers are left out: Hugging Face Daily Papers is a tier-1 source, so every paper is tier 1,
    # and their drops are the ranking working (triage_log.jsonl has them all).
    # The model's drops are left out too: nothing the skill reads lists them (triage_log.jsonl does).
    tier1 = [{"id": g.id, "section": g.section, "title": g.story["title"], "stage": g.stage, "reason": g.reason}
             for g in section_order(ctx, groups)
             if not g.alive and g.tier == 1 and g.stage not in ("merge", "score") and g.section != "papers"]
    return {
        "kind": "triage", "date": run_date, "at": ctx.at, "status": "degraded" if reasons else "ok",
        "degraded_reasons": reasons, "mode": ctx.mode, "scoring": ctx.scoring,
        "ledger": {"kind": ledger_doc["kind"], "stories": len(ledger_doc["dates"]), "errors": ledger_doc["errors"]},
        "collect": coll, "failed_sources": failed, "stages": stages,
        "candidates": sum(st["candidates"] for st in stages.values()),
        "kept": sum(st["kept"] for st in stages.values()), "tier1_dropped": tier1,
        "finished_at": ctx.finished_at,
    }


def _scoring_line(sc):
    if sc.get("error"):
        return [f"Scoring: **failed** ({_esc(sc['error'])[:300]}).", ""]
    u = sc.get("usage") or {}
    how = "replayed from the cache" if sc.get("cached") else f"{sc.get('attempts')} attempt(s), {u.get('duration_s')} s"
    return [f"Scoring: {sc['model']} ({sc['prompt_version']}), {how}; {u.get('input_tokens', 0):,} input and "
            f"{u.get('output_tokens', 0):,} output tokens ({u.get('thinking_tokens', 0):,} thinking), "
            f"${u.get('cost_usd_list_price', 0)} at list price (on the Max plan, quota rather than a bill)."
            + (f" Notes: {_esc('; '.join(sc['notes'][:5]))}." if sc.get("notes") else ""), ""]


def _esc(s):
    return str(s or "").replace("|", "\\|").replace("\n", " ")


def render(ctx, h, groups, kept):
    L = ["```json", json.dumps(h, sort_keys=True, ensure_ascii=False, indent=1), "```", "",
         f"# Triage {h['date']}", "",
         f"Status: **{h['status']}**" + (f" ({'; '.join(h['degraded_reasons'])})" if h["degraded_reasons"] else "") +
         f". Mode: {h['mode']}. Data as of {h['at']}. {h['candidates']} candidates, {h['kept']} kept.", "",
         *(_scoring_line(h["scoring"]) if h["scoring"] else []),
         f"Ledger: {h['ledger']['kind']} ({h['ledger']['stories']} stories)" +
         (f"; errors: {'; '.join(h['ledger']['errors'])}" if h["ledger"]["errors"] else "") + ".", "",
         "## Stages", "",
         "How many stories entered each section's pool, how many each stage dropped, and what's left. "
         "`score` is what the model found not relevant; those are listed only in triage_log.jsonl.", "",
         "| Section | Pool | " + " | ".join(STAGES) + " | Candidates | Kept |",
         "|---|---|" + "---|" * len(STAGES) + "---|---|"]
    for sec, st in h["stages"].items():
        L.append(f"| {sec} | {st['pool']} | " + " | ".join(str(st[x]) for x in STAGES) +
                 f" | {st['candidates']} | {st['kept']} |")
    L += ["", f"## Kept ({h['kept']})", ""]
    for sec in ctx.t["sections"]:
        rows = [line for line in kept if line["section"] == sec]
        if not rows:
            continue
        L += [f"### {sec} ({len(rows)})", "", "| Score | Story | Why | Sources | Discussion | Notes |",
              "|---|---|---|---|---|---|"]
        for line in rows:
            notes = []
            if line.get("merged"):
                notes.append(f"+{len(line['merged'])} merged")
            if line.get("outlets"):
                notes.append(f"{len(line['outlets'])} outlets")
            if line.get("signals"):
                notes.append(", ".join(f"{k} {v:g}" if isinstance(v, (int, float)) and not isinstance(v, bool)
                                       else f"{k} {v}" for k, v in line["signals"].items()))
            if line["status"] == "update":
                notes.append(f"update (reported {line['prev_reported']})")
            d = line["discussion"]
            L.append(f"| {line['score'] if line['score'] is not None else '–'} | {_esc(line['title'][:110])} `{line['id']}` "
                     f"| {_esc(line['why'] or '')} | {', '.join(line['sources'])} "
                     f"| {d['band']}, {d['trend']} | {_esc('; '.join(notes))} |")
        L.append("")
    merged = [g for g in section_order(ctx, groups) if len(g.members) > 1 and g.alive]
    if merged:
        L += ["## Merged and grouped", "", "Kept stories made of more than one story, with what was folded in.", ""]
        for g in merged:
            L.append(f"- `{g.id}` {_esc(g.story['title'][:100])}")
            for sid in g.members[1:]:
                m = g.stories[sid]
                L.append(f"  - `{sid}` ({', '.join(m['sources'])}) {_esc(m['title'][:100])}")
        L.append("")
    if h["tier1_dropped"]:
        L += ["## Tier-1 stories dropped", "", "| Section | Story | Stage | Reason |", "|---|---|---|---|"]
        for x in h["tier1_dropped"]:
            L.append(f"| {x['section']} | {_esc(x['title'][:100])} `{x['id']}` | {x['stage']} | {_esc(x['reason'])} |")
        L.append("")
    L += ["## Sources", "", f"Each source's status in the latest of the day's collect runs that included it "
          f"({', '.join(h['collect']['runs']) or 'none'}).", "",
          "| Source | Status | Requests | Items | New | Error |", "|---|---|---|---|---|---|"]
    for x in h["collect"]["sources"]:
        L.append(f"| `{x['id']}` | {x['status']} | {x.get('requests', '')} | {x.get('items', '')} | {x.get('new', '')} "
                 f"| {_esc(x.get('error', ''))[:200]} |")
    return "\n".join(L) + "\n"


def build(ctx, groups, run_date, ledger_doc, degraded, validator):
    """{relative path: text} for the whole run's output, every document validated."""
    order = section_order(ctx, groups)
    kept = [g for g in order if g.alive]
    for g in kept:
        if g.status == "update":
            g.info["days_discussed"] = days_discussed(ctx.store, g.members, ctx.at)
    lines = []
    for g in kept:
        line = index_line(ctx, g)
        validator.validate("index_line", line)
        lines.append(line)
    sec_rank = {sec: i for i, sec in enumerate(ctx.t["sections"])}
    lines.sort(key=lambda x: (sec_rank[x["section"]], -(x["score"] or 0), -times.parse(x["published_at"]).timestamp(),
                              x["id"]))
    files = {"stories_index.jsonl": "".join(dumps(x) + "\n" for x in lines)}
    for g in kept:
        doc = detail(ctx, g)
        validator.validate("story", doc)
        files[detail_path(g.id)] = json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=1) + "\n"
    log = []
    for g in order:
        x = log_line(g)
        validator.validate("triage_log", x)
        log.append(dumps(x) + "\n")
    files["triage_log.jsonl"] = "".join(log)
    h = header(ctx, groups, run_date, ledger_doc, degraded)
    validator.validate("report_header", h)
    files["run_report.md"] = render(ctx, h, groups, lines)
    return files, h


def write(out_dir, files):
    """Swap the run's files into out_dir together; the previous stories/ goes away."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    tmp = out_dir / f".triage-{stamp}"
    for rel, text in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    old = out_dir / f".stories-old-{stamp}"
    if (out_dir / "stories").exists():
        os.replace(out_dir / "stories", old)
    (tmp / "stories").mkdir(exist_ok=True)
    os.replace(tmp / "stories", out_dir / "stories")
    for rel in files:
        if not rel.startswith("stories/"):
            os.replace(tmp / rel, out_dir / rel)
    shutil.rmtree(tmp)
    if old.exists():
        shutil.rmtree(old)
