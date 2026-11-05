"""Points (docs/jobs/PLAN.md, "M2 design"): a code-only score, 0-100, for each job that passed the
filters. It decides which jobs reach the scoring call (ranking.threshold, ranking.max_candidates)
and breaks ties in the list. Every part comes from the profile:

  title     30  the job title is the position-type title plus early-career words only ("Software
                Engineer, New Grad 2027"); 20 when it carries more ("Software Engineer, Payments")
  early     20  the title says early-career; 15 when only a new-grad list says so
  fit       35  the profile's skills (5 each, up to 25) and its coursework and interest words (2
                each, up to 10) found in the description; a job with no description gets 15, so
                it isn't sunk for what we can't see
  fresh     15  posted today 15, falling to 0 at max_age_days

Watchlist companies skip the threshold (select() puts them first).
"""
from datetime import datetime

from . import filters
from .items import company_norm, early_career

STOP = {"and", "the", "of", "for", "with", "in", "to", "a", "an", "on", "at", "or", "data", "systems"}


def _phrase_in(hay, phrase):
    ws = filters.words(phrase)
    return bool(ws) and f" {' '.join(ws)} " in hay


def _interest_words(profile):
    out = set()
    for s in profile["coursework"] + profile["interests"]:
        out |= {w for w in filters.words(s) if len(w) >= 2 and w not in STOP}
    return out


def points(posting, profile, now, text=None, facts=None, sources=(), early_sources=frozenset()):
    """(total, {part: points})."""
    facts = facts or {}
    title_ws = filters.words(posting["title"])
    matched = filters.words(facts.get("matched") or "")
    extra = [w for w in title_ws if w not in set(matched)]
    plain = [w for w in extra if not early_career(w) and w not in ("new", "grad", "level", "early", "career", "i", "1")]
    part = {"title": 30 if matched and not plain else 20 if matched else 0}
    if early_career(posting["title"]):
        part["early"] = 20
    elif set(sources) & set(early_sources):
        part["early"] = 15
    else:
        part["early"] = 0
    if text:
        hay = f" {' '.join(filters.words(posting['title'] + ' ' + text))} "
        skills = sum(1 for s in profile["skills"] if _phrase_in(hay, s))
        interests = sum(1 for w in _interest_words(profile) if f" {w} " in hay)
        part["fit"] = min(25, 5 * skills) + min(10, 2 * interests)
    else:
        part["fit"] = 15
    when = posting.get("published_at") or posting.get("first_seen_at")
    days = max(0.0, (now - datetime.fromisoformat(when.replace("Z", "+00:00"))).total_seconds() / 86400) if when else 0.0
    max_age = profile["filters"]["max_age_days"]
    part["fresh"] = round(15 * max(0.0, 1 - days / max_age))
    return sum(part.values()), part


def watched(posting, profile):
    names = {company_norm(c["name"]) for c in profile["companies"]}
    return company_norm(posting["company"]) in names


def select(scored, profile, cap=None, first=frozenset(), threshold=True):
    """Split [(posting, points, ...)] into (sent, cut) by the profile's ranking: watchlist companies
    first and past the threshold, then the postings in `first` (tonight's new ones), then the rest,
    each by points (ties: newest, then id), up to the cap (ranking.max_candidates unless `cap` is
    given; cap=0 means no cap; a negative cap means none left). threshold=False skips the threshold
    (a one-time catch-up). cut items carry their reason."""
    r = profile["ranking"]
    cap = r["max_candidates"] if cap is None else cap
    def key(x):
        p, pts = x[0], x[1]
        return (not watched(p, profile), p["posting_id"] not in first, -pts,
                -(_ts(p.get("published_at") or p.get("first_seen_at"))), p["posting_id"])
    sent, cut = [], []
    for x in sorted(scored, key=key):
        p, pts = x[0], x[1]
        if threshold and pts < r["threshold"] and not watched(p, profile):
            cut.append((x, f"below the threshold ({pts} < {r['threshold']})"))
        elif cap < 0 or (cap and len(sent) >= cap):
            cut.append((x, f"over the cap ({r['max_candidates']} a night)"))
        else:
            sent.append(x)
    return sent, cut


def _ts(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() if iso else 0.0

