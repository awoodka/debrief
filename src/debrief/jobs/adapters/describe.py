"""Descriptions for the jobs that may reach the scoring call (docs/jobs/PLAN.md, "M2 design").

fetch() picks the open postings that pass every filter that doesn't need a description
(filters.check with no text), have no description yet, and carry a job-board id whose board we
know, or a Greenhouse id whose board we don't (below). It asks the job board's own public API for each:
  Greenhouse, Lever,  one request per job
  SmartRecruiters
  Workday             the job's cxs page (one request per job)
  Ashby, Workable,    the board's list, once per board, for all of that board's wanted jobs
  Recruitee
A job no board reaches gets the page an aggregator's listing links to (PAGES: jobright, LinkedIn's
guest job page), at most options.max_pages[site] a night, outside max_requests. What the raw store keeps
of such a page is the job alone (its description and criteria), never the people it names. A host that
blocks us is asked nothing more that run (adapters.Ctx); its jobs wait.

Where the board names the job's country (Workday, Lever, Ashby), texts() adds it to the job's facts
(board_country, board_more_places): the filters trust it over a list's place.

The board's answer also says whether a job is still open (gone()): a per-job request (Greenhouse,
Lever, Workday) answering 404 or 410, or a complete board list (Ashby, Workable, Recruitee: one
response, ok and not empty) without the job, means it closed on the company's own board. A
request made while guessing a Greenhouse board name never counts: its 404 means a wrong guess.

A Greenhouse id from a company page ("sambanova.ai/...?gh_jid=6240725004") names the job but not
its board. For those, up to 4 board names (boards.greenhouse_guesses) are tried against the board
API's page for that exact job, stopping at the first that answers: Greenhouse job ids are global,
so an answer proves the board. A found board is reused for the company's other jobs that night,
and directory() files it in the board directory, so later nights know it. A guess that 404s is
never asked again (the refused set), nor is a page that answered 404, 410, or a 403 that wasn't a block.
"""
import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ...http import Fetched
from ...store import raw
from .. import boards, filters, night, profile as profiles
from ..items import company_norm
from . import BLOCK_MARK, Adapter, blocked_by_status

PER_BOARD = ("ashby", "workable", "recruitee")
NEXT_DATA = re.compile(r'<script[^>]*\bid="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
PEOPLE = ("socialConnections", "leadership", "jobRecruiter", "jobRecruiterProfileUrl")
PERSON_PROFILE = "linkedin.com/in/"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PROFILE_LINK = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[^\s)>\]]*", re.I)
WORKDAY_WITHHELD = re.compile(rb'"errorCode"\s*:\s*"S22"')
JOBRIGHT_SECTIONS = (("Requirements", lambda j: (j.get("qualifications") or {}).get("mustHave")),
                     ("About the job", lambda j: [j.get("jobSummary")]),
                     ("Responsibilities", lambda j: j.get("coreResponsibilities")),
                     ("Preferred qualifications", lambda j: (j.get("qualifications") or {}).get("preferredHave")))


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


def _job_result(html):
    """The page's jobResult, empty when jobright has no job there. ValueError: the page holds no job data
    at all (jobright's security challenge)."""
    m = NEXT_DATA.search(html)
    try:
        return json.loads(m.group(1))["props"]["pageProps"]["dataSource"]["jobResult"]
    except (AttributeError, KeyError, TypeError) as e:
        raise ValueError("no job data in the page") from e


def jobright_text(html):
    job = _job_result(html)
    try:
        lines_of = [(head, [str(x).strip() for x in lines(job) or () if x and str(x).strip()])
                    for head, lines in JOBRIGHT_SECTIONS]
    except (AttributeError, TypeError) as e:
        raise ValueError("no job data in the page") from e
    text = "\n".join(line for head, lines in lines_of if lines for line in (head, *lines))
    return text[:20000] or None, {"employment": job.get("employmentType")}


