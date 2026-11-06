"""What the jobs site shows (docs/jobs/PLAN.md, M4), read from the jobs database and data folder.
Everything here only reads; the one write the site makes, the Tracked mark, is store.set_tracked.

The lists come from the same place as `jobs today`: each list's rows in daily_list, and the run
report (runs.report) that holds its entries and the jobs kept but not shown.
"""
import difflib
import json
from collections import Counter
from datetime import date, timedelta
from urllib.parse import urlsplit

from ...store import raw
from .. import profile as profiles, rank, score
from ..coverage import summary_line

LIST_STAGES = ("beyond N", "company limit")


def safe_url(u):
    """The URL if it's http(s) with a host, else None: the site links nothing else."""
    try:
        p = urlsplit(u or "")
    except ValueError:
        return None
    return u if p.scheme in ("http", "https") and p.netloc else None


def anchor(posting_id):
    return "job-" + "".join(ch if ch.isalnum() else "-" for ch in posting_id)


def _report(store, run_id):
    row = store.one("SELECT report FROM runs WHERE run_id=?", (run_id,)) if run_id else None
    return json.loads(row[0]) if row and row[0] else {}


def card(store, prof, e, tracked):
    """One job as the templates draw it, from a list entry (or a cut) plus its posting."""
    p = store.posting(e["posting_id"]) or {}
    d = p.get("data") or {}
    when = e.get("posted") or (rank.posted(p) if p else None)
    return {"posting_id": e["posting_id"], "anchor": anchor(e["posting_id"]), "rank": e.get("rank"),
            "score": e.get("score"), "bonus": e.get("bonus") or 0,
            "title": rank.display_title(e.get("title") or p.get("title") or "", prof),
            "company": e.get("company") or p.get("company") or "",
            "places": list(e.get("locations") or p.get("locations") or [])[:3],
            "pay": score.pay_line(d.get("pay")), "posted": rank._day(when) if when else "",
            "why": e.get("why"), "mismatches": list(e.get("mismatches") or []),
            "no_description": bool(p) and not store.texts_for(e["posting_id"]),
            "url": safe_url(e.get("url") or p.get("url")),
            "also": [{"title": o.get("title") or "another listing", "url": safe_url(o.get("url"))}
                     for o in e.get("also") or [] if safe_url(o.get("url"))],
            "more": e.get("more") or 0, "new": e.get("new", True),
            "tracked": e["posting_id"] in tracked, "closed": bool(p.get("closed_at") or d.get("gone")),
            "reason": e.get("reason") if e.get("stage") else None}


def list_dates(store):
    return [r[0] for r in store.q(f"SELECT DISTINCT list_date FROM daily_list WHERE section IN ({_marks(rank.DAILY)})"
                                  " ORDER BY list_date DESC", rank.DAILY)]


def catchup_lists(store):
    """[{name, title, date, count}] for every catch-up list, newest first."""
    out = []
    for name, d, n in store.q(f"""SELECT section, MAX(list_date), COUNT(*) FROM daily_list WHERE section NOT IN
                                  ({_marks(rank.DAILY)}) GROUP BY section ORDER BY MAX(list_date) DESC, section""", rank.DAILY):
        out.append({"name": name, "title": rank.TITLES.get(name, f"Catch-up: {name}"), "date": d, "count": n})
    return out


def _marks(xs):
    return ",".join("?" * len(xs))


def daily_view(store, prof, list_date=None):
    dates = list_dates(store)
    list_date = list_date if list_date in dates else (dates[0] if dates else None)
    from ...site.page import fmt_date
    view = {"date": list_date, "day": fmt_date(list_date) if list_date else "", "dates": dates, "sections": [],
            "beyond": [], "header": None, "coverage": summary_line(store, list_date) if list_date else None}
    if not list_date:
        return view
    row = store.one(f"SELECT run_id FROM daily_list WHERE list_date=? AND section IN ({_marks(rank.DAILY)}) LIMIT 1",
                    (list_date, *rank.DAILY))
    report = _report(store, row[0] if row else None)
    tracked = store.tracked()
    entries = report.get("list") or {}
    for key, title in (("new", "New"), ("watchlist", "Watchlist")):
        cards = [card(store, prof, e, tracked) for e in entries.get(key) or []]
        if cards:
            view["sections"].append({"key": key, "title": title, "cards": cards})
    view["beyond"] = [card(store, prof, c, tracked) for c in report.get("cuts") or [] if c.get("stage") in LIST_STAGES]
    shown = entries.get("new") or []
    fresh = sum(1 for e in shown if e.get("new", True))
    view["header"] = {"shown": len(shown), "kept": report.get("kept_distinct", report.get("kept")), "new": fresh,
                      "carried": len(shown) - fresh, "version": (report.get("profile") or {}).get("version"),
                      "status": report.get("status")}
    return view


