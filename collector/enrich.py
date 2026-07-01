"""Step 3b-ii: traction enrichment of CANDIDATE papers (on HF Daily OR cited in discourse).

HF paper-pages give the FAST in-field signals that fire on fresh papers — linked models/datasets/spaces
plus the official repo's stars. Semantic Scholar adds citations + tldr (lagging). GitHub code-search for
independent impls is off by default (30/min rate-limited, ~0 on fresh papers; HF's githubStars cover it).
"""
import json
import os
import time
from pathlib import Path

from .http_util import get, session
from .config import ENRICH_GITHUB_SEARCH
from . import score

HF_PAPER = "https://huggingface.co/api/papers/{}"
SS_BATCH = "https://api.semanticscholar.org/graph/v1/paper/batch"
GH_REPOS = "https://api.github.com/search/repositories"


def candidates(items):
    score.cross_reference(items)  # ensure discourse_mentions is set
    return [p for p in items if p["type"] == "paper" and p.get("arxiv_id") and not p.get("noise")
            and ("hf_daily" in p["sources"] or p["raw_signal"].get("discourse_mentions", 0))]


def _hf_pages(cands, log):
    n = 0
    for p in cands:
        try:
            d = get(HF_PAPER.format(p["arxiv_id"]), timeout=20, ttl=12 * 3600).json()
        except Exception:  # noqa: BLE001 — per-paper fail-soft
            continue
        rs = p["raw_signal"]
        rs["hf_linked_models"] = d.get("numTotalModels", 0) or 0
        rs["hf_linked_datasets"] = d.get("numTotalDatasets", 0) or 0
        rs["hf_linked_spaces"] = d.get("numTotalSpaces", len(d.get("linkedSpaces") or [])) or 0
        if d.get("githubStars"):
            rs["github_stars"] = d["githubStars"]
        if d.get("githubRepo"):
            rs["github_repo"] = d["githubRepo"]
        if d.get("upvotes") and not rs.get("hf_upvotes"):
            rs["hf_upvotes"] = d["upvotes"]
        n += 1
    log(f"  enrich: HF paper-pages for {n}/{len(cands)} candidates")


def _semantic_scholar(cands, log):
    key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    headers = {"User-Agent": "debrief/0.1"}
    if key:
        headers["x-api-key"] = key
    by_id = {p["arxiv_id"]: p for p in cands}
    ids = [f"ARXIV:{aid}" for aid in by_id]
    indexed = cited = miss = 0
    debug = {}
    for i in range(0, len(ids), 400):
        batch = ids[i:i + 400]
        data = None
        for attempt in range(4):                       # explicit 429 backoff + retry (S2 rate-limits hard)
            try:
                r = session().post(SS_BATCH,
                                   params={"fields": "citationCount,influentialCitationCount,tldr"},
                                   json={"ids": batch}, headers=headers, timeout=40)
                if r.status_code == 429:
                    log(f"  enrich: S2 429 (attempt {attempt + 1}/4) — backing off")
                    time.sleep(2 ** attempt * 2)
                    continue
                r.raise_for_status()
                data = r.json()
                break
            except Exception as e:  # noqa: BLE001
                log(f"  enrich: S2 batch error — {type(e).__name__}")
                time.sleep(2 ** attempt)
        if data is None:
            for aid in (s.split(":", 1)[1] for s in batch):
                debug[aid] = "batch-failed"
            continue
        for sent, res in zip(batch, data):
            aid = sent.split(":", 1)[1]
            if not res:                                # null = not indexed yet (fresh) — expected, tolerated
                miss += 1
                debug[aid] = "not-indexed"
                continue
            p = by_id.get(aid)
            if not p:
                continue
            rs = p["raw_signal"]
            ic = res.get("influentialCitationCount", 0) or 0
            cc = res.get("citationCount", 0) or 0
            rs["influential_citations"], rs["citations"] = ic, cc
            if res.get("tldr") and res["tldr"].get("text"):
                rs["ss_tldr"] = res["tldr"]["text"]
            indexed += 1
            cited += 1 if cc else 0
            debug[aid] = {"cit": cc, "infl": ic}
        time.sleep(1.0)
    try:
        (Path(__file__).resolve().parent.parent / "data" / "s2_debug.json").write_text(
            json.dumps({"indexed": indexed, "cited": cited, "miss": miss, "papers": debug}, indent=2))
    except Exception:  # noqa: BLE001
        pass
    log(f"  enrich: Semantic Scholar {indexed}/{len(cands)} indexed · {cited} cited (rest 0 = too fresh) "
        f"· {miss} not-indexed → data/s2_debug.json")


def _github_impls(cands, log):
    """Repos referencing the arXiv id ≈ independent implementations (in-field substance).
    Cached (24h) so re-runs are free; live calls are spaced (GitHub search = 30 req/min)."""
    pat = os.environ.get("GITHUB_PAT")
    hdr = {"Accept": "application/vnd.github+json"}
    if pat:
        hdr["Authorization"] = f"Bearer {pat}"
    n = hits = 0
    for p in cands:
        try:
            r = get(GH_REPOS, params={"q": p["arxiv_id"], "per_page": 1}, headers=hdr, timeout=20, ttl=24 * 3600)
            if r.status_code == 200:
                c = r.json().get("total_count", 0)
                p["raw_signal"]["github_impls"] = c
                n += 1
                hits += 1 if c else 0
            if not getattr(r, "from_cache", False):
                time.sleep(2.1)  # GitHub search API: 30 req/min
        except Exception:  # noqa: BLE001
            time.sleep(2.1)
    log(f"  enrich: GitHub impls for {n}/{len(cands)} candidates ({hits} with >=1)")


def enrich(items, log=print):
    cands = candidates(items)
    log(f"  enrich: {len(cands)} candidate papers (on HF Daily or cited in discourse)")
    if not cands:
        return
    _hf_pages(cands, log)
    _semantic_scholar(cands, log)
    if ENRICH_GITHUB_SEARCH:
        _github_impls(cands, log)