def _without(x, keys):
    if isinstance(x, dict):
        return {k: _without(v, keys) for k, v in x.items() if k not in keys}
    return [_without(v, keys) for v in x] if isinstance(x, list) else x


def jobright_kept(html):
    try:
        data = _without(json.loads(NEXT_DATA.search(html).group(1)), PEOPLE)
    except (AttributeError, ValueError):
        return ""
    kept = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    return "" if PERSON_PROFILE in kept else f'<script id="__NEXT_DATA__" type="application/json">{kept}</script>'


def _jobright_kept(fetched):
    """What the raw store keeps of a jobright answer: its job data without people. An empty jobResult
    is jobright saying it has no job there: kept as the 404 it means, so the page isn't asked again."""
    html = fetched.content.decode("utf-8", "replace")
    kept = replace(fetched, content=jobright_kept(html).encode())
    try:
        gone = fetched.ok and not _job_result(html)
    except ValueError:
        return kept
    return replace(kept, status=404, error="HTTP 200 with an empty jobResult: no job at jobright") if gone else kept


def _jobright_block(fetched):
    if fetched.ok:
        try:
            _job_result(fetched.content.decode("utf-8", "replace"))
        except ValueError:
            return "a page with no job data (jobright's challenge)"
    return blocked_by_status(fetched)


def no_people(text):
    """A description without the email addresses and LinkedIn profile links in it."""
    return PROFILE_LINK.sub("", EMAIL.sub("", text or "")).strip() or None


def _kept_job(fetched, description, criteria):
    """The stored form of a job page: {"description", "criteria"} as JSON, nothing else of the page."""
    text = no_people(description)
    if not text:
        return replace(fetched, content=b"")
    kept = {"description": text[:20000], "criteria": {k: v for k, v in criteria.items() if v}}
    return replace(fetched, content=json.dumps(kept, ensure_ascii=False).encode(),
                   headers={"content-type": "application/json"})


LINKEDIN_CRITERIA = ("Seniority level", "Employment type", "Job function")


def _linkedin_kept(fetched):
    """LinkedIn's guest job page, cut to the job's description and criteria (seniority, employment type,
    function). The poster, the hiring team, the people who work there and every photo are left behind."""
    if not fetched.ok:
        return replace(fetched, content=b"")
    soup = BeautifulSoup(fetched.content.decode("utf-8", "replace"), "html.parser")
    body = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
    criteria = {}
    for item in soup.select(".description__job-criteria-item"):
        name, value = item.select_one(".description__job-criteria-subheader"), item.select_one(".description__job-criteria-text")
        if name and value and name.get_text(strip=True) in LINKEDIN_CRITERIA:
            criteria[name.get_text(strip=True)] = value.get_text(" ", strip=True)
    return _kept_job(fetched, boards.html_text(str(body)) if body else None, criteria)


def linkedin_block(fetched):
    wall = urlsplit(fetched.final_url or "").path.startswith(("/authwall", "/login", "/uas/", "/checkpoint"))
    return blocked_by_status(fetched) or ("a login wall" if wall else None)


def _job_text(body):
    """(description, facts) from a page _kept_job stored. ValueError: nothing was kept (no job there)."""
    try:
        kept = json.loads(body)
    except ValueError as e:
        raise ValueError("no job in the page") from e
    c = kept.get("criteria") or {}
    return kept.get("description"), {"employment": c.get("Employment type"), "seniority": c.get("Seniority level")}


@dataclass(frozen=True)
class PageReader:
    """A job page an aggregator's listing links to, read for a job no board reaches. `link` matches the
    listing's URL and names the page's id; `text` reads (description, facts) from what `keep` stored."""
    link: re.Pattern[str]
    url: Callable[[str], str]
    keep: Callable[[Fetched], Fetched]
    block: Callable[[Fetched], str | None]
    text: Callable[[bytes], tuple[str | None, dict]]


