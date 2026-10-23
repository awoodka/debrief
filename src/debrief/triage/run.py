"""`debrief triage [--date D]`: the day's candidates, what's kept, and the output files.

The run's time `at` is the last fetch of that day's collect runs, and the scoring call's answer is
cached, so re-running triage on the same data gives byte-identical files. Stages, in order
(CRITERIA.md):

  pool -> rules -> merge -> ledger -> order and cap (= the candidates) -> threads -> score

Each stage only drops; every drop is logged with its stage and reason. The scoring call keeps every
relevant story (triage/score.py). If it fails, or with --no-model, `keep` takes each section's first
fallback_keep candidates instead and the run is degraded. Triage reads the collector's tables and
never writes them; it records its own run in `runs` (kind "triage").
"""
import os
from datetime import datetime, timezone

from ..collect import state_path
from ..store import Store, dumps
from . import candidates, ledger, merge, output, score


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


def run(cfg, run_date=None, at=None, model=True, model_name=None, runner=None, fresh=False, log=print):
    """Returns (report header, output dir, whether the answer came from the cache)."""
    if model and os.environ.get("ANTHROPIC_API_KEY"):
        raise TriageError("ANTHROPIC_API_KEY is set; triage runs on the Claude subscription, so unset it "
                          "(or pass --no-model)")
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
    groups["discussion"] = candidates.threads(ctx, ledger_doc)
    ctx.mode, ctx.scoring, cached, degraded = "rules_only", None, False, []
    if model:
        ids = {g.id for gs in groups.values() for g in gs if g.alive}
        data = score.payload(ctx, groups, score.reported_list(ctx, ledger_doc, run_date, ids))
        t = ctx.t["scoring"]
        try:
            result = score.call(cfg.data_dir / "cache" / "score", t, data, model=model_name,
                                runner=runner or score.run_claude, fresh=fresh, log=log)
        except score.ScoringError as e:
            result = None
            ctx.scoring = {"model": model_name or t["model"], "error": str(e)[:500]}
            degraded.append(f"the scoring call failed: {str(e)[:300]}")
        if result:
            score.apply(ctx, groups, result, ledger_doc)
            ctx.mode, cached = "scored", result["cached"]
            # What the call was, not whether this run replayed it: so a replay writes the same files.
            ctx.scoring = {k: result[k] for k in ("model", "prompt_version", "key", "attempts", "usage", "notes")}
    else:
        degraded.append("rules only (--no-model)")
    if ctx.mode == "rules_only":
        keep_fallback(ctx, groups)
    ctx.finished_at = at          # output must not depend on the clock; the runs row has the wall time
    files, header = output.build(ctx, groups, run_date, ledger_doc, degraded, validator)
    out_dir = cfg.data_dir / "runs" / run_date
    output.write(out_dir, files)
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = "triage-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with store.transaction():
        store.run("INSERT OR REPLACE INTO runs (run_id, kind, run_date, started_at, finished_at, status, report) "
                  "VALUES (?, 'triage', ?, ?, ?, ?, ?)", (run_id, run_date, started, started, header["status"],
                                                          dumps({**header, "cached": cached})))
    store.close()
    return header, out_dir, cached
