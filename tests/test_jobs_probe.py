import json
from datetime import datetime, timezone

import pytest

from debrief.http import Fetched
from debrief.jobs import config as jconfig, probe

NOW = datetime(2026, 11, 4, 20, 0, tzinfo=timezone.utc)


class StubClient:
    """Answers from a {url: Fetched} map (or one Fetched for everything); records requests."""

    def __init__(self, answers, robots=True):
        self.answers, self.robots, self.requests = answers, robots, []

    def robots_allowed(self, url):
        return self.robots

    def request(self, method, url, params=None, json_body=None, headers=None, etag=None, last_modified=None):
        self.requests.append({"method": method, "url": url, "params": params, "etag": etag})
        if etag and isinstance(self.answers, dict) and "conditional" in self.answers:
            return self.answers["conditional"]
        return self.answers.get(url) if isinstance(self.answers, dict) else self.answers

    def bucket(self, url):
        return "x", 0.5


def fetched(body, status=200, headers=None, error=""):
    if not isinstance(body, bytes):
        body = json.dumps(body).encode()
    return Fetched(url="https://x.test/", final_url="https://x.test/", status=status,
                   headers={"content-type": "application/json", **(headers or {})}, content=body, error=error)


@pytest.mark.parametrize("value,days", [
    ("2026-11-04T08:00:00Z", 0.5), (1762286400, 0.0), (1762286400000, 0.0), ("1762286400", 0.0),
    ("Posted Today", 0.0), ("3 days ago", 3), ("12 hours ago", 0.5), ("Yesterday", 1.0),
    ("Tue, 03 Nov 2026 20:00:00 GMT", 1.0), (None, None), ("soon", None), (True, None),
])
def test_age_days(value, days):
    now = datetime(2025, 11, 4, 20, 0, tzinfo=timezone.utc) if isinstance(value, int) or (
        isinstance(value, str) and value.isdigit()) else NOW
    got = probe.age_days(value, now)
    assert (got is None and days is None) or abs(got - days) < 0.01


def test_md_rows_skips_headers_and_separators():
    text = "intro\n| Company | Role | Link |\n|---|---|---|\n| Acme | SWE | [Apply](https://jobs.lever.co/acme/1) |\n" \
           "| Beta | ML | <a href=\"https://jobs.ashbyhq.com/beta/2\">Apply</a> |\n\n| A | B |\n| :-- | --: |\n| x | y |\n"
    rows = probe.md_rows(text)
    assert len(rows) == 3
    assert probe.board_links(rows) == {"lever:acme": 1, "ashby:beta": 1}


def test_json_probe_fill_dates_and_conditional():
    items = [{"title": "SWE", "date_posted": 1762268400, "company_name": "Acme", "url": "https://jobs.lever.co/acme/1"},
             {"title": "ML", "date_posted": 1761000000, "company_name": "", "url": None}]
    stub = StubClient({"https://x.test/l.json": fetched(items, headers={"etag": '"v1"'}),
                       "conditional": fetched(b"", status=304)})
    spec = {"kind": "json", "items": "", "title": "title", "date": "date_posted",
            "fields": {"company": "company_name", "url": "url"}}
    now = datetime(2025, 11, 4, 20, 0, tzinfo=timezone.utc)
    att, content = probe.probe_url(stub, "https://x.test/l.json", spec, now=now)
    assert (att.status, att.n_items, att.sample_title) == ("ok", 2, "SWE")
    assert att.fill == {"company": 0.5, "url": 0.5}
    assert att.last_24h == 1 and att.newest.startswith("2025-11-04")
    assert att.conditional == "304" and stub.requests[1]["etag"] == '"v1"'
    assert content is not None


def test_json_probe_finds_unknown_list():
    body = {"data": {"meta": 1, "positions": [{"name": "a"}, {"name": "b"}]}, "other": [{"x": 1}]}
    att, _ = probe.probe_url(StubClient(fetched(body)), "https://x.test/", {"kind": "json"}, now=NOW)
    assert att.status == "ok" and att.n_items == 2 and att.items_path == "data.positions"


