"""Y Combinator companies' jobs, two ways.

yc_boards: the boards of YC companies named in Hacker News titles, through the HN Algolia API:
  "Launch HN: Acme (YC W24) – ...", "Show HN: Acme (YC W24) – ...", "Acme (YC W24) is hiring ...",
and HN job posts, which only YC companies may post ("Acme (YC W24) – Founding Engineer").

waas_jobs: Work at a Startup's public job pages (workatastartup.com/jobs/<id>), walked by id. Ids rise
with time and a page has no date, so each night walks up from the highest id this source holds (its
first night: about first_days back, from the seed id and ids_per_day) until max_requests (the walk goes
on the next night) or stop_after misses in a row (ids not yet used, or jobs that aren't public) past
the highest job id any page it read links to (its company's other jobs, similar jobs): a run of
misses below a job we know is public is a gap, not the top. What the raw store keeps of a page is the
job, its company's name, batch and size, the highest job id it links to, and the description without
the founders' names, email addresses or LinkedIn profile links; never the founders, their photos and
bios, or the page's other people. minExperience ("Any (new grads ok)") is the job's level field.
"""
import html
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from typing import NamedTuple
from urllib.parse import urlencode, urlsplit

from ...store import raw
from .. import boards
from ..items import JobItem, company_norm
from . import Adapter, blocked_by_status
from .boards_watch import Watchlist
from .describe import no_people

META_KEY = "yc_companies"
CONFIRMS = re.compile(r"\by\s?combinator\b", re.I)
BY_NAME = ("greenhouse", "lever", "ashby", "workable", "recruitee")
PAGE = 1000                              # Algolia's largest page; one query reaches 1,000 hits at most

TITLE = re.compile(r"^(?:(?P<kind>launch|show) hn:\s*)?(?P<name>.+?)\s*\(yc\s*(?P<batch>[a-z])\s*'?(?P<year>\d{2})"
                   r"(?:\s*[,;/][^)]*)?\)\s*(?P<rest>.*)$", re.I)
HIRING = re.compile(r"(?:is|are)\s+hiring\b", re.I)
ROLE = re.compile(r"\b(?:is|are)\s+hiring\b[\s–—-]*(?:(?:a|an)\s+)?(?P<role>.*)$", re.I)
NOT_A_ROLE = re.compile(r"^(?:in|at|for|across|to|from|on|with)\b", re.I)
DASH = re.compile(r"\s+[–—-]+\s+")


@dataclass(frozen=True)
class YcCompany:
    name: str
    batch: str
    links: tuple = ()


class YcTitle(NamedTuple):
    name: str
    batch: str
    role: str | None


def parse_title(title, job=False):
    """YcTitle when an HN title names a YC company in one of the forms above, else None.
    job: the item is an HN job post, so any "Name (YC W24) ..." title names its poster."""
    m = TITLE.match((title or "").strip())
    if not m or not (m["kind"] or job or HIRING.match(m["rest"])):
        return None
    name = DASH.split(m["name"])[0].strip()
    if not company_norm(name) or len(name) > 60:
        return None
    hiring = ROLE.search(m["rest"])
    role = hiring["role"].strip() if hiring else ""
    return YcTitle(name, f"{m['batch'].upper()}{m['year']}", role if role and not NOT_A_ROLE.match(role) else None)


def companies_from_hits(hits):
    newest, links = {}, {}
    for h in sorted(hits, key=lambda h: h.get("created_at_i") or 0):
        found = parse_title(h.get("title"), job="job" in (h.get("_tags") or ()))
        if not found:
            continue
        key = company_norm(found.name)
        newest[key] = found.name, found.batch
        board = boards.board_from_url(h.get("url") or "")
        if board:
            links.setdefault(key, set()).add(board)
    return [YcCompany(name, batch, tuple(sorted(links.get(k, ())))) for k, (name, batch) in sorted(newest.items())]


def _fold(name):
    return re.sub(r"[^a-z0-9]", "", company_norm(name))


def spells(board, name):
    return _fold(boards.split(board)[1]) == _fold(name)


def targets(companies, lookup, watched_boards=frozenset(), watched_names=frozenset()):
    out = {}
    for c in companies:
        if company_norm(c.name) in watched_names:
            continue
        named = {b for key in {company_norm(c.name), _fold(c.name)} for b in lookup(key)
                 if boards.split(b)[0] in BY_NAME and spells(b, c.name)}
        for b in sorted(set(c.links) | named):
            if b not in watched_boards:
                out.setdefault(b, c)
    return out


