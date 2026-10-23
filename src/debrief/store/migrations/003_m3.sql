-- M3: article pages. The main text of a story's page and the links inside it: triage groups
-- press coverage and merges announcements by these links, and the index's full text comes from
-- here. One row per (story, url); a failed fetch is a row too (status is the HTTP status, 0 for a
-- network error), so a 4xx isn't retried.
CREATE TABLE story_pages (
  story_id TEXT NOT NULL,
  url TEXT NOT NULL,
  source_id TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  status INTEGER NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  links TEXT NOT NULL DEFAULT '[]',
  raw_ref TEXT NOT NULL,
  PRIMARY KEY (story_id, url)
);
