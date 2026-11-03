"""Semantic Scholar citation counts for late papers (triage.yml papers.lookback; CRITERIA.md).

semantic_scholar (enrich): every paper past the pool, up to papers.lookback.days old, is looked up
once every semantic_scholar.every_days (hf_paper_pages.stale_papers, the longest-unread first, at
most max_ids a run), by POST /graph/v1/paper/batch with up to `batch` arXiv ids a request. It
records one signal, `citations`. A paper Semantic Scholar doesn't know yet gets 0, so it has a
baseline once it's indexed: a late-paper trigger fires only on a crossing from an earlier value.

Without SEMANTIC_SCHOLAR_API_KEY it fetches nothing and says so: the keyless shared pool answers
429 (2026-10-23, 2026-11-02), and a failed lookup source would mark the day's triage degraded.
The request body isn't kept, so each request's ids go in its raw record's meta, in order: the
answer is a list in the same order.
"""
import json

from ..store import raw
from .base import Adapter, Item
from .hf_paper_pages import stale_papers

BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch?fields=citationCount"


class SemanticScholar(Adapter):
    name = "semantic_scholar"
    phase = "enrich"
    windowed = False

    def fetch(self, ctx, source):
        if not source.auth_headers(ctx.env):
            ctx.warn(f"no {source.auth.get('env')}: citation counts skipped")
            return
        o = {**ctx.opts["semantic_scholar"], **source.options}
        ids = stale_papers(ctx.store, ctx.run_date, ctx.opts["hf_papers"]["lookback_days"],
                           ctx.settings["triage"]["papers"]["lookback"]["days"], source.id,
                           o["every_days"], o["max_ids"])
        ids = [sid for sid in ids if sid.startswith("arxiv:")]
        for i in range(0, len(ids), o["batch"]):
            chunk = ids[i:i + o["batch"]]
            ctx.post(source, o.get("batch_url", BATCH_URL), {"ids": ["ARXIV:" + sid.split(":", 1)[1] for sid in chunk]},
                     meta={"ids": chunk})

    def parse(self, record, source, opts):
        if not raw.ok(record):
            return []
        data = json.loads(raw.body(record))
        ids = (record.get("meta") or {}).get("ids") or []
        if not isinstance(data, list) or len(data) != len(ids):
            return []
        return [Item(item_key=sid.split(":", 1)[1], role="signal", story_id=sid,
                     signals={"citations": (d or {}).get("citationCount") or 0})
                for sid, d in zip(ids, data)]
