"""Job APIs: The Muse, Himalayas, RemoteOK, We Work Remotely (RSS), Jobicy, Adzuna, USAJobs.

All are "window" sources: they return recent jobs, so a job missing from a response means nothing.
Paged sources stop at options.max_pages, or as soon as a page reaches jobs older than
options.stop_days. Adzuna and USAJobs search with phrases taken from the profile: the first title
of each position type, in each of the profile's areas (the area's first place) and remote. So a
profile change changes the searches, with no code change.
"""
import json
import re
from datetime import datetime

import feedparser

from ...store import raw
from .. import boards
from ..items import JobItem
from . import Adapter
from .lists import clean_url

TAGS = re.compile(r"<[^>]+>")
PERIODS = {"annual": "year", "annually": "year", "yearly": "year", "year": "year", "monthly": "month", "month": "month",
           "weekly": "week", "week": "week", "daily": "day", "day": "day", "hourly": "hour", "hour": "hour"}


def _plain(s):
    return " ".join(TAGS.sub(" ", str(s or "")).split())


def _age_days(iso, fetched_at):
    if not iso:
        return None
    a = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    b = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    return (b - a).total_seconds() / 86400


def _json(record):
    return json.loads(raw.body(record)) if raw.ok(record) else None


def endpoint(source):
    """The collector's base URL: options.endpoint when it differs from the probe's url."""
    return source.options.get("endpoint") or source.url


def searches(profile, source):
    """[(phrase, place or None)] for profile-driven searches: the first title of each position
    type, in each area (its first place, unless options.places names the places to use) and, when
    the profile wants remote jobs, with place None."""
    if not profile:
        return []
    phrases = []
    for pt in profile["position_types"]:
        if pt["titles"] and pt["titles"][0] not in phrases:
            phrases.append(pt["titles"][0])
    places = source.options.get("places")
    if places is None:
        places = [v[0] for v in profile["filters"]["locations"]["places"].values() if v]
    out = [(ph, pl) for ph in phrases for pl in places]
    if profile["filters"]["locations"]["remote_us"]:
        out += [(ph, None) for ph in phrases]
    return out


class Paged(Adapter):
    """Pages until max_pages, an empty or short page, or a page whose oldest job is past stop_days."""

    def requests(self, ctx, source):
        """[(url, params, meta)] for page 0 of each search; page(n) params come from next_params()."""
        return [(endpoint(source), {}, {})]

    def next_params(self, params, page, source):
        return None

    def page_items(self, record, source):
        return self.parse(record, source)

    def fetch(self, ctx, source):
        max_pages = int(source.options.get("max_pages", 1))
        stop = float(source.options.get("stop_days", 3))
        for url, params, meta in self.requests(ctx, source):
            page = 0
            while True:
                rec = ctx.get(source, url, params=params or None, meta={**meta, "page": page},
                              each=meta.get("each"), conditional=False)
                items = self.page_items(rec, source) if raw.ok(rec) else []
                page += 1
                if not items or page >= max_pages or len(items) < int(source.options.get("page_size", 1)):
                    break
                ages = [a for a in (_age_days(i.published_at, rec["fetched_at"]) for i in items) if a is not None]
                if ages and max(ages) > stop:
                    break
                params = self.next_params(params, page, source)
                if params is None:
                    break


class TheMuse(Paged):
    name = "themuse"

    def requests(self, ctx, source):
        out = []
        for cat in source.options.get("categories", ["Software Engineering"]):
            p = {"page": 0, "category": cat}
            if source.options.get("level"):
                p["level"] = source.options["level"]
            out.append((endpoint(source), p, {"each": cat}))
        return out

    def next_params(self, params, page, source):
        return {**params, "page": page}

    def parse(self, record, source):
        d = _json(record) or {}
        out = []
        for j in d.get("results") or []:
            out.append(JobItem(
                item_key=str(j["id"]), title=j.get("name") or "", company=(j.get("company") or {}).get("name") or "",
                locations=tuple(x.get("name") for x in j.get("locations") or [] if x.get("name")),
                url=clean_url((j.get("refs") or {}).get("landing_page")), published_at=boards.when(j.get("publication_date")),
                text=boards.html_text(j.get("contents")),
                extra={"levels": [x.get("name") for x in j.get("levels") or []],
                       "categories": [x.get("name") for x in j.get("categories") or []]}))
        return out


