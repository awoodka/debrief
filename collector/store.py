"""SQLite snapshot store + per-signal VELOCITY (work order P0-5). No LLM.

Persists each item's signals per run, then on later runs computes per-day deltas of the fast signals
(Δlinked-models, Δcitations, Δimpls, Δhn, Δupvotes...). A paper whose linked-repos/citations are
RISING fast surfaces even when absolute counts are still small — the day-1 early-impact signal.
Cold on run 1 (no history). Also the foundation for the future weekly/monthly trend layer.
"""
import json
import re
import sqlite3
import time
from pathlib import Path

from .config import SNAPSHOT_DB, SNAPSHOT_RETENTION_DAYS, VELOCITY_MIN_SIGNAL
from .schema import canonical_key

# signals whose growth-per-day we track = rising SUBSTANCE + community attention.
# Deliberately NOT hf_upvotes / ph_votes — those are insider POPULARITY; velocity is about adoption.
_VEL_W = {   # velocity tracks SUBSTANCE signals only — it boosts the in_field/substance axis (and is capped there)
    "hf_linked_models": 5.0, "hf_linked_datasets": 3.0, "hf_linked_spaces": 2.0,
    "github_impls": 5.0, "influential_citations": 6.0, "citations": 1.0,
    "discourse_mentions": 4.0,
}


def _num(v):
    if isinstance(v, (int, float)):
        return v
    m = re.search(r"[\d,]+", str(v or ""))
    return int(m.group(0).replace(",", "")) if m else 0


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
            "SELECT signals, run_ts FROM snapshots WHERE key=? ORDER BY run_ts DESC LIMIT 1",
            (canonical_key(it),)).fetchone()
        if not (prior and prior[0]):
            continue
        try:
            psig = json.loads(prior[0])
        except Exception:  # noqa: BLE001
            continue
        days = max((now - prior[1]) / 86400.0, 0.04)   # floor ~1h so a quick re-run can't explode deltas
        rs = it["raw_signal"]
        deltas, vscore = {}, 0.0
        for s, w in _VEL_W.items():
            cur = _num(rs.get(s, 0))
            d = (cur - _num(psig.get(s, 0))) / days
            if abs(d) >= 0.01:
                deltas[s] = round(d, 2)
                if d > 0 and cur >= VELOCITY_MIN_SIGNAL:    # Fix 1 floor: a 0->1 blip earns no boost
                    vscore += w * d
        if deltas:
            # Fix 1 cap: velocity BOOSTS substance, never replaces it — bonus <= the item's own substance score.
            vscore = min(vscore, max(it.get("substance_score", 0.0), 0.0))
            it["velocity"] = deltas
            it["velocity_score"] = round(vscore, 2)
            if vscore > 0:
                it["in_field_score"] = round(it["in_field_score"] + vscore, 2)   # rising substance boosts the gem
                it["divergence"] = round(it["in_field_score"] - it["mainstream_score"], 2)
                vel += 1

    rows = [(canonical_key(it), now, it["type"], (it.get("title") or "")[:200],
             it["in_field_score"], it["mainstream_score"], it["divergence"],
             json.dumps(it["raw_signal"])) for it in items]
    c.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?)", rows)
    c.execute("DELETE FROM snapshots WHERE run_ts < ?", (now - SNAPSHOT_RETENTION_DAYS * 86400,))
    c.commit()
    n_runs = c.execute("SELECT COUNT(DISTINCT run_ts) FROM snapshots").fetchone()[0]
    c.close()
    log(f"  snapshots: stored {len(rows)} items (run #{n_runs}) · velocity on {vel} items")
