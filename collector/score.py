"""Deterministic GEM-TRACTION scoring. No LLM. Weights live in config.

  in_field (field traction) = SUBSTANCE (engineers building on it: artifacts, impls, citations)  [heavy]
                            + ATTENTION (the field noticing: upvotes, discussion, stars)          [lighter]
  mainstream (crowd/press)  = newsletters, HN points, tech press
  divergence = in_field - mainstream   ->  a gem has in-field traction the crowd/press hasn't caught.

Substance and attention are kept as separate sub-scores (shown sub=/buzz=) so upvote-buzz can't
masquerade as substance. Magnitudes are log-scaled. Rising in-field VELOCITY is folded in by store.py.
"""
import math
import re
from collections import defaultdict

from .config import (SUBSTANCE_WEIGHTS as SW, ATTENTION_WEIGHTS as AW, MAINSTREAM_WEIGHTS as MW,
                     REDDIT_SUBSTANCE_BASE as RSB, REDDIT_ATTENTION_BASE as RAB,
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
    keys = []
    if it.get("arxiv_id"):
        keys.append(it["arxiv_id"])
    title = re.sub(r"[^a-z0-9 ]", "", (it.get("title") or "").lower()).strip()
    if len(title) >= 15:
        keys.append(title)
    seg = (it.get("url") or "").lower().split("#")[0].rstrip("/").split("/")[-1].split("?")[0]
    if len(seg) >= 10:
        keys.append(seg)
    return keys


def cross_reference(items):
    """newsletter_hits / press_hits = count of DISTINCT OTHER outlets referencing this story (never the
    item's own feed) — cross-coverage, not feed volume. Papers also gain discourse_mentions from Reddit/
    Lobsters. This is what makes mainstream + discourse real."""
    news = [(it["source"], _blob(it)) for it in items if it["source"] in NEWSLETTER_SOURCES]
    press = [(it["source"], _blob(it)) for it in items if it["source"] in PRESS_SOURCES]
    disc = [_blob(it) for it in items if it["source"].startswith(("reddit:", "lobsters:"))]
    for it in items:
        rs, src = it["raw_signal"], it["source"]
        rs["newsletter_hits"] = rs["press_hits"] = 0
        keys = _match_keys(it)
        if not keys:
            continue
        rs["newsletter_hits"] = len({s for s, b in news if s != src and any(k in b for k in keys)})
        rs["press_hits"] = len({s for s, b in press if s != src and any(k in b for k in keys)})
        if it["type"] == "paper":
            dm = sum(1 for b in disc if any(k in b for k in keys))
            if dm:
                rs["discourse_mentions"] = dm


def _substance(it):
    rs = it["raw_signal"]
    return round(
        SW["hf_models"] * _lg(rs.get("hf_linked_models", 0))
        + SW["hf_datasets"] * _lg(rs.get("hf_linked_datasets", 0))
        + SW["hf_spaces"] * _lg(rs.get("hf_linked_spaces", 0))
        + SW["github_impls"] * _lg(rs.get("github_impls", 0))
        + SW["influential_citations"] * _lg(rs.get("influential_citations", 0))
        + SW["citations"] * _lg(rs.get("citations", 0))
        + RSB.get(rs.get("reddit_kind", ""), 0), 2)   # Reddit research posts = substance (H0-3)


def _attention(it):
    rs = it["raw_signal"]
    return round(
        AW["hf_upvotes"] * _lg(rs.get("hf_upvotes", 0))
        + AW["ph_votes"] * _lg(rs.get("ph_votes", 0))
        + AW["github_stars"] * _lg(rs.get("github_stars", 0))
        + AW["github_stars"] * _lg(rs.get("stars_today", 0))
        + AW["discourse_mentions"] * rs.get("discourse_mentions", 0)
        + AW["reddit_score"] * _lg(rs.get("reddit_score", 0))
        + AW["show_launch_hn"] * (1 if (rs.get("show_hn") or rs.get("launch_hn")) else 0)
        + RAB.get(rs.get("reddit_kind", ""), 0), 2)   # Reddit posts by kind (H0-3)


def _hn_bucket(points):
    p = _num(points)
    return 4 if p >= 1000 else 3 if p >= 500 else 2 if p >= 150 else 1 if p >= 50 else 0


def _mainstream(it):
    rs = it["raw_signal"]
    is_release = ((it["type"] == "release" and rs.get("release_official"))          # official drop = mainstream
                  or (it["type"] == "lab_news" and rs.get("axis") == "mainstream"))  # community/open drop = in-field
    is_coverage_src = it["source"] in NEWSLETTER_SOURCES or it["source"] in PRESS_SOURCES
    return round(
        MW["newsletter"] * min(rs.get("newsletter_hits", 0), 3)
        + MW["hn_bucket"] * _hn_bucket(rs.get("hn_points", 0))
        + MW["press"] * min(rs.get("press_hits", 0), 3)
        + MW["lab_release"] * (1 if is_release else 0)
        + MW["mainstream_base"] * (1 if is_coverage_src else 0), 2)


def score_all(items):
    cross_reference(items)
    for it in items:
        s, a = _substance(it), _attention(it)
        it["substance_score"] = s
        it["attention_score"] = a
        it["in_field_score"] = round(s + a, 2)
        it["mainstream_score"] = _mainstream(it)
        it["divergence"] = round(it["in_field_score"] - it["mainstream_score"], 2)
    return items