def test_robots_block_means_no_request():
    stub = StubClient(fetched([]), robots=False)
    att, _ = probe.probe_url(stub, "https://x.test/", {"kind": "json"}, now=NOW)
    assert att.status == "blocked" and stub.requests == []
    stub = StubClient(fetched([]), robots=None)
    assert probe.probe_url(stub, "https://x.test/", {"kind": "json"}, now=NOW)[0].status == "blocked"


def test_secrets_never_reach_the_record():
    err = "ConnectionError: Max retries exceeded with url: /search?api_key=sekrit-123"
    stub = StubClient(fetched(b"", status=0, error=err))
    att, _ = probe.probe_url(stub, "https://x.test/", {"kind": "json"}, secrets=["sekrit-123"], now=NOW)
    assert "sekrit" not in att.error and "<redacted>" in att.error


def _src(**kw):
    base = {"id": "s", "name": "S", "family": "api", "url": "https://x.test/"}
    return jconfig._source({**base, **kw}, set())


def test_missing_key_is_not_requested():
    stub = StubClient(fetched([]))
    src = _src(auth={"params": {"app_id": "ADZ_ID", "app_key": "ADZ_KEY"}})
    r = probe.probe_source(stub, src, env={"ADZ_ID": "1"}, now=NOW)
    assert r.status == "needs_key" and "ADZ_KEY" in r.summary and stub.requests == []
    r = probe.probe_source(stub, src, env={"ADZ_ID": "1", "ADZ_KEY": "abcd"}, now=NOW)
    assert stub.requests[-1]["params"] == {"app_id": "1", "app_key": "abcd"}


def test_candidates_stop_at_first_ok_and_each_needs_all():
    good = fetched({"jobs": [{"t": 1}]})
    stub = StubClient({"https://a.test/": fetched(b"", status=404, error="HTTP 404"), "https://b.test/": good})
    src = _src(url=None, candidates=[{"url": "https://a.test/", "probe": {"items": "jobs"}},
                                     {"url": "https://b.test/", "probe": {"items": "jobs"}},
                                     {"url": "https://c.test/"}])
    r = probe.probe_source(stub, src, env={}, now=NOW)
    assert r.status == "ok" and r.endpoint == "https://b.test/" and len(stub.requests) == 2
    stub = StubClient({"https://x.test/a": good, "https://x.test/b": fetched(b"", status=500, error="HTTP 500")})
    r = probe.probe_source(stub, _src(url="https://x.test/{each}", each=["a", "b"], probe={"items": "jobs"}), env={}, now=NOW)
    assert r.status == "partial" and r.summary == "1/2 ok"


class FakeIMAP:
    def __init__(self, host, port, timeout):
        self.calls = []

    def login(self, u, p):
        if p != "app-pass":
            raise Exception(f"[AUTHENTICATIONFAILED] for {u}")

    def select(self, box, readonly):
        assert readonly is True
        return "OK", [b"12"]

    def search(self, charset, *crit):
        return "OK", [b"1 2 3" if crit[1] == '"linkedin.com"' else b""]

    def logout(self):
        pass


def test_imap_counts_senders_without_reading_bodies():
    spec = {"senders": {"linkedin": "linkedin.com", "indeed": "indeed.com"}}
    att = probe.probe_imap(spec, "me@gmail.com", "app-pass", connect=FakeIMAP)
    assert att.status == "ok" and att.extra == {"inbox_total": 12, "by_sender": {"linkedin": 3, "indeed": 0}}
    att = probe.probe_imap(spec, "me@gmail.com", "wrong", connect=FakeIMAP)
    assert att.status == "failed" and "me@gmail.com" not in att.error


