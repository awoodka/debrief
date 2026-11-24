"""How the watchlist reads a Workday board: every page, or through searches (options.workday_search); a read that
stops before the board's end, or that only searched, closes nothing. And how a Cloudflare challenge is reported."""
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from debrief.http import Client, Fetched
from debrief.jobs import collect, config as jconfig, profile
from debrief.jobs.store import JobsStore, state_path
from test_http import CHALLENGE, FakeResponse, FakeSession
from test_jobs_collect import _cfg

BIG = "workday:big|wd1|Ext"
DAY = "2026-11-05"


def wd_url(board):
    tenant, wd, site = board.split(":", 1)[1].split("|")
    return f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"


def job(n, posted="Posted 2 Days Ago", title=None):
    return {"title": title or f"Engineer {n}", "externalPath": f"/job/NY/Engineer_R{n}", "postedOn": posted,
            "locationsText": "New York, NY"}


class Boards:
    """Workday boards answer POSTs by offset from a list of jobs (newest first). A search (searchText) answers the
    jobs whose title holds each of its words; `totals` overrides the total a whole-board read reports. Greenhouse
    boards answer GETs. Counts requests per URL and keeps each Workday search asked."""
    user_agent = "t"

    def __init__(self, workday=None, greenhouse=None, totals=None):
        self.workday, self.greenhouse, self.totals = workday or {}, greenhouse or {}, totals or {}
        self.calls, self.searches = [], []

    def _ok(self, url, body):
        return Fetched(url=url, final_url=url, status=200, headers={"content-type": "application/json"},
                       content=json.dumps(body).encode())

    def post(self, url, json_body=None, headers=None):
        self.calls.append(url)
        q = json_body["searchText"].lower().split()
        self.searches.append(json_body["searchText"])
        jobs = [j for j in self.workday[url] if all(w in j["title"].lower().split() for w in q)]
        total = len(jobs) if q else self.totals.get(url, len(jobs))
        start = json_body["offset"]
        return self._ok(url, {"total": total, "jobPostings": jobs[start:start + json_body["limit"]]})

    def get(self, url, params=None, headers=None, etag=None, last_modified=None):
        self.calls.append(url)
        return self._ok(url, self.greenhouse[url])

    def requests(self, url):
        return self.calls.count(url)


def setup(tmp_path, monkeypatch, boards, max_age_days=30, **options):
    """The watchlist alone, watching `boards`, with `options` over its sources.yml options."""
    cfg = _cfg(tmp_path, monkeypatch, {"watchlist"})
    wl = cfg.source("watchlist")
    cfg = jconfig.JobsConfig(settings=cfg.settings, sources=(replace(wl, options={**wl.options, **options}),),
                             data_dir=cfg.data_dir, contact=cfg.contact)
    prof = profile.latest(cfg)[1]
    prof["companies"] = [{"name": b.split(":")[1].split("|")[0].title(), "board": b} for b in boards]
    prof["filters"]["max_age_days"] = max_age_days
    profile.save(cfg, prof)
    return cfg, JobsStore(state_path(cfg))


def night(cfg, store, client, n):
    source = next(s for s in cfg.sources if s.id == "watchlist")
    return collect.collect_source(cfg, store, client, source, f"run-{DAY}-{n}", DAY, datetime.now(timezone.utc),
                                  profile.latest(cfg)[1], {}, lambda *a, **k: None)


def open_keys(store):
    return {r[0] for r in store.q("SELECT i.item_key FROM items i JOIN postings p USING (posting_id) WHERE p.closed_at IS NULL")}


@pytest.mark.parametrize("total", [2066, 2000])         # past max_pages; or Workday's 2,000 ceiling on `total`
def test_a_board_longer_than_the_read_closes_nothing(tmp_path, monkeypatch, total):
    cfg, s = setup(tmp_path, monkeypatch, [BIG], workday_search=[])
    old = [job(n) for n in range(40)]
    night(cfg, s, Boards({wd_url(BIG): old}), 1)
    grown = [job(n, "Posted Today") for n in range(1000, 3026)] + old[1:]
    client = Boards({wd_url(BIG): grown}, totals={wd_url(BIG): total})
    out = night(cfg, s, client, 2)
    assert client.requests(wd_url(BIG)) == 100 and out["counts"].get("closed", 0) == 0
    assert "workday:big/R0" in open_keys(s)
    s.close()