def catchup_view(store, prof, name):
    row = store.one("SELECT MAX(list_date) FROM daily_list WHERE section=?", (name,))
    if not row or not row[0]:
        return None
    run = store.one("SELECT run_id FROM daily_list WHERE list_date=? AND section=? LIMIT 1", (row[0], name))
    report = _report(store, run[0] if run else None)
    tracked = store.tracked()
    return {"name": name, "title": rank.TITLES.get(name, f"Catch-up: {name}"), "date": row[0], "day": rank._day(row[0]),
            "since": rank._day(report["posted_since"]) if report.get("posted_since") else None,
            "cards": [card(store, prof, e, tracked) for e in (report.get("list") or {}).get(name) or []]}


def watch_view(cfg, store, prof):
    from .. import watch
    notes = {c["name"]: c.get("note") for c in (prof or {}).get("companies") or []}
    return [{"name": n, "board": b, "open": k, "note": notes.get(n)} for n, b, k in watch.listing(cfg, store)]


def profile_history(cfg):
    """[{version, saved_at, note}] newest first."""
    hist = {}
    path = cfg.data_dir / "profile" / "history.jsonl"
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                h = json.loads(line)
            except ValueError:
                continue
            hist[h.get("version")] = h
    out = []
    for n, _ in reversed(profiles.versions(cfg)):
        h = hist.get(n) or {}
        out.append({"version": n, "saved_at": h.get("saved_at", ""), "note": h.get("note", "")})
    return out


def profile_version(cfg, n):
    """(text of version n, unified diff lines against n-1) or None."""
    paths = dict(profiles.versions(cfg))
    if n not in paths:
        return None
    text = json.dumps(json.loads(paths[n].read_text()), indent=2, ensure_ascii=False)
    prev = json.dumps(json.loads(paths[n - 1].read_text()), indent=2, ensure_ascii=False) if (n - 1) in paths else ""
    diff = list(difflib.unified_diff(prev.splitlines(), text.splitlines(), f"version {n - 1}", f"version {n}", lineterm=""))
    return text, diff


def coverage_view(cfg, store, today=None):
    row = store.one("SELECT report FROM runs WHERE kind='coverage' ORDER BY run_date DESC, started_at DESC LIMIT 1")
    cov = json.loads(row[0]) if row and row[0] else None
    row = store.one("SELECT report FROM runs WHERE kind='collect' AND finished_at IS NOT NULL ORDER BY started_at DESC LIMIT 1")
    col = json.loads(row[0]) if row and row[0] else None
    sources = []
    for s in (col or {}).get("sources") or []:
        c = s.get("counts") or {}
        sources.append({"id": s["id"], "status": s.get("status"), "requests": s.get("requests"), "items": c.get("items"),
                        "new": c.get("postings_new"), "exception": s.get("robots_exception"),
                        "error": (s.get("errors") or [None])[0]})
    sites, d0 = Counter(), today or date.today()
    for back in range(7):
        day = (d0 - timedelta(days=back)).isoformat()
        for _run, by_source in raw.runs_for_day(cfg.data_dir, day).items():
            for rec in (by_source.get("alert-inbox") or (None, []))[1]:
                if raw.ok(rec):
                    sites[(rec.get("meta") or {}).get("site") or "other"] += 1
    return {"coverage": cov, "collect": col, "sources": sources, "alerts": sorted(sites.items(), key=lambda kv: -kv[1])}
