"""Sites without a feed: read the listing page, diff its post links against the seen-set, and fetch
only the new posts for their title, date and summary.

Options (in the source's `options`, falling back to its probe spec and then collect.listing):
  link_pattern / selector   which links are posts
  exclude                   regexes for links that look like posts but aren't (category pages)
  bootstrap_fetch           on the first run, fetch this many of the newest links; the rest are
                            recorded as seen so years of archive don't become today's news
  max_new_per_run           cap on post fetches per run; the rest wait for the next run

robots.txt is checked before every page and post. A disallowed one is never fetched. A post's
main text and links are kept as its story's page (adapters/article_pages.py), so it isn't
requested again.
"""
from .. import canon, extract, sanitize, times
from ..store import raw
from .base import Adapter, Item


def _opt(source, opts, key):
    """The source's own option, else its probe spec's, else the collect.listing default."""
    if key in source.options:
        return source.options[key]
    if key in source.probe:
        return source.probe[key]
    return opts["listing"].get(key)


class ListingPage(Adapter):
    name = "listing_page"
    windowed = "bootstrap"

    def fetch(self, ctx, source):
        page_url = source.url
        if ctx.client.robots_allowed(page_url) is False:
            ctx.warn(f"robots.txt disallows {page_url}; not fetched")
            return
        # Not conditional: links held back by max_new_per_run must be found again even if the
        # page itself hasn't changed.
        page = ctx.get(source, page_url, meta={"kind": "page"}, conditional=False)
        if not raw.ok(page):
            return
        found = [canon.canonical_url(u) for u, _ in extract.links(
            raw.body(page), page.get("final_url") or page_url, _opt(source, ctx.opts, "link_pattern"),
            _opt(source, ctx.opts, "selector"), _opt(source, ctx.opts, "exclude") or ())]
        found = list(dict.fromkeys(u for u in found if u))
        known = ctx.store.known_keys(source.id)
        new = [u for u in found if u not in known]
        first, cap = _opt(source, ctx.opts, "bootstrap_fetch"), _opt(source, ctx.opts, "max_new_per_run")
        if not known:
            fetch_now, baseline, later = new[:first], new[first:], []
        else:
            fetch_now, baseline, later = new[:cap], [], new[cap:]
            if later:
                ctx.warn(f"{len(later)} new links left for the next run (max_new_per_run)")
        blocked = []
        for url in fetch_now:
            if ctx.client.robots_allowed(url) is False:
                blocked.append(url)
                continue
            ctx.get(source, url, meta={"kind": "post", "link": url}, conditional=False)
        if blocked:
            ctx.warn(f"robots.txt disallows {len(blocked)} post(s); recorded as seen, not fetched")
        # The page record lists what parse() should mark seen: everything still listed except the
        # posts fetched now (their own records anchor them) and those left for the next run.
        pending = set(fetch_now) - set(blocked)
        page["meta"].update({"links": found, "baseline": baseline, "blocked": blocked,
                             "seen": [u for u in found if u not in pending and u not in later]})

    def parse(self, record, source, opts):
        meta = record.get("meta") or {}
        if meta.get("kind") == "page":
            reasons = {**{u: "baseline" for u in meta.get("baseline", [])},
                       **{u: "robots" for u in meta.get("blocked", [])}}
            return [Item(item_key=u, role="seen", url=u, extra={"reason": reasons.get(u, "listed")})
                    for u in meta.get("seen", [])]
        if meta.get("kind") != "post" or not raw.ok(record):
            return []
        m = extract.page_metadata(raw.body(record), record.get("final_url") or record["url"])
        key = meta["link"]
        title = sanitize.text(m["title"], opts["title_max_chars"])
        if not title:
            return [Item(item_key=key, role="seen", url=key, extra={"reason": "no title"})]
        # Known category pages are excluded by pattern before fetching; whether the page carries
        # article markup is kept as a signal, not a gate (Meta's posts have none).
        ap = opts["article_pages"]
        text, links = extract.article(raw.body(record), record.get("final_url") or record["url"],
                                      ap["max_chars"], ap["max_links"])
        return [Item(
            item_key=key, role="anchor" if "anchor" in source.roles else "mention", url=key,
            title=title, summary=sanitize.text(m["summary"], opts["summary_max_chars"]),
            published_at=times.iso(m["published"]), kind=source.kind, org=source.org,
            extra={"article_markup": m["is_article"], "date_from": m["date_from"]},
            page={"status": record.get("status", 200), "text": text, "links": links})]
