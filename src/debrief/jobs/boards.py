"""Job-board systems (ATSs): how to list one company's board, read the answer, and recognize a
board from any job URL.

A board is "<system>:<id>", e.g. "greenhouse:anthropic", "workday:nvidia|wd5|NVIDIAExternalCareerSite".
Ids are lowercase except where the system is case-sensitive (SmartRecruiters ids, Workday sites).

list_request(board, page) gives the request for one page of a board's list. parse_list(system, data)
reads the JSON into (jobs, total). `total` is set for paged systems (Workday, SmartRecruiters), and
each job is {"title", "published" (ISO UTC or None), "age_days" (Workday's relative dates), "location"}.
"""
import re
from urllib.parse import parse_qs, urlsplit

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


def list_request(board, page=0):
    """(method, url, json_body) for one page of the board's list."""
    system, b = split(board)
    if system == "greenhouse":
        return "GET", f"https://boards-api.greenhouse.io/v1/boards/{b}/jobs", None
    if system == "lever":
        return "GET", f"https://api.lever.co/v0/postings/{b}?mode=json", None
    if system == "ashby":
        return "GET", f"https://api.ashbyhq.com/posting-api/job-board/{b}?includeCompensation=true", None
    if system == "workable":
        return "GET", f"https://apply.workable.com/api/v1/widget/accounts/{b}", None
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


def parse_list(system, data):
    """(jobs, total) from a board's list JSON. Raises ValueError on an unexpected shape."""
    total = None
    if system == "greenhouse":
        rows = data["jobs"]
        jobs = [{"title": j.get("title"), "published": when(j.get("first_published") or j.get("updated_at")),
                 "location": (j.get("location") or {}).get("name")} for j in rows]
    elif system == "lever":
        if not isinstance(data, list):
            raise ValueError(f"expected a list, got {type(data).__name__}")
        jobs = [{"title": j.get("text"),
                 "published": when(j.get("createdAt")),
                 "location": (j.get("categories") or {}).get("location")} for j in data]
    elif system == "ashby":
        jobs = [{"title": j.get("title"), "published": when(j.get("publishedAt")),
                 "location": j.get("location") or ("Remote" if j.get("isRemote") else None)} for j in data["jobs"]]
    elif system == "workable":
        jobs = [{"title": j.get("title"), "published": when(j.get("published_on") or j.get("created_at")),
                 "location": _loc(j.get("city"), j.get("state"), j.get("country"))
                 + (" (remote)" if j.get("telecommuting") else "")} for j in data["jobs"]]
    elif system == "smartrecruiters":
        total = data.get("totalFound")
        jobs = [{"title": j.get("name"), "published": when(j.get("releasedDate")),
                 "location": _loc((j.get("location") or {}).get("city"), (j.get("location") or {}).get("region"),
                                  (j.get("location") or {}).get("country"))} for j in data["content"]]
    elif system == "recruitee":
        jobs = [{"title": j.get("title"), "published": when(j.get("published_at") or j.get("created_at")),
                 "location": _loc(j.get("city"), j.get("country")) + (" (remote)" if j.get("remote") else "")}
                for j in data["offers"]]
    elif system == "workday":
        total = data.get("total")
        jobs = [{"title": j.get("title"), "published": None, "age_days": workday_age_days(j.get("postedOn")),
                 "location": j.get("locationsText")} for j in data["jobPostings"]]
    else:
        raise ValueError(f"unknown job-board system {system!r}")
    return jobs, total


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
