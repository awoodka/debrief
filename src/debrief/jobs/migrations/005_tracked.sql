-- Jobs state, M4: the site's Tracked mark. Alex marks a job once it's in the tracker Alex keeps
-- outside this system; the site highlights it wherever it appears.
CREATE TABLE tracked (
  posting_id TEXT PRIMARY KEY REFERENCES postings (posting_id),
  at TEXT NOT NULL                       -- when it was marked
);
