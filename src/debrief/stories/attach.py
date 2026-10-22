"""Attach mentions to stories (brief 5.3), deterministically.

A discussion post (an HN thread, a blog post, a comment) gets at most one story, stored on the
mention with match_method and match_score. The rules run in order, and the first that matches wins:

  hint    the adapter already knows the story (an HF paper comment belongs to its paper)   score 1
  parent  a comment takes its thread's story                                             score 1
  url     the post's URL, or a URL it links to, is one of a story's artifact URLs         score 1
  id      an arXiv / Hugging Face / GitHub id in those URLs or the text names a story     score 1
  entity  an identifier-like name in the post's title (a word with a digit, at least 5    score 0.9
          characters: PHP2Shell, Qwen3.8, MemT-1.0) appears in exactly one recent story's title.
          Uniqueness is the safety catch: if two stories share the name, neither gets the post.
          Papers are left out: their titles name the models they study, and they attach by arXiv
          link anyway.
  title   word overlap with a story's title, for stories first seen in the last          Jaccard
          attach.title_days days: Jaccard >= title_min_jaccard and at least title_min_shared
          shared words. Conservative on purpose; the model's same_as pass (M4) covers the rest.

A reference post (a newsletter issue, a Techmeme item) gets no story. Instead it gets a coverage row
for every story it links to, by URL or id. A source with both roles gets both.

mentions() runs on a source's new mentions right after they're stored. retro() runs at the end of
a run over the last attach.retro_days days: it retries unattached posts, so a post attaches once its
artifact shows up (brief 5.4); it moves posts attached only by entity or title when a URL or id now
points at a story (strong evidence wins); and it refreshes coverage from reference posts. Both process mentions in id order, and ties go to
the lowest story_id.
"""
import re
from collections import Counter

from .. import canon, times
from ..store import loads

_ENTITY = re.compile(r"\b[A-Za-z][A-Za-z0-9]*\d[A-Za-z0-9.\-]*[A-Za-z0-9]\b")


def entities(title):
    """Identifier-like names: a word that starts with a letter and contains a digit (PHP2Shell,
    Qwen3.8, V4.1-Flash-1017, MemT-1.0). Plain brand words (SpaceX, DeepSeek, GeForce) don't count:
    on 2026-10-22 they pulled every thread about the company onto one unrelated story."""
    return {e.lower() for e in _ENTITY.findall(title or "") if len(e) >= 5 and not e.replace(".", "").isdigit()}


STOPWORDS = set("""a an and are as at be by can for from has have how in into is it its new of on or
our show hn ask the their this to via vs was we what when where which who why will with without your
you using use used based towards toward""".split())


def words(title):
    return {w for w in re.findall(r"[a-z0-9][a-z0-9.+-]*[a-z0-9]|[a-z0-9]", (title or "").lower())
            if len(w) >= 3 and w not in STOPWORDS}


def _existing(store, story_id):
    return story_id if story_id and store.one("SELECT 1 FROM stories WHERE story_id=?", (story_id,)) else None


def _url_ids(m):
    """The post's own URL first, then the URLs it links to, in order, deduplicated."""
    urls = [canon.canonical_url(m.get("url") or "")] + loads(m.get("links"), [])
    return [u for u in dict.fromkeys(urls) if u]


def by_url_or_id(store, m, want_all=False):
    """[(story_id, method)] the post's URLs and ids resolve to; the first, unless want_all."""
    found = []
    for u in _url_ids(m):
        by_url = store.story_for_url(u)
        sid = by_url or _existing(store, canon.story_id(u))
        if sid:
            found.append((sid, "url" if by_url else "id"))
            if not want_all:
                return found
    for aid in re.findall(r"(?<![\w.])(\d{4}\.\d{4,5})(?:v\d+)?(?!\w)", f"{m.get('title') or ''} {m.get('text') or ''}"):
        sid = _existing(store, f"arxiv:{aid}")
        if sid:
            found.append((sid, "id"))
            if not want_all:
                return found
    return list(dict.fromkeys(found))


