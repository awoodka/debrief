"""The board finder: for jobs that came with no job-board id, find the same job on the company's
own public board, so the description step can read it and the link can point at the company.

Most such jobs come from the new-grad lists, whose links go to jobright.ai pages (robots.txt keeps
ClaudeBot out of /jobs/) and go.vansh.nyc short links (robots.txt: Disallow: /), so we can't read
the job there.

fetch() picks the postings the description step would want (open, no description, past every
filter that doesn't need one) that have no job id, and groups them by company. A company is looked
at when it has a board in the board directory (or the posting names one) on Greenhouse, Lever,
Ashby, Workable, Recruitee or Workday:
  - Greenhouse, Lever, Ashby, Workable, Recruitee: the board's list, one request per board;
  - Workday: a search with the job's title, one request per job and board (the lists are long and
    paged);
  - SmartRecruiters never (robots.txt allows only LinkedInBot), nor the companies in the source's
    exclude_companies (Amazon, Google, Meta: each OFF for its own reason in sources.yml).
A company's boards are asked again only for postings first seen after their last answer, or once
retry_days have passed; a board that answered 403/404/410 rests for dead_days. Both come from the
fetches table, as the description step's refusals do. max_requests caps a night.

The match is the merge rule's (items.core_title and locations_overlap): the same core title and an
overlapping place, across all of the company's boards, with two tie-breaks:
  - the same job twice (one Workday site under two spellings, "External" and "external") is one;
  - for an early-career posting (a new-grad list has it, or the title says so), the one
    early-career job among several wins: Palantir lists "Software Engineer, New Grad - Query
    Engines" next to "Software Engineer – Query Engines".
Otherwise exactly one job must match; none or several, and the posting is left alone. So is a match
whose job id already belongs to another posting.

The match needs every board of the company at once, so fetch() makes it and writes it into the
record that holds the job (meta "found": {job key: {posting, company}}), as the description step
writes "wanted". parse() then emits one listing per found job, aimed at its posting
(JobItem.target), with no network: replay rebuilds the same links.
"""
import json
import re
from datetime import datetime, timedelta

from ...store import raw
from .. import boards, filters
from ..items import JobItem, company_norm, core_title, early_career, locations_overlap, title_norm
from . import Adapter

LIST_SYSTEMS = ("greenhouse", "lever", "ashby", "workable", "recruitee")
SEARCH_SYSTEMS = ("workday",)
DEAD = (403, 404, 410)
_SEVERAL = re.compile(r"^\d+\s+locations?$", re.I)      # Workday's "2 Locations": places unknown, not a city


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def company_boards(store, posting, exclude=()):
    """The boards to look at for the posting's company, Workday sites that differ only in case
    once."""
    norm = company_norm(posting["company"])
    if not norm or norm in exclude:
        return []
    found = {r["board"] for r in store.directory_by_name(norm)}
    if posting.get("board"):
        found.add(posting["board"])
    out, seen = [], set()
    for b in sorted(found):
        system = boards.split(b)[0]
        if system not in LIST_SYSTEMS + SEARCH_SYSTEMS:
            continue
        fold = b.lower() if system == "workday" else b
        if fold in seen:
            continue
        seen.add(fold)
        out.append(b)
    return out


def search_text(title):
    """A Workday search for the job: its title without the early-career words and class years."""
    core = core_title(title)
    return " ".join(t for t in title_norm(title).split() if t in core)


def match(target, pool):
    """The one (record, board, job) in the company's pool that is the target's job, or None.
    target: {title, locations, early}; pool: [(record, board, job)]."""
    core = core_title(target["title"])
    if not core:
        return None
    hits = {}
    for rec, board, job in pool:
        if not job.get("id") or not job.get("title") or core_title(job["title"]) != core:
            continue
        places = [x for x in job.get("locations") or [] if not _SEVERAL.match(x.strip())]
        if not locations_overlap(places, target["locations"]):
            continue
        hits.setdefault(boards.job_key(board, job["id"]), (rec, board, job))     # the same job twice is one
    hits = list(hits.values())
    if len(hits) > 1 and target["early"]:
        early = [h for h in hits if early_career(h[2]["title"])]
        if len(early) == 1:
            hits = early
    return hits[0] if len(hits) == 1 else None


def targets(store, profile, now, early_sources=frozenset(), exclude=()):
    """{company norm: {"boards": [...], "postings": [posting]}} for postings with no job id and no
    description that may reach the scoring call, at companies with a board to look at."""
    out = {}
    rows = store.q("""SELECT p.posting_id FROM postings p WHERE p.closed_at IS NULL
                      AND NOT EXISTS (SELECT 1 FROM posting_text t WHERE t.posting_id = p.posting_id)
                      AND NOT EXISTS (SELECT 1 FROM posting_keys k WHERE k.posting_id = p.posting_id AND k.key LIKE 'job:%')
                      ORDER BY p.posting_id""")
    for r in rows:
        p = store.posting(r["posting_id"])
        if not filters.check(p, profile, now, skip=("level",)).ok:     # the description may show the level
            continue
        bs = company_boards(store, p, exclude)
        if not bs:
            continue
        p["early"] = early_career(p["title"]) or bool(store.posting_sources(p["posting_id"]) & set(early_sources))
        g = out.setdefault(company_norm(p["company"]), {"boards": bs, "postings": []})
        g["postings"].append(p)
    return out


