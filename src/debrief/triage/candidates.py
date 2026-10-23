"""Candidates: the deterministic part of triage (docs/v2/CRITERIA.md; thresholds in config/triage.yml).

The candidate list is a pure function of the state database, the run's time `at` and the config:
every window counts back from `at`, every signal is its latest snapshot at or before `at`, and
every sort ends on story_id.

The stages, in order. triage/run.py logs every drop with its stage.
  pool     per section, the stories in its window that have an enabled source of that section and
           were first seen by `at`:
             papers         first seen in the last papers.pool_days days
             releases       for each release source, a model it first saw in the last 48 hours, or
                            (releases.by_creation: the org watch) a model created in them
             announcements  published in the last 48 hours
             press_policy   published in the last 48 hours
  rules    hard rules (brief 6.2): no title, or a blocklisted domain or pattern
  merge    stories that are one thing become one candidate (triage/merge.py)
  ledger   already in a brief, unless there's meaningful new discussion (triage/ledger.py)
  threads  the most-discussed threads of the last 48 hours that attached to no story join the
           discussion section, for the scoring call (triage/score.py)
  order and cap, per section:
             papers         the use bar, then rank fusion; at most max_per_org per organization
             releases       tier, then likes, then story_id
             announcements  sources take turns: tier 1 first, then sources.yml order, each giving
                            its newest post
             press_policy   outlets, then Techmeme carried it, then discussion level, then newest,
                            then story_id
"""
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .. import times
from ..stories import signals

BAND_LEVEL = {"none": 0, "some": 1, "notable": 2, "heavy": 3}


@dataclass
class Group:
    """One candidate: a story, or stories triage found to be one thing (merged or grouped)."""
    id: str                          # the representative story
    section: str
    members: list                    # story ids, the representative first
    stories: dict                    # story_id -> story row
    stage: str | None = None         # the stage that dropped it; None while it's in
    reason: str = ""
    status: str = "new"
    prev_reported: str | None = None
    rank: int | None = None          # order within the section, from 1
    info: dict = field(default_factory=dict)

    @property
    def story(self):
        return self.stories[self.id]

    @property
    def alive(self):
        return self.stage is None

    @property
    def tier(self):
        return min(s["tier"] for s in self.stories.values())

    @property
    def sources(self):
        return list(dict.fromkeys(x for sid in self.members for x in self.stories[sid]["sources"]))

    @property
    def mentions(self):
        """Threads attached in memory (a thread candidate, or one the model's same_as folded in)."""
        return self.info.get("thread_mentions", [])

    def drop(self, stage, reason):
        self.stage, self.reason = stage, reason

    def absorb(self, other):
        for sid in other.members:
            if sid not in self.stories:
                self.members.append(sid)
                self.stories[sid] = other.stories[sid]
        for m in other.mentions:
            if m not in self.mentions:
                self.info.setdefault("thread_mentions", []).append(m)


def published(s):
    """When a story came out: its publish date, or when we first saw it if that's earlier (a feed
    can't show a post before it exists) or the date is a guess from a scraped page's text
    (trafilatura misdated 1 in 5 of Meta's posts)."""
    if any(ex.get("date_from") in ("text", "none") for ex in s["extra"].values() if isinstance(ex, dict)):
        return s["first_seen_at"]
    return min(s["published_at"] or s["first_seen_at"], s["first_seen_at"])


def since(at, hours):
    return times.iso(times.parse(at).timestamp() - hours * 3600)


class Ctx:
    """What every stage reads: the store, the config, the run's time, and cached discussion."""

    def __init__(self, store, cfg, at):
        self.store, self.cfg, self.at = store, cfg, at
        self.settings, self.t = cfg.settings, cfg.settings["triage"]
        ob = cfg.settings["bands"]
        self.dists = signals.Distributions(store, at, ob["window_days"], ob.get("floor"))
        self.order = {s.id: i for i, s in enumerate(cfg.sources)}
        self.tier = {s.id: s.tier for s in cfg.sources}
        self.section_sources = section_sources(cfg)
        self._discussion = {}

    def discussion(self, story_ids, mentions=()):
        key = (tuple(sorted(story_ids)), tuple(sorted(mentions)))
        if key not in self._discussion:
            self._discussion[key] = signals.discussion(self.store, self.settings, list(key[0]), self.at, self.dists,
                                                       mentions=key[1])
        return self._discussion[key]


