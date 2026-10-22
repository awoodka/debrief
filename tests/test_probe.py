from debrief import probe
from debrief.http import Fetched

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>First post</title><link>https://a.test/1</link><pubDate>Wed, 21 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Second</title><link>https://a.test/2</link></item></channel></rss>"""

PAGE = b"""<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml"></head><body>
<a href="/news/one">One</a><a href="/news/one#x">dupe</a><a href="/news/two">Two</a><a href="/about">About</a>
</body></html>"""


class StubClient:
    def __init__(self, fetched, robots=True):
        self.fetched, self.robots, self.requests = fetched, robots, []

    def robots_allowed(self, url):
        return self.robots

    def get(self, url, headers=None):
        self.requests.append(url)
        return self.fetched

    post = get


def fetched(content, status=200, ctype="text/html"):
    return Fetched(url="https://a.test/news", final_url="https://a.test/news", status=status,
                   headers={"content-type": ctype, "etag": '"1"'}, content=content)


def test_feed_probe_counts_entries():
    att = probe.probe_url(StubClient(fetched(RSS)), "https://a.test/feed", {"kind": "feed"})
    assert (att.status, att.n_items, att.sample_title, att.etag) == ("ok", 2, "First post", True)


def test_links_probe_filters_dedupes_and_finds_feeds():
    att = probe.probe_url(StubClient(fetched(PAGE)), "https://a.test/news",
                          {"kind": "links", "link_pattern": r"^https://a\.test/news/[^/]+$"})
    assert att.n_items == 2 and att.feeds == ["https://a.test/feed.xml"]


def test_links_probe_respects_robots():
    stub = StubClient(fetched(PAGE), robots=False)
    att = probe.probe_url(stub, "https://a.test/news", {"kind": "links"})
    assert att.status == "blocked" and stub.requests == []


def test_json_probe_paths_and_counts():
    body = b'{"hits": [{"title": "Show HN: x", "created_at": "2026-10-22"}], "nbHits": 1432}'
    att = probe.probe_url(StubClient(fetched(body, ctype="application/json")), "https://hn.test",
                          {"kind": "json", "items": "hits", "title": "title", "date": "created_at", "count": "nbHits"})
    assert (att.status, att.n_items, att.count, att.sample_title) == ("ok", 1, 1432, "Show HN: x")


def test_json_probe_reports_keys_when_items_missing():
    att = probe.probe_url(StubClient(fetched(b'{"data": {}}', ctype="application/json")), "https://x.test",
                          {"kind": "json", "items": "results"})
    assert att.status == "failed" and "data" in att.error


def test_missing_key_is_reported_as_needs_key():
    att = probe.probe_url(StubClient(fetched(b"", status=401)), "https://x.test",
                          {"kind": "json", "_auth_env": "SOME_KEY"})
    assert att.status == "needs_key"
