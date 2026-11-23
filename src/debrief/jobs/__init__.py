"""Jobs: a daily job-listing collector that runs inside Debrief's daily run (docs/jobs/).

Same shape as Debrief: deterministic collection, filtering and merging, then one structured
scoring call a night. It reuses Debrief's polite HTTP client, raw store and scoring-call helpers,
and keeps its own files under <data>/jobs/. Unlike Debrief it doesn't obey robots.txt: it reads any
site that doesn't block us, and stops asking a host that does (adapters.Ctx).
"""
