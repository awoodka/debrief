"""`debrief collect`: fetch every eligible source, keep the raw responses, build stories.

Sources run one after another, in sources.yml order:
1. The adapter fetches through the polite client. Each response becomes a raw record.
2. The records are written to the raw store before anything parses them.
3. In one transaction, the builder stores items, stories and mentions, and only then are the
   source's ETags and cursor saved. A crash before that leaves the source to retry next run.

A failing source is reported and the run goes on. Statuses:
  ok         at least one response and no failures (zero *new* items is a quiet day, still ok)
  partial    some requests failed
  failed     every request failed, or the source crashed
  empty      responses arrived but contained nothing at all
  disabled / context / not_built   not collected (switched off, lookup-only, or no adapter yet)

Sources run in phases (PHASES): anchors, lookups about them, discussion, comment trees, reference.
After the last source, attach.retro() re-tries recent unattached posts against this run's stories.
`--cadence 6h` runs only the sources whose options.cadence includes "6h" (the discussion poll).

`collect --replay DATE` rebuilds from that day's raw files into a separate database with no
network, in the same order a live run used.
"""
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .adapters import REGISTRY, FetchContext
from .http import Client
from .store import Store, dumps, raw
from .stories import attach, build

COLLECT_ROLES = {"anchor", "discussion", "reference"}
# Run order. Anchors first, so lookups, discussion and coverage find this run's stories; lookups
# about stories (enrich) before the discussion sources; comment trees (threads) after the threads
# they hang off; reference sources last. Within a phase, sources.yml order.
PHASES = ("anchor", "enrich", "discussion", "threads", "reference")


def state_path(cfg):
    return cfg.data_dir / "state" / "debrief.sqlite"


def phase_of(source):
    adapter = REGISTRY.get(source.adapter)
    if adapter is not None and adapter.phase:
        return adapter.phase
    for role in ("anchor", "discussion", "reference"):
        if role in source.roles:
            return role
    return "enrich"


def ordered(sources):
    return sorted(sources, key=lambda s: PHASES.index(phase_of(s)))


def eligibility(source, cadence="daily"):
    if not source.enabled:
        return "disabled"
    if source.adapter not in REGISTRY:
        return "not_built" if COLLECT_ROLES & set(source.roles) else "context"
    if cadence not in source.options.get("cadence", ["daily"]):
        return "other_cadence"
    return None


def classify(records, counts, crash, nothing_due_ok=False):
    failed = [r for r in records if not raw.ok(r) and r.get("status") != 304]
    if not records and not crash and nothing_due_ok:
        return "ok"            # a lookup or comment-tree source with nothing due this run
    if not records or len(failed) == len(records):
        return "failed" if (crash or failed) else "empty"
    if failed or crash:
        return "partial"
    if counts.get("items", 0) == 0 and not all(r.get("status") == 304 for r in records):
        return "empty"
    return "ok"


def _errors(records, crash):
    errs = [f"{r['url']}: {r.get('error') or r.get('status')}" for r in records
            if not raw.ok(r) and r.get("status") != 304]
    return ([crash] if crash else []) + errs


def collect_source(cfg, store, client, source, run_id, run_date, now, log, backfill_hours=None):
    adapter = REGISTRY[source.adapter]
    ctx = FetchContext(client, store, os.environ, run_id, now, cfg.settings["collect"], log,
                       run_date=run_date, backfill_hours=backfill_hours, settings=cfg.settings,
                       enabled=(s.id for s in cfg.sources if s.enabled))
    crash = None
    try:
        adapter.fetch(ctx, source)
    except Exception as e:  # noqa: BLE001 — one broken source never sinks the run
        crash = f"fetch crashed: {type(e).__name__}: {e}"[:300]
    refs = raw.write(cfg.data_dir, run_date, source.id, run_id, ctx.records) if ctx.records else []
    counts = Counter()
    try:
        with store.transaction():
            counts = build.apply(store, cfg.settings, source, adapter, ctx.records, refs, run_date)
            status = classify(ctx.records, counts, crash, nothing_due_ok=adapter.phase in ("enrich", "threads"))
            for rec, ref in zip(ctx.records, refs):
                store.record_fetch(run_id, source.id, rec["seq"], rec["url"], rec["fetched_at"],
                                   rec.get("status", 0), len(raw.body(rec)), rec.get("error") or None, ref)
            if status != "failed":
                for url, etag, last_modified in ctx.validators:
                    store.save_validators(url, etag, last_modified, now.isoformat(timespec="seconds"))
            errors = _errors(ctx.records, crash)
            store.save_source_state(source.id, run_id, status, "; ".join(errors)[:500] or None,
                                    now.isoformat(timespec="seconds"),
                                    cursor=ctx.cursor if status in ("ok", "empty") else None)
    except Exception as e:  # noqa: BLE001 — a build crash rolls back this source only
        status, errors = "failed", [f"build crashed: {type(e).__name__}: {e}"[:300]]
        store.save_source_state(source.id, run_id, status, errors[0], now.isoformat(timespec="seconds"))
    return {"id": source.id, "status": status, "requests": len(ctx.records),
            "counts": dict(counts), "errors": errors, "warnings": ctx.warnings}


