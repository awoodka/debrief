"""`debrief jobs collect`: fetch every job source, keep the raw responses, build postings.

Sources run one after another, by family: the watchlist first (a board's own data is the best
version of a job, and a posting keeps the first company and title it's made with), then the
community lists, the job APIs, Hacker News, and last the board directory. Within a family,
config/jobs/sources.yml order. For each source:
1. The adapter fetches through the polite client; each response becomes a raw record.
2. The records are written to the raw store (<data>/jobs/raw/) before anything parses them.
3. In one transaction, build.apply() stores items and postings; only then are the source's ETags
   saved. A crash before that leaves the source to retry next run.

Statuses: ok, partial (some requests failed), failed, empty (responses, but no jobs at all),
needs_key (its key isn't set; nothing requested), not_due (the weekly directory), disabled,
probe_only (no collector: example boards, big-company APIs that aren't on the watchlist), and
not_built (the alert inbox and the miss-check, which arrive in M3).

`--replay DATE` rebuilds that day's runs from the raw files into a new database, with no network.
"""
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..http import Client
from ..store import dumps, raw
from . import build, profile as profiles
from .adapters import REGISTRY, Ctx
from .store import JobsStore, state_path

ORDER = ("watchlist", "list", "api", "discussion", "universe", "detail")
LATER = {"email": "M3", "check": "M3"}


def ordered(sources):
    return sorted(sources, key=lambda s: ORDER.index(s.family) if s.family in ORDER else len(ORDER))


def early_sources(cfg):
    """The community lists (family "list"): every one is a new-grad list, so all their listings are
    early-career."""
    return frozenset(s.id for s in cfg.sources if s.family == "list")


def eligibility(source, store, env, now):
    if not source.enabled:
        return "disabled"
    if source.family in LATER:
        return "not_built"
    if not source.adapter:
        return "probe_only"
    if source.adapter not in REGISTRY:
        raise ValueError(f"{source.id}: unknown adapter {source.adapter!r}")
    if source.missing_keys(env):
        return "needs_key"
    if REGISTRY[source.adapter].weekly:
        row = store.one("SELECT last_ok_at FROM source_state WHERE source_id=?", (source.id,))
        if row and row[0] and datetime.fromisoformat(row[0]) > now - timedelta(days=7):
            return "not_due"
    return None


def _failed(r, tolerate=()):
    return not raw.ok(r) and r.get("status") != 304 and r.get("status") not in tolerate


def classify(records, counts, crash, quiet_ok=False, tolerate=()):
    failed = [r for r in records if _failed(r, tolerate)]
    if not records:
        return "failed" if crash else ("ok" if quiet_ok else "empty")
    if len(failed) == len(records):
        return "failed"
    if failed or crash:
        return "partial"
    if (counts.get("items", 0) == 0 and counts.get("directory", 0) == 0 and counts.get("texts", 0) == 0 and not quiet_ok
            and not all(r.get("status") == 304 for r in records)):
        return "empty"
    return "ok"


def collect_source(cfg, store, client, source, run_id, run_date, now, prof, env, log):
    adapter = REGISTRY[source.adapter]
    ctx = Ctx(client, store, env, run_id, prof, cfg.contact, cfg.settings, log, early_sources=early_sources(cfg))
    crash = None
    try:
        adapter.fetch(ctx, source)
    except Exception as e:  # noqa: BLE001 — one broken source never sinks the run
        crash = f"fetch crashed: {type(e).__name__}: {e}"[:300]
    refs = raw.write(cfg.data_dir, run_date, source.id, run_id, ctx.records) if ctx.records else []
    counts = Counter()
    stamp = now.isoformat(timespec="seconds")
    try:
        with store.transaction():
            counts = build.apply(store, source, adapter, ctx.records, refs, warn=ctx.warn,
                                 early_sources=early_sources(cfg))
            status = classify(ctx.records, counts, crash, quiet_ok=adapter.quiet_ok, tolerate=adapter.tolerate)
            for rec, ref in zip(ctx.records, refs):
                store.record_fetch(run_id, source.id, rec["seq"], rec["url"], rec["fetched_at"], rec.get("status", 0),
                                   len(raw.body(rec)), rec.get("error") or None, ref)
            if status != "failed":
                for url, etag, last_modified in ctx.validators:
                    store.save_validators(url, etag, last_modified, stamp)
            errors = ([crash] if crash else []) + [f"{r['url']}: {r.get('error') or r.get('status')}" for r in ctx.records
                                                    if _failed(r, adapter.tolerate)]
            store.save_source_state(source.id, run_id, status, "; ".join(errors)[:500] or None, stamp)
    except Exception as e:  # noqa: BLE001 — a build crash rolls back this source only
        status, errors = "failed", [f"build crashed: {type(e).__name__}: {e}"[:300]]
        store.save_source_state(source.id, run_id, status, errors[0], stamp)
    return {"id": source.id, "status": status, "requests": len(ctx.records), "counts": dict(counts),
            "errors": errors[:10], "warnings": ctx.warnings}


