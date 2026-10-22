"""Hacker News, through the Algolia API: every story, and comment trees for the threads that attach.

hn (discussion): every story posted since the cursor, in hn.slice_hours slices (one request each;
a 6-hour slice is ~400 stories, well under Algolia's 1,000-hit page). The cursor trails the run by
hn.overlap_minutes so stories Algolia indexes late aren't skipped; the seen-set drops repeats. Each
run also re-reads the points and comment counts of threads already attached to a story and posted
in the last hn.refresh_days days, 50 per request. Every story is kept (lossless); attach decides
which ones belong to a story.

hn_comments (threads): for threads attached to stories first seen in the last `days` days, with at
least min_comments comments, the comment tree
from Algolia (text, authors, structure) and the ranked order from the official Firebase API (Algolia's
order isn't the ranking). It keeps the top_ranked top-level comments plus the top_replied most-replied
ones, so disagreement survives, not just the top of the page. A thread is re-read once its comment
count has grown by `regrow` times. The full tree stays in the raw store.
"""
import json

from .. import sanitize, times
from ..store import raw
from .base import Adapter, Item

ALGOLIA = "https://hn.algolia.com/api/v1"
FIREBASE = "https://hacker-news.firebaseio.com/v0"


def thread_url(hn_id):
    return f"https://news.ycombinator.com/item?id={hn_id}"


class Hn(Adapter):
    name = "hn"
    windowed = False

    def fetch(self, ctx, source):
        o = {**ctx.opts["hn"], **source.options}
        now = int(ctx.now.timestamp())
        cursor = ctx.store.cursor(source.id).get("until")
        if ctx.backfill_hours:
            start = now - int(ctx.backfill_hours * 3600)
        elif cursor:
            start = int(cursor) - o["overlap_minutes"] * 60
        else:
            start = now - o["bootstrap_hours"] * 3600
        step, a, all_ok = o["slice_hours"] * 3600, start, True
        while a < now:
            b = min(a + step, now)
            rec = ctx.get(source, f"{ALGOLIA}/search_by_date?tags=story&numericFilters=created_at_i>={a},created_at_i<{b}"
                                  f"&hitsPerPage=1000", meta={"kind": "slice", "from": a, "to": b}, conditional=False)
            if raw.ok(rec):
                if json.loads(raw.body(rec)).get("nbHits", 0) > 1000:
                    ctx.warn(f"slice {a}..{b} has more than 1000 stories; some were not fetched")
            else:
                all_ok = False
            a = b
        since = times.iso(now - o["refresh_days"] * 86400)
        ids = [r[0] for r in ctx.store.q("""SELECT item_key FROM mentions WHERE source_id=? AND kind='thread'
                                            AND story_id IS NOT NULL AND posted_at >= ? ORDER BY item_key""",
                                         (source.id, since))]
        for i in range(0, len(ids), 50):
            tags = ",".join(f"story_{x}" for x in ids[i:i + 50])
            ctx.get(source, f"{ALGOLIA}/search?tags=story,({tags})&hitsPerPage=100", meta={"kind": "refresh"},
                    conditional=False)
        if all_ok:
            ctx.set_cursor({"until": now})

    def parse(self, record, source, opts):
        if not raw.ok(record):
            return []
        out = []
        for h in json.loads(raw.body(record)).get("hits", []):
            hid = str(h.get("objectID") or "")
            if not hid:
                continue
            url = (h.get("url") or "").strip()
            out.append(Item(
                item_key=hid, role="mention", mention_kind="thread", url=thread_url(hid),
                title=sanitize.text(h.get("title"), opts["title_max_chars"]),
                summary=sanitize.text(h.get("story_text"), opts["summary_max_chars"]),
                published_at=times.iso(h.get("created_at_i")), authors=(h.get("author") or "",),
                links=(url,) if url else (),
                engagement={"points": h.get("points") or 0, "comments": h.get("num_comments") or 0},
                extra={"hn_id": hid}))
        return out


def _descendants(node):
    return sum(1 + _descendants(ch) for ch in node.get("children") or [] if ch.get("text"))


class HnComments(Adapter):
    name = "hn_comments"
    windowed = False
    phase = "threads"

    def fetch(self, ctx, source):
        o = {**ctx.opts["hn_comments"], **source.options}
        threads_from = o["thread_source"]
        # Threads on stories first seen recently, whenever the thread started: DeepSeek V4.1 Flash's
        # 959-point thread was five days old by the time the model became a story here.
        rows = ctx.store.q("""SELECT m.item_key,
                                (SELECT comments FROM engagement e WHERE e.mention_id = m.mention_id
                                 ORDER BY observed_at DESC LIMIT 1) AS n
                              FROM mentions m JOIN stories st ON st.story_id = m.story_id
                              WHERE m.source_id=? AND m.kind='thread' AND st.first_seen >= date(?, ?)""",
                           (threads_from, ctx.run_date, f"-{o['days'] - 1} days"))
        done = dict(ctx.store.cursor(source.id).get("fetched", {}))
        due = [(n, k) for k, n in rows
               if n and n >= o["min_comments"] and (k not in done or n >= o["regrow"] * done[k])]
        due.sort(key=lambda x: (-x[0], x[1]))
        for n, hid in due[: o["max_threads"]]:
            kids = ctx.get(source, f"{FIREBASE}/item/{hid}.json", meta={"kind": "kids", "thread": hid},
                           conditional=False)
            ranked = (json.loads(raw.body(kids)) or {}).get("kids", []) if raw.ok(kids) else []
            tree = ctx.get(source, f"{ALGOLIA}/items/{hid}", conditional=False,
                           meta={"kind": "tree", "thread": f"{threads_from}|{hid}", "ranked_kids": ranked})
            if raw.ok(tree):
                done[hid] = n
        if len(due) > o["max_threads"]:
            ctx.warn(f"{len(due) - o['max_threads']} threads left for the next run (max_threads)")
        ctx.set_cursor({"fetched": dict(sorted(done.items())[-1000:])})

    def parse(self, record, source, opts):
        meta = record.get("meta") or {}
        if meta.get("kind") != "tree" or not raw.ok(record):
            return []
        o = opts["hn_comments"]
        tree = json.loads(raw.body(record))
        order = {cid: i for i, cid in enumerate(meta.get("ranked_kids") or [])}
        kids = [ch for ch in tree.get("children") or [] if ch.get("text") and ch.get("author")]
        kids.sort(key=lambda ch: (order.get(ch["id"], len(order)), ch.get("created_at_i") or 0, ch["id"]))
        top = kids[: o["top_ranked"]]
        replied = sorted((ch for ch in kids if ch not in top), key=lambda ch: (-_descendants(ch), kids.index(ch)))
        keep = top + [ch for ch in replied[: o["top_replied"]] if _descendants(ch) > 0]
        return [Item(
            item_key=str(ch["id"]), role="mention", mention_kind="comment", url=thread_url(ch["id"]),
            summary=sanitize.text(ch.get("text"), o["comment_max_chars"]),
            published_at=times.iso(ch.get("created_at_i")), authors=(ch.get("author") or "",),
            parent_id=meta["thread"], rank=kids.index(ch) + 1, replies=_descendants(ch))
            for ch in keep]