class BoardFinder(Adapter):
    name = "board_finder"
    quiet_ok = True              # nothing to find, or nothing found, is a normal night
    tolerate = DEAD              # a board that's gone is an answer, not a broken source

    def fetch(self, ctx, source):
        if not ctx.profile:
            return
        opts = source.options or {}
        cap = int(opts.get("max_requests", 150))
        retry = timedelta(days=int(opts.get("retry_days", 14)))
        dead_rest = timedelta(days=int(opts.get("dead_days", 30)))
        exclude = {company_norm(c) for c in opts.get("exclude_companies") or ()}
        now = datetime.fromisoformat(ctx.fetched_at.replace("Z", "+00:00"))

        last_ok, last_dead = {}, {}
        for row in ctx.store.q("SELECT url, http_status, MAX(fetched_at) AS at FROM fetches WHERE source_id=? "
                               "GROUP BY url, http_status", (source.id,)):
            if row["http_status"] == 200:
                last_ok[row["url"]] = row["at"]
            elif row["http_status"] in DEAD:
                last_dead[row["url"]] = row["at"]

        def url_of(board):
            return boards.list_request(board)[1]

        def resting(board):
            u = url_of(board)
            d = last_dead.get(u)
            return d is not None and d > last_ok.get(u, "") and d > _iso(now - dead_rest)

        def looked(p, board):
            at = last_ok.get(url_of(board))
            return at is not None and at >= p["first_seen_at"] and at > _iso(now - retry)

        plan = []
        for norm, g in targets(ctx.store, ctx.profile, now, ctx.early_sources, exclude).items():
            bs = [b for b in g["boards"] if not resting(b)]
            if not bs:
                continue
            due = [p for p in g["postings"] if not all(looked(p, b) for b in bs)]
            if not due:
                continue
            lists = [b for b in bs if boards.split(b)[0] in LIST_SYSTEMS]
            searches = [b for b in bs if boards.split(b)[0] in SEARCH_SYSTEMS]
            plan.append((norm, g["postings"], due, lists, searches,
                         len(lists) + len(searches) * len({search_text(p["title"]) for p in due})))
        plan.sort(key=lambda x: (-len(x[2]), x[0]))

        n, cache = 0, {}
        for i, (norm, postings, due, lists, searches, needed) in enumerate(plan):
            if n and n + needed > cap:
                ctx.warn(f"stopped at {n} requests (cap {cap}); {len(plan) - i} companies wait for the next run")
                break
            pool = []

            def take(rec, board):
                if not raw.ok(rec):
                    return
                try:
                    jobs, _ = boards.parse_list(boards.split(board)[0], json.loads(raw.body(rec)))
                except Exception:  # noqa: BLE001 — an unreadable answer finds nothing
                    return
                pool.extend((rec, board, j) for j in jobs)

            for b in lists:
                key = ("GET", url_of(b), None)
                if key not in cache:
                    cache[key] = ctx.get(source, url_of(b), each=b, conditional=False,
                                         meta={"system": boards.split(b)[0], "board": b})
                    n += 1
                take(cache[key], b)
            for b in searches:
                method, url, body = boards.list_request(b)
                for q in sorted({search_text(p["title"]) for p in due}):
                    key = ("POST", url, q)
                    if key not in cache:
                        cache[key] = ctx.post(source, url, {**body, "searchText": q}, each=b,
                                              meta={"system": "workday", "board": b},
                                              headers={"Accept": "application/json"})
                        n += 1
                    take(cache[key], b)
            claimed = set()
            for p in sorted(postings, key=lambda p: p["posting_id"]):
                hit = match({"title": p["title"], "locations": p["locations"], "early": p["early"]}, pool)
                if not hit:
                    continue
                rec, board, job = hit
                jk = boards.job_key(board, job["id"])
                if jk in claimed or ctx.store.find_key(f"job:{jk}"):
                    continue                          # one job, one posting; a known job stays where it is
                claimed.add(jk)
                rec["meta"].setdefault("found", {})[jk] = {"posting": p["posting_id"], "company": p["company"]}

    def parse(self, record, source):
        if not raw.ok(record):
            return []
        meta = record.get("meta") or {}
        found, board, system = meta.get("found") or {}, meta.get("board"), meta.get("system")
        if not found or not board:
            return []
        jobs, _ = boards.parse_list(system, json.loads(raw.body(record)))
        out = []
        for j in jobs:
            if not j.get("id"):
                continue
            jk = boards.job_key(board, j["id"])
            f = found.get(jk)
            if not f:
                continue
            url = j.get("url") or (boards.workday_job_url(board, j.get("path")) if system == "workday" else None) or ""
            extra = {"employment": j["employment"]} if j.get("employment") else {}
            out.append(JobItem(item_key=jk, title=j["title"], company=f["company"], locations=tuple(j.get("locations") or ()),
                               url=url, board=board, job_key=f"job:{jk}", published_at=j.get("published"),
                               scope=board, remote=j.get("remote"), pay=j.get("pay"), text=j.get("text"),
                               extra=extra, target=f["posting"]))
            found = {k: v for k, v in found.items() if k != jk}     # once per job, even if the list repeats it
        return out


ADAPTERS = [BoardFinder()]