def section_sources(cfg):
    """{section: [enabled anchor source ids, in sources.yml order]}, straight from the config."""
    out = {}
    for s in cfg.sources:
        sec = cfg.settings["sections"].get(s.kind)
        if s.enabled and "anchor" in s.roles and sec:
            out.setdefault(sec, []).append(s.id)
    return out


# ------------------------------------------------------------------------------------------ pool

def pools(ctx):
    """{section: [Group]} for every story in a section's window, each alone for now."""
    store, at, t = ctx.store, ctx.at, ctx.t
    out = {sec: [] for sec in t["sections"]}
    earliest = since(at, max(24 * t["papers"]["pool_days"], t["announcements"]["window_hours"],
                             t["press_policy"]["window_hours"]))
    rows = store.q("SELECT story_id FROM stories WHERE first_seen_at > ? AND first_seen_at <= ? ORDER BY story_id",
                   (earliest, at))
    release_ids = _releases_in_window(ctx)
    for sid in sorted({r[0] for r in rows} | release_ids):
        s = store.story(sid)
        sec = s["section"]
        if sec not in out or not set(s["sources"]) & set(ctx.section_sources.get(sec, ())):
            continue
        if sec == "papers":
            keep = s["first_seen_at"] > since(at, 24 * t["papers"]["pool_days"])
        elif sec == "releases":
            keep = sid in release_ids
        elif sec in ("announcements", "press_policy"):
            keep = published(s) > since(at, t[sec]["window_hours"])
        else:
            keep = False
        if keep:
            out[sec].append(Group(id=sid, section=sec, members=[sid], stories={sid: s}))
    return out


def _releases_in_window(ctx):
    t, at = ctx.t["releases"], ctx.at
    start = since(at, t["window_hours"])
    srcs = ctx.section_sources.get("releases", [])
    if not srcs:
        return set()
    marks = ",".join("?" * len(srcs))
    rows = ctx.store.q(f"""SELECT i.source_id, i.story_id, i.first_seen_at, s.published_at FROM items i
                           JOIN stories s ON s.story_id = i.story_id
                           WHERE i.role = 'anchor' AND i.source_id IN ({marks}) AND i.first_seen_at <= ?""",
                       (*srcs, at))
    out = set()
    for src, sid, first, pub in rows:
        when = pub if src in t["by_creation"] else first
        if when and start < when <= at:
            out.add(sid)
    return out


# ----------------------------------------------------------------------------------- hard rules

def hard_rules(ctx, groups):
    b = ctx.t["blocklist"]
    patterns = [(name, re.compile(p, re.I)) for name, p in (b.get("patterns") or {}).items()]
    domains = [d.lower() for d in b.get("domains") or []]
    for sec, gs in groups.items():
        for g in gs:
            s = g.story
            if not s["title"] or s["title"].startswith(("http://", "https://")):
                g.drop("rules", "no title")
                continue
            host = (urlsplit(s["artifact_urls"][0]).hostname or "") if s["artifact_urls"] else ""
            if any(host == d or host.endswith("." + d) for d in domains):
                g.drop("rules", f"blocklisted domain {host}")
                continue
            if sec in b.get("exempt_sections", []):
                continue
            text = f"{s['title']} . {s['summary'][:200]}"
            hit = next((name for name, rx in patterns if rx.search(text)), None)
            if hit:
                g.drop("rules", f"blocklist: {hit}")


# ------------------------------------------------------------------------------ order and caps

