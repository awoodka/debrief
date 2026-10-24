"""LessWrong and the Alignment Forum, through their GraphQL API (one ForumMagnum site behind two hosts).

lesswrong (discussion): every post of the last lesswrong.lookback_hours, newest first, in pages of
page_size, re-read every run (daily and the 6-hour poll) so karma and comment counts stay current;
the seen-set drops repeats. A post is a thread: its karma is its points, and its body (links reduced
to their text) is its text, so it needs no page fetch. links[0] is what the post is about: the page
a link post links to, or the post itself. Then come the first lead_links links of its body (off the
forum), which is how a post about a paper or a model attaches to that story. A post cites far more
than it's about ("The Hackening" has 98 links), so later links are left out, and so is every link
of a numbered roundup (roundup_title: "AI #191", "Against Moloch #48"), whose links are the week's
news, not its subject. On 2026-10-24 those two roundups attached to whatever their 57th and 3rd
links named, and the two posts about one thing matched their 1st and 2nd links.

Which posts:
  - Every Alignment Forum post is also on LessWrong, flagged af, with the same karma (checked
    2026-10-24). The LessWrong source leaves them to the source named by options.af_source while
    that one is enabled, so a cross-post counts once. That's decided at fetch time and recorded in
    the raw record, so a replay decides the same way.
  - options.ai_tag: a post is left out only when its tags say it's about something else, meaning
    it carries another of LessWrong's core tags (Rationality, World Modeling, World Optimization,
    Practical, Community) and not this one. LessWrong tags aren't a hierarchy, so a post tagged only
    AI Control or Redwood Research lacks the AI tag. On the week to 2026-10-24, 38 of 157 posts had
    no tags, most of them about AI. This rule kept 146 of 158 posts, and the 12 it left out were
    all about something else. The scoring call drops whatever isn't relevant.
  - Events are left out.

lesswrong_comments (threads): the best comments of posts from lesswrong_comments.thread_sources
that attached to stories first seen in the last `days` days, and on daily runs also of the posts
triage may score on their own (triage.yml discussion.sources.<id>.prefetch), with at least
min_comments comments. A post's comments are re-read once its count has grown by `regrow` times.
They always come from LessWrong, which has every comment (the Alignment Forum shows only its
members'). Of the top-level comments that say enough (min_chars once links are taken out), it keeps
the top_ranked best by karma, plus the top_replied most-replied of the rest.
"""
import json
import re
from urllib.parse import urlsplit

from .. import extract, sanitize, times
from ..store import raw
from .base import Adapter, Item
from .hn import substantive

POSTS = """query Posts($terms: JSON) { posts(input: {terms: $terms}) { results {
  _id title pageUrl postedAt baseScore afBaseScore voteCount commentCount afCommentCount af url question
  isEvent wordCount frontpageDate tags { slug name core } user { displayName } coauthors { displayName }
  contents { markdown } } } }"""

COMMENTS = """query Comments($terms: JSON) { comments(input: {terms: $terms}) { results {
  _id postedAt baseScore af parentCommentId descendentCount deleted pageUrl user { displayName }
  contents { markdown } } } }"""

FORUM_HOSTS = ("lesswrong.com", "alignmentforum.org")


def results(data, field):
    """The result list of a GraphQL response, or [] (errors, nulls)."""
    return (((data or {}).get("data") or {}).get(field) or {}).get("results") or []


def errors(rec):
    """The GraphQL errors in a record, as a short string, or "" (HTTP failures are the record's own)."""
    if not raw.ok(rec):
        return ""
    try:
        errs = json.loads(raw.body(rec)).get("errors")
    except ValueError:
        return "the response isn't JSON"
    return str(errs)[:200] if errs else ""


def _on_forum(url):
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in FORUM_HOSTS)


class LessWrong(Adapter):
    name = "lesswrong"
    windowed = False          # every run reads back a fixed window from now; the seen-set drops repeats

    def fetch(self, ctx, source):
        o = {**ctx.opts["lesswrong"], **source.options}
        after = times.iso(ctx.now.timestamp() - o["lookback_hours"] * 3600)
        skip_af = bool(o.get("af_source")) and o["af_source"] in ctx.enabled
        for page in range(o["max_pages"]):
            terms = {"view": "new", "after": after, "limit": o["page_size"], "offset": page * o["page_size"]}
            rec = ctx.post(source, source.url, {"query": POSTS, "variables": {"terms": terms}},
                           meta={"kind": "posts", "terms": terms, "skip_af": skip_af})
            err = errors(rec)
            if err:
                ctx.warn(f"GraphQL: {err}")
            if not raw.ok(rec) or err or len(results(json.loads(raw.body(rec)), "posts")) < o["page_size"]:
                return
        ctx.warn(f"more than {o['max_pages'] * o['page_size']} posts in {o['lookback_hours']} h; "
                 f"the oldest were not read (max_pages)")

    def parse(self, record, source, opts):
        meta = record.get("meta") or {}
        if meta.get("kind") != "posts" or not raw.ok(record) or errors(record):
            return []
        o = {**opts["lesswrong"], **source.options}
        out = []
        for p in results(json.loads(raw.body(record)), "posts"):
            pid = p.get("_id") or ""
            if not pid or p.get("isEvent") or (meta.get("skip_af") and p.get("af")):
                continue
            tags = [t for t in p.get("tags") or [] if t]
            if (o.get("ai_tag") and not any(t.get("slug") == o["ai_tag"] for t in tags)
                    and any(t.get("core") for t in tags)):
                continue
            page = p.get("pageUrl") or ""
            title = sanitize.text(p.get("title"), opts["title_max_chars"])
            text, links = extract.markdown((p.get("contents") or {}).get("markdown"), o["body_max_chars"],
                                           o["max_links"])
            lead = [] if re.search(o["roundup_title"], title) else [u for u in links if not _on_forum(u)][: o["lead_links"]]
            people = [x.get("displayName") for x in [p.get("user"), *(p.get("coauthors") or [])] if x]
            out.append(Item(
                item_key=pid, role="mention", mention_kind="thread", url=page, title=title, summary=text,
                published_at=times.iso(p.get("postedAt")),
                authors=tuple(sanitize.text(a, 120) for a in people if a)[: opts["max_authors"]],
                links=tuple(dict.fromkeys([p.get("url") or page, *lead])),
                engagement={"points": p.get("baseScore") or 0, "comments": p.get("commentCount") or 0}))
        return out


