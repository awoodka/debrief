"""Triage: from the state database to the day's index (docs/CRITERIA.md).

  candidates.py   the deterministic stages: pools, hard rules, per-section order and caps
  merge.py        stories that are one thing: announcements with their artifacts, press events
  ledger.py       what's already been in a brief
  output.py       the output contract (schemas/): index, detail files, triage log, run report
  run.py          `debrief triage`: the stages in order

The scoring call (one Haiku call over every candidate) arrives in M4. Until then, and whenever it
fails, each section keeps its first triage.yml fallback_keep candidates and the run is degraded.
"""