def names_yc(record):
    return bool(CONFIRMS.search(raw.body(record).decode("utf-8", "replace")))


def judged(record):
    if not raw.ok(record):
        return None
    try:
        jobs, _ = boards.parse_list(boards.split(record["meta"]["board"])[0], json.loads(raw.body(record)))
    except ValueError:
        return None
    return names_yc(record) if jobs else None


class YcBoards(Watchlist):
    name = "yc_boards"
    tolerate = (404,)

    def fetch(self, ctx, source):
        stored = json.loads(ctx.store.get_meta(META_KEY) or "{}")
        companies, refreshed_at = self._companies(ctx, source, stored)
        verdicts = stored.get("judged") or {}
        gone = set() if refreshed_at else set(stored.get("gone") or ())
        prof = ctx.profile or {}
        watched = prof.get("companies") or []
        found = targets(companies, lambda n: [r["board"] for r in ctx.store.directory_by_name(n)],
                        {c["board"] for c in watched if c.get("board")}, {company_norm(c["name"]) for c in watched})
        max_pages = int(source.options.get("max_pages", 100))
        for board, c in found.items():
            verdict = True if board in c.links else verdicts.get(board)
            if board in gone or verdict is False:
                continue
            first, *_ = self.read_board(ctx, source, board, {"board": board, "company": c.name, "yc": c.batch,
                                                             "confirmed": bool(verdict)}, max_pages)
            if first.get("status") == 404:
                gone.add(board)
            elif verdict is None and (verdict := judged(first)) is not None:
                verdicts[board] = verdict
        ctx.store.set_meta(META_KEY, json.dumps({"fetched_at": refreshed_at or stored.get("fetched_at"),
                                                 "companies": [asdict(c) for c in companies],
                                                 "judged": verdicts, "gone": sorted(gone)}))

    def _companies(self, ctx, source, stored):
        kept = [YcCompany(c["name"], c["batch"], tuple(c["links"])) for c in stored.get("companies") or []]
        age = datetime.fromisoformat(ctx.fetched_at.replace("Z", "+00:00")) - datetime.fromisoformat(
            (stored.get("fetched_at") or "1970-01-01T00:00:00Z").replace("Z", "+00:00"))
        if kept and age < timedelta(days=float(source.options.get("refresh_days", 7))):
            return kept, None
        hits = self._search(ctx, source)
        fresh = companies_from_hits(hits) if hits is not None else []
        if not fresh:
            ctx.warn("the YC company list wasn't refreshed from HN: " + ("a search failed" if hits is None else "no company found"))
            return kept, None
        return fresh, ctx.fetched_at

    def _search(self, ctx, source):
        since = int(datetime.fromisoformat(str(source.options["since"]) + "T00:00:00+00:00").timestamp())
        hits, before = [], None
        for _ in range(int(source.options.get("max_searches", 30))):
            numeric = f"created_at_i>{since}" + (f",created_at_i<{before}" if before else "")
            q = urlencode({"query": '"(YC"', "tags": "(story,job)", "restrictSearchableAttributes": "title",
                           "hitsPerPage": PAGE, "numericFilters": numeric})
            rec = ctx.get(source, f"{source.options['search']}?{q}", conditional=False)
            if not raw.ok(rec):
                return None
            page = json.loads(raw.body(rec)).get("hits") or []
            hits += page
            if len(page) < PAGE:
                return hits
            before = min(h["created_at_i"] for h in page)
        ctx.warn(f"the HN search stopped after {source.options.get('max_searches', 30)} pages")
        return hits

    def parse(self, record, source):
        meta = record.get("meta") or {}
        if "board" not in meta or not (meta["confirmed"] or names_yc(record)):
            return []
        out = super().parse(record, source)
        for it in out:
            it.extra = {**it.extra, "yc": meta["yc"]}
        return out


WAAS_JOB = "https://www.workatastartup.com/jobs/{}"
DATA_PAGE = re.compile(r'data-page="([^"]+)"')
JOB_FIELDS = ("id", "title", "location", "jobType", "minExperience", "salaryRange", "equityRange", "sponsorsVisa", "skills")
COMPANY_FIELDS = ("name", "slug", "batch", "teamSize", "url", "location", "industry", "description")


