"""Article pages for new stories: the main text, and the links inside it (CRITERIA.md, M3).

Most lab and press feeds carry a title and a teaser only (15 of 24 on 2026-10-23), so the page is
where an article's links are. Triage groups press coverage of one event and merges an
announcement into the artifact it announces by those links, and the index's full text comes
from here too.

fetch: one request for each story that
  - is in an article_pages.sections section and new in the last article_pages.days days (published,
    for announcements and press; first seen, for releases), the way triage's windows count,
  - has an enabled source, and
  - has no stored page yet. A failed fetch is retried while the story is in the window, unless
    the server answered 4xx.
What gets requested:
  announcements, press   the story's own URL
  releases               the model card (README.md) of its Hugging Face repo (the model's page if
                         that's refused: gated repos answer 401 for the file but show the card), or
                         a GitHub repo's README; OpenRouter's model page otherwise; nothing for a
                         leaderboard entry
robots.txt is checked first (an unreachable one counts as a disallow, RFC 9309), and
article_pages.skip_domains (paywalls) are never requested.

parse: trafilatura's main text, as markdown so the links survive; text and links are stored
apart. Listing-page sources (Anthropic, Meta) fetch the post anyway, so their anchors carry the
page themselves and nothing is requested twice.

ThreadPages (source hn-thread-pages, daily, after the discussion sources): the page linked by each of
the most-discussed threads that attached to nothing (triage.yml discussion.sources.<id>.prefetch, for
the sources in article_pages.thread_sources), stored under the thread's id (hn:<item>), so triage and
the skill can read what the thread is about. A GitHub repo gives its README. Skipped: Ask and Tell HN
posts (their text is the thread's own), posts on X (they need JavaScript), PDFs and videos. LessWrong
posts aren't fetched: their body is their text.
"""
from urllib.parse import urlsplit

from .. import extract, times
from ..store import loads, raw
from ..triage.candidates import published
from .base import Adapter, Item


