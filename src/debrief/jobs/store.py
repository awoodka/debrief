"""The jobs state database, <data>/jobs/state/jobs.sqlite: Debrief's Store (runs, fetches,
validators, source state, transactions, migrations) with the jobs tables (migrations/)."""
from pathlib import Path

from ..store.db import Store, dumps, loads

MIGRATIONS = Path(__file__).with_name("migrations")


def state_path(cfg):
    return cfg.data_dir / "state" / "jobs.sqlite"


class JobsStore(Store):
    migrations = MIGRATIONS

    # ---------------------------------------------------------------------------- items
    def item(self, source_id, item_key):
        return self.one("SELECT * FROM items WHERE source_id=? AND item_key=?", (source_id, item_key))

    def insert_item(self, source_id, item_key, scope, posting_id, at, content_hash, raw_ref, data, active=True):
        self.run("INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (source_id, item_key, scope, posting_id, at, at, int(active), content_hash, raw_ref, dumps(data)))

    def update_item(self, source_id, item_key, at, active, content_hash, raw_ref, data):
        self.run("""UPDATE items SET last_seen_at=?, active=?, content_hash=?, raw_ref=?, data=?
                    WHERE source_id=? AND item_key=?""",
                 (at, int(active), content_hash, raw_ref, dumps(data), source_id, item_key))

    def active_in_scope(self, source_id, scope):
        return [r["item_key"] for r in self.q(
            "SELECT item_key FROM items WHERE source_id=? AND scope=? AND active=1", (source_id, scope))]

    def deactivate(self, source_id, item_key):
        self.run("UPDATE items SET active=0 WHERE source_id=? AND item_key=?", (source_id, item_key))

    # ------------------------------------------------------------------------- postings
    def posting(self, posting_id):
        row = self.one("SELECT * FROM postings WHERE posting_id=?", (posting_id,))
        if row is None:
            return None
        d = dict(row)
        d["locations"], d["data"] = loads(d["locations"], []), loads(d["data"], {})
        return d

    def save_posting(self, p):
        cols = ("posting_id", "company", "title", "locations", "url", "board", "published_at", "first_seen_at",
                "last_seen_at", "closed_at", "data")
        row = {**p, "locations": dumps(p["locations"]), "data": dumps(p["data"])}
        self.run(f"INSERT OR REPLACE INTO postings ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 tuple(row[c] for c in cols))

    def find_key(self, key):
        row = self.one("SELECT posting_id FROM posting_keys WHERE key=?", (key,))
        return row[0] if row else None

    def add_key(self, key, posting_id):
        self.run("INSERT OR IGNORE INTO posting_keys VALUES (?,?)", (key, posting_id))

    def job_keys(self, posting_id):
        return [r[0] for r in self.q("SELECT key FROM posting_keys WHERE posting_id=? AND key LIKE 'job:%'", (posting_id,))]

    def posting_links(self, posting_id):
        """Every listing's link for a posting: source_id, item_key, url, active, published_at, first_seen_at."""
        return self.q("""SELECT source_id, item_key, COALESCE(json_extract(data, '$.url'), '') AS url, active,
                                json_extract(data, '$.published_at') AS published_at, first_seen_at
                         FROM items WHERE posting_id=?""", (posting_id,))

    def open_postings_at(self, company_norm):
        """Open postings whose fuzzy key names this company: posting_id, title, locations (JSON)."""
        return self.q("""SELECT DISTINCT p.posting_id, p.title, p.locations FROM posting_keys k JOIN postings p USING (posting_id)
                         WHERE k.key >= ? AND k.key < ? AND p.closed_at IS NULL ORDER BY p.posting_id""",
                      (f"fz:{company_norm}|", f"fz:{company_norm}}}"))       # "}" sorts right after "|"

    def posting_sources(self, posting_id):
        return {r[0] for r in self.q("SELECT DISTINCT source_id FROM items WHERE posting_id=?", (posting_id,))}

    def any_active(self, posting_id):
        return self.one("SELECT 1 FROM items WHERE posting_id=? AND active=1 LIMIT 1", (posting_id,)) is not None

    def save_text(self, posting_id, source_id, text, at):
        self.run("INSERT OR REPLACE INTO posting_text VALUES (?,?,?,?)", (posting_id, source_id, text, at))

    # --------------------------------------------------------------------- scores (M2)
    def score_for(self, posting_id, profile_id):
        row = self.one("SELECT * FROM scores WHERE posting_id=? AND profile_id=?", (posting_id, profile_id))
        if row is None:
            return None
        d = dict(row)
        d["mismatches"], d["keep"] = loads(d["mismatches"], []), bool(d["keep"])
        return d

    def save_score(self, posting_id, profile_id, run_id, at, answer, model, prompt_version):
        self.run("INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (posting_id, profile_id, run_id, at, answer["score"], int(answer["keep"]), answer["why"],
                  dumps(answer["mismatches"]), model, prompt_version))

    def save_list(self, list_date, run_id, rows):
        """rows: [(section, rank, posting_id, score, points)]; replaces that date's list."""
        self.run("DELETE FROM daily_list WHERE list_date=?", (list_date,))
        for section, rank, pid, score, pts in rows:
            self.run("INSERT INTO daily_list VALUES (?,?,?,?,?,?,?)", (list_date, run_id, section, rank, pid, score, pts))

    def texts_for(self, posting_id):
        """The longest description any source gave, or None."""
        row = self.one("SELECT text FROM posting_text WHERE posting_id=? ORDER BY length(text) DESC, source_id LIMIT 1",
                       (posting_id,))
        return row[0] if row else None

    def get_meta(self, key):
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return row[0] if row else None

    def set_meta(self, key, value):
        self.run("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))

    # ------------------------------------------------------------------------ directory
    def save_directory(self, board, name, name_norm, source_id, at):
        self.run("INSERT OR REPLACE INTO directory VALUES (?,?,?,?,?)", (board, name, name_norm, source_id, at))

    def directory_by_name(self, name_norm):
        return self.q("SELECT board, name, source_id FROM directory WHERE name_norm=? ORDER BY board, source_id", (name_norm,))

    def directory_like(self, fragment, limit=10):
        return self.q("SELECT DISTINCT board, name FROM directory WHERE name_norm LIKE ? ORDER BY length(name_norm), board LIMIT ?",
                      (f"%{fragment}%", limit))