def paper_values(store, settings, at, story_ids, dists):
    """{story_id: the paper's signals as of `at`}: upvotes, stars, adoption (models, datasets and
    Spaces built on it), hn_points (its best HN thread, from bands.floor up: a 2-point submission
    isn't traction), spread, comments."""
    floor = (settings["bands"].get("floor") or {}).get("hn", 0)
    out = {}
    for sid in story_ids:
        u = signals.paper_usage(store, sid, at)
        d = signals.discussion(store, settings, sid, at, dists)
        hn = (d["by_source"].get("hn") or {}).get("points", 0)
        out[sid] = {"upvotes": u["upvotes"], "stars": u["stars"], "adoption": u["artifacts"],
                    "hn_points": hn if hn >= floor else 0, "spread": d["spread"], "comments": u["comments"]}
    return out


def passes_use_bar(v, bar):
    return v["adoption"] >= bar["min_artifacts"] or v["stars"] >= bar["min_stars"]


def fusion(values_by_id, names, k):
    """{id: score}: each signal ranks the ids by value (ties share a rank, the best is 1; a
    missing or zero value isn't ranked), and an id scores the sum of 1 / (k + rank)."""
    score = {sid: 0.0 for sid in values_by_id}
    for name in names:
        vals = {sid: v[name] for sid, v in values_by_id.items() if v.get(name)}
        ordered = sorted(vals.values(), reverse=True)
        for sid, v in vals.items():
            rank = 1 + sum(1 for x in ordered if x > v)
            score[sid] += 1.0 / (k + rank)
    return score


def rank_papers(store, settings, at, story_ids, dists=None):
    """The papers among story_ids that pass the use bar, best first, and the ones that don't:
    ([(story_id, score, signals)], {story_id: signals}). Triage uses it, and so does the collector:
    only the best-ranked papers get their Hugging Face comments read."""
    t = settings["triage"]["papers"]
    if dists is None:
        ob = settings["bands"]
        dists = signals.Distributions(store, at, ob["window_days"], ob.get("floor"))
    vals = paper_values(store, settings, at, story_ids, dists)
    eligible = {sid: v for sid, v in vals.items() if passes_use_bar(v, t["use_bar"])}
    score = fusion(eligible, t["signals"], t["k"])
    order = sorted(eligible, key=lambda sid: (-score[sid], -eligible[sid]["upvotes"], sid))
    return ([(sid, score[sid], eligible[sid]) for sid in order],
            {sid: v for sid, v in vals.items() if sid not in eligible})


def order_papers(ctx, gs):
    t = ctx.t["papers"]
    by_id = {g.id: g for g in gs if g.alive}
    ranked, failed = rank_papers(ctx.store, ctx.settings, ctx.at, list(by_id), ctx.dists)
    for sid, v in failed.items():
        by_id[sid].info["signals"] = v
        by_id[sid].drop("use_bar", f"no evidence of use: {v['adoption']:g} artifacts, {v['stars']:g} stars")
    per_org, n = {}, 0
    for sid, score, v in ranked:
        g = by_id[sid]
        g.info.update({"fusion": round(score, 6), "signals": v})
        org = (g.story["org"] or "").strip().casefold()
        if org:
            if per_org.get(org, 0) >= t["max_per_org"]:
                g.drop("org_cap", f"already {t['max_per_org']} papers from {g.story['org']}")
                continue
            per_org[org] = per_org.get(org, 0) + 1
        n += 1
        g.rank = n
        if n > t["cap"]:
            g.drop("cap", f"paper #{n}, over the cap of {t['cap']}")


def order_releases(ctx, gs):
    def likes(g):
        return max(ctx.store.latest_signals(sid, ctx.at).get("hf_likes", 0) for sid in g.members)
    alive = [g for g in gs if g.alive]
    for g in alive:
        g.info["likes"] = likes(g)
    _cap(sorted(alive, key=lambda g: (g.tier, -g.info["likes"], g.id)), ctx.t["releases"]["cap"], "release")


