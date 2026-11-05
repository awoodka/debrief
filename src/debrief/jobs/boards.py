"""Job-board systems (ATSs): how to list one company's board, read the answer, and recognize a
board from any job URL.

A board is "<system>:<id>", e.g. "greenhouse:anthropic", "workday:nvidia|wd5|NVIDIAExternalCareerSite".
Ids are lowercase except where the system is case-sensitive (SmartRecruiters ids, Workday sites).

list_request(board, page) gives the request for one page of a board's list. parse_list(system, data)
reads the JSON into (jobs, total). `total` is set for paged systems (Workday, SmartRecruiters), and
each job is {"title", "published" (ISO UTC or None), "age_days" (Workday's relative dates), "location"}.
"""
import re
from html import unescape
from urllib.parse import parse_qs, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .. import times

SYSTEMS = ("greenhouse", "lever", "ashby", "workable", "smartrecruiters", "recruitee", "workday")
WORKDAY_PAGE = 20
SMARTRECRUITERS_PAGE = 100


def key(system, board_id):
    if system not in SYSTEMS:
        raise ValueError(f"unknown job-board system {system!r}")
    if system == "workday":
        tenant, wd, site = board_id.split("|")
        return f"workday:{tenant.lower()}|{wd.lower()}|{site}"
    if system == "smartrecruiters":
        return f"smartrecruiters:{board_id}"
    return f"{system}:{board_id.lower()}"


def split(board):
    system, _, board_id = board.partition(":")
    return system, board_id


def list_request(board, page=0, full=False):
    """(method, url, json_body) for one page of the board's list. full=True asks for descriptions
    and pay where the list call can carry them (watchlist boards; the probe lists without)."""
    system, b = split(board)
    if system == "greenhouse":
        return "GET", f"https://boards-api.greenhouse.io/v1/boards/{b}/jobs" + ("?content=true&pay_transparency=true" if full else ""), None
    if system == "lever":
        return "GET", f"https://api.lever.co/v0/postings/{b}?mode=json", None
    if system == "ashby":
        return "GET", f"https://api.ashbyhq.com/posting-api/job-board/{b}?includeCompensation=true", None
    if system == "workable":
        return "GET", f"https://apply.workable.com/api/v1/widget/accounts/{b}" + ("?details=true" if full else ""), None
    if system == "smartrecruiters":
        return ("GET", f"https://api.smartrecruiters.com/v1/companies/{b}/postings"
                       f"?limit={SMARTRECRUITERS_PAGE}&offset={page * SMARTRECRUITERS_PAGE}", None)
    if system == "recruitee":
        return "GET", f"https://{b}.recruitee.com/api/offers/", None
    if system == "workday":
        tenant, wd, site = b.split("|")
        return ("POST", f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
                {"appliedFacets": {}, "limit": WORKDAY_PAGE, "offset": page * WORKDAY_PAGE, "searchText": ""})
    raise ValueError(f"unknown job-board system {system!r}")


