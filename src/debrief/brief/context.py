"""`debrief brief context`: runs/<date>/brief_context.json, the first thing the skill reads.

What the skill can't see by reading the index alone:
  - the day's triage run: status, mode, data time, how many stories were kept, why it's degraded
  - sources that failed or were partial in the day's collect runs, so the brief can say so
  - the stories that are follow-ups (status update), with when they were reported
  - the last brief.recent_briefs written briefs: headline, what_happened and the stories they
    covered, so the brief can pick up threads and doesn't repeat yesterday's framing
  - the limits the check enforces
Sorted keys, no clock: the same day gives the same file.
"""
import json
import os

from .day import DRAFT


def build(day):
    o = day.cfg.settings["brief"]
    h = day.header
    coll = {x["id"]: x for x in (h.get("collect") or {}).get("sources", [])}
    failed = [{"id": sid, "status": coll.get(sid, {}).get("status"), "error": coll.get(sid, {}).get("error")}
              for sid in h.get("failed_sources", [])]
    kept = {}
    for line in day.index:
        kept[line["section"]] = kept.get(line["section"], 0) + 1
    recent = []
    for doc in day.earlier_briefs(o["recent_briefs"]):
        covered = sorted({sid for e in doc["what_happened"] for sid in e["stories"]}
                         | {sid for s in doc["sections"] for sid in s["stories"]})
        recent.append({"date": doc["date"], "headline": doc["headline"],
                       "what_happened": [e["text"] for e in doc["what_happened"]],
                       "covered": [{"id": sid, "title": doc["stories"].get(sid, {}).get("title")} for sid in covered]})
    return {
        "date": day.date,
        "files": {"index": "stories_index.jsonl", "details": "each index line's `detail` path, under this folder",
                  "draft": DRAFT},
        "triage": {k: h.get(k) for k in ("status", "mode", "at", "candidates", "kept", "degraded_reasons")},
        "kept_by_section": kept,
        "failed_sources": failed,
        "updates": [{"id": x["id"], "title": x["title"], "prev_reported": x.get("prev_reported")}
                    for x in day.index if x["status"] == "update"],
        "recent_briefs": recent,
        "limits": {"fetches": o["fetch_cap"], "article_words": o["words"], "what_happened": o["what_happened"],
                   "heading_max_chars": o["heading_max_chars"]},
    }


def write(day):
    """Write brief_context.json, and move an earlier draft aside (the skill writes a fresh one; Claude
    Code's Write refuses to replace a file it hasn't read). Returns the context."""
    ctx = build(day)
    if day.draft_path.exists():
        n = 1
        while (day.dir / f"{DRAFT}.{n}").exists():
            n += 1
        os.replace(day.draft_path, day.dir / f"{DRAFT}.{n}")
    tmp = day.dir / ".brief_context.json.tmp"
    tmp.write_text(json.dumps(ctx, sort_keys=True, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, day.dir / "brief_context.json")
    return ctx