def _new_run_id(store, started):
    base = started.strftime("%Y%m%dT%H%M%SZ")
    run_id, n = base, 1
    while store.one("SELECT 1 FROM runs WHERE run_id=?", (run_id,)):
        n += 1
        run_id = f"{base}-{n}"
    return run_id


def widened(prof, since, now):
    """The profile with max_age_days reaching back to `since` (a date), for the detail steps of a
    one-time look at older jobs (collect --since); every other source and step keeps the profile."""
    from copy import deepcopy
    from datetime import date
    out = deepcopy(prof)
    days = (now.date() - date.fromisoformat(str(since))).days + 1
    out["filters"]["max_age_days"] = max(out["filters"]["max_age_days"], days)
    return out


def run(cfg, only=None, run_date=None, client=None, env=os.environ, log=print, since=None):
    """since: a date; for this run only, the board finder and describe target jobs posted back to
    it (the older-roles catch-up, docs/jobs/PLAN.md "Revision 6")."""
    started = datetime.now(timezone.utc)
    local = started.astimezone()
    run_date = run_date or local.date().isoformat()
    store = JobsStore(state_path(cfg))
    run_id = _new_run_id(store, started)
    store.start_run(run_id, "collect", run_date, started.strftime("%Y-%m-%dT%H:%M:%SZ"))
    client = client or Client(cfg.user_agent, cfg.settings["http"])
    version, prof = profiles.latest(cfg)
    report = {"kind": "collect", "run_id": run_id, "run_date": run_date,
              "started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "profile": {"version": version, "id": profiles.profile_id(prof) if prof else None}, "sources": [],
              "since": since}
    wide = widened(prof, since, started) if since and prof else prof
    if prof is None:
        log("no profile saved: the watchlist and the profile-driven searches (Adzuna, USAJobs) have nothing to do")
    for source in ordered(cfg.sources):
        if only and source.id not in only:
            continue
        why = eligibility(source, store, env, started)
        if why:
            report["sources"].append({"id": source.id, "status": why})
            continue
        log(f"[{source.id}]", end=" ", flush=True)
        entry = collect_source(cfg, store, client, source, run_id, run_date, started,
                               wide if source.family == "detail" else prof, env, log)
        c = entry["counts"]
        log(f"{entry['status']}: {entry['requests']} req, {c.get('items', 0)} items, {c.get('new', 0)} new "
            f"({c.get('postings_new', 0)} new postings, {c.get('merged', 0)} merged), {c.get('closed', 0)} closed"
            + (f", {c['texts']} descriptions" if c.get("texts") else "")
            + (f" — {entry['errors'][0]}" if entry["errors"] else ""))
        report["sources"].append(entry)
    removed = raw.prune(cfg.data_dir, cfg.settings["collect"]["raw_retention_days"], run_date)
    finish(cfg, store, report, removed)
    store.close()
    return report