def _new_run_id(store, started):
    base = started.strftime("%Y%m%dT%H%M%SZ")
    run_id, n = base, 1
    while store.one("SELECT 1 FROM runs WHERE run_id=?", (run_id,)):
        n += 1
        run_id = f"{base}-{n}"          # sorts after base, so replay keeps run order
    return run_id


def roles_by_source(cfg):
    return {s.id: s.roles for s in cfg.sources}


def run(cfg, only=None, run_date=None, client=None, log=print, cadence="daily", backfill_hours=None):
    started = datetime.now(timezone.utc)
    local = started.astimezone()
    run_date = run_date or local.date().isoformat()
    store = Store(state_path(cfg))
    run_id = _new_run_id(store, started)
    store.start_run(run_id, "collect", run_date, started.strftime("%Y-%m-%dT%H:%M:%SZ"))
    client = client or Client(cfg.user_agent, cfg.settings["http"])
    report = {"kind": "collect", "run_id": run_id, "run_date": run_date, "cadence": cadence,
              "started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"), "sources": []}
    for source in ordered(cfg.sources):
        if only and source.id not in only:
            continue
        why = eligibility(source, cadence)
        if why:
            if why != "other_cadence":
                report["sources"].append({"id": source.id, "status": why})
            continue
        log(f"[{source.id}]", end=" ", flush=True)
        entry = collect_source(cfg, store, client, source, run_id, run_date, local, log, backfill_hours)
        c = entry["counts"]
        log(f"{entry['status']}: {entry['requests']} req, {c.get('items', 0)} items, {c.get('new', 0)} new, "
            f"{c.get('stories_new', 0)} new stories" + (f" — {entry['errors'][0]}" if entry["errors"] else ""))
        report["sources"].append(entry)
    last = store.one("SELECT MAX(fetched_at) FROM fetches WHERE run_id=?", (run_id,))[0]
    if last:
        with store.transaction():
            report["attach"] = dict(attach.retro(store, cfg.settings, last, roles_by_source(cfg)))
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
    report["status"] = "ok" if not (statuses.get("failed") or statuses.get("partial")) else "degraded"
    store.finish_run(report["run_id"], report["finished_at"], report["status"], report)
    out = cfg.data_dir / "runs" / report["run_date"]
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{report['kind']}-{report['run_id']}"
    (out / f"{stem}.json").write_text(dumps(report) + "\n")
    (out / f"{stem}.md").write_text(render(report))


