"""The brief (layer 3): the day's triage output turned into an article-style brief.

The writing is a Claude Code skill (skill/, invoked as /debrief:brief). Everything around it is
deterministic code here:

  context    runs/<date>/brief_context.json: the skill's first read (context.py)
  check      validates the skill's runs/<date>/brief.draft.json; the skill's Stop hook runs it (check.py)
  hooks      the WebFetch cap and the Stop hook (hooks.py)
  finalize   brief.json (self-contained, what a page renders), brief.md, and reported.json, the
             ledger triage reads (finalize.py)

The model writes only the draft. Links, story details, the scan of every kept story, and the ledger
all come from the triage data, never from the model.
"""
