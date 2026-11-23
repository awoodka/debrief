"""LinkedIn's public job search (the guest search the logged-out jobs page pages through): jobs posted in the
last day, options.us_wide keywords across the US (the startup roles: founding, forward deployed), then each of
options.by_place keywords in each of options.places. A "window" source.

LinkedIn's own entry-level filter (f_E=2) isn't used: of 15 jobs it returned, no job page said "Entry level"
(9 Not Applicable, 5 Mid-Senior, 1 Associate; 2026-11-23). by_place keywords ask for early-career words instead,
since a job reaches the scoring call only when something says it's for new grads, and LinkedIn's cards carry
no description.

Each answer is an HTML fragment of up to 10 job cards. A search pages on until a short page, max_pages,
or the night's max_requests. A card keeps the job's LinkedIn id, title, company, place and posted date;
its link becomes the bare https://www.linkedin.com/jobs/view/<id> (the card's own link carries tracking
ids). Items are keyed linkedin:<id>, as the alert emails' are. Requests share the linkedin.com rate bucket
and block rule with describe's LinkedIn job pages.
"""
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from ...store import raw
from .. import boards
from ..items import JobItem
from . import Adapter
from .apis import endpoint
from .describe import linkedin_block

PAGE = 10
VIEW = "https://www.linkedin.com/jobs/view/{}"
URN = "urn:li:jobPosting:"


def searches(options):
    """[(label, query params)] in the order they're asked: the US-wide keywords first,
    then each place's keywords. Place "remote" asks LinkedIn's remote filter, which also returns on-site jobs
    (Valco Melton's Cincinnati headquarters role, 2026-11-23), so its jobs are not marked remote."""
    since = {"f_TPR": options.get("posted", "r86400")}
    out = [(f"{kw} @ United States", {"keywords": kw, "location": "United States", **since})
           for kw in options.get("us_wide") or ()]
    for place in options.get("places") or ():
        where = {"location": "United States", "f_WT": "2"} if place == "remote" else {"location": place}
        out += [(f"{kw} @ {place}", {"keywords": kw, **where, **since}) for kw in options.get("by_place") or ()]
    return out


def _text(node):
    return " ".join(node.get_text(" ").split()) if node else ""


class LinkedInSearch(Adapter):
    name = "linkedin_search"

    def fetch(self, ctx, source):
        o = source.options
        cap, max_pages, asked = int(o.get("max_requests", 50)), int(o.get("max_pages", 4)), 0
        for label, params in searches(o):
            for page in range(max_pages):
                if asked >= cap:
                    ctx.warn(f"stopped at {cap} requests; the rest of tonight's searches were not asked")
                    return
                url = f"{endpoint(source)}?{urlencode({**params, 'start': page * PAGE})}"
                rec = ctx.get(source, url, each=label, meta={"search": label, "page": page},
                              conditional=False, block=linkedin_block)
                asked += 1
                if ctx.blocked(url):
                    return
                if len(self.parse(rec, source)) < PAGE:
                    break

    def parse(self, record, source):
        if not raw.ok(record):
            return []
        out = []
        for card in BeautifulSoup(raw.body(record).decode("utf-8", "replace"), "html.parser").select(f'[data-entity-urn^="{URN}"]'):
            job, title = card["data-entity-urn"][len(URN):], _text(card.select_one(".base-search-card__title"))
            if not job.isdigit() or not title:
                continue
            place, posted = _text(card.select_one(".job-search-card__location")), card.select_one("time[datetime]")
            out.append(JobItem(item_key=f"linkedin:{job}", title=title, company=_text(card.select_one(".base-search-card__subtitle")),
                               locations=(place,) if place else (), url=VIEW.format(job),
                               published_at=boards.when(posted["datetime"]) if posted else None, extra={"via": "LinkedIn"}))
        return out


ADAPTERS = [LinkedInSearch()]
