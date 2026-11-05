"""The coverage check's Google sample (docs/jobs/PLAN.md, "Coverage" and "Revision 6"): Google's job
results through SerpAPI's free plan. Never a source of jobs: parse() gives nothing, and
`jobs coverage` (coverage.py) reads these responses back from the raw store.

fetch() first reads the account (account.json, which doesn't use a search) and spends at most
options.per_day searches, never more than an even share of what's left this month. The searches
rotate by date through the profile's role types (each type's first title plus options.suffix) x
options.locations, so every pair comes round every few days. The API key is a query parameter
and is redacted from the raw records like every key.
"""
import calendar
import json
from datetime import date

from ...store import raw
from . import Adapter


def queries(profile, options):
    """[(q, google location)] in a stable order: every role type x every location."""
    if not profile:
        return []
    suffix = options.get("suffix", "new grad")
    phrases = []
    for pt in profile["position_types"]:
        if pt["titles"] and pt["titles"][0] not in phrases:
            phrases.append(pt["titles"][0])
    out = []
    for loc in options.get("locations") or []:
        for ph in phrases:
            q = f"{ph} {suffix}".strip() + (" remote" if loc.get("remote") else "")
            out.append((q, loc["google"]))
    return out


def todays(all_queries, n, day):
    """n queries for this day, continuing the rotation where the previous day stopped."""
    if not all_queries or n <= 0:
        return []
    start = (day.toordinal() * n) % len(all_queries)
    return [all_queries[(start + i) % len(all_queries)] for i in range(min(n, len(all_queries)))]


def budget(left, per_day, day):
    """Searches to spend today: per_day at most, and no more than an even share of what's left."""
    if not left or left <= 0:
        return 0
    days_left = calendar.monthrange(day.year, day.month)[1] - day.day + 1
    return max(0, min(per_day, left // days_left))


class SerpSample(Adapter):
    name = "serpapi_sample"
    quiet_ok = True                       # never gives items

    def fetch(self, ctx, source):
        opts = source.options or {}
        acct = ctx.get(source, opts["account_url"], conditional=False, meta={"kind": "account"})
        try:
            info = json.loads(raw.body(acct)) if raw.ok(acct) else {}
        except ValueError:
            info = {}
        left = info.get("total_searches_left", info.get("plan_searches_left"))
        today = date.today()
        n = budget(left if isinstance(left, int) else 0, int(opts.get("per_day", 8)), today)
        if not n:
            ctx.warn(f"no searches today (searches left this month: {left})")
            return
        for q, loc in todays(queries(ctx.profile, opts), n, today):
            ctx.get(source, opts["endpoint"], conditional=False, each=f"{q} @ {loc}",
                    params={"engine": "google_jobs", "q": q, "location": loc, "hl": "en", "gl": "us"},
                    meta={"kind": "search", "q": q, "location": loc})

    def parse(self, record, source):
        return []


ADAPTERS = [SerpSample()]
