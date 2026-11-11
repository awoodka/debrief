"""`debrief jobs coverage` (docs/jobs/PLAN.md, "Coverage" and "Revision 6"): how much of the market
the collector caught, measured against two independent samples, with a cause for every miss.

  google  Google's job results (SerpAPI: adapters/serpapi.py), read back from the raw store.
  alerts  the jobs the alert emails brought: did any other source have them too?

A Google sample is first checked against the profile (filters.check, with its description and its
estimated posting date): one Alex wouldn't want is "ineligible", keeps its filter stage, and stays
out of the recall. An eligible sample is matched against every posting, open or closed, whatever
the filters said:
  job id     an apply link is a job-board URL whose job id we have
  same job   same company, same core title, overlapping place
  near       a similar company name and titles sharing at least 60% of their core words: counted as
             found, but listed, since it points to a dedupe gap
An unmatched sample is "pending" and checked again on later runs, because the lists lag a day or
two; after `checks` runs it's a "miss" with a cause:
  not watched        an apply link is on a job board none of our sources read -> watch the company
  company site only  only the company's own careers site -> an alert, or the watchlist
  closed site only   only LinkedIn, Indeed, Glassdoor and the like -> widen that alert search
  broken source      the watchlist reads that board, but it failed that night
The report (runs/<date>/coverage-<run>.json and .md) has the day's and the last 7 days' recall, every
miss with its cause and suggestion, and every near match; `jobs today` prints its summary line.
"""
import hashlib
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

from ..store import dumps, loads, raw
from . import boards, filters, profile as profiles
from .build import _aggregator
from .items import company_norm, core_title, locations_overlap
from .store import JobsStore, state_path

SOURCE = "serpapi-google-jobs"
ALERTS = "alert-inbox"
OWN = (ALERTS, "board-finder", "describe")        # not independent of the alert sample
_AGO = re.compile(r"(\d+)\+?\s*(minute|hour|day|week|month)s?\s+ago", re.I)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def posted_from(text, fetched_at):
    """An ISO date from Google's "3 days ago" / "21 hours ago" / "just posted", or None."""
    at = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    t = (text or "").lower()
    if "just" in t or "today" in t:
        return _iso(at)
    m = _AGO.search(t)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    delta = {"minute": timedelta(minutes=n), "hour": timedelta(hours=n), "day": timedelta(days=n),
             "week": timedelta(weeks=n), "month": timedelta(days=30 * n)}[unit]
    return _iso(at - delta)


def samples_in(record):
    """[sample row] from one raw SerpAPI search response."""
    meta = record.get("meta") or {}
    if meta.get("kind") != "search" or not raw.ok(record):
        return []
    try:
        data = json.loads(raw.body(record))
    except ValueError:
        return []
    out = []
    for j in data.get("jobs_results") or []:
        title, company = (j.get("title") or "").strip(), (j.get("company_name") or "").strip()
        if not title or not company:
            continue
        ext = j.get("detected_extensions") or {}
        urls = [o.get("link") for o in j.get("apply_options") or [] if o.get("link")]
        ident = j.get("job_id") or hashlib.sha1(f"{title}|{company}|{j.get('location')}".encode()).hexdigest()[:20]
        out.append({"sample_key": f"google:{ident}", "sample": "google", "fetched_at": record["fetched_at"],
                    "query": f"{meta.get('q')} @ {meta.get('location')}", "title": title, "company": company,
                    "location": (j.get("location") or "").strip() or None, "via": (j.get("via") or "").removeprefix("via ").strip() or None,
                    "posted_at": posted_from(ext.get("posted_at"), record["fetched_at"]),
                    "apply_urls": urls, "text": (j.get("description") or "")[:6000] or None})
    return out


