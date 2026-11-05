"""Descriptions for the jobs that may reach the scoring call (docs/jobs/PLAN.md, "M2 design").

fetch() picks the open postings that pass every filter that doesn't need a description
(filters.check with no text), have no description yet, and carry a job-board id whose board we
know, or a Greenhouse id whose board we don't (below). It asks the job board's own public API for each:
  Greenhouse, Lever   one request per job
  Workday             the job's cxs page (one request per job)
  Ashby, Workable,    the board's list, once per board, for all of that board's wanted jobs
  Recruitee
SmartRecruiters is never asked (its robots.txt allows only LinkedInBot). Company sites and
aggregators are never fetched. Every request goes through the context, so robots.txt is checked
and the response is kept raw; texts() reads a record back with no network, so replay rebuilds
the same descriptions.

A Greenhouse id from a company page ("sambanova.ai/...?gh_jid=6240725004") names the job but not
its board. For those, up to 4 board names (boards.greenhouse_guesses) are tried against the board
API's page for that exact job, stopping at the first that answers: Greenhouse job ids are global,
so an answer proves the board. A found board is reused for the company's other jobs that night,
and directory() files it in the board directory, so later nights know it. A guess that 404s is
never asked again (the refused set).
"""
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

from ...store import raw
from .. import boards, filters
from ..items import company_norm
from . import Adapter

PER_JOB = ("greenhouse", "lever", "workday")
PER_BOARD = ("ashby", "workable", "recruitee")


def _board_for(store, posting, system):
    """The posting's board on `system`: its own, or the one directory board filed under its
    company's name on that system."""
    if posting.get("board") and boards.split(posting["board"])[0] == system:
        return posting["board"]
    rows = [r["board"] for r in store.directory_by_name(company_norm(posting["company"]))
            if r["board"].startswith(system + ":")]
    rows = sorted(set(rows))
    return rows[0] if len(rows) == 1 else None


def _workday_path(urls):
    """'/job/...' from a Workday job URL (any locale prefix dropped)."""
    for u in urls:
        if not u or not boards.job_from_url(u) or not boards.job_from_url(u).startswith("workday:"):
            continue
        parts = [x for x in urlsplit(u).path.split("/") if x]
        if "job" in parts:
            return "/" + "/".join(parts[parts.index("job"):])
    return None


def targets(store, profile, now):
    """[(system, board, job key "system:id", posting)] for every posting that needs a description."""
    out = []
    rows = store.q("""SELECT p.posting_id FROM postings p WHERE p.closed_at IS NULL
                      AND NOT EXISTS (SELECT 1 FROM posting_text t WHERE t.posting_id = p.posting_id)
                      ORDER BY p.posting_id""")
    for r in rows:
        p = store.posting(r["posting_id"])
        if not filters.check(p, profile, now, skip=("level",)).ok:      # the description may show the level
            continue
        guess = None
        for key in sorted(store.job_keys(p["posting_id"])):
            jk = key.removeprefix("job:")
            system = jk.split(":", 1)[0]
            if system not in PER_JOB + PER_BOARD:
                continue
            board = _board_for(store, p, system)
            if board:
                out.append((system, board, jk, p))
                break
            if system == "greenhouse" and guess is None:
                guess = (system, None, jk, p)          # board unknown: fetch() guesses it
        else:
            if guess:
                out.append(guess)
    return out


def _gh_url(board_name, job_id):
    return f"https://boards-api.greenhouse.io/v1/boards/{board_name}/jobs/{job_id}?pay_transparency=true"