PAGES = {
    "jobright": PageReader(re.compile(r"^https://jobright\.ai/jobs/info/(?P<page_id>[0-9a-f]+)$"),
                           "https://jobright.ai/jobs/info/{}".format, _jobright_kept, _jobright_block,
                           lambda body: jobright_text(body.decode("utf-8", "replace"))),
    "linkedin": PageReader(re.compile(r"^https://(?:www\.)?linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#]*-)?(?P<page_id>\d{6,})/?(?:[?#].*)?$"),
                           "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}".format, _linkedin_kept,
                           linkedin_block, _job_text),
}


def _page_listings(store, posting):
    """[(site, listing (source id, item key), page id)] for each PAGES site a listing of the posting links
    to, in PAGES order; per site, an open listing first."""
    rows = sorted((not r["active"], r["source_id"], r["item_key"], r["url"]) for r in store.posting_links(posting["posting_id"]))
    out = []
    for site, reader in PAGES.items():
        hit = next(((sid, key, m.group("page_id")) for _, sid, key, url in rows if (m := reader.link.match(url))), None)
        if hit:
            out.append((site, hit[:2], hit[2]))
    return out


def _workday_block(fetched):
    """Workday answers some job pages with a JSON 403 (errorCode S22, "permission denied") while the rest
    of the tenant answers: that job's own answer, not a block (every stored 403 from a Workday job page,
    and in 7 of 16 runs the tenant answered 200 after it; checked 2026-11-23)."""
    if fetched.status == 403 and WORKDAY_WITHHELD.search(fetched.content):
        return None
    return blocked_by_status(fetched)


def _smartrecruiters_kept(fetched):
    """A SmartRecruiters posting without its creator (the recruiter's name and photo)."""
    try:
        data = json.loads(fetched.content)
    except ValueError:
        return fetched
    return replace(fetched, content=json.dumps(_without(data, ("creator",)), ensure_ascii=False).encode())


def smartrecruiters_text(data):
    sections = (data.get("jobAd") or {}).get("sections") or {}
    text = "\n".join(x for key in ("jobDescription", "qualifications", "additionalInformation")
                     if (sec := sections.get(key) or {}).get("text")
                     for x in (sec.get("title"), boards.html_text(sec["text"])) if x)
    loc = data.get("location") or {}
    return text[:20000] or None, {"employment": (data.get("typeOfEmployment") or {}).get("label"),
                                  "experience": (data.get("experienceLevel") or {}).get("label"),
                                  **_country(loc.get("country"), False)}


def targets(store, profile, run_date, now):
    out = []
    pool = {r["posting_id"] for r in store.pool(run_date, night.DAILY)}
    rows = store.q("""SELECT p.posting_id FROM postings p WHERE p.closed_at IS NULL
                      AND NOT EXISTS (SELECT 1 FROM posting_text t WHERE t.posting_id = p.posting_id)
                      ORDER BY p.posting_id""")
    for r in rows:
        if r["posting_id"] not in pool:
            continue
        p = store.posting(r["posting_id"])
        if not filters.check(p, profile, now, skip=("level",)).ok:      # the description may show the level
            continue
        guess = None
        for key in sorted(store.job_keys(p["posting_id"])):
            jk = key.removeprefix("job:")
            system = jk.split(":", 1)[0]
            if system not in JOB_PAGES and system not in PER_BOARD:
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
            if pages := _page_listings(store, p):
                out.append(("page", None, pages, p))      # after a guess, read only if it finds no board
    since, ranking = store.score_since(run_date), profiles.ranking(profile)
    newest = sorted(out, key=lambda t: t[3]["first_seen_at"], reverse=True)
    return sorted(newest, key=lambda t: not night.tonight(t[3], since, ranking, now))


def _country(country, more_places):
    """The board's own word on where the job is: {board_country, board_more_places} when the board
    names a country (Workday, Lever, Ashby), else nothing. filters.check uses it to keep a list's
    wrong place from passing a job its board puts abroad."""
    return {"board_country": str(country).strip(), "board_more_places": bool(more_places)} if country else {}


