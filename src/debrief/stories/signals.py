"""Discussion level (band), trend, and the paper use bar, from stored snapshots only.

Everything here reads the state database as of a reference time `at` (the run's time), so the same
data always gives the same answer.

Band (brief 8.1), for one story (or a merged group of stories, counted as one):
  For each discussion source with a thread on the story, the story's percentile is its best
  thread's points among that source's threads posted in the trailing bands.window_days days, each at
  its latest observation up to `at`, counting only threads at or above the source's bands.floor
  (most HN submissions are never discussed; the floor keeps them out of the comparison). A source with fewer than bands.min_observations
  threads in that window has no percentile, since its distribution isn't known yet.
  spread = how many distinct discussion sources mention the story (threads, posts and comments; a
  comment counts under its thread's source, so an HN comment tree is still HN).
    none     nothing mentions it
    heavy    best percentile >= heavy_pctl, or spread >= 3 with best percentile >= notable_pctl
    notable  best percentile >= notable_pctl, or spread >= 2
    some     otherwise
  With no percentile available: heavy at spread >= 3, notable at spread >= 2, else some.

Trend:
  T(t) = points + comments summed over the story's threads, each at its latest observation as of t,
  for every time t the threads were observed. With three or more such times, the rate over the
  last interval is compared with the one before:
    rising   at least trend.rising_ratio times faster, or growth after none
    falling  at most trend.falling_ratio times the earlier rate
    flat     otherwise
  Also rising when a source that hadn't discussed the story starts after the second-to-last
  observation. flat when neither interval grew by trend.min_growth (noise). unknown with fewer
  than three observations.

Paper use bar (CRITERIA.md; triage.yml papers.use_bar): at least min_artifacts models, datasets or
Spaces built on the paper, or an official repo with at least min_stars stars.
"""
from bisect import bisect_right

from .. import times

THREAD_KINDS = ("thread", "post")


def _since(at, days):
    return times.iso(times.parse(at).timestamp() - days * 86400)


def _latest(store, mention_id, at):
    row = store.one("""SELECT points, comments, likes, reposts, observed_at FROM engagement
                       WHERE mention_id=? AND observed_at <= ? ORDER BY observed_at DESC LIMIT 1""",
                    (mention_id, at))
    return dict(row) if row else {}


class Distributions:
    """Each discussion source's thread points in the trailing window, sorted; computed once. Only
    threads at or above the source's bands.floor count (most submissions never get discussed)."""

    def __init__(self, store, at, days, floors=None):
        self.store, self.at, self.since, self.cache = store, at, _since(at, days), {}
        self.floors = floors or {}

    def of(self, source_id):
        if source_id not in self.cache:
            rows = self.store.q("""
                SELECT (SELECT points FROM engagement e WHERE e.mention_id = m.mention_id AND e.observed_at <= ?
                        ORDER BY e.observed_at DESC LIMIT 1) AS pts
                FROM mentions m WHERE m.source_id = ? AND m.kind IN ('thread', 'post') AND m.posted_at >= ?
                  AND m.posted_at <= ?""", (self.at, source_id, self.since, self.at))
            floor = self.floors.get(source_id, 0)
            self.cache[source_id] = sorted(r[0] for r in rows if r[0] is not None and r[0] >= floor)
        return self.cache[source_id]


def percentile(values, v):
    return round(100.0 * bisect_right(values, v) / len(values), 1) if values else None


def _ids(story_ids):
    ids = [story_ids] if isinstance(story_ids, str) else list(story_ids)
    return ids, ",".join("?" * len(ids))


