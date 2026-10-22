-- M1: run bookkeeping, the seen-set, stories, and unattached mentions.
-- JSON columns hold canonical JSON (sorted keys) so identical input gives identical rows.

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- Where each source's cursor stands and how its last run went.
CREATE TABLE source_state (
  source_id TEXT PRIMARY KEY,
  cursor TEXT NOT NULL DEFAULT '{}',
  last_run_id TEXT,
  last_status TEXT,
  last_error TEXT,
  last_ok_at TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0
);

-- Conditional-request validators. Saved only once that source's run is stored, so a crash can't
-- leave a 304 hiding data we never kept.
CREATE TABLE http_validators (
  url TEXT PRIMARY KEY,
  etag TEXT,
  last_modified TEXT,
  checked_at TEXT NOT NULL
);

CREATE TABLE runs (
  run_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,                 -- collect | replay
  run_date TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT,
  report TEXT
);

-- Every request a run made.
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

-- The seen-set: every item any source has returned, and what it became.
CREATE TABLE items (
  source_id TEXT NOT NULL,
  item_key TEXT NOT NULL,
  role TEXT NOT NULL,                 -- anchor | mention | seen
  story_id TEXT,
  mention_id TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  raw_ref TEXT NOT NULL,
  PRIMARY KEY (source_id, item_key)
);
CREATE INDEX items_story ON items (story_id);

CREATE TABLE stories (
  story_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  section TEXT NOT NULL,
  title TEXT NOT NULL,
  org TEXT,
  authors TEXT NOT NULL DEFAULT '[]',
  published_at TEXT,
  first_seen TEXT NOT NULL,           -- run date (YYYY-MM-DD) the story first appeared
  first_seen_at TEXT NOT NULL,        -- fetch time of the record that created it
  last_changed_at TEXT NOT NULL,
  tier INTEGER NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  artifact_urls TEXT NOT NULL DEFAULT '[]',
  code_urls TEXT NOT NULL DEFAULT '[]',
  sources TEXT NOT NULL DEFAULT '[]',
  extra TEXT NOT NULL DEFAULT '{}',   -- per-source details, keyed by source id
  content_hash TEXT NOT NULL,
  merged_into TEXT
);
CREATE INDEX stories_first_seen ON stories (first_seen);

-- Canonical URL -> story, for attaching mentions by exact URL (M2).
CREATE TABLE story_urls (
  url TEXT PRIMARY KEY,
  story_id TEXT NOT NULL
);

-- Posts from discussion and reference sources. story_id stays NULL until M2 attaches them.
CREATE TABLE mentions (
  mention_id TEXT PRIMARY KEY,        -- <source_id>|<item_key>
  source_id TEXT NOT NULL,
  item_key TEXT NOT NULL,
  kind TEXT NOT NULL,                 -- reference | post
  url TEXT,
  canonical_url TEXT,
  title TEXT,
  text TEXT,
  author TEXT,
  posted_at TEXT,
  story_id TEXT,
  match_method TEXT,
  match_score REAL,
  first_seen_at TEXT NOT NULL,
  raw_ref TEXT NOT NULL
);
CREATE INDEX mentions_story ON mentions (story_id);
CREATE INDEX mentions_url ON mentions (canonical_url);