def _gh_url(board_name, job_id):
    return f"https://boards-api.greenhouse.io/v1/boards/{board_name}/jobs/{job_id}?pay_transparency=true"


def _workday_url(board_id, job_id, links):
    path = _workday_path(links)
    if not path:
        return None
    tenant, wd, site = board_id.split("|")
    return f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{path}"


@dataclass(frozen=True)
class JobPage:
    """A job board's page for one job. url(board id, job id, the posting's links) is None when the links
    don't name the page."""
    url: Callable[[str, str, list[str]], str | None]
    keep: Callable[[Fetched], Fetched] | None = None
    block: Callable[[Fetched], str | None] | None = None
    headers: dict[str, str] | None = None


JOB_PAGES = {
    "greenhouse": JobPage(lambda b, job_id, _: _gh_url(b, job_id)),
    "lever": JobPage(lambda b, job_id, _: f"https://api.lever.co/v0/postings/{b}/{job_id}?mode=json"),
    "smartrecruiters": JobPage(lambda b, job_id, _: f"https://api.smartrecruiters.com/v1/companies/{b}/postings/{job_id}",
                               keep=_smartrecruiters_kept),
    "workday": JobPage(_workday_url, block=_workday_block, headers={"Accept": "application/json"}),
}


class Describe(Adapter):
    name = "describe"
    quiet_ok = True          # nothing to describe is a normal night
    tolerate = (403, 404, 410)   # a job page that's gone or withheld: no description, not a broken source

    def fetch(self, ctx, source):
        if not ctx.profile:
            return
        now = datetime.now(timezone.utc)
        cap = int(source.options.get("max_requests", 300))
        refused = {r[0] for r in ctx.store.q("""SELECT DISTINCT url FROM fetches WHERE source_id=? AND http_status IN (403, 404, 410)
                                                AND COALESCE(error, '') NOT LIKE ?""", (source.id, f"%({BLOCK_MARK}:%"))}
        per_board = {}
        n = 0

        def get(url, **kw):
            nonlocal n
            if url in refused or ctx.blocked(url):
                return None
            n += 1
            return ctx.get(source, url, conditional=False, **kw)

        guessed = {}                                # company -> the Greenhouse board found tonight, or None
        boarded = set()
        found = targets(ctx.store, ctx.profile, ctx.run_date, now)
        for system, board, jk, _ in found:
            if system in PER_BOARD:
                per_board.setdefault(board, []).append(jk)
        for system, board, jk, p in found:
            if system == "page":
                continue
            if n >= cap:
                ctx.warn(f"stopped at {cap} requests; the rest wait for the next run")
                break
            if system in PER_BOARD:
                wanted = per_board.pop(board, None)
                if wanted:
                    _, url, _ = boards.list_request(board, full=True)
                    get(url, each=board, meta={"system": system, "board": board, "wanted": sorted(wanted)})
                continue
            job_id = jk.split(":", 1)[1]
            if board is None:                       # a Greenhouse id, its board unknown
                if self._guess(ctx, get, p, jk, job_id, guessed, lambda: n >= cap):
                    boarded.add(p["posting_id"])
                continue
            page = JOB_PAGES[system]
            links = [p.get("url")] + [r["url"] for r in ctx.store.posting_links(p["posting_id"])]
            if url := page.url(boards.split(board)[1], job_id, links):
                get(url, each=board, meta={"system": system, "board": board, "wanted": [jk]}, keep=page.keep,
                    block=page.block, headers=page.headers)
        self._pages(ctx, source, [t for t in found if t[0] == "page" and t[3]["posting_id"] not in boarded], refused)

    @staticmethod
    def _pages(ctx, source, found, refused):
        """Each job's first page (PAGES order) that isn't gone, blocked tonight or over its site's cap; a
        block moves the job on to its next site's page."""
        caps = {site: int(n) for site, n in (source.options.get("max_pages") or {}).items()}
        asked, waiting = Counter(), Counter()
        for _, _, pages, _ in found:
            for site, listing, page_id in pages:
                url = PAGES[site].url(page_id)
                if url in refused or ctx.blocked(url):
                    continue
                if asked[site] >= caps.get(site, 0):
                    waiting[site] += 1
                    continue
                asked[site] += 1
                ctx.get(source, url, conditional=False, meta={"system": site, "listing": list(listing)},
                        keep=PAGES[site].keep, block=PAGES[site].block)
                if not ctx.blocked(url):
                    break
        for site, n in sorted(waiting.items()):
            if caps.get(site, 0):
                ctx.warn(f"stopped at {caps[site]} {site} pages; {n} wait for the next run")

    @staticmethod
    def _guess(ctx, get, p, jk, job_id, guessed, over_cap):
        """The Greenhouse board found for the job's company tonight, or None."""
        company = company_norm(p["company"])
        meta = {"system": "greenhouse", "wanted": [jk], "company": p["company"], "guessed": True}
        if company in guessed:                      # tried tonight for another of its jobs
            if guessed[company]:
                get(_gh_url(boards.split(guessed[company])[1], job_id), each=guessed[company],
                    meta={**meta, "board": guessed[company]})
            return guessed[company]
        urls = [p.get("url")] + [r["url"] for r in ctx.store.posting_links(p["posting_id"])]
        page = next((u for u in urls if u and boards.job_from_url(u) == jk), None)
        guessed[company] = None
        for name in boards.greenhouse_guesses(p["company"], page):
            if over_cap():
                return None
            board = boards.key("greenhouse", name)
            rec = get(_gh_url(name, job_id), each=board, meta={**meta, "board": board})
            if rec is not None and raw.ok(rec):
                guessed[company] = board
                return board
        return None

    def parse(self, record, source):
        return []

    def gone(self, record, source):
        meta = record.get("meta") or {}
        wanted, system = set(meta.get("wanted") or ()), meta.get("system")
        if not wanted or meta.get("guessed") or not system:
            return {}
        status = record.get("status")
        if system in JOB_PAGES:
            if status in (404, 410):
                return {k: True for k in wanted}
            return {k: False for k in wanted} if raw.ok(record) else {}
        if not raw.ok(record):
            return {}
        try:
            jobs, _ = boards.parse_list(system, json.loads(raw.body(record)))
        except ValueError:
            return {}
        if not jobs:
            return {}                               # an empty list may be a format change: conclude nothing
        have = {boards.job_key(meta["board"], j["id"]) for j in jobs if j.get("id")}
        return {k: k not in have for k in wanted}

    def directory(self, record, source):
        """A Greenhouse board found by guessing, under the company's name."""
        meta = record.get("meta") or {}
        if meta.get("guessed") and meta.get("company") and meta.get("board") and raw.ok(record):
            return [(meta["board"], meta["company"])]
        return []

    def texts(self, record, source):
        """[(job key, text, facts)] for the wanted jobs this record answers; for an aggregator's page
        (PAGES), [(listing (source id, item key), text, facts)]."""
        if not raw.ok(record):
            return []
        meta = record.get("meta") or {}
        system, board, wanted = meta.get("system"), meta.get("board"), set(meta.get("wanted") or ())
        if system in PAGES:
            text, facts = PAGES[system].text(raw.body(record))
            return [(tuple(meta["listing"]), text, facts)] if text else []
        data = json.loads(raw.body(record))
        if system == "smartrecruiters":
            text, facts = smartrecruiters_text(data)
            return [(next(iter(wanted)), text, facts)] if wanted and text else []
        if system == "workday":
            info = data.get("jobPostingInfo") or {}
            text = boards.html_text(info.get("jobDescription"))
            facts = {"employment": info.get("timeType"),
                     **_country((info.get("country") or {}).get("descriptor"), bool(info.get("additionalLocations")))}
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
                out.append((key, j.get("text"), {"employment": j.get("employment"), "pay": j.get("pay"),
                                                 **_country(j.get("country"), j.get("more_places"))}))
        return out


ADAPTERS = [Describe()]