class LessWrongComments(Adapter):
    name = "lesswrong_comments"
    windowed = False
    phase = "threads"

    def fetch(self, ctx, source):
        o = {**ctx.opts["lesswrong_comments"], **source.options}
        srcs = [s for s in o["thread_sources"] if s in ctx.enabled]
        if not srcs:
            return
        marks = ",".join("?" * len(srcs))
        rows = ctx.store.q(f"""SELECT m.mention_id,
                                 (SELECT comments FROM engagement e WHERE e.mention_id = m.mention_id
                                  ORDER BY observed_at DESC LIMIT 1) AS n
                               FROM mentions m JOIN stories st ON st.story_id = m.story_id
                               WHERE m.source_id IN ({marks}) AND m.kind='thread' AND st.first_seen >= date(?, ?)""",
                           (*srcs, ctx.run_date, f"-{o['days'] - 1} days"))
        rows = [tuple(r) for r in rows]
        if ctx.cadence == "daily":
            from ..triage.candidates import unattached_threads
            quotas = ctx.settings["triage"]["discussion"]["sources"]
            have = {mid for mid, _ in rows}
            for src in srcs:
                if src not in quotas:
                    continue
                for m, _, comments in unattached_threads(ctx.store, ctx.settings, ctx.stored_until, src,
                                                         quotas[src]["prefetch"]):
                    if m["mention_id"] not in have:
                        rows.append((m["mention_id"], comments))
        done = dict(ctx.store.cursor(source.id).get("fetched", {}))
        due = [(n, mid) for mid, n in rows
               if n and n >= o["min_comments"] and (mid not in done or n >= o["regrow"] * done[mid])]
        due.sort(key=lambda x: (-x[0], x[1]))
        for n, mid in due[: o["max_posts"]]:
            terms = {"view": "postCommentsTop", "postId": mid.split("|", 1)[1], "limit": o["limit"]}
            rec = ctx.post(source, source.url, {"query": COMMENTS, "variables": {"terms": terms}},
                           meta={"kind": "comments", "thread": mid})
            err = errors(rec)
            if err:
                ctx.warn(f"GraphQL ({mid}): {err}")
            elif raw.ok(rec):
                done[mid] = n
        if len(due) > o["max_posts"]:
            ctx.warn(f"{len(due) - o['max_posts']} posts left for the next run (max_posts)")
        ctx.set_cursor({"fetched": dict(sorted(done.items())[-1000:])})

    def parse(self, record, source, opts):
        meta = record.get("meta") or {}
        if meta.get("kind") != "comments" or not raw.ok(record) or errors(record):
            return []
        o = opts["lesswrong_comments"]
        good = []
        for c in results(json.loads(raw.body(record)), "comments"):       # best by karma first
            name = (c.get("user") or {}).get("displayName")
            if c.get("parentCommentId") or c.get("deleted") or not name:
                continue
            text, _ = extract.markdown((c.get("contents") or {}).get("markdown"), o["comment_max_chars"])
            if substantive(text, o["min_chars"]):
                good.append({"rank": len(good) + 1, "c": c, "name": name, "text": text,
                             "replies": c.get("descendentCount") or 0})
        top = good[: o["top_ranked"]]
        replied = sorted(good[o["top_ranked"]:], key=lambda x: -x["replies"])      # stable: karma breaks ties
        keep = top + [x for x in replied[: o["top_replied"]] if x["replies"] > 0]
        return [Item(
            item_key=x["c"]["_id"], role="mention", mention_kind="comment", url=x["c"].get("pageUrl") or "",
            summary=x["text"], published_at=times.iso(x["c"].get("postedAt")), authors=(sanitize.text(x["name"], 120),),
            parent_id=meta["thread"], rank=x["rank"], replies=x["replies"],
            engagement={"points": x["c"].get("baseScore") or 0})
            for x in keep]