class Himalayas(Paged):
    name = "himalayas"

    def requests(self, ctx, source):
        return [(endpoint(source), {"limit": 20, "offset": 0}, {})]

    def next_params(self, params, page, source):
        return {**params, "offset": page * 20}

    def parse(self, record, source):
        d = _json(record) or {}
        out = []
        for j in d.get("jobs") or []:
            link = j.get("applicationLink") or j.get("guid") or ""
            out.append(JobItem(
                item_key=str(j.get("guid") or link), title=j.get("title") or "", company=j.get("companyName") or "",
                locations=tuple(j.get("locationRestrictions") or ()), url=clean_url(link), board=boards.board_from_url(link),
                published_at=boards.when(j.get("pubDate")), remote=True,
                pay=({"min": j.get("minSalary"), "max": j.get("maxSalary"), "currency": j.get("currency"),
                      "period": PERIODS.get(str(j.get("salaryPeriod") or "").lower(), "year")}
                     if j.get("minSalary") or j.get("maxSalary") else None),
                text=boards.html_text(j.get("description")) or j.get("excerpt"),
                extra={"seniority": j.get("seniority"), "employment": j.get("employmentType"), "via": "Himalayas"}))
        return out


class RemoteOK(Adapter):
    name = "remoteok"

    def fetch(self, ctx, source):
        ctx.get(source, source.url, conditional=False)

    def parse(self, record, source):
        out = []
        for j in _json(record) or []:
            if not isinstance(j, dict) or not j.get("id") or not j.get("position"):
                continue                              # the first element is RemoteOK's legal notice
            link = j.get("apply_url") or j.get("url") or ""
            out.append(JobItem(
                item_key=str(j["id"]), title=j.get("position") or "", company=j.get("company") or "",
                locations=tuple(x for x in [j.get("location")] if x), url=clean_url(j.get("url") or link),
                board=boards.board_from_url(link), published_at=boards.when(j.get("epoch") or j.get("date")), remote=True,
                pay=({"min": j.get("salary_min"), "max": j.get("salary_max"), "currency": "USD", "period": "year"}
                     if j.get("salary_min") or j.get("salary_max") else None),
                text=boards.html_text(j.get("description")), extra={"tags": j.get("tags") or [], "via": "RemoteOK"}))
        return out


class WeWorkRemotely(Adapter):
    name = "wwr_rss"

    def fetch(self, ctx, source):
        ctx.get(source, source.url)

    def parse(self, record, source):
        if not raw.ok(record):
            return []
        out = []
        for e in feedparser.parse(raw.body(record)).entries:
            company, _, title = (e.get("title") or "").partition(": ")
            if not title:
                company, title = "", company
            out.append(JobItem(
                item_key=e.get("id") or e.get("link") or title, title=title.strip(), company=company.strip(),
                locations=tuple(x for x in [e.get("region"), e.get("country")] if x), url=clean_url(e.get("link")),
                published_at=boards.when(e.get("published")), remote=True,
                text=boards.html_text(e.get("summary")), extra={"via": "We Work Remotely"}))
        return out


class Jobicy(Adapter):
    name = "jobicy"

    def fetch(self, ctx, source):
        ctx.get(source, source.url, conditional=False)

    def parse(self, record, source):
        d = _json(record) or {}
        out = []
        for j in d.get("jobs") or []:
            lo, hi = j.get("salaryMin") or j.get("annualSalaryMin"), j.get("salaryMax") or j.get("annualSalaryMax")
            out.append(JobItem(
                item_key=str(j.get("id")), title=unescape_text(j.get("jobTitle")), company=unescape_text(j.get("companyName")),
                locations=tuple(x for x in [j.get("jobGeo")] if x), url=clean_url(j.get("url")),
                published_at=boards.when(j.get("pubDate")), remote=True,
                pay=({"min": lo, "max": hi, "currency": j.get("salaryCurrency"), "period": j.get("salaryPeriod")} if lo or hi else None),
                text=boards.html_text(j.get("jobDescription")) or j.get("jobExcerpt"),
                extra={"level": j.get("jobLevel"), "employment": ", ".join(j["jobType"]) if isinstance(j.get("jobType"), list)
                       else j.get("jobType"), "via": "Jobicy"}))
        return out


