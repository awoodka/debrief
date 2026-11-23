"""Tonight's jobs, for the morning list (rank) and the descriptions read before it (describe): pool
postings first seen since the last scoring run (store.score_since) and posted within ranking.fresh_days."""
from datetime import date, datetime

DAILY = ("new", "nearby", "watchlist")
BARE_DATE = "T00:00:00Z"     # how boards.when stores a date with no time


def posted(p):
    return p.get("published_at") or p.get("first_seen_at")


def age_days(when, now):
    if when.endswith(BARE_DATE):
        return (now.astimezone().date() - date.fromisoformat(when[:10])).days
    return (now - datetime.fromisoformat(when.replace("Z", "+00:00"))).total_seconds() / 86400


def fresh(p, ranking, now):
    """Posted within ranking fresh_days; a job with no posted date counts from when it was found."""
    return age_days(posted(p), now) <= ranking["fresh_days"]


def tonight(p, since, ranking, now):
    """First seen since `since` (store.score_since: None before the first run) and fresh."""
    return (not since or p["first_seen_at"] > since) and fresh(p, ranking, now)