def render(report):
    t = report["totals"]
    L = [f"# Collect run {report['run_id']} ({report['run_date']})", "",
         f"Status: **{report['status']}**. Sources: " +
         ", ".join(f"{k} {v}" for k, v in sorted(report["statuses"].items())) + ".", "",
         f"Items {t.get('items', 0)}, new {t.get('new', 0)}; stories new {t.get('stories_new', 0)}, "
         f"merged {t.get('stories_merged', 0)}, changed {t.get('stories_changed', 0)}; "
         f"mentions new {t.get('mentions_new', 0)}; recorded as seen {t.get('seen', 0)} "
         f"(of which too old {t.get('too_old', 0)}).", "",
         "| Source | Status | Requests | Items | New | Stories new / merged | Mentions | Notes |",
         "|---|---|---|---|---|---|---|---|"]
    for s in report["sources"]:
        if "counts" not in s:
            continue
        c = s["counts"]
        notes = "; ".join([*s["errors"], *s["warnings"]]).replace("|", "\\|")[:300]
        L.append(f"| `{s['id']}` | {s['status']} | {s['requests']} | {c.get('items', 0)} | {c.get('new', 0)} "
                 f"| {c.get('stories_new', 0)} / {c.get('stories_merged', 0)} | {c.get('mentions_new', 0)} | {notes} |")
    skipped = [s for s in report["sources"] if "counts" not in s]
    if skipped:
        L += ["", "Not collected: " + ", ".join(f"`{s['id']}` ({s['status']})" for s in skipped)]
    return "\n".join(L) + "\n"


def replay(cfg, run_date, into, log=print, fresh=True):
    """Rebuild one day's raw data into the database at `into` (which must not exist yet, unless
    fresh=False, as when rebuild() replays day after day into the same database)."""
    into = Path(into)
    if fresh and into.exists():
        raise FileExistsError(f"{into} exists; replay builds a fresh database")
    store = Store(into)
    runs = raw.runs_for_day(cfg.data_dir, run_date)
    by_id = {s.id: s for s in cfg.sources}
    totals = Counter()
    for run_id, by_source in runs.items():
        for sid in sorted(set(by_source) - set(by_id)):
            log(f"replay {run_id}: skipping {sid} (no longer in sources.yml)")
        last = None
        for source in ordered(cfg.sources):
            if source.id not in by_source:
                continue
            if source.adapter not in REGISTRY:
                log(f"replay {run_id}: skipping {source.id} (adapter {source.adapter!r} not built)")
                continue
            rel, records = by_source[source.id]
            refs = [f"{rel}#{r['seq']}" for r in records]
            last = max([last or "", *(r["fetched_at"] for r in records)])
            with store.transaction():
                totals.update(build.apply(store, cfg.settings, source, REGISTRY[source.adapter],
                                          records, refs, run_date))
        if last:   # the same end-of-run pass a live run made
            with store.transaction():
                totals.update(attach.retro(store, cfg.settings, last, roles_by_source(cfg)))
    store.close()
    return {"runs": list(runs), "totals": dict(totals), "into": str(into)}


STATE_TABLES = ("source_state", "http_validators", "runs", "fetches")   # live-only: not rebuilt from raw


def rebuild(cfg, log=print):
    """Rebuild the state database from every raw day after a parser change, without the network.

    Replays all of raw/ in date and run order into a new database, carries over what replay can't
    reproduce (cursors, ETags, run history), backs up the current database next to it, and copies
    the rebuilt one over it. Only as complete as the raw store: days pruned by raw_retention_days
    are gone."""
    import sqlite3
    live = state_path(cfg)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tmp = live.with_name(f"rebuild-{stamp}.sqlite")
    dates = sorted(p.name for p in (cfg.data_dir / "raw").iterdir() if p.is_dir())
    totals = Counter()
    for d in dates:
        out = replay(cfg, d, tmp, log=log, fresh=False)
        totals.update(out["totals"])
        log(f"rebuild: {d} ({len(out['runs'])} runs)")
    new = sqlite3.connect(tmp)
    if live.exists():
        new.execute("ATTACH DATABASE ? AS old", (str(live),))
        for t in STATE_TABLES:
            new.execute(f"DELETE FROM main.{t}")
            new.execute(f"INSERT INTO main.{t} SELECT * FROM old.{t}")
        new.commit()
        new.execute("DETACH DATABASE old")
        backup = sqlite3.connect(live.with_name(f"debrief.before-rebuild-{stamp}.sqlite"))
        sqlite3.connect(live).backup(backup)
        backup.close()
    target = sqlite3.connect(live)
    new.backup(target)            # page-level copy into the live file; safe with WAL
    target.close()
    new.close()
    tmp.unlink()
    return {"dates": dates, "totals": dict(totals), "backup": f"debrief.before-rebuild-{stamp}.sqlite"}
