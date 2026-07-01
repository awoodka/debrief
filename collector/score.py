"""Deterministic THREE-AXIS scoring (work order P0-1). No LLM. Weights live in config.

  in-field  = SUBSTANCE  — researchers/builders building on it (artifacts, impls, citations, discourse)
  mainstream = COVERAGE  — newsletters, HN points, tech press (the crowd/press knows)
  insider    = POPULARITY — HF upvotes, PH votes, GitHub stars (enthusiast likes; kept SEPARATE)

divergence = in_field - mainstream. A high-upvote paper with no substance and no coverage is NOT a gem.
The collector attaches signals and scores; it never hard-culls the gem decision (that's the agent digest).
Magnitudes are log-scaled (lg = ln(1+x)) so a viral story doesn't swamp a high-adoption paper.
"""
import math
import re
from collections import defaultdict

from .config import (IN_FIELD_WEIGHTS as IFW, MAINSTREAM_WEIGHTS as MW, INSIDER_WEIGHTS as INS,
                     NEWSLETTER_SOURCES, PRESS_SOURCES)

ARXIV_RE = re.compile(r"(\d{4}\.\d{4,5})")


def _num(v):
    if isinstance(v, (int, float)):
        return v
    m = re.search(r"[\d,]+", str(v or ""))
    return int(m.group(0).replace(",", "")) if m else 0


def _lg(x):
    x = _num(x)
    return math.log1p(x) if x > 0 else 0.0


def _blob(it):
    return " ".join([it.get("title", ""), it.get("summary", ""), it.get("url", "")]).lower()


def _match_keys(it):
    """Distinctive strings to look for an item by, inside a newsletter/press/discourse blob."""
    keys = []
    if it.get("arxiv_id"):
        keys.append(it["arxiv_id"])
    title = re.sub(r"[^a-z0-9 ]", "", (it.get("title") or "").lower()).strip()
    if len(title) >= 15:                       # distinctive enough to avoid false matches
        keys.append(title)
    seg = (it.get("url") or "").lower().rstrip("/").split("/")[-1].split("?")[0]
    if len(seg) >= 10:
        keys.append(seg)
    return keys


def cross_reference(items):
    """For EVERY item, compute newsletter_hits / press_hits (mainstream coverage) and, for papers,
    discourse_mentions (in-field engagement from Reddit/Lobsters). This is what makes mainstream real."""
    news = [_blob(it) for it in items if it["source"] in NEWSLETTER_SOURCES]
    press = [_blob(it) for it in items if it["source"] in PRESS_SOURCES]
    disc = [_blob(it) for it in items if it["source"].startswith(("reddit:", "lobsters:"))]
    for it in items:
        keys = _match_keys(it)
        if not keys:
            continue
        rs = it["raw_signal"]
        rs["newsletter_hits"] = sum(1 for b in news if any(k in b for k in keys))
        rs["press_hits"] = sum(1 for b in press if any(k in b for k in keys))
        if it["type"] == "paper":
            dm = sum(1 for b in disc if any(k in b for k in keys))
            if dm:
                rs["discourse_mentions"] = dm


def _in_field(it):
    rs = it["raw_signal"]
    return round(
        IFW["hf_models"] * _lg(rs.get("hf_linked_models", 0))
        + IFW["hf_datasets"] * _lg(rs.get("hf_linked_datasets", 0))
        + IFW["hf_spaces"] * _lg(rs.get("hf_linked_spaces", 0))
        + IFW["github_impls"] * _lg(rs.get("github_impls", 0))
        + IFW["influential_citations"] * _lg(rs.get("influential_citations", 0))
        + IFW["citations"] * _lg(rs.get("citations", 0))
        + IFW["reddit_score"] * _lg(rs.get("reddit_score", 0))
        + IFW["discourse_mentions"] * rs.get("discourse_mentions", 0)
        + IFW["show_launch_hn"] * (1 if (rs.get("show_hn") or rs.get("launch_hn")) else 0), 2)


def _hn_bucket(points):
    p = _num(points)
    return 4 if p >= 1000 else 3 if p >= 500 else 2 if p >= 150 else 1 if p >= 50 else 0


def _mainstream(it):
    rs = it["raw_signal"]
    is_release = it["type"] in ("lab_news", "release") and rs.get("axis") == "mainstream"
    is_coverage_src = it["source"] in NEWSLETTER_SOURCES or it["source"] in PRESS_SOURCES
    return round(
        MW["newsletter"] * min(rs.get("newsletter_hits", 0), 3)
        + MW["hn_bucket"] * _hn_bucket(rs.get("hn_points", 0))
        + MW["press"] * min(rs.get("press_hits", 0), 3)
        + MW["lab_release"] * (1 if is_release else 0)
        + MW["mainstream_base"] * (1 if is_coverage_src else 0), 2)


def _insider(it):
    rs = it["raw_signal"]
    return round(
        INS["hf_upvotes"] * _lg(rs.get("hf_upvotes", 0))
        + INS["ph_votes"] * _lg(rs.get("ph_votes", 0))
        + INS["github_stars"] * _lg(rs.get("github_stars", 0))
        + INS["github_stars"] * _lg(rs.get("stars_today", 0))   # trending-repo stars
        + INS["on_hf_daily"] * (1 if "hf_daily" in it["sources"] else 0), 2)


def score_all(items):
    cross_reference(items)
    for it in items:
        it["in_field_score"] = _in_field(it)
        it["mainstream_score"] = _mainstream(it)
        it["insider_score"] = _insider(it)
        it["divergence"] = round(it["in_field_score"] - it["mainstream_score"], 2)
    return items