def finish(cfg, store, report, pruned=()):
    report["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    statuses = Counter(s["status"] for s in report["sources"])
    totals = Counter()
    for s in report["sources"]:
        totals.update(s.get("counts", {}))
    report["statuses"], report["totals"], report["raw_pruned"] = dict(statuses), dict(totals), list(pruned)
    report["postings"] = {"open": store.one("SELECT COUNT(*) FROM postings WHERE closed_at IS NULL")[0],
                          "closed": store.one("SELECT COUNT(*) FROM postings WHERE closed_at IS NOT NULL")[0],
                          "listings": store.one("SELECT COUNT(*) FROM items")[0]}
    report["status"] = "ok" if not (statuses.get("failed") or statuses.get("partial")) else "degraded"
    store.finish_run(report["run_id"], report["finished_at"], report["status"], report)
    out = cfg.data_dir / "runs" / report["run_date"]
    out.mkdir(parents=True, exist_ok=True)
    stem = f"collect-{report['run_id']}"
    (out / f"{stem}.json").write_text(dumps(report) + "\n")
    (out / f"{stem}.md").write_text(render(report))


def render(report):
    t, p = report["totals"], report["postings"]
    L = [f"# Jobs collect {report['run_id']} ({report['run_date']})", "",
         f"Status: **{report['status']}**. Sources: " + ", ".join(f"{k} {v}" for k, v in sorted(report["statuses"].items())) + ".",
         f"Profile version {report['profile']['version']}.", "",
         f"Listings read {t.get('items', 0)}, new {t.get('new', 0)} ({t.get('postings_new', 0)} new postings, "
         f"{t.get('merged', 0)} joined an existing posting), changed {t.get('changed', 0)}, closed postings "
         f"{t.get('closed', 0)}, reopened {t.get('reopened', 0)}. Skipped (closed before we saw them): "
         f"{t.get('skipped_inactive', 0)}. Directory rows {t.get('directory', 0)}.", "",
         f"Now: {p['open']} open postings, {p['closed']} closed, from {p['listings']} listings.", "",
         "| Source | Status | Requests | Listings | New | New postings | Merged | Closed | Notes |",
         "|---|---|---|---|---|---|---|---|---|"]
    for s in report["sources"]:
        if "counts" not in s:
            continue
        c = s["counts"]
        notes = "; ".join([*s["errors"], *s["warnings"]]).replace("|", "\\|")[:300]
        L.append(f"| `{s['id']}` | {s['status']} | {s['requests']} | {c.get('items', 0)} | {c.get('new', 0)} | "
                 f"{c.get('postings_new', 0)} | {c.get('merged', 0)} | {c.get('closed', 0)} | {notes} |")
    skipped = [s for s in report["sources"] if "counts" not in s]
    if skipped:
        L += ["", "Not collected: " + ", ".join(f"`{s['id']}` ({s['status']})" for s in skipped)]
    return "\n".join(L) + "\n"


TABLES = ("postings", "posting_keys", "items", "posting_text", "directory")


def replay(cfg, run_date, into, log=print, fresh=True):
    """Rebuild one day's runs from raw files into a new database at `into`, with no network
    (fresh=False: into an existing one, as rebuild() does day after day)."""
    into = Path(into)
    if fresh and into.exists():
        raise FileExistsError(f"{into} exists; replay builds a fresh database")
    store = JobsStore(into)
    by_id = {s.id: s for s in cfg.sources}
    totals = Counter()
    runs = raw.runs_for_day(cfg.data_dir, run_date)
    for run_id, by_source in runs.items():
        for source in ordered(cfg.sources):
            if source.id not in by_source or not source.adapter:
                continue
            rel, records = by_source[source.id]
            refs = [f"{rel}#{r['seq']}" for r in records]
            with store.transaction():
                totals.update(build.apply(store, source, REGISTRY[source.adapter], records, refs,
                                          early_sources=early_sources(cfg)))
        for sid in sorted(set(by_source) - set(by_id)):
            log(f"replay {run_id}: skipping {sid} (no longer in sources.yml)")
    store.close()
    return {"runs": list(runs), "totals": dict(totals), "into": str(into)}


# Live-only tables, not rebuilt from raw: carried over by rebuild() when the old database has them.
STATE_TABLES = ("source_state", "http_validators", "runs", "fetches", "scores", "daily_list", "highlighted", "meta")


def remap_carried(con):
    """Inside rebuild(), with the old database attached as `old`: rows the carried tables keep for
    a posting the rebuilt database no longer has (a matching change merged it into another, or
    renumbered it) move to the posting its listings now belong to (old and new items joined on
    source and item key). A job shown before stays shown, so it never comes back as a repeat; a
    score moves only where the posting has none for that profile (merges never join different job
    ids); rows of postings that still exist are untouched. Returns {table: rows moved}."""
    from collections import defaultdict
    pairs = con.execute("""SELECT DISTINCT o.posting_id, n.posting_id FROM old.items o
                           JOIN main.items n ON n.source_id = o.source_id AND n.item_key = o.item_key
                           WHERE o.posting_id != n.posting_id
                             AND o.posting_id NOT IN (SELECT posting_id FROM main.postings)""").fetchall()
    to = defaultdict(set)
    for gone, now in pairs:
        to[gone].add(now)
    tables = {r[0] for r in con.execute("SELECT name FROM main.sqlite_master WHERE type='table'")}
    moved = Counter()
    for gone, nows in sorted(to.items()):
        nows = sorted(nows)
        for table in ("highlighted", "scores"):
            if table not in tables:
                continue
            rows = con.execute(f"SELECT * FROM main.{table} WHERE posting_id=?", (gone,)).fetchall()
            for row in rows:
                for now in nows:
                    con.execute(f"INSERT OR IGNORE INTO main.{table} VALUES ({','.join('?' * len(row))})", (now, *row[1:]))
            moved[table] += con.execute(f"DELETE FROM main.{table} WHERE posting_id=?", (gone,)).rowcount
        for table in ("daily_list", "coverage_samples"):
            if table in tables:
                moved[table] += con.execute(f"UPDATE main.{table} SET posting_id=? WHERE posting_id=?",
                                            (nows[0], gone)).rowcount
    return {k: v for k, v in moved.items() if v}


def rebuild(cfg, log=print):
    """Rebuild the state database from every raw day after a parser or matching change, with no
    network. Replays all of raw/ in date and run order into a new database, carries over what
    replay can't reproduce (ETags, source state, run history, scores, lists, what was shown; rows of
    a posting the rebuild merged away move to its new posting: remap_carried), backs up the current database next
    to it, and copies the rebuilt one over it. Only as complete as the raw store (60 days)."""
    import sqlite3
    live = state_path(cfg)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tmp = live.with_name(f"rebuild-{stamp}.sqlite")
    root = cfg.data_dir / "raw"
    dates = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    totals = Counter()
    for d in dates:
        out = replay(cfg, d, tmp, log=log, fresh=False)
        totals.update(out["totals"])
        log(f"rebuild: {d} ({len(out['runs'])} runs)")
    JobsStore(tmp).close()                       # an empty raw store still gets a migrated database
    new = sqlite3.connect(tmp)
    backup_name, moved = None, {}
    if live.exists():
        new.execute("ATTACH DATABASE ? AS old", (str(live),))
        old_tables = {r[0] for r in new.execute("SELECT name FROM old.sqlite_master WHERE type='table'")}
        for name in STATE_TABLES:
            if name not in old_tables:
                continue
            keep_version = " WHERE key != 'schema_version'" if name == "meta" else ""   # the new file's own version stays
            new.execute(f"DELETE FROM main.{name}{keep_version}")
            new.execute(f"INSERT INTO main.{name} SELECT * FROM old.{name}{keep_version}")
        moved = remap_carried(new)
        if moved:
            log(f"rebuild: rows moved to the postings their listings now belong to: {moved}")
        new.commit()
        new.execute("DETACH DATABASE old")
        backup_name = f"jobs.before-rebuild-{stamp}.sqlite"
        backup = sqlite3.connect(live.with_name(backup_name))
        sqlite3.connect(live).backup(backup)
        backup.close()
    target = sqlite3.connect(live)
    new.backup(target)                           # a page-level copy into the live file; safe with WAL
    target.close()
    new.close()
    tmp.unlink()
    return {"dates": dates, "totals": dict(totals), "backup": backup_name, "moved": moved}
