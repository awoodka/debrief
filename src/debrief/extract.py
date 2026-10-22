"""HTML helpers shared by the probe and the listing-page adapter."""
import json
import re
from urllib.parse import urljoin

import trafilatura
from bs4 import BeautifulSoup

FEED_TYPES = {"application/rss+xml", "application/atom+xml", "application/feed+json"}


def links(html, base_url, link_pattern=None, selector=None, exclude=()):
    """[(absolute url, link text)] in page order, fragment-free and deduplicated. link_pattern is
    a regex on the absolute URL; selector is CSS; exclude is a list of regexes to drop."""
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select(selector) if selector else soup.find_all("a", href=True)
    pattern = re.compile(link_pattern) if link_pattern else None
    drop = [re.compile(x) for x in exclude]
    seen, out = set(), []
    for a in anchors:
        href = urljoin(base_url, (a.get("href") or "").strip()).split("#")[0]
        if not href.startswith("http") or href in seen:
            continue
        if (pattern and not pattern.search(href)) or any(d.search(href) for d in drop):
            continue
        seen.add(href)
        out.append((href, a.get_text(" ", strip=True)))
    return out


def advertised_feeds(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    feeds = []
    for ln in soup.find_all("link", href=True):
        rel = " ".join(ln.get("rel") or []).lower()
        if "alternate" in rel and (ln.get("type") or "").lower() in FEED_TYPES:
            feeds.append(urljoin(base_url, ln["href"]))
    return feeds


def _ld_json(soup):
    out = []
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except ValueError:
            continue
        for d in data if isinstance(data, list) else [data]:
            if isinstance(d, dict):
                out.extend(d.get("@graph") or [d])
    return [d for d in out if isinstance(d, dict)]


def page_metadata(html, url):
    """{title, published, summary, is_article, date_from} for an article page: Open Graph, article
    and JSON-LD tags first, then <title>, <time>, the first paragraphs, and finally a date found in
    the text. Values are raw strings; callers sanitize them."""
    soup = BeautifulSoup(html, "html.parser")
    meta = {}
    for m in soup.find_all("meta"):
        k = (m.get("property") or m.get("name") or "").lower()
        if k and m.get("content") and k not in meta:
            meta[k] = m["content"]
    ld = _ld_json(soup)
    article_ld = next((d for d in ld if str(d.get("@type", "")).lower().endswith(("article", "posting"))), {})

    title = meta.get("og:title") or article_ld.get("headline") or (soup.title.string if soup.title else "")
    published, date_from = (meta.get("article:published_time") or article_ld.get("datePublished")
                            or meta.get("date") or meta.get("dc.date") or ""), "metadata"
    if not published:
        t = soup.find("time", datetime=True)
        published, date_from = (t["datetime"], "time-tag") if t else ("", "")
    if not published:
        # Last resort: trafilatura's finder, which reads visible text and URL patterns. It can be
        # wrong (it once took a 2022 date from a July 2026 Meta post), which is why the age guard
        # for scraped pages only runs on a source's first run.
        found = trafilatura.extract_metadata(html, default_url=url)
        published, date_from = ((found.date, "text") if found and found.date else ("", "none"))
    summary = meta.get("og:description") or meta.get("description") or article_ld.get("description") or ""
    if not summary:
        node = soup.find("article") or soup.find("main") or soup.body or soup
        summary = " ".join(p.get_text(" ", strip=True) for p in node.find_all("p")[:3])
    is_article = (meta.get("og:type", "").lower() == "article" or bool(article_ld)
                  or bool(meta.get("article:published_time")))
    return {"title": title or "", "published": published or "", "summary": summary or "",
            "is_article": is_article, "date_from": date_from, "url": meta.get("og:url") or url}