def discussion(store, settings, story_ids, at, dists=None):
    """{band, spread, where, trend, by_source} for a story, or a list of stories taken as one, as
    of `at`."""
    ob, ot = settings["bands"], settings["trend"]
    dists = dists or Distributions(store, at, ob["window_days"], ob.get("floor"))
    ids, marks = _ids(story_ids)
    # A comment counts under its thread's source: HN comment trees are still HN (one community).
    rows = store.q(f"""SELECT m.mention_id, COALESCE(p.source_id, m.source_id), m.kind FROM mentions m
                       LEFT JOIN mentions p ON p.mention_id = m.parent_id
                       WHERE m.story_id IN ({marks}) AND m.kind != 'reference' AND m.first_seen_at <= ?
                       ORDER BY m.mention_id""", (*ids, at))
    by_source = {}
    for mid, src, kind in rows:
        b = by_source.setdefault(src, {"threads": 0, "comments_captured": 0, "points": 0, "comments": 0, "pctl": None})
        if kind in THREAD_KINDS:
            e = _latest(store, mid, at)
            b["threads"] += 1
            pts = e.get("points") or 0
            b["comments"] += e.get("comments") or 0
            if pts >= b["points"]:
                b["points"] = pts
        else:
            b["comments_captured"] += 1
    best = None
    for src, b in by_source.items():
        if b["threads"]:
            dist = dists.of(src)
            if len(dist) >= ob["min_observations"]:
                b["pctl"] = percentile(dist, b["points"])
                best = b["pctl"] if best is None else max(best, b["pctl"])
    spread = len(by_source)
    if not spread:
        band = "none"
    elif best is not None:
        band = ("heavy" if best >= ob["heavy_pctl"] or (spread >= 3 and best >= ob["notable_pctl"])
                else "notable" if best >= ob["notable_pctl"] or spread >= 2 else "some")
    else:
        band = "heavy" if spread >= 3 else "notable" if spread >= 2 else "some"
    return {"band": band, "spread": spread, "where": sorted(by_source), "by_source": by_source,
            "trend": trend(store, ids, at, ot)}


def trend(store, story_ids, at, o):
    ids, smarks = _ids(story_ids)
    threads = [r[0] for r in store.q(f"""SELECT mention_id FROM mentions WHERE story_id IN ({smarks})
                                         AND kind IN ('thread', 'post') AND first_seen_at <= ? ORDER BY mention_id""",
                                     (*ids, at))]
    if not threads:
        return "unknown"
    marks = ",".join("?" * len(threads))
    obs = [r[0] for r in store.q(f"""SELECT DISTINCT observed_at FROM engagement WHERE mention_id IN ({marks})
                                     AND observed_at <= ? ORDER BY observed_at""", (*threads, at))]
    if len(obs) < 3:
        return "unknown"
    t1, t2, t3 = obs[-3:]

    def total(t):
        return sum((e.get("points") or 0) + (e.get("comments") or 0) for e in (_latest(store, m, t) for m in threads))

    def hours(a, b):
        return max((times.parse(b) - times.parse(a)).total_seconds() / 3600.0, 1e-6)

    before = {r[0] for r in store.q(f"""SELECT DISTINCT source_id FROM mentions WHERE story_id IN ({smarks})
                                        AND first_seen_at <= ?""", (*ids, t2))}
    after = {r[0] for r in store.q(f"""SELECT DISTINCT source_id FROM mentions WHERE story_id IN ({smarks})
                                       AND kind != 'reference' AND first_seen_at > ? AND first_seen_at <= ?""",
                                   (*ids, t2, at))}
    if after - before:
        return "rising"
    d1, d2 = total(t2) - total(t1), total(t3) - total(t2)
    if max(abs(d1), abs(d2)) < o["min_growth"]:     # a few points either way is noise, not a trend
        return "flat"
    r1, r2 = d1 / hours(t1, t2), d2 / hours(t2, t3)
    if r2 > 0 and (r1 <= 0 or r2 >= o["rising_ratio"] * r1):
        return "rising"
    if r1 > 0 and r2 <= o["falling_ratio"] * r1:
        return "falling"
    return "flat"


def paper_usage(store, story_id, at=None):
    """The paper's latest usage signals (as of `at`, if given): upvotes, stars, and artifacts built
    on it."""
    s = store.latest_signals(story_id, at)
    return {"upvotes": s.get("hf_upvotes", 0), "stars": s.get("github_stars", 0),
            "artifacts": s.get("hf_models", 0) + s.get("hf_datasets", 0) + s.get("hf_spaces", 0),
            "comments": s.get("hf_comments", 0)}


def passes_use_bar(usage, o):
    return usage["artifacts"] >= o["min_artifacts"] or usage["stars"] >= o["min_stars"]