def pages_needed(system, total):
    if total is None:
        return 1
    size = {"workday": WORKDAY_PAGE, "smartrecruiters": SMARTRECRUITERS_PAGE}.get(system)
    return max(1, -(-int(total) // size)) if size else 1


_POSTED = re.compile(r"posted\s+(today|yesterday|(\d+)\+?\s+days?\s+ago)", re.I)


def workday_age_days(text):
    """Workday's list dates are relative: "Posted Today", "Posted Yesterday", "Posted 3 Days Ago",
    "Posted 30+ Days Ago". The age in days, or None."""
    m = _POSTED.search(text or "")
    if not m:
        return None
    word = m.group(1).lower()
    return 0 if word == "today" else 1 if word == "yesterday" else int(m.group(2))


_ZONE = re.compile(r"\s*(UTC|GMT)$", re.I)


def when(value):
    """ISO 8601 UTC from any date a job source sends. Like times.iso, plus "2026-10-29 08:16:53 UTC"
    (Recruitee) and epoch milliseconds (Lever)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 1e11:
        value = value / 1000
    out = times.iso(value)
    if out is None and isinstance(value, str) and _ZONE.search(value.strip()):
        out = times.iso(_ZONE.sub("Z", value.strip()))
    return out


def _loc(*parts):
    return ", ".join(str(p) for p in parts if p)


def html_text(html, cap=20000):
    """Plain text from a description's HTML (Greenhouse sends it HTML-escaped), capped."""
    if not html:
        return None
    text = BeautifulSoup(unescape(str(html)), "html.parser").get_text("\n")
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return text[:cap] or None


def _pay(lo, hi, currency=None, period=None):
    if lo in (None, "", 0) and hi in (None, "", 0):
        return None
    return {"min": lo, "max": hi, "currency": currency, "period": period}


def parse_list(system, data):
    """(jobs, total) from a board's list JSON. Raises ValueError on an unexpected shape.

    Each job: id (the system's id for it), title, published (ISO UTC or None), age_days (Workday's
    relative dates), location (one line), locations (list), url, path (Workday's externalPath),
    text (description, when the list carries one), pay, remote, employment (the system's own words
    for full-time, part-time, contract ..., when it gives them)."""
    total = None
    if system == "greenhouse":
        jobs = []
        for j in data["jobs"]:
            ranges = j.get("pay_input_ranges") or []
            r = ranges[0] if ranges else {}
            loc = (j.get("location") or {}).get("name")
            jobs.append({"id": str(j["id"]) if j.get("id") is not None else None, "title": j.get("title"),
                         "published": when(j.get("first_published") or j.get("updated_at")),
                         "location": loc, "locations": [loc] if loc else [], "url": j.get("absolute_url"),
                         "text": html_text(j.get("content")),
                         "pay": _pay(r.get("min_cents") and r["min_cents"] / 100, r.get("max_cents") and r["max_cents"] / 100,
                                     r.get("currency_type")) if r else None,
                         "remote": None})
    elif system == "lever":
        if not isinstance(data, list):
            raise ValueError(f"expected a list, got {type(data).__name__}")
        jobs = []
        for j in data:
            cat = j.get("categories") or {}
            sr = j.get("salaryRange") or {}
            text = "\n".join(x for x in [j.get("descriptionPlain")] + [
                f"{li.get('text', '')}\n{html_text(li.get('content')) or ''}" for li in j.get("lists") or []] if x)
            jobs.append({"id": j.get("id"), "title": j.get("text"), "published": when(j.get("createdAt")),
                         "location": cat.get("location"),
                         "locations": list(cat.get("allLocations") or ([cat["location"]] if cat.get("location") else [])),
                         "url": j.get("hostedUrl"), "text": (text or None) and text[:20000],
                         "pay": _pay(sr.get("min"), sr.get("max"), sr.get("currency"), sr.get("interval")),
                         "remote": (j.get("workplaceType") == "remote") if j.get("workplaceType") else None,
                         "employment": cat.get("commitment")})
    elif system == "ashby":
        jobs = []
        for j in data["jobs"]:
            locs = [j["location"]] if j.get("location") else []
            locs += [x.get("location") for x in j.get("secondaryLocations") or [] if x.get("location")]
            comp = j.get("compensation") or {}
            jobs.append({"id": j.get("id"), "title": j.get("title"), "published": when(j.get("publishedAt")),
                         "location": j.get("location") or ("Remote" if j.get("isRemote") else None),
                         "locations": locs, "url": j.get("jobUrl"),
                         "text": (j.get("descriptionPlain") or html_text(j.get("descriptionHtml")) or None),
                         "pay": ({"summary": comp["compensationTierSummary"]} if comp.get("compensationTierSummary") else None),
                         "remote": j.get("isRemote"), "employment": j.get("employmentType")})
    elif system == "workable":
        jobs = []
        for j in data["jobs"]:
            loc = _loc(j.get("city"), j.get("state"), j.get("country"))
            locs = [_loc(x.get("city"), x.get("region"), x.get("country")) for x in j.get("locations") or []] or ([loc] if loc else [])
            jobs.append({"id": j.get("shortcode"), "title": j.get("title"),
                         "published": when(j.get("published_on") or j.get("created_at")),
                         "location": loc + (" (remote)" if j.get("telecommuting") else ""), "locations": locs,
                         "url": j.get("url") or j.get("shortlink"), "text": html_text(j.get("description")),
                         "pay": None, "remote": bool(j.get("telecommuting")), "employment": j.get("employment_type")})
    elif system == "smartrecruiters":
        total = data.get("totalFound")
        jobs = []
        for j in data["content"]:
            loc = _loc((j.get("location") or {}).get("city"), (j.get("location") or {}).get("region"),
                       (j.get("location") or {}).get("country"))
            jobs.append({"id": j.get("id"), "title": j.get("name"), "published": when(j.get("releasedDate")),
                         "location": loc, "locations": [loc] if loc else [], "url": None, "text": None, "pay": None,
                         "remote": (j.get("location") or {}).get("remote")})
    elif system == "recruitee":
        jobs = []
        for j in data["offers"]:
            loc = _loc(j.get("city"), j.get("country"))
            sal = j.get("salary") or {}
            jobs.append({"id": j.get("slug") or str(j.get("id")), "title": j.get("title"),
                         "published": when(j.get("published_at") or j.get("created_at")),
                         "location": loc + (" (remote)" if j.get("remote") else ""), "locations": [loc] if loc else [],
                         "url": j.get("careers_url"), "text": html_text(j.get("description")),
                         "pay": _pay(sal.get("min"), sal.get("max"), sal.get("currency"), sal.get("period")),
                         "remote": j.get("remote")})
    elif system == "workday":
        total = data.get("total")
        jobs = []
        for j in data["jobPostings"]:
            path = j.get("externalPath") or ""
            jobs.append({"id": workday_req(path), "title": j.get("title"), "published": None,
                         "age_days": workday_age_days(j.get("postedOn")), "location": j.get("locationsText"),
                         "locations": [j["locationsText"]] if j.get("locationsText") else [], "path": path,
                         "url": None, "text": None, "pay": None, "remote": None})
    else:
        raise ValueError(f"unknown job-board system {system!r}")
    return jobs, total


_WD_REQ = re.compile(r"_([A-Za-z0-9-]+)$")


def workday_req(path):
    """A Workday job's requisition id from its path ("/job/US-CA-Santa-Clara/Engineer_JR2011267" ->
    "JR2011267"); the last path segment when there's no "_<id>" suffix."""
    last = [x for x in (path or "").split("/") if x][-1:] or [""]
    m = _WD_REQ.search(last[0])
    return m.group(1) if m else (last[0] or None)


# ------------------------------------------------------------------ recognizing a board from a URL

_WD_HOST = re.compile(r"^([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com$", re.I)
_WD_SITE_HOST = re.compile(r"^(wd\d+)\.myworkdaysite\.com$", re.I)
_LOCALE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


def board_from_url(url):
    """The board a job (or board) URL belongs to, or None if it isn't on a known system.
    Company-hosted pages that embed a board (e.g. "?gh_jid=") aren't recognized."""
    try:
        p = urlsplit((url or "").strip())
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    parts = [x for x in p.path.split("/") if x]
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io",
                "job-boards.eu.greenhouse.io") and parts:
        if parts[0] != "embed":
            return key("greenhouse", parts[0])
        token = parse_qs(p.query).get("for")          # /embed/job_app?for=<board>&token=<job>
        return key("greenhouse", token[0]) if token and token[0] else None
    if host == "boards-api.greenhouse.io" and len(parts) >= 3 and parts[:2] == ["v1", "boards"]:
        return key("greenhouse", parts[2])
    if host in ("jobs.lever.co", "jobs.eu.lever.co") and parts:
        return key("lever", parts[0])
    if host == "jobs.ashbyhq.com" and parts:
        return key("ashby", parts[0])
    if host == "apply.workable.com" and parts and parts[0] not in ("api", "j"):
        return key("workable", parts[0])
    if host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com") and parts:
        return key("smartrecruiters", parts[0])
    if host.endswith(".recruitee.com") and host.count(".") == 2:
        sub = host.split(".")[0]
        if sub not in ("www", "api", "app", "careers"):
            return key("recruitee", sub)
    m = _WD_SITE_HOST.match(host)                     # wd5.myworkdaysite.com/recruiting/<tenant>/<site>/...
    if m and len(parts) >= 3 and parts[0] == "recruiting":
        return key("workday", f"{parts[1]}|{m.group(1)}|{parts[2]}")
    m = _WD_HOST.match(host)
    if m and parts:
        if _LOCALE.match(parts[0]):
            parts = parts[1:]
        if parts and parts[0] not in ("wday", "job"):
            return key("workday", f"{m.group(1)}|{m.group(2)}|{parts[0]}")
    return None


def universe_rows(system, rows):
    """Board keys from one universe dataset file: kalil0321-style CSV rows ({name, slug, url}) or
    Riley-Dorrington-style JSON (a list of ids; Workday ids are "tenant|wdN|site")."""
    out = []
    for r in rows:
        try:
            if isinstance(r, dict):
                k = board_from_url(r.get("url") or "") if system == "workday" else key(system, r["slug"])
            else:
                k = key(system, str(r))
        except (KeyError, ValueError):
            k = None
        if k and k.startswith(system + ":"):
            out.append(k)
    return out


# ------------------------------------------------------------------- recognizing a job from a URL

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def job_from_url(url):
    """The job-board id of the job a URL points at, as "<system>:<id>", or None. Greenhouse,
    Lever, Ashby and SmartRecruiters ids are global; Workable, Recruitee and Workday ids are scoped
    to the company ("workable:<board>/<code>", "workday:<tenant>/<req>"). Company-hosted pages that
    carry a Greenhouse or Ashby id in the query ("?gh_jid=", "?ashby_jid=") count too."""
    try:
        p = urlsplit((url or "").strip())
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    parts = [x for x in p.path.split("/") if x]
    q = parse_qs(p.query)
    if q.get("gh_jid") and q["gh_jid"][0].isdigit():
        return f"greenhouse:{q['gh_jid'][0]}"
    if q.get("ashby_jid") and _UUID.match(q["ashby_jid"][0]):
        return f"ashby:{q['ashby_jid'][0].lower()}"
    if host.endswith("greenhouse.io"):
        if parts[:2] == ["embed", "job_app"] and q.get("token") and q["token"][0].isdigit():
            return f"greenhouse:{q['token'][0]}"
        if len(parts) >= 3 and parts[1] == "jobs" and parts[2].isdigit():
            return f"greenhouse:{parts[2]}"
        return None
    if host in ("jobs.lever.co", "jobs.eu.lever.co") and len(parts) >= 2 and _UUID.match(parts[1]):
        return f"lever:{parts[1].lower()}"
    if host == "jobs.ashbyhq.com" and len(parts) >= 2 and _UUID.match(parts[1]):
        return f"ashby:{parts[1].lower()}"
    if host == "apply.workable.com" and len(parts) >= 3 and parts[1] == "j":
        return f"workable:{parts[0].lower()}/{parts[2]}"
    if host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com") and len(parts) >= 2:
        m = re.match(r"^(\d{6,})", parts[1])
        return f"smartrecruiters:{m.group(1)}" if m else None
    if host.endswith(".recruitee.com") and len(parts) >= 2 and parts[0] == "o":
        return f"recruitee:{host.split('.')[0]}/{parts[1]}"
    tenant = None
    m = _WD_HOST.match(host)
    if m:
        tenant = m.group(1).lower()
    elif _WD_SITE_HOST.match(host) and len(parts) >= 2 and parts[0] == "recruiting":
        tenant = parts[1].lower()
    if tenant and any(x in ("job", "details") for x in parts):
        req = workday_req("/".join(parts))
        return f"workday:{tenant}/{req}" if req else None
    return None


# A company's words that a Greenhouse board name often leaves off, or shortens.
_GENERIC_TAIL = {"inc", "llc", "ltd", "corp", "corporation", "co", "company", "systems", "technology",
                 "technologies", "labs", "ai", "group", "holdings"}
_SHORTER = {"technology": "tech", "technologies": "tech"}
_HOST_SKIP = {"www", "careers", "jobs", "job", "apply", "greenhouse", "co", "com"}


def greenhouse_guesses(company, url=None, most=4):
    """Board names to try for a job that carries a Greenhouse id ("?gh_jid=") on a company page
    whose board we don't know: the company's words joined ("sambanovasystems"), without generic
    last words ("sambanova"), with "technology" shortened ("bluerivertech"), and the page's own
    domain name ("precisely" from www.precisely.com). Only the board's API answering for that
    exact job id proves a guess: Greenhouse job ids are global."""
    words = re.findall(r"[a-z0-9]+", re.sub(r"['’]", "", (company or "").lower()))
    out = []

    def add(name):
        if name and name not in out:
            out.append(name)

    if words:
        add("".join(words))
        core = list(words)
        while len(core) > 1 and core[-1] in _GENERIC_TAIL:
            core.pop()
        add("".join(core))
        add("".join(_SHORTER.get(w, w) for w in words))
    try:
        labels = [x for x in (urlsplit(url or "").hostname or "").lower().split(".") if x]
    except ValueError:
        labels = []
    if len(labels) >= 2:
        label = labels[-2]
        if label in ("co", "com") and len(labels) >= 3:      # careers.acme.co.uk
            label = labels[-3]
        if label not in _HOST_SKIP:
            add(re.sub(r"[^a-z0-9]", "", label))
    return out[:most]


def job_page(url):
    """A job board's application-form link turned into the job's own page, where the page is the
    same URL without the form's suffix:
      Ashby     jobs.ashbyhq.com/<board>/<uuid>/application[?embed=true]  -> .../<uuid>
      Lever     jobs.lever.co/<company>/<uuid>/apply                       -> .../<uuid>
      Workable  apply.workable.com/<company>/j/<code>/apply                -> .../j/<code>/
    Any other URL comes back unchanged."""
    try:
        p = urlsplit(url or "")
    except ValueError:
        return url
    host = (p.hostname or "").lower()
    parts = [x for x in p.path.split("/") if x]
    if host == "jobs.ashbyhq.com" and len(parts) == 3 and _UUID.match(parts[1]) and parts[2] == "application":
        query = "&".join(kv for kv in p.query.split("&") if kv and kv.split("=")[0] != "embed")
        return urlunsplit((p.scheme, p.netloc, "/" + "/".join(parts[:2]), query, ""))
    if host in ("jobs.lever.co", "jobs.eu.lever.co") and len(parts) == 3 and _UUID.match(parts[1]) and parts[2] == "apply":
        return urlunsplit((p.scheme, p.netloc, "/" + "/".join(parts[:2]), p.query, ""))
    if host == "apply.workable.com" and len(parts) == 4 and parts[1] == "j" and parts[3] == "apply":
        return urlunsplit((p.scheme, p.netloc, "/" + "/".join(parts[:3]) + "/", p.query, ""))
    return url


def job_key(board, job_id):
    """The same id job_from_url gives, from a board and the id its list call returned."""
    system, b = split(board)
    if system in ("greenhouse", "smartrecruiters"):
        return f"{system}:{job_id}"
    if system in ("lever", "ashby"):
        return f"{system}:{str(job_id).lower()}"
    if system in ("workable", "recruitee"):
        return f"{system}:{b.lower()}/{job_id}"
    if system == "workday":
        return f"workday:{b.split('|')[0].lower()}/{job_id}"
    raise ValueError(f"unknown job-board system {system!r}")


def workday_job_url(board, path):
    tenant, wd, site = split(board)[1].split("|")
    return f"https://{tenant}.{wd}.myworkdayjobs.com/{site}{path}" if path else None
