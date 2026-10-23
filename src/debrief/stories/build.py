"""Turn parsed items into the seen-set, stories, mentions and snapshots (brief 5.1–5.3).

apply() is the only writer of items, stories and mentions. Every timestamp it stores comes from
the raw records, never the clock, and records are handled in order. So replaying the same raw
data into the same starting state gives byte-identical rows.

For each item, keyed by (source, item_key):
- Already seen: touch it. If an anchor's content changed and this source owns the story (it
  created it), the story takes the new title and summary.
- New anchor: find or create the story by id (the adapter's id, else canon.story_id(url)) and
  merge this source into it. Where the adapter's age guard applies (Adapter.windowed), an anchor
  whose publish date is older than collect.window_days is only recorded as seen.
- New mention: stored, then attached to a story (or recorded as coverage) by stories.attach.
- Every sighting, new or repeat, records the item's signals (about the artifact) and engagement
  (about the post) as timestamped snapshots, which is where bands and trend come from.
- New seen: just recorded.
- A page (an article's main text and links, from adapters/article_pages.py, or carried by an
  anchor whose adapter fetched it anyway) is stored for its story; like a signal, it's not part
  of the seen-set.
"""
import hashlib
from collections import Counter
from datetime import timedelta

from .. import canon, times
from ..store import dumps
from . import attach


def _h(value):
    return hashlib.sha1(dumps(value).encode()).hexdigest()[:16]


def item_hash(it):
    return _h([it.title, it.summary, it.url, list(it.artifact_urls), list(it.code_urls)])


def story_hash(s):
    return _h([s["title"], s["summary"], s["artifact_urls"], s["code_urls"]])


def _too_old(published_at, fetched_at, window_days):
    if not published_at:
        return False
    return times.parse(published_at) < times.parse(fetched_at) - timedelta(days=window_days)


def _urls(values):
    return [u for u in dict.fromkeys(canon.canonical_url(v) for v in values) if u]


def _union(old, new):
    return list(dict.fromkeys([*old, *new]))


def _new_story(sid, it, source, at, run_date, sections):
    kind = it.kind or source.kind or "other"
    return {
        "story_id": sid, "kind": kind, "section": sections.get(kind, "other"),
        "title": it.title or it.url, "org": it.org or source.org, "authors": list(it.authors),
        "published_at": it.published_at, "first_seen": run_date, "first_seen_at": at,
        "last_changed_at": at, "tier": source.tier, "summary": it.summary,
        "artifact_urls": _urls([it.url, *it.artifact_urls]), "code_urls": _urls(it.code_urls),
        "sources": [source.id], "extra": {source.id: it.extra} if it.extra else {},
        "content_hash": "", "merged_into": None,
    }


def _merge(s, it, source):
    """Fold another source's view of the same artifact into a story. Fills gaps; never overwrites."""
    s["sources"] = _union(s["sources"], [source.id])
    s["tier"] = min(s["tier"], source.tier)
    s["artifact_urls"] = _union(s["artifact_urls"], _urls([it.url, *it.artifact_urls]))
    s["code_urls"] = _union(s["code_urls"], _urls(it.code_urls))
    if it.published_at and (not s["published_at"] or it.published_at < s["published_at"]):
        s["published_at"] = it.published_at
    for field, value in (("title", it.title), ("summary", it.summary), ("org", it.org or source.org)):
        if value and not s[field]:
            s[field] = value
    if it.authors and not s["authors"]:
        s["authors"] = list(it.authors)
    if it.extra:
        s["extra"][source.id] = it.extra


def _save(store, s, at, old_hash):
    s["content_hash"] = story_hash(s)
    if old_hash is not None and s["content_hash"] != old_hash:
        s["last_changed_at"] = at
    store.save_story(s)
    for u in s["artifact_urls"]:
        store.add_story_url(u, s["story_id"])


