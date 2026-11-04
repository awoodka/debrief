"""Jobs: a daily job-listing collector that runs inside Debrief's daily run (docs/jobs/).

Same shape as Debrief: deterministic collection, filtering and merging, then one structured
scoring call a night. It reuses Debrief's polite HTTP client, robots.txt check, raw store and
scoring-call helpers, and keeps its own files under <data>/jobs/.
"""
