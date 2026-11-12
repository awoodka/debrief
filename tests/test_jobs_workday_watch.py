"""A Workday board read that stops before the board's end closes nothing."""
import json
from datetime import datetime, timezone

import pytest

from debrief.http import Fetched
from debrief.jobs import collect, profile
from debrief.jobs.store import JobsStore, state_path
from test_jobs_collect import _cfg

BIG = "workday:big|wd1|Ext"
DAY = "2026-11-05"


def wd_url(board):
    tenant, wd, site = board.split(":", 1)[1].split("|")
    return f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"


def job(n, posted="Posted 2 Days Ago"):
    return {"title": f"Engineer {n}", "externalPath": f"/job/NY/Engineer_R{n}", "postedOn": posted,
            "locationsText": "New York, NY"}


class Boards:
    """Workday boards answer POSTs by offset from a list of jobs (newest first), with `total` on every
    page unless one is given; Greenhouse boards answer GETs. Counts requests per URL."""
    user_agent = "t"

    def __init__(self, workday=None, greenhouse=None, totals=None):
        self.workday, self.greenhouse, self.totals = workday or {}, greenhouse or {}, totals or {}
        self.calls = []

    def robots_allowed(self, url):
        return True

    def _ok(self, url, body):
        return Fetched(url=url, final_url=url, status=200, headers={"content-type": "application/json"},
                       content=json.dumps(body).encode())

    def post(self, url, json_body=None, headers=None):
        self.calls.append(url)
        jobs = self.workday[url]
        start = json_body["offset"]
        return self._ok(url, {"total": self.totals.get(url, len(jobs)), "jobPostings": jobs[start:start + json_body["limit"]]})

    def get(self, url, params=None, headers=None, etag=None, last_modified=None):
        self.calls.append(url)
        return self._ok(url, self.greenhouse[url])

    def requests(self, url):
        return self.calls.count(url)


def setup(tmp_path, monkeypatch, boards, max_age_days=30):
    cfg = _cfg(tmp_path, monkeypatch, {"watchlist"})
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
    cfg, s = setup(tmp_path, monkeypatch, [BIG])
    old = [job(n) for n in range(40)]
    night(cfg, s, Boards({wd_url(BIG): old}), 1)
    grown = [job(n, "Posted Today") for n in range(1000, 3026)] + old[1:]
    client = Boards({wd_url(BIG): grown}, totals={wd_url(BIG): total})
    out = night(cfg, s, client, 2)
    assert client.requests(wd_url(BIG)) == 100 and out["counts"].get("closed", 0) == 0
    assert "workday:big/R0" in open_keys(s)
    s.close()