def test_an_empty_search_list_reads_every_page_and_closes_what_left_the_board(tmp_path, monkeypatch):
    cfg, s = setup(tmp_path, monkeypatch, [BIG], workday_search=[])
    night(cfg, s, Boards({wd_url(BIG): [job(n) for n in range(30)]}), 1)
    client = Boards({wd_url(BIG): [job(n) for n in range(1, 30)]})
    out = night(cfg, s, client, 2)
    assert client.requests(wd_url(BIG)) == 2 and set(client.searches) == {""}
    assert out["counts"]["closed"] == 1 and "workday:big/R0" not in open_keys(s)
    s.close()


def test_searches_read_only_the_jobs_they_find_once_each_and_close_nothing(tmp_path, monkeypatch):
    cfg, s = setup(tmp_path, monkeypatch, [BIG], workday_search=["junior", "new grad"], search_max_pages=10)
    board = [job(1, title="Junior Software Engineer"), job(2, title="Software Engineer, New Grad"),
             job(3, title="Junior Developer, New Grad"), job(4, title="Senior Software Engineer")]
    client = Boards({wd_url(BIG): board})
    out = night(cfg, s, client, 1)
    assert client.searches == ["junior", "new grad"]
    assert open_keys(s) == {"workday:big/R1", "workday:big/R2", "workday:big/R3"}
    assert out["counts"]["postings_new"] == 3
    out = night(cfg, s, Boards({wd_url(BIG): board[1:]}), 2)          # job 1 is no longer on the board
    assert out["counts"].get("closed", 0) == 0 and "workday:big/R1" in open_keys(s)
    s.close()


def test_a_search_pages_to_its_end_or_to_search_max_pages(tmp_path, monkeypatch):
    cfg, s = setup(tmp_path, monkeypatch, [BIG], workday_search=["junior", "associate"], search_max_pages=3)
    board = ([job(n, title=f"Junior Engineer {n}") for n in range(100)]
             + [job(n, title=f"Associate Engineer {n}") for n in range(100, 125)])
    client = Boards({wd_url(BIG): board})
    out = night(cfg, s, client, 1)
    assert client.searches == ["junior"] * 3 + ["associate"] * 2
    assert len(open_keys(s)) == 60 + 25
    assert out["warnings"] == ["Big: stopped after 3 pages of the search 'junior'"]
    s.close()


def test_a_cloudflare_challenge_is_named_and_ends_the_board_for_the_night(tmp_path, monkeypatch):
    cfg, s = setup(tmp_path, monkeypatch, [BIG], workday_search=["junior", "associate"])
    s.close()
    host = "big.wd1.myworkdayjobs.com"
    session = FakeSession([FakeResponse(429, {"Server": "cloudflare", "cf-ray": "a5f3-EWR", "cf-mitigated": "challenge"},
                                        CHALLENGE, url=wd_url(BIG))])
    client = Client("t", cfg.settings["http"], sleep=lambda s: None, session=session)
    report = collect.run(cfg, client=client, run_date=DAY, env={}, log=lambda *a, **k: None)
    wl = report["sources"][0]
    assert len(session.calls) == 1
    assert wl["errors"] == [f"{wd_url(BIG)}: Cloudflare challenge, HTTP 429 (a block: {host} not asked again this run)"]
    assert f"{host} blocked us (Cloudflare challenge, HTTP 429); not asked again this run" in wl["warnings"]
    assert wl["blocked"] == [{"host": host, "why": "Cloudflare challenge, HTTP 429", "by": "watchlist", "skipped": 1}]
    assert "Cloudflare challenge" in collect.render(report)
