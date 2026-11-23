"""The watchlist (the profile's companies): each company's whole board, every night, every page.
And the board directory: the two company-board lists, refreshed weekly, for finding a watchlist
company's board. The directory is a lookup table only; its boards are never polled as a whole.
"""
import csv
import io
import json
from datetime import datetime, timedelta

from ...store import raw
from .. import boards
from ..items import JobItem
from . import Adapter


class Watchlist(Adapter):
    """A board's list is complete, so a job that drops off a fully read board has closed."""
    name = "watchlist"
    snapshot = "full"

    def fetch(self, ctx, source):
        companies = (ctx.profile or {}).get("companies") or []
        max_pages = int(source.options.get("max_pages", 100))
        for c in companies:
            board = c.get("board")
            if not board:
                ctx.warn(f"{c['name']}: no board yet (debrief jobs watch add, or paste its careers URL)")
                continue
            self.read_board(ctx, source, board, {"board": board, "company": c["name"]}, max_pages)

    def read_board(self, ctx, source, board, meta, max_pages):
        """The read's records, first page first. A paged board (Workday, SmartRecruiters) is read to its
        end, and every record of a read that didn't reach the end is marked partial."""
        system = boards.split(board)[0]
        if system not in boards.PAGE_SIZE:
            _, url, _ = boards.list_request(board, 0, full=True)
            return [ctx.get(source, url, each=board, meta=meta)]
        recs, total, reached_end = [], None, False
        for page in range(max_pages):
            method, url, body = boards.list_request(board, page, full=True)
            page_meta = {**meta, "page": page}
            rec = (ctx.post(source, url, body, each=board, meta=page_meta) if method == "POST"
                   else ctx.get(source, url, each=board, meta=page_meta, conditional=False))
            recs.append(rec)
            if not raw.ok(rec):
                break
            try:
                jobs, page_total = boards.parse_list(system, json.loads(raw.body(rec)))
            except ValueError:
                break
            if total is None:
                total = page_total or 0
            if not jobs or page + 1 >= boards.pages_needed(system, total):
                reached_end = system != "workday" or total < boards.WORKDAY_MAX_TOTAL
                break
        if not reached_end:
            for rec in recs:
                rec["meta"]["partial"] = True
        return recs

    def parse(self, record, source):
        if not raw.ok(record):
            return []
        meta = record.get("meta") or {}
        board, company = meta["board"], meta["company"]
        system = boards.split(board)[0]
        jobs, _ = boards.parse_list(system, json.loads(raw.body(record)))
        day = datetime.fromisoformat(record["fetched_at"].replace("Z", "+00:00")).replace(hour=0, minute=0, second=0)
        out = []
        for j in jobs:
            if not j.get("id") or not j.get("title"):
                continue
            key = boards.job_key(board, j["id"])
            published = j.get("published")
            if not published and j.get("age_days") is not None:
                published = (day - timedelta(days=j["age_days"])).strftime("%Y-%m-%dT%H:%M:%SZ")
            out.append(JobItem(
                item_key=key, title=j["title"].strip(), company=company, locations=tuple(j.get("locations") or ()),
                url=j.get("url") or boards.workday_job_url(board, j.get("path")) or "", board=board, job_key=f"job:{key}",
                published_at=published, scope=board, remote=j.get("remote"), pay=j.get("pay"), text=j.get("text"),
                extra={"employment": j["employment"]} if j.get("employment") else {}))
        return out

    def directory(self, record, source):
        meta = record.get("meta") or {}
        return [(meta["board"], meta["company"])] if raw.ok(record) and meta.get("board") else []


class Directory(Adapter):
    """The company-board lists (family universe), refreshed at most weekly with conditional GETs."""
    name = "directory"
    weekly = True

    def fetch(self, ctx, source):
        from ... import urls
        for each, url in urls.expand(source.url, source.each):
            ctx.get(source, url, each=each)

    def parse(self, record, source):
        return []

    def directory(self, record, source):
        system = record.get("each")
        if not raw.ok(record) or system not in boards.SYSTEMS:
            return []
        text = raw.body(record).decode("utf-8", "replace")
        out = []
        if source.probe.get("kind") == "csv":
            for row in csv.DictReader(io.StringIO(text)):
                found = boards.universe_rows(system, [row])
                if found:
                    out.append((found[0], (row.get("name") or found[0].split(":", 1)[1]).strip()))
        else:
            for b in boards.universe_rows(system, json.loads(text)):
                out.append((b, b.split(":", 1)[1].split("|")[0]))
        return out


ADAPTERS = [Watchlist(), Directory()]