def _page(content):
    """The page's Inertia props, or None when it has no data-page (a 404 page, or not a job page at all)."""
    m = DATA_PAGE.search(content.decode("utf-8", "replace"))
    return json.loads(html.unescape(m.group(1))).get("props") if m else None


def _without_names(text, people):
    for name in sorted({n for p in people for n in [p, *p.split()] if len(n) >= 3}, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(name)}\b", "[name]", text)
    return no_people(text)


def waas_kept(fetched):
    """A job page cut to {job, company, description}: listed fields only, the description without the
    founders' names. A page with no job (not public) is kept as the 404 it means."""
    if not fetched.ok:
        return replace(fetched, content=b"")
    props = _page(fetched.content) or {}
    job, company = props.get("job"), props.get("company") or {}
    if not job:
        return replace(fetched, status=404, content=b"", error="HTTP 200 with no job: not public")
    founders = [f.get("name") or "" for f in company.get("founders") or () if isinstance(f, dict)]
    linked = [j.get("id") for j in [*(props.get("similarJobs") or ()), *(company.get("jobs") or ())] if isinstance(j, dict)]
    kept = {"job": {k: job.get(k) for k in JOB_FIELDS}, "company": {k: company.get(k) for k in COMPANY_FIELDS},
            "description": _without_names(boards.html_text(job.get("descriptionHtml")) or "", founders) or None,
            "links_to": max((i for i in linked if isinstance(i, int)), default=None)}
    return replace(fetched, content=json.dumps(kept, ensure_ascii=False).encode(), headers={"content-type": "application/json"})


def waas_block(fetched):
    if urlsplit(fetched.final_url or "").hostname == "account.ycombinator.com":
        return "a login wall"
    if fetched.ok and DATA_PAGE.search(fetched.content.decode("utf-8", "replace")) is None:
        return "a page with no job data (a challenge?)"
    return blocked_by_status(fetched)


def start_id(options, run_date):
    """Where the first night's walk starts: first_days of ids before tonight's estimated top, from the seed
    (a job id seen on a known day) and a low estimate of ids_per_day, so it starts in the past, never ahead."""
    seed, per_day = options["seed"], int(options["ids_per_day"])
    days = (date.fromisoformat(str(run_date)) - date.fromisoformat(str(seed["date"]))).days
    return int(seed["id"]) + (days - int(options.get("first_days", 3))) * per_day


class WaasJobs(Adapter):
    name = "waas_jobs"
    tolerate = (404,)

    def fetch(self, ctx, source):
        o = source.options
        top = ctx.store.top_item_number(source.id)
        jid = top + 1 if top else start_id(o, ctx.run_date)
        cap, stop_after, asked, misses, linked = int(o.get("max_requests", 300)), int(o.get("stop_after", 60)), 0, 0, 0
        while misses < stop_after or jid <= linked:
            if asked >= cap:
                ctx.warn(f"stopped at {cap} job pages (at id {jid}); the walk goes on next night")
                return
            url = WAAS_JOB.format(jid)
            rec = ctx.get(source, url, meta={"id": jid}, conditional=False, keep=waas_kept, block=waas_block)
            asked += 1
            if ctx.blocked(url):
                return
            if raw.ok(rec):
                misses, linked = 0, max(linked, json.loads(raw.body(rec)).get("links_to") or 0)
            else:
                misses += 1
            jid += 1

    def parse(self, record, source):
        if not raw.ok(record):
            return []
        kept = json.loads(raw.body(record))
        job, company = kept["job"], kept["company"]
        where, batch, team = str(job.get("location") or ""), company.get("batch"), company.get("teamSize")
        size = ", ".join(x for x in (f"{team} people" if team else "", f"YC {batch}" if batch else "") if x) or None
        return [JobItem(item_key=f"waas:{job['id']}", title=str(job["title"]).strip(), company=str(company.get("name") or "").strip(),
                        locations=tuple(x.strip() for x in where.split(" / ") if x.strip()), url=WAAS_JOB.format(job["id"]),
                        remote=True if "remote" in where.lower() else None,
                        pay={"summary": job["salaryRange"]} if job.get("salaryRange") else None, text=kept.get("description"),
                        extra={"experience": job.get("minExperience"), "employment": job.get("jobType"), "yc": batch,
                               "company_size": size, "via": "Work at a Startup"})]


ADAPTERS = [YcBoards(), WaasJobs()]
