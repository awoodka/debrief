"""GitHub Trending (HTML scrape; no API). Repo + description + 'stars today/this week'."""
from bs4 import BeautifulSoup

from ..http_util import get
from ..config import GITHUB_TRENDING_URLS
from ..schema import make_item


def _stars(article):
    for sel in ("span.d-inline-block.float-sm-right", "span.float-sm-right"):
        el = article.select_one(sel)
        if el:
            return el.get_text(strip=True)
    return ""


def fetch(urls=None, log=print):
    urls = urls or GITHUB_TRENDING_URLS
    items, failed = [], 0
    for url in urls:
        try:
            html = get(url, timeout=30).text
        except Exception as e:  # noqa: BLE001
            failed += 1
            log(f"  github_trending[{url}]: ERROR {e}")
            continue
        soup = BeautifulSoup(html, "html.parser")
        for art in soup.select("article.Box-row"):
            a = art.select_one("h2 a")
            if not a:
                continue
            repo = a.get("href", "").strip("/")
            desc_el = art.select_one("p")
            items.append(make_item(
                source="github_trending", type="repo",
                title=repo, url=f"https://github.com/{repo}",
                summary=desc_el.get_text(strip=True) if desc_el else "",
                raw_signal={"stars_today": _stars(art), "axis": "in_field"},
            ))
    log(f"  github_trending: {len(items)} repos" + (f" ({failed} pages failed)" if failed else ""))
    return items