def order_announcements(ctx, gs):
    """Sources take turns, tier 1 first, then sources.yml order; each turn gives a source's newest
    post. So no single vendor fills the section on a conference day."""
    queues = {}
    for g in sorted((g for g in gs if g.alive), key=lambda g: (published(g.story), g.id), reverse=True):
        queues.setdefault(g.story["sources"][0], []).append(g)
    turn = sorted(queues, key=lambda src: (ctx.tier.get(src, 9), ctx.order.get(src, 1e9), src))
    order = []
    while any(queues.values()):
        for src in turn:
            if queues[src]:
                order.append(queues[src].pop(0))
    _cap(order, ctx.t["announcements"]["cap"], "announcement")


def press_facts(ctx, g):
    wires = set(ctx.t["press_policy"]["wires"])
    press = set(ctx.section_sources.get("press_policy", []))
    outlets = sorted({x for x in g.sources if x in press and x not in wires})
    marks = ",".join("?" * len(g.members))
    techmeme = bool(ctx.store.one(f"""SELECT 1 FROM coverage c JOIN mentions m ON m.mention_id = c.mention_id
                                      WHERE c.story_id IN ({marks}) AND c.source_id = 'techmeme'
                                      AND m.first_seen_at <= ? LIMIT 1""", (*g.members, ctx.at)))
    d = ctx.discussion(g.members, g.mentions)
    newest = max(published(g.stories[sid]) for sid in g.members)
    return {"outlets": outlets, "techmeme": techmeme, "band": d["band"], "newest": newest}


def order_press(ctx, gs):
    alive = [g for g in gs if g.alive]
    for g in alive:
        g.info.update(press_facts(ctx, g))
    key = lambda g: (-len(g.info["outlets"]), not g.info["techmeme"], -BAND_LEVEL[g.info["band"]],  # noqa: E731
                     _neg_time(g.info["newest"]), g.id)
    _cap(sorted(alive, key=key), ctx.t["press_policy"]["cap"], "press event")


def _neg_time(iso):
    return -times.parse(iso).timestamp()


def _cap(order, cap, what):
    for i, g in enumerate(order, 1):
        g.rank = i
        if i > cap:
            g.drop("cap", f"{what} #{i}, over the cap of {cap}")


def threads(ctx, ledger_doc):
    """[Group] for the discussion.max_threads most-discussed threads posted in the last
    discussion.window_hours that attached to no story and aren't already in a brief: at least
    the source's bands.floor in points, ordered by points, then id."""
    from .score import thread_story
    o = ctx.t["discussion"]
    floors = ctx.settings["bands"].get("floor") or {}
    marks = ",".join("?" * len(o["sources"]))
    rows = ctx.store.q(f"""SELECT * FROM mentions WHERE source_id IN ({marks}) AND kind = 'thread' AND story_id IS NULL
                           AND posted_at > ? AND posted_at <= ? AND first_seen_at <= ?""",
                       (*o["sources"], since(ctx.at, o["window_hours"]), ctx.at, ctx.at))
    found = []
    for m in rows:
        e = signals._latest(ctx.store, m["mention_id"], ctx.at)
        pts = e.get("points") or 0
        if pts >= floors.get(m["source_id"], 0) and "hn:" + m["item_key"] not in ledger_doc["dates"]:
            found.append((-pts, m["mention_id"], dict(m), e))
    out = []
    for i, (neg, mid, m, e) in enumerate(sorted(found)[: o["max_threads"]], 1):
        s = thread_story(ctx, m, -neg, e.get("comments") or 0)
        out.append(Group(id=s["story_id"], section="discussion", members=[s["story_id"]], stories={s["story_id"]: s},
                         rank=i, info={"thread_mentions": [mid], "points": -neg, "candidate": True}))
    return out


ORDER = {"papers": order_papers, "releases": order_releases, "announcements": order_announcements,
         "press_policy": order_press}
