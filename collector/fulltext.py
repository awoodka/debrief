"""Deterministic fulltext stage — fetch + extract the linked page for agent-digest items.

Runs inside digest.write(), between build() and render_md(). For each selected item not excluded by
the skip rules, fetch item["url"] through the shared cached HTTP layer, extract the main text
(trafilatura first, BeautifulSoup fallback), cap it, and write data/fulltext/<id>.txt. The item is
annotated in place — it["fulltext"] = {"path", "chars", "status", "note"} — so the digest can point
the summarizer at the file. Special routes: GitHub repos → README (API, PAT-aware), HN item pages →
Algolia (post + top comments), Reddit → old.reddit.com (server-rendered).

Fail-soft everywhere: an item that can't be fetched or extracted keeps its thin summary and is
marked failed in the manifest. attach() NEVER raises. No LLM here.

Dry-run unit test (no collector run needed):  python -m collector.fulltext --limit 10
"""
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from . import http_util
from .config import (FULLTEXT_DIR, FULLTEXT_MAX_CHARS, FULLTEXT_MIN_CHARS, FULLTEXT_TIMEOUT,
                     FULLTEXT_RETRIES, FULLTEXT_SKIP_TYPES, FULLTEXT_SKIP_DOMAINS,
                     FULLTEXT_PAYWALL_DOMAINS, FULLTEXT_DOMAIN_INTERVALS, FULLTEXT_DEFAULT_INTERVAL)

_GH_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/#?]+)/?$")
_HN_RE = re.compile(r"^https?://news\.ycombinator\.com/item\?id=(\d+)")
_last_hit = {}   # domain -> monotonic ts of the last LIVE fetch (cache hits don't count)


def _domain(url):
    d = (urlparse(url).netloc or "").lower()
    return d[4:] if d.startswith("www.") else d


def _in_domains(dom, domains):
    return any(dom == d or dom.endswith("." + d) for d in domains)


def _should_fetch(it):
    """(fetch?, note). The note lands in the manifest so skips are auditable."""
    if it.get("type") in FULLTEXT_SKIP_TYPES:
        return False, f"type:{it['type']}"
    url = it.get("url") or ""
    if not url.startswith("http"):
        return False, "no-url"
    if url.lower().endswith(".pdf"):
        return False, "pdf"
    dom = _domain(url)
    if _in_domains(dom, FULLTEXT_SKIP_DOMAINS):
        return False, f"skip-domain:{dom}"
    if _in_domains(dom, FULLTEXT_PAYWALL_DOMAINS):
        return False, f"paywall:{dom}"
    return True, ""


def _polite_get(url, headers=None):
    """Cached GET with per-domain spacing on live fetches (cache hits return immediately)."""
    dom = _domain(url)
    interval = next((v for d, v in FULLTEXT_DOMAIN_INTERVALS.items()
                     if dom == d or dom.endswith("." + d)), FULLTEXT_DEFAULT_INTERVAL)
    wait = _last_hit.get(dom, 0) + interval - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    r = http_util.get(url, timeout=FULLTEXT_TIMEOUT, retries=FULLTEXT_RETRIES, headers=headers)
    if not getattr(r, "from_cache", False):
        _last_hit[dom] = time.monotonic()
    return r


def _clean(text):
    if not text:
        return ""
    text = re.sub(r"\n{3,}", "\n\n", text.replace("\r", "")).strip()
    return text[:FULLTEXT_MAX_CHARS]


def _extract(html, url):
    """Main-text extraction: trafilatura, then a BS4 density fallback. Returns "" on nothing."""
    try:
        import trafilatura
        text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False)
        if text and len(text) >= FULLTEXT_MIN_CHARS:
            return text
    except Exception:  # noqa: BLE001 — fall through to BS4
        pass
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        node = soup.find("article") or soup.find("main") or soup.body or soup
        text = "\n\n".join(p.get_text(" ", strip=True) for p in node.find_all("p"))
        if not text:
            text = node.get_text(" ", strip=True)
        return text or ""
    except Exception:  # noqa: BLE001
        return ""


def _github_readme(url):
    m = _GH_RE.match(url)
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    hdr = {"Accept": "application/vnd.github.raw+json"}
    pat = os.environ.get("GITHUB_PAT")
    if pat:
        hdr["Authorization"] = f"Bearer {pat}"
    try:
        return _polite_get(f"https://api.github.com/repos/{owner}/{repo}/readme", headers=hdr).text
    except Exception:  # noqa: BLE001 — unauthenticated fallback, no API quota
        try:
            return _polite_get(f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md").text
        except Exception:  # noqa: BLE001
            return None


def _hn_item(url):
    """HN self-posts/comment pages via Algolia: story text + the top few comments."""
    m = _HN_RE.match(url)
    if not m:
        return None
    try:
        d = _polite_get(f"https://hn.algolia.com/api/v1/items/{m.group(1)}").json()
    except Exception:  # noqa: BLE001
        return None
    from bs4 import BeautifulSoup

    def strip(html):
        return BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)

    parts = [d.get("title") or "", strip(d.get("text"))]
    for c in (d.get("children") or [])[:5]:
        t = strip(c.get("text"))
        if t:
            parts.append(f"[comment by {c.get('author', '?')}] {t}")
    return "\n\n".join(p for p in parts if p)