def unescape_text(s):
    from html import unescape
    return unescape(str(s or "")).strip()


class Adzuna(Paged):
    name = "adzuna"

    def requests(self, ctx, source):
        out = []
        for phrase, place in searches(ctx.profile, source):
            p = {"what": phrase + ("" if place else " remote"), "max_days_old": source.options.get("max_days_old", 2),
                 "results_per_page": 50, "content-type": "application/json"}
            if place:
                p["where"] = place
            out.append((endpoint(source), p, {"each": f"{phrase} @ {place or 'remote'}"}))
        return out

    def fetch(self, ctx, source):
        max_pages = int(source.options.get("max_pages", 2))
        for url, params, meta in self.requests(ctx, source):
            for page in range(1, max_pages + 1):
                rec = ctx.get(source, url.replace("{page}", str(page)), params=params,
                              meta={**meta, "page": page}, each=meta["each"], conditional=False)
                if len(self.parse(rec, source)) < 50:
                    break

    def parse(self, record, source):
        d = _json(record) or {}
        out = []
        for j in d.get("results") or []:
            loc = (j.get("location") or {}).get("display_name")
            out.append(JobItem(
                item_key=str(j.get("id")), title=_plain(j.get("title")), company=_plain((j.get("company") or {}).get("display_name")),
                locations=tuple(x for x in [loc] if x), url=j.get("redirect_url") or "",
                published_at=boards.when(j.get("created")),
                pay=({"min": j.get("salary_min"), "max": j.get("salary_max"), "currency": "USD", "period": "year",
                      "predicted": j.get("salary_is_predicted") in ("1", 1, True)} if j.get("salary_min") or j.get("salary_max") else None),
                text=_plain(j.get("description")) or None,
                extra={"category": (j.get("category") or {}).get("label"), "contract": j.get("contract_time"),
                       "via": "Adzuna"}))
        return out


class USAJobs(Adapter):
    name = "usajobs"

    def fetch(self, ctx, source):
        for phrase, place in searches(ctx.profile, source):
            p = {"Keyword": phrase, "DatePosted": source.options.get("date_posted", 3), "ResultsPerPage": 100}
            if place:
                p["LocationName"] = place
            else:
                p["RemoteIndicator"] = "True"
            ctx.get(source, endpoint(source), params=p, each=f"{phrase} @ {place or 'remote'}", conditional=False)

    def parse(self, record, source):
        d = _json(record) or {}
        out = []
        for row in ((d.get("SearchResult") or {}).get("SearchResultItems") or []):
            j = row.get("MatchedObjectDescriptor") or {}
            pay = (j.get("PositionRemuneration") or [{}])[0]
            details = ((j.get("UserArea") or {}).get("Details") or {})
            out.append(JobItem(
                item_key=str(row.get("MatchedObjectId") or j.get("PositionID")), title=j.get("PositionTitle") or "",
                company=j.get("OrganizationName") or j.get("DepartmentName") or "",
                locations=tuple(x.get("LocationName") for x in j.get("PositionLocation") or [] if x.get("LocationName")),
                url=j.get("PositionURI") or "", published_at=boards.when(j.get("PublicationStartDate")),
                remote=(details.get("RemoteIndicator") is True) or None,
                pay=({"min": pay.get("MinimumRange"), "max": pay.get("MaximumRange"), "currency": "USD",
                      "period": pay.get("RateIntervalCode")} if pay else None),
                text="\n".join(x for x in [details.get("JobSummary"), j.get("QualificationSummary")] if x) or None,
                extra={"department": j.get("DepartmentName"), "clearance": details.get("SecurityClearance"),
                       "via": "USAJobs"}))
        return out


ADAPTERS = [TheMuse(), Himalayas(), RemoteOK(), WeWorkRemotely(), Jobicy(), Adzuna(), USAJobs()]