def _host_matches(url, domains):
    host = (urlsplit(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def model_id(story):
    """The Hugging Face repo id behind a release story: an original over a format copy."""
    ids = []
    for details in story["extra"].values():
        mid = details.get("model_id") or details.get("hugging_face_id")
        if mid and "/" in mid:
            ids.append((bool(details.get("format")), mid))
    if ids:
        return sorted(ids)[0][1]
    if story["story_id"].startswith("hf-model:"):
        for u in story["artifact_urls"]:
            parts = urlsplit(u)
            seg = [x for x in parts.path.split("/") if x]
            if parts.hostname == "huggingface.co" and len(seg) == 2:
                return "/".join(seg)
    return None


def page_targets(story):
    """[(url, kind)] to try for a story, in order (the next one only after a 4xx), or
    ([], why not)."""
    sid = story["story_id"]
    if story["section"] != "releases":
        return ([(story["artifact_urls"][0], "html")], None) if story["artifact_urls"] else ([], "no url")
    mid = model_id(story)
    if mid:
        return [(f"https://huggingface.co/{mid}/raw/main/README.md", "readme"),
                (f"https://huggingface.co/{mid}", "html")], None
    if sid.startswith("gh-repo:"):
        repo = next((u for u in story["code_urls"] + story["artifact_urls"] if "github.com/" in u), "")
        full = "/".join(urlsplit(repo).path.strip("/").split("/")[:2]) if repo else sid.split(":", 1)[1]
        return [(f"https://raw.githubusercontent.com/{full}/HEAD/README.md", "readme")], None
    for u in story["artifact_urls"]:
        if u.startswith("https://openrouter.ai/"):
            return [(u, "html")], None
    return [], "no page for this kind of release"


def due(store, settings, enabled, at):
    """Stories that need a page, oldest first: [(story, url, kind)], plus counts of the skipped."""
    o = settings["collect"]["article_pages"]
    since = times.iso(times.parse(at).timestamp() - o["days"] * 86400)
    marks = ",".join("?" * len(o["sections"]))
    rows = store.q(f"""SELECT story_id FROM stories WHERE section IN ({marks}) AND first_seen_at >= ?
                       ORDER BY first_seen_at, story_id""", (*o["sections"], since))
    out, skipped = [], {}
    for (sid,) in rows:
        s = store.story(sid)
        if not set(s["sources"]) & enabled:
            continue
        if s["section"] != "releases" and published(s) < since:
            continue
        targets, why = page_targets(s)
        if not targets:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        prev = {r[0]: r[1] for r in store.q("SELECT url, status FROM story_pages WHERE story_id=?", (sid,))}
        if any(200 <= st < 300 for st in prev.values()):
            continue
        url, kind = next(((u, k) for u, k in targets if not 400 <= prev.get(u, 0) < 500), (None, None))
        if not url:
            continue
        if _host_matches(url, o["skip_domains"]):
            skipped["paywalled domain"] = skipped.get("paywalled domain", 0) + 1
            continue
        out.append((s, url, kind))
    return out, skipped


class ArticlePages(Adapter):
    name = "article_pages"
    phase = "enrich"
    windowed = False
    page_by_page = True

    def fetch(self, ctx, source):
        o = ctx.settings["collect"]["article_pages"]
        todo, skipped = due(ctx.store, ctx.settings, ctx.enabled, times.iso(ctx.now))
        if len(todo) > o["max_per_run"]:
            ctx.warn(f"{len(todo) - o['max_per_run']} pages left for the next run (max_per_run)")
        blocked = 0
        for s, url, kind in todo[: o["max_per_run"]]:
            if ctx.client.robots_allowed(url) is not True:
                blocked += 1
                continue
            ctx.get(source, url, each=s["story_id"], meta={"story": s["story_id"], "kind": kind}, conditional=False)
        if blocked:
            ctx.warn(f"robots.txt disallows (or couldn't be read for) {blocked} page(s); not fetched")
        for why, n in sorted(skipped.items()):
            ctx.warn(f"{n} stor{'y' if n == 1 else 'ies'} without a page: {why}")

    def parse(self, record, source, opts):
        meta = record.get("meta") or {}
        sid = meta.get("story")
        if not sid:
            return []
        o = opts["article_pages"]
        text, links = "", []
        if raw.ok(record):
            body = raw.body(record)
            url = record.get("final_url") or record["url"]
            if meta.get("kind") == "readme":
                text, links = extract.readme(body, url, o["max_chars"], o["max_links"])
            else:
                text, links = extract.article(body, url, o["max_chars"], o["max_links"])
        return [Item(item_key=sid, role="page", story_id=sid, url=record["url"],
                     page={"status": record.get("status", 0), "text": text, "links": links})]


_NOT_PAGES = (("news.ycombinator.com", "a post on Hacker News itself (its text is the thread's)"),
              ("x.com", "a post on X (needs JavaScript)"), ("youtube.com", "a video"), ("youtu.be", "a video"))


def thread_target(m):
    """(url, kind) to fetch for an unattached thread, or (None, why not)."""
    links = loads(m["links"], []) if isinstance(m["links"], str) else list(m["links"] or [])
    link = links[0] if links else m["url"]
    if not link:
        return None, "no link"
    parts = urlsplit(link)
    host = (parts.hostname or "").lower()
    for domain, why in _NOT_PAGES:
        if host == domain or host.endswith("." + domain):
            return None, why
    if parts.path.lower().endswith(".pdf"):
        return None, "a PDF"
    seg = [x for x in parts.path.split("/") if x]
    if host == "github.com" and len(seg) == 2:
        return f"https://raw.githubusercontent.com/{seg[0]}/{seg[1]}/HEAD/README.md", "readme"
    return link, "html"


class ThreadPages(ArticlePages):
    name = "thread_pages"
    phase = "threads"

    def fetch(self, ctx, source):
        from ..triage.candidates import thread_id, unattached_threads
        o = ctx.settings["collect"]["article_pages"]
        quotas = ctx.settings["triage"]["discussion"]["sources"]
        threads = [m for src in o["thread_sources"] if src in quotas
                   for m, _, _ in unattached_threads(ctx.store, ctx.settings, ctx.stored_until, src,
                                                     quotas[src]["prefetch"])]
        skipped, blocked = {}, 0
        for m in threads:
            sid = thread_id(m)
            url, kind = thread_target(m)
            if not url:
                skipped[kind] = skipped.get(kind, 0) + 1
                continue
            prev = ctx.store.one("SELECT status FROM story_pages WHERE story_id=? AND url=?", (sid, url))
            if prev and (200 <= prev[0] < 300 or 400 <= prev[0] < 500):
                continue
            if _host_matches(url, o["skip_domains"]):
                skipped["paywalled domain"] = skipped.get("paywalled domain", 0) + 1
                continue
            if ctx.client.robots_allowed(url) is not True:
                blocked += 1
                continue
            ctx.get(source, url, each=sid, meta={"story": sid, "kind": kind}, conditional=False)
        if blocked:
            ctx.warn(f"robots.txt disallows (or couldn't be read for) {blocked} page(s); not fetched")
        for why, n in sorted(skipped.items()):
            ctx.warn(f"{n} thread{'' if n == 1 else 's'} without a page: {why}")


def stored_links(store, story_id):
    """Every link in the story's fetched pages, in order."""
    out = []
    for r in store.q("SELECT links FROM story_pages WHERE story_id=? AND status BETWEEN 200 AND 299 ORDER BY url",
                     (story_id,)):
        out += loads(r[0], [])
    return list(dict.fromkeys(out))
