"""Deterministic in_field / mainstream / divergence scoring. No LLM. Weights live in config.

The collector attaches signals and scores; it NEVER hard-culls the gem decision — the agent digest
(see digest.py) applies a minimal pulse floor + cap, but every item stays in the raw digest_input.
"""
import re
from collections import defaultdict

from .config import IN_FIELD_WEIGHTS as IFW, MAINSTREAM_WEIGHTS as MW

ARXIV_RE = re.compile(r"(\d{4}\.\d{4,5})")


def _num(v):
    if isinstance(v, (int, float)):
        return v
    m = re.search(r"[\d,]+", str(v or ""))
    return int(m.group(0).replace(",", "")) if m else 0


def cross_reference(items):
    """Papers gain discourse_mentions / mainstream_mentions when a non-paper item cites their arXiv id.
    A paper that researchers are linking in discussion has a real in-field pulse even at 0 citations."""
    papers = {i["arxiv_id"]: i for i in items if i["type"] == "paper" and i.get("arxiv_id")}
    inf, main = defaultdict(int), defaultdict(int)
    for it in items:
        if it["type"] == "paper":
            continue
        text = " ".join([it.get("url", ""), it.get("title", ""), it.get("summary", "")])
        for aid in set(ARXIV_RE.findall(text)):
            if aid in papers:
                (main if it["raw_signal"].get("axis") == "mainstream" else inf)[aid] += 1
    for aid, p in papers.items():
        if inf[aid]:
            p["raw_signal"]["discourse_mentions"] = inf[aid]
        if main[aid]:
            p["raw_signal"]["mainstream_mentions"] = main[aid]


def _in_field(it):
    rs = it["raw_signal"]
    return round(
        IFW["on_hf_daily"] * (1 if "hf_daily" in it["sources"] else 0)
        + IFW["hf_upvotes"] * rs.get("hf_upvotes", 0)
        + IFW["hf_models"] * rs.get("hf_linked_models", 0)
        + IFW["hf_datasets"] * rs.get("hf_linked_datasets", 0)
        + IFW["hf_spaces"] * rs.get("hf_linked_spaces", 0)
        + IFW["github_impls"] * rs.get("github_impls", 0)
        + IFW["discourse_mentions"] * rs.get("discourse_mentions", 0)
        + IFW["influential_citations"] * rs.get("influential_citations", 0)
        + IFW["citations"] * rs.get("citations", 0)
        + IFW["stars"] * _num(rs.get("stars_today", 0))
        + IFW["github_stars"] * rs.get("github_stars", 0)
        + IFW["show_launch_hn"] * (1 if (rs.get("show_hn") or rs.get("launch_hn")) else 0)
        + IFW["in_field_axis"] * (1 if rs.get("axis") == "in_field" else 0), 2)


def _mainstream(it):
    rs = it["raw_signal"]
    return round(
        MW["hn_points"] * rs.get("hn_points", 0)
        + MW["ph_votes"] * rs.get("ph_votes", 0)
        + MW["mainstream_mentions"] * rs.get("mainstream_mentions", 0)
        + MW["mainstream_axis"] * (1 if rs.get("axis") == "mainstream" else 0), 2)


def score_all(items):
    cross_reference(items)
    for it in items:
        it["in_field_score"] = _in_field(it)
        it["mainstream_score"] = _mainstream(it)
        it["divergence"] = round(it["in_field_score"] - it["mainstream_score"], 2)
    return items