class TitleIndex:
    """Stories first seen recently, indexed by title word, for the conservative title rule."""

    def __init__(self, store, at, days):
        since = times.iso(times.parse(at).timestamp() - days * 86400)
        self.titles, self.index, self.entity_index = {}, {}, {}
        for sid, title, section in store.q("""SELECT story_id, title, section FROM stories WHERE first_seen_at >= ?
                                              ORDER BY story_id""", (since,)):
            w = words(title)
            self.titles[sid] = w
            for t in w:
                self.index.setdefault(t, []).append(sid)
            if section != "papers":      # paper titles name the models they study ("...RL of Qwen3.8")
                for e in entities(title):
                    self.entity_index.setdefault(e, set()).add(sid)

    def by_entity(self, title):
        """The one story whose title shares a distinctive name with this title, if exactly one does."""
        hits = {next(iter(self.entity_index[e])) for e in entities(title)
                if len(self.entity_index.get(e, ())) == 1}
        return hits.pop() if len(hits) == 1 else None

    def best(self, title, min_jaccard, min_shared):
        mine = words(title)
        if len(mine) < min_shared:
            return None, 0.0
        shared = Counter(sid for t in mine for sid in self.index.get(t, ()))
        best, best_score = None, 0.0
        for sid, n in sorted(shared.items()):
            if n < min_shared:
                continue
            score = n / len(mine | self.titles[sid])
            if score > best_score:
                best, best_score = sid, score
        return (best, round(best_score, 3)) if best_score >= min_jaccard else (None, 0.0)


def _attach_one(store, m, hint, index, o):
    if m["kind"] == "reference":
        return None
    if hint and _existing(store, hint):
        return hint, "hint", 1.0
    if m.get("parent_id"):
        parent = store.mention(m["parent_id"])
        if parent and parent["story_id"]:
            return parent["story_id"], "parent", 1.0
    found = by_url_or_id(store, m)
    if found:
        return found[0][0], found[0][1], 1.0
    if m["kind"] in ("thread", "post") and m.get("title"):
        sid = index.by_entity(m["title"])
        if sid:
            return sid, "entity", 0.9
        sid, score = index.best(m["title"], o["title_min_jaccard"], o["title_min_shared"])
        if sid:
            return sid, "title", score
    return None


def _cover(store, m, source_roles):
    """Coverage rows for a reference post: every story it links to."""
    n = 0
    if "reference" not in source_roles:
        return 0
    for sid, _ in by_url_or_id(store, m, want_all=True):
        store.add_coverage(sid, m["mention_id"], m["source_id"], m.get("url") or "", m.get("posted_at"))
        n += 1
    return n


def mentions(store, settings, new, at, roles):
    """Attach one source's new mentions, [(mention_id, story_hint)]; roles are that source's.
    Returns counts."""
    o = settings["attach"]
    index = TitleIndex(store, at, o["title_days"])
    c = Counter()
    for mid, hint in sorted(new, key=lambda x: x[0]):
        m = store.mention(mid)
        c["covered"] += _cover(store, m, roles)
        hit = _attach_one(store, m, hint, index, o)
        if hit:
            store.set_mention_story(mid, *hit)
            c[f"attached_{hit[1]}"] += 1
    return c


def retro(store, settings, at, roles_by_source):
    """End-of-run pass: retry recent unattached posts, and refresh coverage from recent reference
    posts, now that this run's stories exist. Returns counts."""
    o = settings["attach"]
    since = times.iso(times.parse(at).timestamp() - o["retro_days"] * 86400)
    index = TitleIndex(store, at, o["title_days"])
    c = Counter()
    for (mid,) in store.q("""SELECT mention_id FROM mentions WHERE story_id IS NULL AND kind != 'reference'
                             AND first_seen_at >= ? ORDER BY mention_id""", (since,)):
        m = store.mention(mid)
        hit = _attach_one(store, m, None, index, o)
        if hit:
            store.set_mention_story(mid, *hit)
            c[f"retro_{hit[1]}"] += 1
    for (mid,) in store.q("""SELECT mention_id FROM mentions WHERE match_method IN ('entity', 'title')
                             AND first_seen_at >= ? ORDER BY mention_id""", (since,)):
        m = store.mention(mid)
        found = by_url_or_id(store, m)
        if found and found[0][0] != m["story_id"]:
            store.set_mention_story(mid, found[0][0], found[0][1], 1.0)
            c["retro_upgraded"] += 1
    for (mid,) in store.q("SELECT mention_id FROM mentions WHERE first_seen_at >= ? ORDER BY mention_id", (since,)):
        m = store.mention(mid)
        before = store.one("SELECT COUNT(*) FROM coverage WHERE mention_id=?", (mid,))[0]
        _cover(store, m, roles_by_source.get(m["source_id"], ()))
        c["retro_covered"] += store.one("SELECT COUNT(*) FROM coverage WHERE mention_id=?", (mid,))[0] - before
    return c
