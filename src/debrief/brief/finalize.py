"""`debrief brief finalize`: the skill's draft plus the triage data, as the day's brief.

Writes to runs/<date>/, in this order:
  brief.json     self-contained (schemas/brief.schema.json): the draft's parts, with story ids
                 resolved to their index line (a merged member's id becomes its story's), plus
                   scan      every story triage kept, in index order: title, gist, link, and whether
                             the article covers it (headline and gist, for scanning)
                   stories   for every story the brief references: title, org, link, sources, code
                             links and threads, copied from the detail files
                   quotes    with author, source and thread link, from where the check found them
                   fetches   from the fetch hook's log (the record), with the skill's reason for each
                   meta      triage status, failed sources, counts, warnings, and the session
  brief.md       the same, as plain markdown, to read without a page
  reported.json  the ledger triage reads: every story in the scan, and the stories merged into them,
                 since the brief showed them all. Written last, and only for a written brief.

If there's no draft, or it doesn't pass the check, the brief is scan_only: the scan, with a note
saying why, and no reported.json (any earlier one for the day is removed), so triage offers those
stories again tomorrow. Nothing depends on the clock, so the same inputs give the same files.
"""
import json
import os

from ..triage.output import Validator
from . import check as checks
from .render import markdown


def _story(day, sid):
    d, line = day.detail(sid), day.by_id[day.rep[sid]]
    threads = [{"source": t["src"], "url": t["url"], "title": t["title"], "points": t["points"],
                "comments": t["comments"]} for t in d["discussion"]["threads"][:5]]
    return {"title": line["title"], "section": line["section"], "org": line["org"],
            "url": (d["artifact_urls"] or [None])[0], "sources": line["sources"], "code_urls": d["code_urls"][:3],
            "threads": threads}


def _scan(day, covered):
    return [{"id": x["id"], "section": x["section"], "title": x["title"], "gist": x["gist"],
             "url": (day.detail(x["id"])["artifact_urls"] or [None])[0], "org": x["org"], "status": x["status"],
             "covered": x["id"] in covered} for x in day.index]


def _meta(day, covered, words, warnings):
    h = day.header
    return {"triage_status": h.get("status") or "unknown", "failed_sources": list(h.get("failed_sources", [])),
            "kept": len(day.index), "covered": len(covered), "words": words, "warnings": warnings,
            "session": day.session()}


def build(day, result):
    """The brief.json document for a draft that passed the check (`result` is its check result)."""
    draft, rep = day.draft(), day.rep
    ids = lambda xs: list(dict.fromkeys(rep[x] for x in xs))  # noqa: E731
    sections = []
    for s in draft["sections"]:
        quotes = []
        for q in s.get("quotes", []):
            kind, _, author, source, url = checks.find_quote(day.detail(q["story"]), q["text"])
            quotes.append({"story": rep[q["story"]], "text": q["text"], "kind": kind, "author": author,
                           "source": source, "url": url})
        out = {"heading": s["heading"], "body": s["body"], "stories": ids(s["stories"]), "quotes": quotes}
        if s.get("split"):
            out["split"] = s["split"]
        sections.append(out)
    what = [{"text": e["text"], "stories": ids(e["stories"])} for e in draft["what_happened"]]
    updates = [{"story": rep[u["story"]], "text": u["text"], "prev_reported": day.by_id[rep[u["story"]]].get("prev_reported")}
               for u in draft.get("updates", [])]
    referenced = list(dict.fromkeys([*(sid for e in what for sid in e["stories"]),
                                     *(sid for s in sections for sid in [*s["stories"], *(q["story"] for q in s["quotes"])]),
                                     *(u["story"] for u in updates)]))
    notes_by_url = draft.get("fetch_notes") or {}
    fetches = [{"url": f["url"], "allowed": f["allowed"], "why": notes_by_url.get(f["url"])} for f in day.fetches()]
    covered = set(referenced)
    return {"date": day.date, "mode": "written", "headline": draft["headline"], "what_happened": what,
            "lead": draft["lead"], "sections": sections, "updates": updates, "notes": draft.get("notes", []),
            "scan": _scan(day, covered), "stories": {sid: _story(day, sid) for sid in sorted(covered)},
            "fetches": fetches, "meta": _meta(day, covered, result["words"], result["warnings"])}


def scan_only(day, why):
    """The fallback brief: only the scan. `why` goes to meta.warnings, not to the reader."""
    return {"date": day.date, "mode": "scan_only", "headline": "", "what_happened": [], "lead": "", "sections": [],
            "updates": [], "notes": ["The brief couldn't be written today. Below is every story triage kept."],
            "scan": _scan(day, set()), "stories": {},
            "fetches": [{"url": f["url"], "allowed": f["allowed"], "why": None} for f in day.fetches()],
            "meta": _meta(day, set(), 0, [f"scan only: {why}"])}


def _write(path, text):
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def finalize(day, validator=None):
    """Write the day's brief. Returns (brief.json document, check result)."""
    validator = validator or Validator()
    result = checks.check(day, validator)
    doc = build(day, result) if result["ok"] else scan_only(day, "; ".join(result["errors"])[:500])
    validator.validate("brief", doc)
    _write(day.dir / "brief.json", json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=1) + "\n")
    _write(day.dir / "brief.md", markdown(doc))
    ledger = day.dir / "reported.json"
    if doc["mode"] == "written":
        story_ids = list(dict.fromkeys(sid for x in day.index for sid in [x["id"], *x.get("merged", [])]))
        rep_doc = {"date": day.date, "story_ids": story_ids}
        validator.validate("reported", rep_doc)
        _write(ledger, json.dumps(rep_doc, sort_keys=True, ensure_ascii=False, indent=1) + "\n")
    elif ledger.exists():
        ledger.unlink()
    return doc, result
