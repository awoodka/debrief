"""`debrief triage [--date D]`: the day's candidates, what's kept, and the output files.

The run's time `at` is the last fetch of that day's collect runs, so re-running triage on the same
data gives byte-identical files. Stages, in order (CRITERIA.md):

  pool -> rules -> merge -> ledger -> order and cap (= the candidates) -> score (M4) -> keep

Each stage only drops; every drop is logged with its stage and reason. Without the scoring call,
keep takes each section's first fallback_keep candidates. Triage reads the collector's tables and
never writes them; it records its own run in `runs` (kind "triage").
"""
from datetime import datetime, timezone

from ..collect import state_path
from ..store import Store, dumps
from . import candidates, ledger, merge, output


class TriageError(Exception):
    pass


def collect_at(store, run_date):
    row = store.one("""SELECT MAX(f.fetched_at) FROM fetches f JOIN runs r ON r.run_id = f.run_id
                       WHERE r.kind = 'collect' AND r.run_date = ?""", (run_date,))
    return row[0] if row else None


def keep_fallback(ctx, groups):
    for sec, gs in groups.items():
        n = ctx.t["fallback_keep"].get(sec, 0)
        for g in sorted((g for g in gs if g.alive), key=lambda g: (g.rank, g.id)):
            if g.rank > n:
                g.drop("keep", f"candidate #{g.rank}; without the scoring call, the first {n} are kept")


def select(store, cfg, at, ledger_doc):
    """Every stage up to the candidates. Returns (ctx, {section: [Group]})."""
    ctx = candidates.Ctx(store, cfg, at)
    groups = candidates.pools(ctx)
    candidates.hard_rules(ctx, groups)
    merge.announcements(ctx, groups)
    merge.press(ctx, groups)
    ledger.apply(ctx, groups, ledger_doc)
    for sec, order in candidates.ORDER.items():
        order(ctx, groups.get(sec, []))
    for gs in groups.values():
        for g in gs:
            if g.alive:
                g.info["candidate"] = True
    return ctx, groups


def run(cfg, run_date=None, at=None, log=print):
    run_date = run_date or datetime.now().astimezone().date().isoformat()
    path = state_path(cfg)
    if not path.exists():
        raise TriageError(f"no state database at {path}; run `debrief collect` first")
    store = Store(path)
    at = at or collect_at(store, run_date)
    if not at:
        raise TriageError(f"no collect run for {run_date}; run `debrief collect` first (or pass --at)")
    validator = output.Validator()
    ledger_doc = ledger.load(cfg.data_dir, run_date, validator.validate)
    ctx, groups = select(store, cfg, at, ledger_doc)
    ctx.mode = "rules_only"
    degraded = ["rules only: the scoring call arrives in M4"]
    keep_fallback(ctx, groups)
    ctx.finished_at = at          # output must not depend on the clock; the runs row has the wall time
    files, header = output.build(ctx, groups, run_date, ledger_doc, degraded, validator)
    out_dir = cfg.data_dir / "runs" / run_date
    output.write(out_dir, files)
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = "triage-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with store.transaction():
        store.run("INSERT OR REPLACE INTO runs (run_id, kind, run_date, started_at, finished_at, status, report) "
                  "VALUES (?, 'triage', ?, ?, ?, ?, ?)", (run_id, run_date, started, started, header["status"], dumps(header)))
    store.close()
    return header, out_dir
