"""Expand a source's URL template into the concrete URLs to fetch.

Placeholders:
  {each}       one URL per entry in the source's `each` list (repos, orgs, packages, ...)
  {today}      YYYY-MM-DD, local date
  {yesterday}  YYYY-MM-DD, local date
  {week_ago}   YYYY-MM-DD, 7 days ago
  {since_24h}  unix seconds, 24 hours ago
"""
from datetime import datetime, timedelta


def placeholders(now=None):
    now = now or datetime.now().astimezone()
    return {
        "today": now.date().isoformat(),
        "yesterday": (now - timedelta(days=1)).date().isoformat(),
        "week_ago": (now - timedelta(days=7)).date().isoformat(),
        "since_24h": str(int((now - timedelta(hours=24)).timestamp())),
    }


def fill(template, now=None, **extra):
    values = {**placeholders(now), **extra}
    out = template
    for k, v in values.items():
        out = out.replace("{" + k + "}", v)
    return out


def expand(template, each=(), now=None):
    """[(each_value or None, url), ...] for a template."""
    if "{each}" in template:
        return [(e, fill(template, now, each=e)) for e in each]
    return [(None, fill(template, now))]
