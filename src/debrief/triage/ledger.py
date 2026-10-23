"""The reported ledger: which stories are already in a brief.

The skill (or its publish step) writes runs/<date>/reported.json as {"date", "story_ids"}
(schemas/reported.schema.json). Until any such file exists, a story counts as reported once an
earlier day's index carried it (runs/<date>/stories_index.jsonl). Only days before the run's date
count, so re-running triage on the same day gives the same answer. The files are read on every
run and nothing is copied into the state database, so a rebuild can't lose them.

A reported story stays out of the candidates unless it has meaningful new discussion since: at
least ledger.update_min_new_threads new threads, each at or above its source's bands.floor. Then
it comes back with status "update", plus prev_reported and days_discussed.
"""
import json

from ..stories import signals


def load(data_dir, run_date, validate=None):
    """{"kind": "reported" | "index" | "none", "dates": {story_id: [dates]}, "errors": [...]}"""
    runs = data_dir / "runs"
    days = sorted(p.name for p in runs.iterdir() if p.is_dir() and p.name < run_date) if runs.is_dir() else []
    out = {"kind": "none", "dates": {}, "errors": []}
    reported = [runs / d / "reported.json" for d in days if (runs / d / "reported.json").is_file()]
    if reported:
        out["kind"] = "reported"
        for f in reported:
            try:
                doc = json.loads(f.read_text())
                if validate:
                    validate("reported", doc)
            except Exception as e:  # noqa: BLE001 — a bad ledger file is reported, not fatal
                out["errors"].append(f"{f.parent.name}/reported.json: {str(e).splitlines()[0][:200]}")
                continue
            for sid in doc["story_ids"]:
                out["dates"].setdefault(sid, []).append(doc["date"])
        return out
    for d in days:
        f = runs / d / "stories_index.jsonl"
        if f.is_file():
            out["kind"] = "index"
            for line in f.read_text().splitlines():
                if line.strip():
                    line = json.loads(line)
                    for sid in [line["id"], *line.get("merged", [])]:
                        out["dates"].setdefault(sid, []).append(d)
    return out


def cutoff(store, day):
    """When that day's brief was made from: the last fetch of its collect runs (else the day's end)."""
    row = store.one("""SELECT MAX(f.fetched_at) FROM fetches f JOIN runs r ON r.run_id = f.run_id
                       WHERE r.run_date = ?""", (day,))
    return row[0] if row and row[0] else f"{day}T23:59:59Z"


def days_discussed(store, ids, at):
    marks = ",".join("?" * len(ids))
    row = store.one(f"""SELECT COUNT(DISTINCT substr(posted_at, 1, 10)) FROM mentions
                        WHERE story_id IN ({marks}) AND kind != 'reference' AND posted_at IS NOT NULL
                        AND first_seen_at <= ?""", (*ids, at))
    return row[0] if row else 0


def apply(ctx, groups, ledger):
    floors = ctx.settings["bands"].get("floor") or {}
    need = ctx.t["ledger"]["update_min_new_threads"]
    for gs in groups.values():
        for g in gs:
            if not g.alive:
                continue
            dates = sorted(d for sid in g.members for d in ledger["dates"].get(sid, []))
            if not dates:
                continue
            prev = dates[-1]
            since = cutoff(ctx.store, prev)
            marks = ",".join("?" * len(g.members))
            new = 0
            for mid, src in ctx.store.q(f"""SELECT mention_id, source_id FROM mentions WHERE story_id IN ({marks})
                                            AND kind IN ('thread', 'post') AND first_seen_at > ? AND first_seen_at <= ?""",
                                        (*g.members, since, ctx.at)):
                pts = signals._latest(ctx.store, mid, ctx.at).get("points") or 0
                new += pts >= floors.get(src, 0)
            if new >= need:
                g.status, g.prev_reported = "update", prev
                g.info["new_threads"] = new
            else:
                where = "reported" if ledger["kind"] == "reported" else "in the index"
                g.drop("ledger", f"{where} on {prev}; no new discussion since")
