-- Jobs state (<data>/jobs/state/jobs.sqlite), M1. The first four tables have Debrief's shapes, so
-- debrief.store.Store's run, fetch, validator and source-state methods work unchanged.
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE source_state (
  source_id TEXT PRIMARY KEY,
  cursor TEXT NOT NULL DEFAULT '{}',
  last_run_id TEXT,
  last_status TEXT,
  last_error TEXT,
  last_ok_at TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE http_validators (
  url TEXT PRIMARY KEY,
  etag TEXT,
  last_modified TEXT,
  checked_at TEXT NOT NULL
);

CREATE TABLE runs (
  run_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  run_date TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT,
  report TEXT
);

CREATE TABLE fetches (
  run_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  url TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  http_status INTEGER NOT NULL,
  n_bytes INTEGER NOT NULL,
  error TEXT,
  raw_ref TEXT NOT NULL,
  PRIMARY KEY (run_id, source_id, seq)
);

-- One real job, however many sources list it.
CREATE TABLE postings (
  posting_id TEXT PRIMARY KEY,           -- "p:" + a hash of the first key it was made with
  company TEXT NOT NULL,
  title TEXT NOT NULL,
  locations TEXT NOT NULL,               -- JSON list, union over its sightings
  url TEXT,                              -- best link: the job board's own page when known
  board TEXT,                            -- system:id, when known
  published_at TEXT,                     -- earliest date any source gave
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  closed_at TEXT,                        -- every source that listed it now says it's gone
  data TEXT NOT NULL DEFAULT '{}'        -- pay, remote, sponsorship, degrees, category ... (JSON)
);
CREATE INDEX postings_first_seen ON postings (first_seen_at);

-- Keys that identify a posting: "job:<system>:<id>" (a job-board job id) or "fz:<company>|<title>".
CREATE TABLE posting_keys (
  key TEXT PRIMARY KEY,
  posting_id TEXT NOT NULL REFERENCES postings (posting_id)
);
CREATE INDEX posting_keys_posting ON posting_keys (posting_id);

-- One source's listing of a job. `active` is what the source last said; `scope` groups items a
-- full snapshot covers (a watchlist board, a list), so a job missing from a full snapshot closes.
CREATE TABLE items (
  source_id TEXT NOT NULL,
  item_key TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT '',
  posting_id TEXT NOT NULL REFERENCES postings (posting_id),
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  content_hash TEXT NOT NULL,
  raw_ref TEXT NOT NULL,
  data TEXT NOT NULL,                    -- the normalized item (JSON)
  PRIMARY KEY (source_id, item_key)
);
CREATE INDEX items_posting ON items (posting_id);
CREATE INDEX items_scope ON items (source_id, scope);

-- Posting text (descriptions), when a source gives it: what M2's pre-score reads.
CREATE TABLE posting_text (
  posting_id TEXT NOT NULL REFERENCES postings (posting_id),
  source_id TEXT NOT NULL,
  text TEXT NOT NULL,
  seen_at TEXT NOT NULL,
  PRIMARY KEY (posting_id, source_id)
);

-- The board directory: which company is on which job board. For finding a watchlist company's
-- board; never polled as a whole.
CREATE TABLE directory (
  board TEXT NOT NULL,
  name TEXT NOT NULL,
  name_norm TEXT NOT NULL,
  source_id TEXT NOT NULL,
  seen_at TEXT NOT NULL,
  PRIMARY KEY (board, source_id)
);
CREATE INDEX directory_name ON directory (name_norm);
