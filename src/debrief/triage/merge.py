"""Stories that are one thing become one candidate (brief 6.1; CRITERIA.md), by links and titles
only, so it's deterministic. The model's same_as (M4) catches what this misses.

Every link here comes from a story's article page (adapters/article_pages.py): the links inside
its main text, canonicalized.

Announcements, together with the artifacts they announce:
  - An announcement that links exactly one artifact (an arXiv paper, a Hugging Face model,
    dataset or Space, a GitHub repo) whose story was published within merge.window_days of it is
    one story with that artifact. The merged story lives in whichever section comes first in
    merge.section_precedence, among the two that are in their pools: a lab post about its new
    model is a release, a lab post about its paper is an announcement (the paper leaves the paper
    pool).
  - Announcements with the same title (lowercased words) are one story, for a post cross-posted on
    two blogs (OlmoEarth on Ai2's and Hugging Face's).

Press articles become one event when:
  - they have the same title, or
  - one links the other, from another site (outlets link their own earlier coverage as context:
    TechCrunch's piece on Anthropic's Claude Duet links its piece on Meta's Muse), or
  - both link the same URL, where the URL isn't on either article's own site and isn't a
    profile, home or index page (merge.ignore_links).
An event also records the stories in other sections its articles link (`about`), and those
stories record the event (`press`).

In each merged story or event, the representative is the earliest published member (then the
lowest story_id), within the chosen section. The other members are logged as merged into it.
"""
import re
from urllib.parse import urlsplit

from .. import canon, times
from ..adapters.article_pages import stored_links
from ..adapters.hf_hub import model_story_id
from .candidates import published

ARTIFACT_PREFIXES = ("arxiv:", "hf-model:", "hf-dataset:", "hf-space:", "gh-repo:", "gh-release:")


def artifact_id(url):
    """The artifact a URL names (normalized as stories name them), or None."""
    sid = canon.story_id(url)
    if not sid or not sid.startswith(ARTIFACT_PREFIXES):
        return None
    if sid.startswith("gh-release:"):
        return "gh-repo:" + sid.split(":", 1)[1].split("@", 1)[0]
    if sid.startswith("hf-model:"):
        return model_story_id(sid.split(":", 1)[1])
    return sid


def title_key(title):
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    key = " ".join(words)
    return key if len(key) >= 12 else None


class Union:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def groups(self):
        out = {}
        for x in sorted(self.parent):
            out.setdefault(self.find(x), []).append(x)
        return list(out.values())


def _days_apart(a, b):
    return abs((times.parse(a) - times.parse(b)).total_seconds()) / 86400


def _earliest(stories, ids):
    return min(ids, key=lambda sid: (published(stories[sid]), sid))


def announcements(ctx, groups):
    """Merge announcements with the artifacts they announce, and cross-posts with each other."""
    t = ctx.t["merge"]
    store = ctx.store
    in_pool = {g.id: g for gs in groups.values() for g in gs if g.alive}
    anns = [g for g in groups.get("announcements", []) if g.alive]
    u, stories, linked = Union(), {}, {}
    for g in anns:
        stories[g.id] = g.story
        u.find(g.id)
        ids = {artifact_id(x) for x in stored_links(store, g.id)} - {None, g.id}
        linked[g.id] = sorted(ids)
        if len(ids) != 1:
            continue
        target = ids.pop()
        ts = in_pool[target].story if target in in_pool else store.story(target)
        if ts and _days_apart(published(ts), published(g.story)) <= t["window_days"]:
            stories[target] = ts
            u.join(g.id, target)
    by_title = {}
    for g in anns:
        key = title_key(g.story["title"])
        if key:
            by_title.setdefault(key, []).append(g.id)
    for ids in by_title.values():
        for other in ids[1:]:
            u.join(ids[0], other)

    precedence = t["section_precedence"]
    for ids in u.groups():
        if len(ids) == 1:
            continue
        pooled = [sid for sid in ids if sid in in_pool]
        section = min({in_pool[sid].section for sid in pooled}, key=lambda sec: precedence.index(sec)
                      if sec in precedence else len(precedence))
        rep = _earliest(stories, [sid for sid in pooled if in_pool[sid].section == section])
        keeper = in_pool[rep]
        for sid in sorted(ids, key=lambda sid: (published(stories[sid]), sid)):
            if sid == rep:
                continue
            if sid in in_pool:
                keeper.absorb(in_pool[sid])
                in_pool[sid].drop("merge", f"merged into {rep}")
                in_pool[sid].info["merged_into"] = rep
            else:
                keeper.members.append(sid)
                keeper.stories[sid] = stories[sid]
        keeper.info["merged"] = [sid for sid in keeper.members if sid != rep]
    return {g.id: linked.get(g.id, []) for g in anns}


def _countable(url, own_hosts, ignore):
    host = (urlsplit(url).hostname or "").lower()
    return host not in own_hosts and not any(rx.search(url) for rx in ignore)


def press(ctx, groups):
    """Group press articles into events; record what else each event links."""
    t = ctx.t["merge"]
    ignore = [re.compile(p, re.I) for p in t["ignore_links"]]
    store = ctx.store
    arts = [g for g in groups.get("press_policy", []) if g.alive]
    if not arts:
        return
    u = Union()
    url_to = {}
    for g in arts:
        u.find(g.id)
        for x in g.story["artifact_urls"]:
            url_to[x] = g.id
    links, hosts = {}, {}
    for g in arts:
        links[g.id] = stored_links(store, g.id)
        hosts[g.id] = {(urlsplit(x).hostname or "").lower() for x in g.story["artifact_urls"]}
    by_title, by_link = {}, {}
    for g in arts:
        key = title_key(g.story["title"])
        if key:
            by_title.setdefault(key, []).append(g.id)
        for x in links[g.id]:
            other = url_to.get(x)
            if other not in (None, g.id):
                if not hosts[g.id] & hosts[other]:
                    u.join(g.id, other)                      # one article links the other's
            elif _countable(x, hosts[g.id], ignore):
                by_link.setdefault(x, set()).add(g.id)
    for ids in [*by_title.values(), *(sorted(v) for v in by_link.values())]:
        for other in ids[1:]:
            u.join(ids[0], other)

    by_id = {g.id: g for g in arts}
    for ids in u.groups():
        if len(ids) == 1:
            continue
        stories = {sid: by_id[sid].story for sid in ids}
        rep = _earliest(stories, ids)
        keeper = by_id[rep]
        for sid in sorted(ids, key=lambda sid: (published(stories[sid]), sid)):
            if sid != rep:
                keeper.absorb(by_id[sid])
                by_id[sid].drop("merge", f"same event as {rep}")
                by_id[sid].info["merged_into"] = rep
        keeper.info["merged"] = [sid for sid in keeper.members if sid != rep]

    # What each event links in the other sections.
    others = {}
    for sec, gs in groups.items():
        if sec == "press_policy":
            continue
        for g in gs:
            if g.alive:
                for sid in g.members:
                    others[sid] = g
                    for x in g.stories[sid]["artifact_urls"]:
                        others[x] = g
    for g in arts:
        if not g.alive:
            continue
        about = []
        for sid in g.members:
            for x in links.get(sid, []):
                hit = others.get(x) or others.get(artifact_id(x) or "")
                if hit and hit.id not in about:
                    about.append(hit.id)
                    hit.info.setdefault("press", [])
                    if g.id not in hit.info["press"]:
                        hit.info["press"].append(g.id)
        if about:
            g.info["about"] = about