def apply(store, settings, source, adapter, records, refs, run_date):
    """Parse a source's raw records and store the results. Returns counts for the run report."""
    opts, sections = settings["collect"], settings["sections"]
    c = Counter()
    # A source's first run: no items from before this batch. Decided once, from the data, so a
    # replay decides the same way.
    first_at = min((r["fetched_at"] for r in records), default=None)
    first_run = first_at is not None and not store.one(
        "SELECT 1 FROM items WHERE source_id=? AND first_seen_at < ? LIMIT 1", (source.id, first_at))
    guard = adapter.windowed is True or (adapter.windowed == "bootstrap" and first_run)
    new_mentions, last_at = [], first_at
    for rec, ref in zip(records, refs):
        at = last_at = rec["fetched_at"]
        for it in adapter.parse(rec, source, opts):
            c["items"] += 1
            if it.role == "signal":            # a lookup about a story we have; not part of the seen-set
                if it.story_id and store.one("SELECT 1 FROM stories WHERE story_id=?", (it.story_id,)):
                    store.save_signals(it.story_id, at, source.id, it.signals)
                    c["signals"] += 1
                else:
                    c["signals_orphaned"] += 1
                continue
            if it.role == "page":
                if it.story_id and store.one("SELECT 1 FROM stories WHERE story_id=?", (it.story_id,)):
                    ok = 200 <= it.page["status"] < 300
                    page = {**it.page, "links": _urls(it.page.get("links") or [])}
                    if store.save_page(it.story_id, it.url, source.id, at, page, ref):
                        c["pages" if ok else "pages_failed"] += 1
                continue
            h = item_hash(it)
            prev = store.item(source.id, it.item_key)
            if prev:
                _snapshots(store, source, prev["story_id"], prev["mention_id"], it, at)
                if it.role == "seen":          # still listed; its content isn't ours to judge
                    store.run("UPDATE items SET last_seen_at=? WHERE source_id=? AND item_key=?",
                              (at, source.id, it.item_key))
                    continue
                if prev["content_hash"] != h and prev["role"] == "anchor" and prev["story_id"]:
                    s = store.story(prev["story_id"])
                    if s and s["sources"] and s["sources"][0] == source.id:
                        old = s["content_hash"]
                        s["title"], s["summary"] = it.title or s["title"], it.summary or s["summary"]
                        _save(store, s, at, old)
                        c["stories_changed"] += s["content_hash"] != old
                store.touch_item(source.id, it.item_key, at, h, ref)
                continue

            c["new"] += 1
            role = it.role
            if role == "anchor" and adapter.first_run_seen and first_run:
                role, c["first_run_seen"] = "seen", c["first_run_seen"] + 1
            if role == "anchor" and guard and _too_old(it.published_at, at, opts["window_days"]):
                role, c["too_old"] = "seen", c["too_old"] + 1
            sid = (it.story_id or canon.story_id(it.url)) if role == "anchor" else None
            if role == "anchor" and not sid:
                role = "seen"
            if role == "anchor":
                s = store.story(sid)
                if s is None:
                    s, old = _new_story(sid, it, source, at, run_date, sections), None
                    c["stories_new"] += 1
                else:
                    old = s["content_hash"]
                    _merge(s, it, source)
                    c["stories_merged"] += 1
                _save(store, s, at, old)
                store.insert_item(source.id, it.item_key, "anchor", sid, None, at, h, ref)
                _snapshots(store, source, sid, None, it, at)
                if it.page:
                    page = {**it.page, "links": _urls(it.page.get("links") or [])}
                    store.save_page(sid, canon.canonical_url(it.url) or it.url, source.id, at, page, ref)
                    c["pages"] += 1
            elif role == "mention":
                mid = f"{source.id}|{it.item_key}"
                store.insert_mention({
                    "mention_id": mid, "source_id": source.id, "item_key": it.item_key,
                    "kind": it.mention_kind or ("post" if "discussion" in source.roles else "reference"),
                    "url": it.url, "canonical_url": canon.canonical_url(it.url), "title": it.title,
                    "text": it.summary, "author": ", ".join(it.authors) or None,
                    "posted_at": it.published_at, "story_id": None, "match_method": None,
                    "match_score": None, "first_seen_at": at, "raw_ref": ref,
                    "links": dumps(_urls(it.links)), "parent_id": it.parent_id, "rank": it.rank,
                    "replies": it.replies})
                store.insert_item(source.id, it.item_key, "mention", None, mid, at, h, ref)
                _snapshots(store, source, None, mid, it, at)
                new_mentions.append((mid, it.story_hint))
                c["mentions_new"] += 1
            else:
                store.insert_item(source.id, it.item_key, "seen", None, None, at, h, ref)
                c["seen"] += 1
    if new_mentions:
        c.update(attach.mentions(store, settings, new_mentions, last_at, source.roles))
    return c


def _snapshots(store, source, story_id, mention_id, it, at):
    """Record what this sighting says about the artifact and the post, if anything."""
    if story_id and it.signals:
        store.save_signals(story_id, at, source.id, it.signals)
    if mention_id and it.engagement:
        store.save_engagement(mention_id, at, it.engagement)
