"""Big companies' own careers search: Amazon (amazon.jobs).

A "window" source: the newest US jobs in options.categories, newest first, with each job's description
and qualifications in the list, so a job needs no description step. Pages until max_pages, a short page,
or a page whose oldest job is older than stop_days.
"""
import json
from datetime import datetime, timezone

from .. import boards
from ..items import JobItem
from .apis import Paged, _json, endpoint

AMAZON_TEXT = (("", "description"), ("Basic qualifications", "basic_qualifications"),
               ("Preferred qualifications", "preferred_qualifications"))


def amazon_date(value):
    """ISO UTC midnight from amazon.jobs's "November 20, 2026", or None."""
    try:
        return datetime.strptime(str(value or "").strip(), "%B %d, %Y").replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def amazon_places(job):
    places = []
    for x in job.get("locations") or ():
        try:
            place = json.loads(x).get("normalizedLocation") if isinstance(x, str) else None
        except ValueError:
            place = None
        if place and place not in places:
            places.append(place)
    return places or [p for p in (job.get("normalized_location"),) if p]


class Amazon(Paged):
    name = "amazon"

    def requests(self, ctx, source):
        o = source.options
        return [(endpoint(source), {"result_limit": int(o.get("page_size", 100)), "offset": 0, "sort": "recent",
                                    "country": o.get("country", "USA"), "category[]": list(o.get("categories") or ())}, {})]

    def next_params(self, params, page, source):
        return {**params, "offset": page * params["result_limit"]}

    def parse(self, record, source):
        data = _json(record)
        if data is None:
            return []
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs, list):
            raise ValueError("expected a list of jobs under 'jobs'")
        out = []
        for j in jobs:
            if not isinstance(j, dict) or not j.get("id_icims") or not j.get("title"):
                continue
            text = "\n".join(x for head, key in AMAZON_TEXT if (body := boards.html_text(j.get(key)))
                             for x in (head, body) if x)
            out.append(JobItem(item_key=f"amazon:{j['id_icims']}", title=str(j["title"]).strip(), company="Amazon",
                               locations=tuple(amazon_places(j)),
                               url=f"https://www.amazon.jobs{j['job_path']}" if j.get("job_path") else "",
                               published_at=amazon_date(j.get("posted_date")), text=text[:20000] or None,
                               extra={"employment": j["job_schedule_type"]} if j.get("job_schedule_type") else {}))
        return out


ADAPTERS = [Amazon()]
