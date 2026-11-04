"""Hacker News: the monthly "Ask HN: Who is hiring?" thread, and HN job posts (YC companies).

Who is hiring: every top-level comment is one company's post. By convention its first line reads
"Company | Role | Location | ...", so the company is the first segment and the first line is the
title; the full text goes along for M2's pre-score and the scoring call. The current month's thread
is read whole every night (one search, one request for the tree), since comments arrive all month.

Job posts: the ids in jobstories.json; only ids not seen before are requested.
"""
import json
import re

from ...store import raw
from .. import boards
from ..items import JobItem
from . import Adapter
from .lists import clean_url

HN_ITEM = "https://news.ycombinator.com/item?id={}"
WHERE = re.compile(r"remote|on-?site|hybrid|\b[A-Z]{2}\b|,", re.I)
LINK = re.compile(r"https?://[^\s<>\"')]+")


class WhoIsHiring(Adapter):
    name = "hn_whoishiring"

    def fetch(self, ctx, source):
        search = ctx.get(source, source.url, conditional=False)
        if not raw.ok(search):
            return
        hits = json.loads(raw.body(search)).get("hits") or []
        story = next((h for h in hits if (h.get("title") or "").startswith("Ask HN: Who is hiring?")), None)
        if not story:
            ctx.warn("no 'Who is hiring?' thread in the search results")
            return
        ctx.get(source, f"https://hn.algolia.com/api/v1/items/{story['objectID']}", conditional=False,
                meta={"story": story["objectID"], "title": story["title"]}, each=story["title"])

    def parse(self, record, source):
        if not raw.ok(record) or "/items/" not in record["url"]:
            return []
        tree = json.loads(raw.body(record))
        out = []
        for c in tree.get("children") or []:
            if not c.get("text") or not c.get("author"):
                continue                                   # deleted or dead
            text = boards.html_text(c["text"].replace("<p>", "\n<p>")) or ""
            first = text.split("\n", 1)[0].strip()
            segs = [s.strip() for s in first.split("|")]
            if len(segs) < 2 or not segs[0]:
                continue                                   # not the "Company | Role | ..." convention
            links = [clean_url(u.rstrip(".,")) for u in LINK.findall(c["text"].replace("&#x2F;", "/"))]
            board = next((b for b in (boards.board_from_url(u) for u in links) if b), None)
            out.append(JobItem(
                item_key=str(c["id"]), title=first[:300], company=segs[0][:120],
                locations=tuple(s for s in segs[1:] if WHERE.search(s) and len(s) < 80),
                url=HN_ITEM.format(c["id"]), board=board, published_at=boards.when(c.get("created_at")),
                text=text[:20000], scope=str(record.get("meta", {}).get("story", "")),
                extra={"thread": record.get("meta", {}).get("title"), "links": links[:10], "via": "HN Who is hiring"}))
        return out


COMPANY = re.compile(r"^(.*?)\s*(?:\((?:YC|yc)\s*[A-Za-z]?\d{2,4}\)|\s+(?:is|are)\s+hiring\b)", re.I)


class HNJobs(Adapter):
    name = "hn_jobs"
    quiet_ok = True

    def fetch(self, ctx, source):
        idx = ctx.get(source, source.url, conditional=False)
        if not raw.ok(idx):
            return
        known = ctx.store.known_keys(source.id)
        new = [i for i in json.loads(raw.body(idx)) if str(i) not in known][: int(source.options.get("max_new", 40))]
        for i in new:
            ctx.get(source, f"https://hacker-news.firebaseio.com/v0/item/{i}.json", each=str(i), conditional=False)

    def parse(self, record, source):
        if not raw.ok(record) or "/item/" not in record["url"]:
            return []
        j = json.loads(raw.body(record)) or {}
        if j.get("type") != "job" or j.get("dead") or j.get("deleted"):
            return []
        title = (j.get("title") or "").strip()
        m = COMPANY.match(title)
        url = clean_url(j.get("url")) or HN_ITEM.format(j["id"])
        return [JobItem(item_key=str(j["id"]), title=title, company=(m.group(1).strip() if m else ""), url=url,
                        board=boards.board_from_url(url), published_at=boards.when(j.get("time")),
                        text=boards.html_text(j.get("text")), extra={"hn": HN_ITEM.format(j["id"]), "via": "HN jobs"})]


ADAPTERS = [WhoIsHiring(), HNJobs()]