def ingest(cfg, store, run_date, days):
    """Add the last `days` days' Google samples (raw store) that aren't in coverage_samples yet."""
    n = 0
    d0 = date.fromisoformat(run_date)
    for back in range(days):
        day = (d0 - timedelta(days=back)).isoformat()
        for _run, by_source in raw.runs_for_day(cfg.data_dir, day).items():
            if SOURCE not in by_source:
                continue
            for rec in by_source[SOURCE][1]:
                for s in samples_in(rec):
                    if store.one("SELECT 1 FROM coverage_samples WHERE sample_key=?", (s["sample_key"],)):
                        continue
                    store.run("""INSERT INTO coverage_samples (sample_key, sample, first_date, fetched_at, query, title,
                                 company, location, via, posted_at, apply_urls, text) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                              (s["sample_key"], s["sample"], day, s["fetched_at"], s["query"], s["title"], s["company"],
                               s["location"], s["via"], s["posted_at"], dumps(s["apply_urls"]), s["text"]))
                    n += 1
    return n


def eligible(s, prof, now):
    """filters.check on the sample as if it were a posting: (ok, stage)."""
    p = {"posting_id": s["sample_key"], "title": s["title"], "company": s["company"],
         "locations": [s["location"]] if s["location"] else [], "published_at": s["posted_at"],
         "first_seen_at": s["fetched_at"], "data": {}}
    v = filters.check(p, prof, now, text=s["text"])
    return v.ok, v.stage


def _like(store, company):
    """Postings at companies whose normalized name starts with this one's first word (for near)."""
    first = company_norm(company).split(" ")[0]
    if len(first) < 3:
        return []
    return store.q("""SELECT DISTINCT p.posting_id, p.title, p.locations FROM posting_keys k JOIN postings p USING (posting_id)
                      WHERE k.key >= ? AND k.key < ?""", (f"fz:{first}", f"fz:{first}~"))


def match(store, s):
    """(posting_id, how) or (None, None)."""
    for u in s["apply_urls"]:
        jk = boards.job_from_url(u)
        pid = store.find_key(f"job:{jk}") if jk else None
        if pid:
            return pid, "job id"
    core = core_title(s["title"])
    locs = [s["location"]] if s["location"] else []
    for c in store.postings_at(company_norm(s["company"])):
        if core and core_title(c["title"]) == core and locations_overlap(loads(c["locations"], []), locs):
            return c["posting_id"], "same job"
    if core:
        for c in _like(store, s["company"]):
            other = core_title(c["title"])
            if other and len(core & other) / len(core | other) >= 0.6:
                return c["posting_id"], "near"
    return None, None


def cause(s, watch_boards, watch_failed):
    """(cause, suggestion) for a miss."""
    urls = s["apply_urls"]
    found = [b for b in (boards.board_from_url(u) for u in urls) if b]
    if found:
        b = found[0]
        if b in watch_boards and watch_failed:
            return "broken source", f"the watchlist reads {b}, but it failed that night"
        page = next(u for u in urls if boards.board_from_url(u) == b)
        return "not watched", f'{s["company"]} posts on {b}: debrief jobs watch add "{s["company"]}" --kind large|startup --url {page}'
    if urls and all(_aggregator(u) for u in urls):
        hosts = sorted({(urlsplit(u).hostname or "").removeprefix("www.") for u in urls})
        return "closed site only", f"only on {', '.join(hosts)}: widen the alert searches there"
    return "company site only", f"{s['company']}'s own careers site: an alert search, or the watchlist if it has a board"


def run(cfg, run_date=None, now=None, log=print, days=3, checks=3, window=7):
    started = now or datetime.now(timezone.utc)
    run_date = run_date or started.astimezone().date().isoformat()
    _, prof = profiles.latest(cfg)
    if prof is None:
        raise SystemExit("no profile saved (debrief jobs profile save FILE)")
    store = JobsStore(state_path(cfg))
    run_id, n = "coverage-" + started.strftime("%Y%m%dT%H%M%SZ"), 1
    while store.one("SELECT 1 FROM runs WHERE run_id=?", (run_id,)):
        n += 1
        run_id = f"coverage-{started:%Y%m%dT%H%M%SZ}-{n}"
    watch_boards = {c.get("board") for c in prof["companies"] if c.get("board")}
    wrow = store.one("SELECT last_status FROM source_state WHERE source_id='watchlist'")
    watch_failed = bool(wrow and wrow[0] in ("failed", "partial"))
    since = (date.fromisoformat(run_date) - timedelta(days=window - 1)).isoformat()
    with store.transaction():
        added = ingest(cfg, store, run_date, days)
        todo = store.q("""SELECT * FROM coverage_samples WHERE sample='google' AND first_date >= ?
                          AND status IN ('pending', 'near', 'matched', 'ineligible') ORDER BY sample_key""", (since,))
        for row in todo:
            s = {**dict(row), "apply_urls": loads(row["apply_urls"], [])}
            if s["status"] in ("matched", "ineligible"):
                continue
            ok, stage = eligible(s, prof, started)
            if not ok:
                store.run("UPDATE coverage_samples SET status='ineligible', stage=?, last_checked=? WHERE sample_key=?",
                          (stage, run_date, s["sample_key"]))
                continue
            pid, how = match(store, s)
            if pid and how != "near":
                store.run("UPDATE coverage_samples SET status='matched', posting_id=?, how=?, last_checked=? WHERE sample_key=?",
                          (pid, how, run_date, s["sample_key"]))
                continue
            if pid:                                          # near: found, but listed
                store.run("UPDATE coverage_samples SET status='near', posting_id=?, how='near', last_checked=? WHERE sample_key=?",
                          (pid, run_date, s["sample_key"]))
                continue
            n = s["checks"] + (0 if s["last_checked"] == run_date else 1)
            if n >= checks:
                c, why = cause(s, watch_boards, watch_failed)
                store.run("""UPDATE coverage_samples SET status='miss', checks=?, cause=?, suggestion=?, last_checked=?
                             WHERE sample_key=?""", (n, c, why, run_date, s["sample_key"]))
            else:
                store.run("UPDATE coverage_samples SET status='pending', checks=?, last_checked=? WHERE sample_key=?",
                          (n, run_date, s["sample_key"]))
    report = _report(store, run_id, run_date, started, since, added)
    with store.transaction():
        store.start_run(run_id, "coverage", run_date, _iso(started))
        store.finish_run(run_id, report["finished_at"], "ok", report)
    out = cfg.data_dir / "runs" / run_date
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{run_id}.json").write_text(dumps(report) + "\n")
    (out / f"{run_id}.md").write_text(render(report))
    store.close()
    log(report["summary"])
    return report


def _counts(rows):
    c = Counter(r["status"] for r in rows)
    found = c["matched"] + c["near"]
    decided = found + c["miss"]
    return {"sampled": len(rows), "eligible": decided + c["pending"], "found": found, "near": c["near"],
            "missed": c["miss"], "pending": c["pending"], "ineligible": c["ineligible"],
            "recall": round(found / decided, 3) if decided else None}


def _report(store, run_id, run_date, started, since, added):
    week = [dict(r) for r in store.q("SELECT * FROM coverage_samples WHERE sample='google' AND first_date >= ?", (since,))]
    day = [r for r in week if r["first_date"] == run_date]
    misses = sorted((r for r in week if r["status"] == "miss"), key=lambda r: (r["cause"] or "", r["company"]))
    near = [r for r in week if r["status"] == "near"]
    for r in near:
        p = store.posting(r["posting_id"]) or {}
        r["near_title"], r["near_company"] = p.get("title"), p.get("company")
    alert_rows = store.q("""SELECT DISTINCT i.posting_id FROM items i WHERE i.source_id=? AND substr(i.first_seen_at, 1, 10) >= ?""",
                         (ALERTS, since))
    only = sum(1 for r in alert_rows if not (store.posting_sources(r[0]) - set(OWN)))
    w = _counts(week)
    causes = Counter(r["cause"] for r in misses)
    if not w["sampled"]:
        summary = "Coverage: no Google sample yet (it needs the SerpAPI key)"
    elif w["recall"] is None:
        summary = "Coverage: no Google sample decided yet" + (f" ({w['pending']} pending)" if w["pending"] else "")
    else:
        summary = (f"Coverage (7 days): {round(100 * w['recall'])}% of {w['found'] + w['missed']} sampled jobs found"
                   + (f"; {w['missed']} missed: " + ", ".join(f"{n} {c}" for c, n in causes.most_common()) if w["missed"] else "")
                   + (f"; {w['pending']} pending" if w["pending"] else ""))
    if alert_rows:
        summary += f" · alerts brought {len(alert_rows)} jobs, {only} that no other source had"
    keep = ("title", "company", "location", "via", "posted_at", "query", "cause", "suggestion", "first_date")
    return {"kind": "coverage", "run_id": run_id, "run_date": run_date, "started_at": _iso(started),
            "finished_at": _iso(datetime.now(timezone.utc)), "since": since, "added": added,
            "google": {"day": _counts(day), "week": w},
            "ineligible_stages": dict(Counter(r["stage"] for r in week if r["status"] == "ineligible")),
            "alerts": {"week": len(alert_rows), "only_alerts": only},
            "misses": [{k: r[k] for k in keep} | {"apply_urls": loads(r["apply_urls"], [])} for r in misses],
            "near": [{k: r[k] for k in ("title", "company", "near_title", "near_company", "query")} for r in near],
            "summary": summary}


def render(r):
    g = r["google"]
    L = [f"# Jobs coverage {r['run_id']} ({r['run_date']})", "", r["summary"], "",
         "| Google sample | Today | Last 7 days |", "|---|---|---|"]
    for k in ("sampled", "ineligible", "eligible", "found", "near", "missed", "pending", "recall"):
        L.append(f"| {k} | {g['day'][k]} | {g['week'][k]} |")
    L += ["", f"Not jobs the profile wants (by filter): {r['ineligible_stages'] or 'none'}.",
          f"Alert emails, last 7 days: {r['alerts']['week']} jobs, {r['alerts']['only_alerts']} that no other source had.", ""]
    L += ["## Misses", ""] + ([f"- **{m['title']}** — {m['company']} ({m['location'] or '?'}; via {m['via'] or '?'}; "
                                f"sampled {m['first_date']}). Cause: **{m['cause']}**. {m['suggestion']}" for m in r["misses"]]
                              or ["(none)"])
    L += ["", "## Near matches (possible dedupe gaps)", ""] + (
        [f"- {n['title']} — {n['company']}  ≈  {n['near_title']} — {n['near_company']}" for n in r["near"]] or ["(none)"])
    return "\n".join(L) + "\n"


def summary_line(store, list_date):
    """The latest coverage summary on or before list_date, or None."""
    row = store.one("SELECT report FROM runs WHERE kind='coverage' AND run_date <= ? ORDER BY run_date DESC, started_at DESC LIMIT 1",
                    (list_date,))
    return json.loads(row[0]).get("summary") if row and row[0] else None
