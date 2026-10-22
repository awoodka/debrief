"""The state database (data/state/debrief.sqlite): cursors, the seen-set, stories, mentions.

Schema changes are numbered SQL files in store/migrations/, applied in order on open. JSON
columns are written with sorted keys, so the same input always produces byte-identical rows,
which is what makes `collect --replay` checkable.
"""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS = Path(__file__).with_name("migrations")


def dumps(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def loads(text, default=None):
    return json.loads(text) if text else default


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None)   # explicit transactions only
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def close(self):
        self.conn.close()

    # ------------------------------------------------------------------------------- plumbing

    def _migrate(self):
        has_meta = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
        version = 0
        if has_meta:
            row = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            version = int(row[0]) if row else 0
        for f in sorted(MIGRATIONS.glob("*.sql")):
            n = int(f.name.split("_", 1)[0])
            if n <= version:
                continue
            # executescript commits any open transaction first, so each migration carries its own
            # BEGIN/COMMIT and is rolled back whole if any statement fails.
            try:
                self.conn.executescript(
                    f"BEGIN IMMEDIATE;\n{f.read_text()}\n"
                    f"INSERT OR REPLACE INTO meta VALUES ('schema_version', '{n}');\nCOMMIT;")
            except sqlite3.Error:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
                raise

    @contextmanager
    def transaction(self):
        if self.conn.in_transaction:          # nested use joins the outer transaction
            yield
            return
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def q(self, sql, args=()):
        return self.conn.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        return self.conn.execute(sql, args).fetchone()

    def run(self, sql, args=()):
        self.conn.execute(sql, args)

    @property
    def schema_version(self):
        return int(self.one("SELECT value FROM meta WHERE key='schema_version'")[0])

    # ------------------------------------------------------------------------- runs and state

    def start_run(self, run_id, kind, run_date, started_at):
        self.run("INSERT INTO runs (run_id, kind, run_date, started_at) VALUES (?,?,?,?)",
                 (run_id, kind, run_date, started_at))

    def finish_run(self, run_id, finished_at, status, report):
        self.run("UPDATE runs SET finished_at=?, status=?, report=? WHERE run_id=?",
                 (finished_at, status, dumps(report), run_id))

    def cursor(self, source_id):
        row = self.one("SELECT cursor FROM source_state WHERE source_id=?", (source_id,))
        return loads(row[0], {}) if row else {}

    def save_source_state(self, source_id, run_id, status, error, at, cursor=None):
        prev = self.one("SELECT consecutive_failures FROM source_state WHERE source_id=?", (source_id,))
        failures = 0 if status != "failed" else (prev[0] if prev else 0) + 1
        self.run("""INSERT INTO source_state (source_id, cursor, last_run_id, last_status, last_error,
                                              last_ok_at, consecutive_failures)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (source_id) DO UPDATE SET
                      cursor = COALESCE(?, cursor), last_run_id = excluded.last_run_id,
                      last_status = excluded.last_status, last_error = excluded.last_error,
                      last_ok_at = COALESCE(excluded.last_ok_at, last_ok_at),
                      consecutive_failures = excluded.consecutive_failures""",
                 (source_id, dumps(cursor or {}), run_id, status, error,
                  at if status != "failed" else None, failures,
                  dumps(cursor) if cursor is not None else None))

    def validators(self, url):
        row = self.one("SELECT etag, last_modified FROM http_validators WHERE url=?", (url,))
        return (row[0], row[1]) if row else (None, None)

    def save_validators(self, url, etag, last_modified, at):
        self.run("INSERT OR REPLACE INTO http_validators VALUES (?,?,?,?)", (url, etag, last_modified, at))

    def record_fetch(self, run_id, source_id, seq, url, fetched_at, status, n_bytes, error, raw_ref):
        self.run("INSERT INTO fetches VALUES (?,?,?,?,?,?,?,?,?)",
                 (run_id, source_id, seq, url, fetched_at, status, n_bytes, error, raw_ref))

    # ------------------------------------------------------------------------------- the seen-set

    def item(self, source_id, item_key):
        return self.one("SELECT * FROM items WHERE source_id=? AND item_key=?", (source_id, item_key))

    def known_keys(self, source_id):
        return {r[0] for r in self.q("SELECT item_key FROM items WHERE source_id=?", (source_id,))}

    def insert_item(self, source_id, item_key, role, story_id, mention_id, at, content_hash, raw_ref):
        self.run("INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?)",
                 (source_id, item_key, role, story_id, mention_id, at, at, content_hash, raw_ref))

    def touch_item(self, source_id, item_key, at, content_hash, raw_ref):
        self.run("""UPDATE items SET last_seen_at=?, content_hash=?, raw_ref=?
                    WHERE source_id=? AND item_key=?""", (at, content_hash, raw_ref, source_id, item_key))

    # ------------------------------------------------------------------------------------ stories

    STORY_JSON = ("authors", "artifact_urls", "code_urls", "sources", "extra")

    def story(self, story_id):
        row = self.one("SELECT * FROM stories WHERE story_id=?", (story_id,))
        if not row:
            return None
        s = dict(row)
        for k in self.STORY_JSON:
            s[k] = loads(s[k], [] if k != "extra" else {})
        return s

    def save_story(self, s):
        row = {k: (dumps(v) if k in self.STORY_JSON else v) for k, v in s.items()}
        cols = ", ".join(row)
        self.run(f"INSERT OR REPLACE INTO stories ({cols}) VALUES ({', '.join('?' * len(row))})",
                 tuple(row.values()))

    def add_story_url(self, url, story_id):
        self.run("INSERT OR IGNORE INTO story_urls VALUES (?, ?)", (url, story_id))

    def insert_mention(self, m):
        cols = ", ".join(m)
        self.run(f"INSERT INTO mentions ({cols}) VALUES ({', '.join('?' * len(m))})", tuple(m.values()))

    def mention(self, mention_id):
        row = self.one("SELECT * FROM mentions WHERE mention_id=?", (mention_id,))
        return dict(row) if row else None

    def set_mention_story(self, mention_id, story_id, method, score):
        self.run("UPDATE mentions SET story_id=?, match_method=?, match_score=? WHERE mention_id=?",
                 (story_id, method, score, mention_id))

    def save_engagement(self, mention_id, at, e):
        self.run("INSERT OR REPLACE INTO engagement VALUES (?,?,?,?,?,?)",
                 (mention_id, at, e.get("points"), e.get("comments"), e.get("likes"), e.get("reposts")))

    def save_signals(self, story_id, at, source_id, signals):
        for name, value in sorted(signals.items()):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                self.run("INSERT OR REPLACE INTO story_signals VALUES (?,?,?,?,?)",
                         (story_id, at, source_id, name, float(value)))

    def latest_signals(self, story_id):
        """{name: value} from the most recent observation of each signal."""
        rows = self.q("""SELECT name, value FROM story_signals s WHERE story_id=? AND observed_at =
                         (SELECT MAX(observed_at) FROM story_signals t
                          WHERE t.story_id=s.story_id AND t.name=s.name) ORDER BY name""", (story_id,))
        return {r[0]: r[1] for r in rows}

    def add_coverage(self, story_id, mention_id, source_id, url, covered_at):
        self.run("INSERT OR IGNORE INTO coverage VALUES (?,?,?,?,?)",
                 (story_id, mention_id, source_id, url, covered_at))

    def story_for_url(self, url):
        row = self.one("SELECT story_id FROM story_urls WHERE url=?", (url,))
        return row[0] if row else None

    def dump(self, table, order_by):
        """Every row of a table as plain tuples, for comparing two databases."""
        return [tuple(r) for r in self.q(f"SELECT * FROM {table} ORDER BY {order_by}")]
