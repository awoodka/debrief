-- Jobs state, revision 5: every job already shown on a list, so no list shows it again.

-- A posting is highlighted once: in a morning list ("new", "watchlist"), in the one-time
-- "catchup" list, or as a copy shown under one of those ("also listed as").
CREATE TABLE highlighted (
  posting_id TEXT PRIMARY KEY REFERENCES postings (posting_id),
  list_date TEXT NOT NULL,
  run_id TEXT NOT NULL,
  section TEXT NOT NULL                  -- "new", "watchlist" or "catchup"
);
CREATE INDEX highlighted_by_date ON highlighted (list_date, section);
