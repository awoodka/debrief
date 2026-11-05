-- Jobs state, revision 6 (M3): the coverage check's samples.

-- One row per sampled job: a Google job result (SerpAPI) or an alert email's job. `jobs coverage`
-- decides whether we had it: matched (a posting with its job id, or the same company, core title
-- and place), near (same company, similar title: a possible dedupe gap), ineligible (not a job the
-- profile wants: `stage` says which filter), pending (not found yet; the lists lag, so it's
-- checked again on later days), or miss (not found after three checks; `cause` and `suggestion`).
CREATE TABLE coverage_samples (
  sample_key TEXT PRIMARY KEY,           -- "google:<job_id>" or "alerts:<posting_id>"
  sample TEXT NOT NULL,                  -- "google" or "alerts"
  first_date TEXT NOT NULL,              -- the run date it was first sampled
  fetched_at TEXT NOT NULL,
  query TEXT,
  title TEXT NOT NULL,
  company TEXT NOT NULL,
  location TEXT,
  via TEXT,
  posted_at TEXT,                        -- estimated from "3 days ago"
  apply_urls TEXT NOT NULL DEFAULT '[]', -- JSON list
  text TEXT,                             -- the result's description, for the eligibility check
  status TEXT NOT NULL DEFAULT 'pending',
  stage TEXT,
  posting_id TEXT,
  how TEXT,                              -- "job id", "same job", "near"
  cause TEXT,
  suggestion TEXT,
  checks INTEGER NOT NULL DEFAULT 0,
  last_checked TEXT
);
CREATE INDEX coverage_by_date ON coverage_samples (first_date, sample);