def _reddit(url):
    """old.reddit.com is server-rendered, but generic extraction grabs the subreddit sidebar —
    pull the post body + top comments with targeted selectors instead."""
    html = _polite_get(url.replace("://www.reddit.com", "://old.reddit.com")).text
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    parts = []
    table = soup.find(id="siteTable")                # the post itself (selftext, if any)
    if table:
        body = table.select_one(".usertext-body")
        if body:
            parts.append(body.get_text(" ", strip=True))
    for c in soup.select("div.commentarea .entry .usertext-body")[:5]:
        t = c.get_text(" ", strip=True)
        if t:
            parts.append(f"[comment] {t}")
    return "\n\n".join(parts)


def _route(it):
    """Fetch raw content for one item. Returns extracted text or ""."""
    url = it["url"]
    gh = _github_readme(url)
    if gh is not None:
        return gh                                    # README markdown reads fine as plain text
    hn = _hn_item(url)
    if hn is not None:
        return hn
    dom = _domain(url)
    if dom == "reddit.com" or dom.endswith(".reddit.com"):
        return _reddit(url)
    html = _polite_get(url).text
    return _extract(html, url)


def attach(selected, generated_at="", log=print):
    """Annotate every selected digest item with fulltext (or a skip/fail note) and write
    data/fulltext/<id>.txt + manifest.json. selected = digest.build() output. Never raises."""
    from .digest import item_id   # local import — digest imports this module

    out = Path(FULLTEXT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.txt"):   # stale-run hygiene: fulltext always mirrors the current digest
        old.unlink()

    stats = {"fetched": 0, "skipped": 0, "failed": 0}
    manifest = {}
    for rows in selected.values():
        for it, _full in rows:
            iid = item_id(it)
            ok, note = _should_fetch(it)
            if not ok:
                it["fulltext"] = {"status": "skipped", "note": note}
                manifest[iid] = it["fulltext"]
                stats["skipped"] += 1
                continue
            try:
                text = _clean(_route(it))
            except Exception as e:  # noqa: BLE001 — fail-soft: thin summary survives
                text, note = "", f"{type(e).__name__}: {e}"[:120]
            existing = len(it.get("summary") or "")
            if len(text) >= FULLTEXT_MIN_CHARS or len(text) > existing:
                path = out / f"{iid}.txt"
                path.write_text(f"[#{iid}] {it['title']}\n{it['url']}\n\n{text}")
                rel = str(path.relative_to(out.parent.parent))
                it["fulltext"] = {"status": "ok", "path": rel, "chars": len(text)}
                stats["fetched"] += 1
            else:
                it["fulltext"] = {"status": "failed", "note": note or f"extracted {len(text)} chars"}
                stats["failed"] += 1
            manifest[iid] = it["fulltext"]
    (out / "manifest.json").write_text(json.dumps(
        {"generated_at": generated_at, "stats": stats, "items": manifest}, indent=2))
    log(f"  fulltext: {stats['fetched']} fetched / {stats['skipped']} skipped / {stats['failed']} failed")
    return stats


if __name__ == "__main__":
    # Dry-run unit test against the existing agent_digest.json — no collector run, no LLM.
    import argparse

    ap = argparse.ArgumentParser(description="Fulltext dry run against data/agent_digest.json")
    ap.add_argument("--limit", type=int, default=10, help="max items to attempt (skips don't count)")
    a = ap.parse_args()

    digest_json = Path(FULLTEXT_DIR).parent / "agent_digest.json"
    flat = json.loads(digest_json.read_text())["items"]
    picked, attempts = [], 0
    for it in flat:
        it.setdefault("raw_signal", {})
        ok, _ = _should_fetch(it)
        picked.append((it, True))
        if ok:
            attempts += 1
        if attempts >= a.limit:
            break
    fake_selected = {"dryrun": picked}
    s = attach(fake_selected, generated_at="dry-run")
    for it, _ in picked:
        ft = it.get("fulltext", {})
        print(f"  [{ft.get('status', '?'):7s}] {it.get('type', '?'):10s} {it.get('id', it.get('title', ''))[:8]:8s}"
              f" {ft.get('chars', ft.get('note', '')):>6}  {it.get('title', '')[:60]}")
    print(json.dumps(s))