def test_universe_and_discovery():
    srcs = [_src(id="uk", family="universe", url="https://x.test/{each}.csv", each=["ashby", "workday"], probe={"kind": "csv"}),
            _src(id="ur", family="universe", url="https://x.test/{each}.json", each=["ashby"], probe={"kind": "json"}),
            _src(id="li", family="list", probe={"kind": "json"})]
    bodies = {"uk": {"ashby": b"name,slug,url\nA,acme,https://jobs.ashbyhq.com/acme\n",
                     "workday": b"name,slug,url\nN,n/s,https://nv.wd5.myworkdayjobs.com/Site\n"},
              "ur": {"ashby": json.dumps(["Acme", "beta"]).encode()},
              "li": {None: json.dumps([{"url": "https://jobs.ashbyhq.com/acme/1", "active": True},
                                       {"url": "https://jobs.lever.co/newco/2", "active": True},
                                       {"url": "https://careers.google.com/x", "active": True},
                                       {"url": "https://jobs.lever.co/old/3", "active": False}]).encode()}}
    uni, per = probe.universe_from_bodies(bodies, srcs)
    assert uni["ashby"] == {"ashby:acme", "ashby:beta"} and uni["workday"] == {"workday:nv|wd5|Site"}
    assert per["ashby"] == {"uk": 1, "ur": 2}
    d = probe.discovery(bodies, srcs, uni)
    assert d["listings"] == 3 and d["listings_on_a_board_system"] == 2
    assert d["by_system"] == {"ashby": {"linked": 1, "not_in_universe": 0}, "lever": {"linked": 1, "not_in_universe": 1}}


def test_summarize_extrapolates():
    rows = [{"board": "greenhouse:a", "status": "ok", "elapsed_s": 0.2, "bytes": 10000, "jobs": 10, "pages": 1,
             "new_24h": 2, "new_24h_rough": 1, "validators": False},
            {"board": "greenhouse:b", "status": "dead", "http": 404}]
    s = probe.summarize("greenhouse", 1000, rows, StubClient(None))
    assert s["alive_share"] == 0.5 and s["est_alive_boards"] == 500
    assert s["est_requests"] == 501 and s["est_minutes"] == round(501 * 0.5 / 60, 1)
    assert s["est_new_24h"] == 1000 and s["est_new_24h_rough"] == 500 and s["est_mb"] == 5.0


def test_real_config_loads():
    cfg = jconfig.load(env={"DEBRIEF_DATA_DIR": "/tmp/x", "DEBRIEF_CONTACT": "me@example.com"})
    assert cfg.user_agent == "debrief-jobs/0.1 (personal job search; me@example.com)"
    assert str(cfg.data_dir) == "/tmp/x/jobs"
    assert {s.family for s in cfg.sources} == jconfig.FAMILIES
    assert cfg.source("adzuna").auth_env() == ["ADZUNA_APP_ID", "ADZUNA_APP_KEY"]


def test_skip_carries_the_last_result_over(tmp_path, monkeypatch):
    cfg = jconfig.load(env={"DEBRIEF_DATA_DIR": str(tmp_path)})
    (cfg.data_dir / "probe").mkdir(parents=True)
    old = {"id": "jobicy", "name": "Jobicy", "family": "api", "enabled": True, "status": "ok", "endpoint": "e",
           "attempts": [], "summary": "", "notes": "", "terms": ""}
    (cfg.data_dir / "probe" / "probe-20261104T200000Z.json").write_text(
        json.dumps({"generated_at": "2026-11-04T20:00:00Z", "sources": [old]}))
    monkeypatch.setattr(probe, "Client", lambda *a, **k: StubClient(fetched([])))
    report = probe.run(cfg, only={"jobicy"}, skip={"jobicy"}, log=lambda *a, **k: None)
    (r,) = report["sources"]
    assert r["status"] == "ok" and "carried from the probe at 2026-11-04T20:00:00Z" in r["summary"]