class Describe(Adapter):
    name = "describe"
    quiet_ok = True          # nothing to describe is a normal night
    tolerate = (403, 404, 410)   # a job page that's gone or refused: no description, not a broken source

    def fetch(self, ctx, source):
        if not ctx.profile:
            return
        now = datetime.now(timezone.utc)
        cap = int(source.options.get("max_requests", 300))
        refused = {r[0] for r in ctx.store.q("SELECT DISTINCT url FROM fetches WHERE source_id=? AND http_status IN (403, 404, 410)",
                                             (source.id,))}
        per_board = {}
        n = 0

        def get(url, **kw):
            nonlocal n
            if url in refused:                      # asked before and refused or gone: don't ask again
                return None
            n += 1
            return ctx.get(source, url, conditional=False, **kw)

        guessed = {}                                # company -> the Greenhouse board found tonight, or None
        for system, board, jk, p in targets(ctx.store, ctx.profile, now):
            if system in PER_BOARD:
                per_board.setdefault(board, []).append(jk)
                continue
            if n >= cap:
                ctx.warn(f"stopped at {cap} requests; the rest wait for the next run")
                break
            job_id = jk.split(":", 1)[1]
            if board is None:                       # a Greenhouse id, its board unknown
                self._guess(ctx, get, p, jk, job_id, guessed, lambda: n >= cap)
                continue
            b = boards.split(board)[1]
            meta = {"system": system, "board": board, "wanted": [jk]}
            if system == "greenhouse":
                get(_gh_url(b, job_id), each=board, meta=meta)
            elif system == "lever":
                get(f"https://api.lever.co/v0/postings/{b}/{job_id}?mode=json", each=board, meta=meta)
            else:                                   # workday
                urls = [p.get("url")] + [r["url"] for r in ctx.store.posting_links(p["posting_id"])]
                path = _workday_path(urls)
                if not path:
                    continue
                tenant, wd, site = b.split("|")
                get(f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{path}", each=board,
                    meta=meta, headers={"Accept": "application/json"})
        for board, wanted in sorted(per_board.items()):
            if n >= cap:
                ctx.warn(f"stopped at {cap} requests; the rest wait for the next run")
                break
            _, url, _ = boards.list_request(board, full=True)
            get(url, each=board, meta={"system": boards.split(board)[0], "board": board, "wanted": sorted(wanted)})

    @staticmethod
    def _guess(ctx, get, p, jk, job_id, guessed, over_cap):
        company = company_norm(p["company"])
        meta = {"system": "greenhouse", "wanted": [jk], "company": p["company"], "guessed": True}
        if company in guessed:                      # tried tonight for another of its jobs
            if guessed[company]:
                get(_gh_url(boards.split(guessed[company])[1], job_id), each=guessed[company],
                    meta={**meta, "board": guessed[company]})
            return
        urls = [p.get("url")] + [r["url"] for r in ctx.store.posting_links(p["posting_id"])]
        page = next((u for u in urls if u and boards.job_from_url(u) == jk), None)
        guessed[company] = None
        for name in boards.greenhouse_guesses(p["company"], page):
            if over_cap():
                return
            board = boards.key("greenhouse", name)
            rec = get(_gh_url(name, job_id), each=board, meta={**meta, "board": board})
            if rec is not None and raw.ok(rec):
                guessed[company] = board
                return

    def parse(self, record, source):
        return []

    def directory(self, record, source):
        """A Greenhouse board found by guessing, under the company's name."""
        meta = record.get("meta") or {}
        if meta.get("guessed") and meta.get("company") and meta.get("board") and raw.ok(record):
            return [(meta["board"], meta["company"])]
        return []

    def texts(self, record, source):
        """[(job key, text, facts)] for the wanted jobs this record answers."""
        if not raw.ok(record):
            return []
        meta = record.get("meta") or {}
        system, board, wanted = meta.get("system"), meta.get("board"), set(meta.get("wanted") or ())
        data = json.loads(raw.body(record))
        if system == "workday":
            info = data.get("jobPostingInfo") or {}
            text = boards.html_text(info.get("jobDescription"))
            facts = {"employment": info.get("timeType")}
            return [(next(iter(wanted)), text, facts)] if wanted and (text or info.get("timeType")) else []
        if system == "greenhouse":
            jobs, _ = boards.parse_list("greenhouse", {"jobs": [data]})
        elif system == "lever":
            jobs, _ = boards.parse_list("lever", [data] if isinstance(data, dict) else data)
        else:
            jobs, _ = boards.parse_list(system, data)
        out = []
        for j in jobs:
            if not j.get("id"):
                continue
            key = boards.job_key(board, j["id"])
            if key in wanted:
                out.append((key, j.get("text"), {"employment": j.get("employment"), "pay": j.get("pay")}))
        return out


ADAPTERS = [Describe()]
