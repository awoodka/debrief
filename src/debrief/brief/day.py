"""One day's triage output, as the brief code reads it: runs/<date>/ under the data dir."""
import json
import re
from functools import cached_property
from urllib.parse import urlsplit

DRAFT = "brief.draft.json"
FETCH_LOG = "brief_fetches.jsonl"
SESSION = "brief_session.json"


class DayError(Exception):
    pass


def host(url):
    h = (urlsplit(url or "").hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


class Day:
    def __init__(self, cfg, date):
        self.cfg, self.date = cfg, date
        self.dir = cfg.data_dir / "runs" / date
        if not (self.dir / "stories_index.jsonl").is_file():
            raise DayError(f"no stories_index.jsonl for {date}; run `debrief triage --date {date}` first")
        self._details = {}

    @property
    def draft_path(self):
        return self.dir / DRAFT

    @cached_property
    def index(self):
        """The index lines, in index order (section, then score, then newest)."""
        return [json.loads(x) for x in (self.dir / "stories_index.jsonl").read_text().splitlines() if x.strip()]

    @cached_property
    def by_id(self):
        return {line["id"]: line for line in self.index}

    @cached_property
    def rep(self):
        """story id -> the id of its index line: itself, or the story it was merged into."""
        out = {}
        for line in self.index:
            out[line["id"]] = line["id"]
            for sid in line.get("merged", []):
                out.setdefault(sid, line["id"])
        return out

    def detail(self, story_id):
        """The detail file of an index line (by its id or a merged member's)."""
        sid = self.rep[story_id]
        if sid not in self._details:
            self._details[sid] = json.loads((self.dir / self.by_id[sid]["detail"]).read_text())
        return self._details[sid]

    @cached_property
    def header(self):
        """The JSON header of run_report.md (schemas/report_header.schema.json), or {}."""
        p = self.dir / "run_report.md"
        if not p.is_file():
            return {}
        m = re.match(r"```json\n(.*?)\n```", p.read_text(), re.S)
        return json.loads(m.group(1)) if m else {}

    def draft(self):
        """The skill's draft, or None if there isn't one. Raises ValueError if it isn't JSON."""
        if not self.draft_path.is_file():
            return None
        return json.loads(self.draft_path.read_text())

    def fetches(self, session_id=None):
        """The fetch hook's log for one session (default: the latest one in the log)."""
        p = self.dir / FETCH_LOG
        rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.is_file() else []
        if session_id is None and rows:
            session_id = rows[-1]["session_id"]
        return [r for r in rows if r["session_id"] == session_id]

    def session(self):
        p = self.dir / SESSION
        return json.loads(p.read_text()) if p.is_file() else None

    def earlier_briefs(self, n):
        """The last n days before this one that have a written brief.json, newest first."""
        runs = self.cfg.data_dir / "runs"
        out = []
        for d in sorted((p.name for p in runs.iterdir() if p.is_dir() and p.name < self.date), reverse=True):
            f = runs / d / "brief.json"
            if f.is_file():
                doc = json.loads(f.read_text())
                if doc.get("mode") == "written":
                    out.append(doc)
                    if len(out) == n:
                        break
        return out
