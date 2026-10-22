from debrief.http import Client

SETTINGS = {"timeout_s": 5, "max_retries": 2, "backoff_s": 5, "max_retry_after_s": 120,
            "default_interval_s": 2.0,
            "buckets": {"arxiv": {"hosts": ["arxiv.org"], "interval_s": 3.5},
                        "hf-pages": {"hosts": ["huggingface.co/papers"], "interval_s": 3.5},
                        "hf": {"hosts": ["huggingface.co"], "interval_s": 1.0}}}


class FakeResponse:
    def __init__(self, status, headers=None, content=b"ok", url="https://x.test/"):
        self.status_code, self.headers, self.content, self.url = status, headers or {}, content, url


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls, self.headers = list(responses), [], {}

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw.get("headers")))
        return self.responses.pop(0)


class Clock:
    def __init__(self):
        self.t, self.sleeps = 0.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(round(s, 2))
        self.t += s


def client(responses, clock=None):
    clock = clock or Clock()
    return Client("ua", SETTINGS, sleep=clock.sleep, clock=clock.now, session=FakeSession(responses)), clock


def test_arxiv_hosts_share_one_bucket_and_are_spaced():
    c, clock = client([FakeResponse(200)] * 3)
    c.get("https://rss.arxiv.org/rss/cs.LG")
    c.get("https://export.arxiv.org/api/query")
    c.get("https://alphaxiv.org/abs/1.md")          # not an arxiv.org subdomain: its own bucket
    assert clock.sleeps == [3.5]
    assert c.bucket("https://oaipmh.arxiv.org/oai")[0] == "arxiv"
    assert c.bucket("https://alphaxiv.org/x")[0] == "alphaxiv.org"


def test_retry_after_is_honoured_then_succeeds():
    c, clock = client([FakeResponse(429, {"Retry-After": "7"}), FakeResponse(200)])
    r = c.get("https://api.test/x")
    assert r.ok and r.attempts == 2 and 7.0 in clock.sleeps


def test_long_retry_after_fails_instead_of_stalling():
    c, _ = client([FakeResponse(429, {"Retry-After": "3600"})])
    r = c.get("https://api.test/x")
    assert not r.ok and r.status == 429 and "exceeds cap" in r.error


def test_client_errors_are_not_retried_and_never_raise():
    c, _ = client([FakeResponse(404)])
    r = c.get("https://api.test/missing")
    assert (r.ok, r.status, r.attempts, r.error) == (False, 404, 1, "HTTP 404")


def test_conditional_headers_are_sent_and_304_is_reported():
    c, _ = client([FakeResponse(304)])
    r = c.get("https://api.test/feed", etag='"abc"', last_modified="Wed, 21 Oct 2026 07:28:00 GMT")
    sent = c.session.calls[0][2]
    assert sent["If-None-Match"] == '"abc"' and "If-Modified-Since" in sent
    assert r.not_modified


def test_path_prefix_buckets_take_precedence_in_order():
    c, _ = client([])
    assert c.bucket("https://huggingface.co/papers/2501.12948") == ("hf-pages", 3.5)
    assert c.bucket("https://huggingface.co/api/papers/2501.12948") == ("hf", 1.0)
    assert c.bucket("https://huggingface.co/papersx") == ("hf-pages", 3.5)   # prefix match, like robots paths
