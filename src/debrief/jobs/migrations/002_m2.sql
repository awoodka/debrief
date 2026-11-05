-- Jobs state, M2: the scoring call's answers and the morning lists.

-- One answer per job and profile version: a job is never scored twice for the same profile.
CREATE TABLE scores (
  posting_id TEXT NOT NULL REFERENCES postings (posting_id),
  profile_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  scored_at TEXT NOT NULL,
  score INTEGER NOT NULL,                -- 1-10, relevance to the profile
  keep INTEGER NOT NULL,                 -- 1: Alex could apply (the keep rule)
  why TEXT NOT NULL,
  mismatches TEXT NOT NULL DEFAULT '[]', -- JSON list
  model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  PRIMARY KEY (posting_id, profile_id)
);

-- Each morning's list: the top kept jobs that were new, and the watchlist's kept jobs.
CREATE TABLE daily_list (
  list_date TEXT NOT NULL,
  run_id TEXT NOT NULL,
  section TEXT NOT NULL,                 -- "new" or "watchlist"
  rank INTEGER NOT NULL,
  posting_id TEXT NOT NULL REFERENCES postings (posting_id),
  score INTEGER,                         -- null when the call failed and points ranked the list
  points INTEGER NOT NULL,
  PRIMARY KEY (list_date, section, rank)
);
