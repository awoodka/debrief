"""VC portfolio job boards on Getro: one public JSON API (api.getro.com) serves many funds' boards, each a
Getro network (options.networks: {fund: network id}). A "window" source.

Each night, per network: the search for options.job_functions, newest first, 20 jobs a page, until a page
reaches the newest job this source already holds from that network (its first night: options.first_pages
pages), a short page, or options.max_pages. Getro's job ids are global, so a job two funds list is one
listing. A job's link is the employer's own job page (Greenhouse, Ashby, ...), so it joins the board's
copy by job key. Each job carries its company's funding stage and head count (company_size), a startup
sign the scoring call sees.
"""
import json

from ...store import raw
from .. import boards
from ..items import JobItem
from . import Adapter
from .apis import endpoint
from .lists import clean_url

PAGE = 20
# Getro's head-count buckets, read from companies of known size (Google and Stripe are 6, Anthropic 5).
HEAD_COUNT = {1: "1-10", 2: "11-50", 3: "51-200", 4: "201-1,000", 5: "1,001-5,000", 6: "5,001+"}
NO_STAGE = {"", "other", "undisclosed"}


def company_size(org):
    """"11-50 people, series a" from a Getro organization, or None when it says neither."""
    size, stage = HEAD_COUNT.get(org.get("head_count")), str(org.get("stage") or "").replace("_", " ")
    return ", ".join(x for x in (f"{size} people" if size else "", "" if stage in NO_STAGE else stage) if x) or None


def _pay(j):
    lo, hi = j.get("compensation_amount_min_cents"), j.get("compensation_amount_max_cents")
    if not j.get("compensation_public") or not (lo or hi):
        return None
    return {"min": lo / 100 if lo else None, "max": hi / 100 if hi else None, "currency": j.get("compensation_currency"),
            "period": j.get("compensation_period")}


class Getro(Adapter):
    name = "getro"

    def fetch(self, ctx, source):
        o = source.options
        query = {"hitsPerPage": PAGE, "query": "", "filters": {"job_functions": list(o["job_functions"])}}
        for network, nid in o["networks"].items():
            url = endpoint(source).format(network=nid)
            since = ctx.store.newest_published(source.id, network)
            pages = int(o.get("max_pages", 10) if since else o.get("first_pages", 3))
            for page in range(pages):
                rec = ctx.post(source, url, {**query, "page": page}, each=network, meta={"network": network, "page": page})
                if ctx.blocked(url):
                    return
                posted = [j.published_at for j in self.parse(rec, source)]
                if len(posted) < PAGE or (since and min((d for d in posted if d), default="") <= since):
                    break
            else:
                if since:
                    ctx.warn(f"{network}: stopped at {pages} pages before reaching the jobs it had last night")

    def parse(self, record, source):
        if not raw.ok(record):
            return []
        data = json.loads(raw.body(record))
        jobs = (data.get("results") or {}).get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs, list):
            raise ValueError("expected a list of jobs under results.jobs")
        network = (record.get("meta") or {}).get("network") or ""
        out = []
        for j in jobs:
            org = j.get("organization") or {}
            if not j.get("id") or not j.get("title"):
                continue
            link = clean_url(j.get("url"))
            out.append(JobItem(item_key=f"getro:{j['id']}", title=str(j["title"]).strip(), company=str(org.get("name") or "").strip(),
                               locations=tuple(j.get("locations") or ()), url=link, board=boards.board_from_url(link),
                               published_at=boards.when(j.get("created_at")), remote=True if j.get("work_mode") == "remote" else None,
                               pay=_pay(j), scope=network,
                               extra={"seniority": j.get("seniority"), "company_size": company_size(org), "via": f"Getro ({network})"}))
        return out


ADAPTERS = [Getro()]
