"""Hugging Face per-paper lookups for the paper pool (papers HF listed in the last
hf_papers.lookback_days days).

hf_paper_api (enrich): /api/papers/<id> for each paper in the pool, every daily run. It records
signals only: upvotes, the official repo's stars, and how many models, datasets and Spaces on the
Hub link the paper. These feed the use bar and the ranking (CRITERIA.md).

hf_trending_papers (enrich): the Daily Papers API sorted by trending, one request for the top
hf_trending_papers.limit. A signal, not a source of stories (Alex, 2026-10-24): on 2026-10-24 its
top 100 were mostly old papers with popular repos (vLLM's from 2023, Transformers' from 2019), and
the recent ones were already in the pool through Daily Papers. Records hf_trending_rank (1 is the
top) for every listed paper we have, and 0 for the pool's papers that aren't listed, so a paper's
latest value is always as of the latest list. The pool is recorded in the raw record at fetch time.

hf_paper_page (threads): there is no comments API, so the paper page's embedded PaperContent data
is read, only for the hf_paper_comments.top_n best papers of the pool in triage's ranking (the use
bar, then rank fusion) that have comments, and again when the comment count has grown. Comments
become mentions attached to the paper; reactions count as likes.
"""
import json
import re
from html import unescape

from .. import sanitize, times
from ..store import raw
from ..triage.candidates import rank_papers
from .base import Adapter, Item

_PROPS = re.compile(r'data-target="PaperContent" data-props="([^"]+)"')


def paper_pool(store, run_date, days):
    """Story ids of papers Hugging Face listed whose story was first seen in the last `days` days."""
    rows = store.q("""SELECT story_id FROM stories WHERE section='papers' AND first_seen >= date(?, ?)
                      AND sources LIKE '%"hf-daily-papers"%' ORDER BY story_id""", (run_date, f"-{days - 1} days"))
    return [r[0] for r in rows]


class HfPaperApi(Adapter):
    name = "hf_paper_api"
    phase = "enrich"
    windowed = False

    def fetch(self, ctx, source):
        base = source.options.get("api_base", "https://huggingface.co/api/papers")
        for sid in paper_pool(ctx.store, ctx.run_date, ctx.opts["hf_papers"]["lookback_days"]):
            ctx.get(source, f"{base}/{sid.split(':', 1)[1]}", each=sid, conditional=False)

    def parse(self, record, source, opts):
        if not raw.ok(record):
            return []
        d = json.loads(raw.body(record))
        aid = d.get("id") or (record.get("each") or "").split(":", 1)[-1]
        if not aid:
            return []
        return [Item(item_key=aid, role="signal", story_id=f"arxiv:{aid}", signals={
            "hf_upvotes": d.get("upvotes") or 0, "github_stars": d.get("githubStars") or 0,
            "hf_models": d.get("numTotalModels") or 0, "hf_datasets": d.get("numTotalDatasets") or 0,
            "hf_spaces": d.get("numTotalSpaces") or 0})]


class HfTrendingPapers(Adapter):
    name = "hf_trending_papers"
    phase = "enrich"
    windowed = False

    def fetch(self, ctx, source):
        o = {**ctx.opts["hf_trending_papers"], **source.options}
        pool = paper_pool(ctx.store, ctx.run_date, ctx.opts["hf_papers"]["lookback_days"])
        ctx.get(source, f"{source.url}&limit={o['limit']}", meta={"pool": pool}, conditional=False)

    def parse(self, record, source, opts):
        if not raw.ok(record):
            return []
        data = json.loads(raw.body(record)) or []
        rank = {}
        for d in data if isinstance(data, list) else []:
            aid = ((d.get("paper") or {}).get("id") or "").strip()
            if aid and aid not in rank:
                rank[aid] = len(rank) + 1
        unlisted = [sid.split(":", 1)[1] for sid in (record.get("meta") or {}).get("pool", [])]
        return [Item(item_key=aid, role="signal", story_id=f"arxiv:{aid}", signals={"hf_trending_rank": rank.get(aid, 0)})
                for aid in dict.fromkeys([*rank, *unlisted])]


class HfPaperPage(Adapter):
    name = "hf_paper_page"
    phase = "threads"
    windowed = False

    def fetch(self, ctx, source):
        o = ctx.opts
        done = dict(ctx.store.cursor(source.id).get("fetched", {}))
        pool = paper_pool(ctx.store, ctx.run_date, o["hf_papers"]["lookback_days"])
        ranked, _ = rank_papers(ctx.store, ctx.settings, ctx.stored_until, pool)
        for sid, _, v in ranked[: o["hf_paper_comments"]["top_n"]]:
            n = int(v["comments"])
            if n <= done.get(sid, 0):
                continue
            rec = ctx.get(source, f"https://huggingface.co/papers/{sid.split(':', 1)[1]}", each=sid,
                          meta={"paper": sid}, conditional=False)
            if raw.ok(rec):
                done[sid] = n
        ctx.set_cursor({"fetched": dict(sorted(done.items())[-2000:])})

    def parse(self, record, source, opts):
        if not raw.ok(record):
            return []
        m = _PROPS.search(raw.body(record).decode("utf-8", "replace"))
        if not m:
            return []
        props = json.loads(unescape(m.group(1)))
        sid = (record.get("meta") or {}).get("paper")
        o = opts["hf_paper_comments"]
        bot = re.compile(o["skip_author_pattern"], re.I)
        out = []
        for i, c in enumerate(props.get("comments") or []):
            data = c.get("data") or {}
            author = c.get("author") or {}
            name = author.get("name") or ""
            if c.get("type") != "comment" or data.get("hidden") or name in o["skip_authors"] or bot.search(name):
                continue
            out.append(Item(
                item_key=str(c.get("id")), role="mention", mention_kind="comment", story_hint=sid,
                url=f"https://huggingface.co/papers/{sid.split(':', 1)[1]}#{c.get('id')}",
                summary=sanitize.text((data.get("latest") or {}).get("raw"), opts["hn_comments"]["comment_max_chars"]),
                published_at=times.iso(c.get("createdAt")), authors=(author.get("name") or "",), rank=i + 1,
                engagement={"likes": sum(r.get("count") or 0 for r in data.get("reactions") or [])}))
        return out
