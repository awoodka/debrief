"""SQLite snapshot store: persist each item's signals per run -> velocity across runs.

Velocity (Δ in-field per day vs the most recent prior run) is the early-impact signal — a paper whose
in-field traction is *accelerating* is taking off before the crowd. Cold for the first few runs; it
needs history to accumulate. Also the foundation for the future weekly/monthly trend layer.
"""
import json
import sqlite3
import time
from pathlib import Path

from .config import SNAPSHOT_DB, SNAPSHOT_RETENTION_DAYS
from .schema import canonical_key


def _conn():
    Path(SNAPSHOT_DB).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(SNAPSHOT_DB)
    c.execute("""CREATE TABLE IF NOT EXISTS snapshots (
        key TEXT, run_ts INTEGER, type TEXT, title TEXT,
        in_field REAL, mainstream REAL, divergence REAL, signals TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_snap_key ON snapshots(key, run_ts)")
    c.commit()
    return c


def snapshot(items, log=print):
    c = _conn()
    now = int(time.time())
    vel = 0
    for it in items:
        prior = c.execute(
            "SELECT in_field, divergence, run_ts FROM snapshots WHERE key=? ORDER BY run_ts DESC LIMIT 1",
            (canonical_key(it),)).fetchone()
        if prior:
            days = max((now - prior[2]) / 86400.0, 0.01)
            it["velocity_in_field"] = round((it["in_field_score"] - prior[0]) / days, 2)
            it["velocity_divergence"] = round((it["divergence"] - prior[1]) / days, 2)
            if it["velocity_in_field"]:
                vel += 1
    rows = [(canonical_key(it), now, it["type"], (it.get("title") or "")[:200],
             it["in_field_score"], it["mainstream_score"], it["divergence"],
             json.dumps(it["raw_signal"])) for it in items]
    c.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?)", rows)
    c.execute("DELETE FROM snapshots WHERE run_ts < ?", (now - SNAPSHOT_RETENTION_DAYS * 86400,))
    c.commit()
    n_runs = c.execute("SELECT COUNT(DISTINCT run_ts) FROM snapshots").fetchone()[0]
    c.close()
    log(f"  snapshots: stored {len(rows)} items (run #{n_runs} in store) · velocity computed on {vel}")
