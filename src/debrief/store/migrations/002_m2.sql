-- M2: discussion attached to stories, and signals over time.

-- What a post links to (canonical URLs), its place in a thread, and its rank among its siblings
-- in the source's own ranked order (HN comments). replies counts all descendants.
ALTER TABLE mentions ADD COLUMN links TEXT NOT NULL DEFAULT '[]';
ALTER TABLE mentions ADD COLUMN parent_id TEXT;
ALTER TABLE mentions ADD COLUMN rank INTEGER;
ALTER TABLE mentions ADD COLUMN replies INTEGER;
CREATE INDEX mentions_parent ON mentions (parent_id);

-- Engagement per mention over time (HN points and comments, likes, reposts). Two or more
-- observations give a rate, which is where trend comes from.
CREATE TABLE engagement (
  mention_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  points INTEGER,
  comments INTEGER,
  likes INTEGER,
  reposts INTEGER,
  PRIMARY KEY (mention_id, observed_at)
);

-- Signals about the artifact itself over time: HF upvotes and comment counts, the official repo's
-- stars, models/datasets/Spaces built on a paper, a model's likes and downloads.
CREATE TABLE story_signals (
  story_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  source_id TEXT NOT NULL,
  name TEXT NOT NULL,
  value REAL NOT NULL,
  PRIMARY KEY (story_id, observed_at, source_id, name)
);
CREATE INDEX story_signals_story ON story_signals (story_id, name);

-- A reference source (newsletter, Techmeme) linking a story: covered_by, and the recall check.
CREATE TABLE coverage (
  story_id TEXT NOT NULL,
  mention_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  url TEXT NOT NULL,
  covered_at TEXT,
  PRIMARY KEY (story_id, mention_id)
);
CREATE INDEX coverage_story ON coverage (story_id);
