"""A job source answering 200 with something we can't read, as Workday's maintenance page did on 2026-11-07: that
one request failed, and the source's other answers still count. Nothing is concluded from it: no job closes, no
"gone" mark clears, no ETag is kept, and it's asked again next night."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from debrief.http import Fetched
from debrief.jobs import boards, build, collect, profile
from debrief.jobs.adapters import REGISTRY
from debrief.jobs.store import JobsStore, state_path
from test_jobs_collect import _cfg, it, rec, src
from test_jobs_finder import ASHBY, WD, BoardClient, _describe_night, _iso, _list, _night

MAINTENANCE_URL = "https://static.community.workday.com/maintenance-page.html"
MAINTENANCE = b'<!DOCTYPE html>\n<html lang="en" dir="ltr">\n<head><title>Workday is currently unavailable.</title></head></html>'
GH_JOB = "https://boards-api.greenhouse.io/v1/boards/acme/jobs/{}?pay_transparency=true"
GH_BOARD = "https://boards-api.greenhouse.io/v1/boards/{}/jobs?content=true&pay_transparency=true"
WD_JOB = "https://globex.wd1.myworkdayjobs.com/wday/cxs/globex/Ext/job/NY/SWE_R1"


class Outage(BoardClient):
    """These URLs redirect to the maintenance page, status 200; the rest answer as usual."""

    def __init__(self, pages, down=(), searches=None, etags=None):
        super().__init__(pages, searches)
        self.down, self.etags, self.sent_etag = set(down), etags or {}, {}

    def _answer(self, url, body):
        if url in self.down:
            return Fetched(url=url, final_url=MAINTENANCE_URL, status=200, content=MAINTENANCE,
                           headers={"content-type": "text/html", "etag": '"maintenance"'})
        out = super()._answer(url, body)
        if url in self.etags:
            out.headers["etag"] = self.etags[url]
        return out

    def get(self, url, params=None, headers=None, etag=None, last_modified=None):
        self.sent_etag[url] = etag
        return super().get(url, params, headers, etag, last_modified)


def three_jobs(cfg, s):
    """Two Greenhouse jobs and one Workday job, all waiting for a description. Returns their posting ids."""
    wd_url = "https://globex.wd1.myworkdayjobs.com/en-US/Ext/job/NY/SWE_R1"
    _list(s, [it("a", title="Software Engineer", url="https://job-boards.greenhouse.io/acme/jobs/1",
                 job_key="job:greenhouse:1", board="greenhouse:acme"),
              it("b", title="Software Engineer", company="Globex", url=wd_url,
                 job_key="job:workday:globex/R1", board="workday:globex|wd1|Ext"),
              it("c", title="Software Engineer, Platform", url="https://job-boards.greenhouse.io/acme/jobs/2",
                 job_key="job:greenhouse:2", board="greenhouse:acme")],
          _iso(datetime.now(timezone.utc) - timedelta(hours=1)), source_id="simplify-newgrad", snapshot="window")
    return [s.find_key(k) for k in ("job:greenhouse:1", "job:workday:globex/R1", "job:greenhouse:2")]


GOOD = {GH_JOB.format(1): {"id": 1, "title": "Software Engineer", "content": "Python and SQL"},
        GH_JOB.format(2): {"id": 2, "title": "Software Engineer, Platform", "content": "Spark"}}


def test_a_maintenance_page_costs_only_its_own_job(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"describe", "simplify-newgrad"})
    s = JobsStore(state_path(cfg))
    a, b, c = three_jobs(cfg, s)
    out = _describe_night(cfg, s, Outage(GOOD, down={WD_JOB}))
    assert out["status"] == "partial"
    assert s.texts_for(a) == "Python and SQL" and s.texts_for(c) == "Spark" and s.texts_for(b) is None
    assert MAINTENANCE_URL in out["errors"][0] and WD_JOB in out["errors"][0]
    assert "crashed" not in out["errors"][0]
    s.close()


def test_the_job_is_asked_again_and_described_once_the_board_is_back(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"describe", "simplify-newgrad"})
    s = JobsStore(state_path(cfg))
    _, b, _ = three_jobs(cfg, s)
    _describe_night(cfg, s, Outage(GOOD, down={WD_JOB}))
    back = Outage({WD_JOB: {"jobPostingInfo": {"jobDescription": "<p>Backend work</p>", "timeType": "Full time"}}})
    out = _describe_night(cfg, s, back)
    assert [c[1] for c in back.calls] == [WD_JOB]                          # the others have theirs already
    assert out["status"] == "ok" and s.texts_for(b) == "Backend work"
    s.close()


def test_a_maintenance_page_says_nothing_about_a_job_marked_gone(store_with_gone):
    s, pid, wd_rec = store_with_gone
    wd_rec.update(body=MAINTENANCE.decode(), final_url=MAINTENANCE_URL)
    with s.transaction():
        build.apply(s, src(id="describe", family="detail", url=None), REGISTRY["describe"], [wd_rec], ["ref#0"])
    assert s.posting(pid)["data"].get("gone")


def test_a_readable_answer_still_clears_the_gone_mark(store_with_gone):
    s, pid, wd_rec = store_with_gone
    with s.transaction():
        build.apply(s, src(id="describe", family="detail", url=None), REGISTRY["describe"], [wd_rec], ["ref#0"])
    assert not s.posting(pid)["data"].get("gone")


@pytest.fixture
def store_with_gone(tmp_path):
    s = JobsStore(tmp_path / "j.sqlite")
    _list(s, [it("b", title="Software Engineer", job_key="job:workday:globex/R1", board="workday:globex|wd1|Ext",
                 url="https://globex.wd1.myworkdayjobs.com/en-US/Ext/job/NY/SWE_R1")],
          "2026-11-05T08:00:00Z", source_id="simplify-newgrad", snapshot="window")
    pid = s.find_key("job:workday:globex/R1")
    p = s.posting(pid)
    p["data"]["gone"] = {"at": "2026-11-06T08:00:00Z", "status": 404, "url": WD_JOB}
    with s.transaction():
        s.save_posting(p)
    answer = {"jobPostingInfo": {"jobDescription": "<p>Backend work</p>"}}
    yield s, pid, rec(answer, url=WD_JOB, meta={"system": "workday", "board": "workday:globex|wd1|Ext",
                                                "wanted": ["workday:globex/R1"]})
    s.close()


def two_boards(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"watchlist"})
    prof = profile.latest(cfg)[1]
    prof["companies"] = [{"name": "Acme", "board": "greenhouse:acme"}, {"name": "Beta", "board": "greenhouse:beta"}]
    profile.save(cfg, prof)
    return cfg, JobsStore(state_path(cfg))


def board(*ids):
    return {"jobs": [{"id": i, "title": f"Engineer {i}", "first_published": "2026-11-03T10:00:00Z", "location": {"name": "New York, NY"},
                      "absolute_url": f"https://job-boards.greenhouse.io/x/jobs/{i}"} for i in ids]}


def watchlist_night(cfg, s, client, when):
    source = next(x for x in cfg.sources if x.id == "watchlist")
    return collect.collect_source(cfg, s, client, source, f"run-{when}", when[:10], datetime.now(timezone.utc),
                                  profile.latest(cfg)[1], {}, lambda *a, **k: None)


def test_a_maintenance_page_closes_no_job_and_the_other_board_still_counts(tmp_path, monkeypatch):
    cfg, s = two_boards(tmp_path, monkeypatch)
    acme, beta = GH_BOARD.format("acme"), GH_BOARD.format("beta")
    assert watchlist_night(cfg, s, Outage({acme: board(1, 2, 3), beta: board(4)}), "2026-11-05T08:00:00Z")["status"] == "ok"
    out = watchlist_night(cfg, s, Outage({beta: board(4, 5)}, down={acme}), "2026-11-06T08:00:00Z")
    assert out["status"] == "partial"
    open_now = {r[0] for r in s.q("SELECT i.item_key FROM items i JOIN postings p ON p.posting_id=i.posting_id WHERE p.closed_at IS NULL")}
    assert open_now == {"greenhouse:1", "greenhouse:2", "greenhouse:3", "greenhouse:4", "greenhouse:5"}
    s.close()


def test_a_board_that_really_dropped_a_job_still_closes_it(tmp_path, monkeypatch):
    cfg, s = two_boards(tmp_path, monkeypatch)
    acme, beta = GH_BOARD.format("acme"), GH_BOARD.format("beta")
    watchlist_night(cfg, s, Outage({acme: board(1, 2), beta: board(4)}), "2026-11-05T08:00:00Z")
    watchlist_night(cfg, s, Outage({acme: board(1), beta: board(4)}), "2026-11-06T08:00:00Z")
    closed = {r[0] for r in s.q("SELECT i.item_key FROM items i JOIN postings p ON p.posting_id=i.posting_id WHERE p.closed_at IS NOT NULL")}
    assert closed == {"greenhouse:2"}
    s.close()


def test_an_unreadable_answer_keeps_no_etag(tmp_path, monkeypatch):
    cfg, s = two_boards(tmp_path, monkeypatch)
    acme, beta = GH_BOARD.format("acme"), GH_BOARD.format("beta")
    good = Outage({acme: board(1), beta: board(4)}, etags={acme: '"acme-1"', beta: '"beta-1"'})
    watchlist_night(cfg, s, good, "2026-11-05T08:00:00Z")
    watchlist_night(cfg, s, Outage({beta: board(4)}, down={acme}, etags={beta: '"beta-2"'}), "2026-11-06T08:00:00Z")
    next_night = Outage({acme: board(1), beta: board(4)})
    watchlist_night(cfg, s, next_night, "2026-11-07T08:00:00Z")
    assert next_night.sent_etag[acme] == '"acme-1"'                        # not the maintenance page's
    assert next_night.sent_etag[beta] == '"beta-2"'                        # a board that answered keeps its own
    s.close()


def test_the_only_answer_being_unreadable_fails_that_source_without_a_crash(tmp_path, monkeypatch):
    cfg, s = two_boards(tmp_path, monkeypatch)
    acme, beta = GH_BOARD.format("acme"), GH_BOARD.format("beta")
    out = watchlist_night(cfg, s, Outage({}, down={acme, beta}), "2026-11-06T08:00:00Z")
    assert out["status"] == "failed" and MAINTENANCE_URL in out["errors"][0] and "crashed" not in out["errors"][0]
    s.close()


def test_the_finder_looks_again_after_a_maintenance_page(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {build.FINDER, "jobright-newgrad"})
    s = JobsStore(state_path(cfg))
    now = datetime.now(timezone.utc)
    with s.transaction():
        s.save_directory("workday:globex|wd1|Ext", "Globex", "globex", "universe", _iso(now))
    _list(s, [it("a", title="Software Engineer", company="Globex", url="https://jobright.ai/jobs/info/1", scope="list")],
          _iso(now - timedelta(hours=1)))
    down = Outage({}, down={WD})
    out = _night(cfg, s, down, _iso(now))
    assert len(down.calls) == 1 and out["status"] == "failed" and MAINTENANCE_URL in out["errors"][0]
    up = Outage({}, searches={(WD, "software engineer"): {"total": 0, "jobPostings": []}})
    _night(cfg, s, up, _iso(now))
    assert len(up.calls) == 1                                              # the page wasn't taken for a search
    up.calls.clear()
    _night(cfg, s, up, _iso(now))
    assert up.calls == []                                                  # a real search is remembered
    s.close()


def test_replay_gets_through_a_night_with_a_maintenance_page(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"watchlist", "describe"})
    s = JobsStore(state_path(cfg))
    day = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    listed = {"jobs": [{"id": i, "title": "Software Engineer", "first_published": f"{day}T01:00:00Z", "location": {"name": "New York, NY"},
                        "absolute_url": f"https://job-boards.greenhouse.io/acme/jobs/{i}"} for i in (1, 2)]}
    watchlist_night(cfg, s, Outage({GH_BOARD.format("acme"): listed}), f"{day}T08:00:00Z")
    source = next(x for x in cfg.sources if x.id == "describe")
    collect.collect_source(cfg, s, Outage(GOOD, down={GH_JOB.format(2)}), source, f"run-describe-{day}", day, datetime.now(timezone.utc),
                           profile.latest(cfg)[1], {}, lambda *a, **k: None)
    assert s.texts_for(s.find_key("job:greenhouse:1")) == "Python and SQL"
    s.close()
    out = collect.replay(cfg, day, tmp_path / "replayed.sqlite", log=lambda *a, **k: None)
    assert out["totals"]["texts"] == 1 and out["totals"]["unreadable"] == 1      # a replay has no report, so it says so here
    r = JobsStore(tmp_path / "replayed.sqlite")
    assert r.texts_for(r.find_key("job:greenhouse:1")) == "Python and SQL" and r.texts_for(r.find_key("job:greenhouse:2")) is None
    r.close()


def test_a_bad_page_during_fetch_keeps_what_was_read_before_it(tmp_path, monkeypatch):
    cfg, s = two_boards(tmp_path, monkeypatch)
    prof = profile.latest(cfg)[1]
    prof["companies"] = [{"name": "Acme", "board": "greenhouse:acme"}, {"name": "Globex", "board": "workday:globex|wd1|Ext"}]
    profile.save(cfg, prof)
    out = watchlist_night(cfg, s, Outage({GH_BOARD.format("acme"): board(1)}, down={WD}), "2026-11-06T08:00:00Z")
    assert out["status"] == "partial"
    assert [r[0] for r in s.q("SELECT item_key FROM items")] == ["greenhouse:1"]
    s.close()


@pytest.mark.parametrize("system", ["greenhouse", "lever", "ashby", "workable", "smartrecruiters", "recruitee", "workday"])
@pytest.mark.parametrize("answer", [{"ok": False, "error": "try later"}, "try later", None, {"jobs": "try later", "offers": None, "content": 1, "jobPostings": 1},
                                    {"jobs": [1], "offers": [1], "content": [1], "jobPostings": [1]}, [1, 2]])
def test_every_board_calls_the_wrong_json_shape_unreadable(system, answer):
    with pytest.raises(ValueError):
        boards.parse_list(system, answer)


@pytest.mark.parametrize("second,url", [("greenhouse:wrong", GH_BOARD.format("wrong")), ("ashby:wrong", boards.list_request("ashby:wrong", full=True)[1]),
                                        ("lever:wrong", boards.list_request("lever:wrong", full=True)[1])])
def test_a_200_with_the_wrong_json_shape_is_a_failed_request_not_a_crash(tmp_path, monkeypatch, second, url):
    cfg, s = two_boards(tmp_path, monkeypatch)
    prof = profile.latest(cfg)[1]
    prof["companies"] = [{"name": "Acme", "board": "greenhouse:acme"}, {"name": "Wrong", "board": second}]
    profile.save(cfg, prof)
    out = watchlist_night(cfg, s, Outage({GH_BOARD.format("acme"): board(1), url: {"ok": False}}), "2026-11-06T08:00:00Z")
    assert out["status"] == "partial" and "crashed" not in out["errors"][0] and "ValueError" in out["errors"][0]
    assert [r[0] for r in s.q("SELECT item_key FROM items")] == ["greenhouse:1"]
    s.close()


def test_the_finder_gets_through_the_wrong_json_shape_and_looks_again(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {build.FINDER, "jobright-newgrad"})
    s = JobsStore(state_path(cfg))
    now = datetime.now(timezone.utc)
    with s.transaction():
        s.save_directory("ashby:acme", "Acme", "acme", "universe", _iso(now))
    _list(s, [it("a", title="Software Engineer, Data", url="https://jobright.ai/jobs/info/1", scope="list")], _iso(now - timedelta(hours=1)))
    out = _night(cfg, s, Outage({ASHBY: {"ok": False}}), _iso(now))
    assert out["status"] == "failed" and "crashed" not in out["errors"][0]
    up = Outage({ASHBY: {"jobs": []}})
    _night(cfg, s, up, _iso(now))
    assert up.calls == [("GET", ASHBY)]
    s.close()


def test_an_unreadable_company_list_costs_only_that_list(tmp_path):
    s = JobsStore(tmp_path / "j.sqlite")
    bad = rec("<html>maintenance</html>", url="https://x.test/ashby.json", each="ashby", seq=1)
    good = rec(["acme"], url="https://x.test/greenhouse.json", each="greenhouse", seq=0)
    with s.transaction():
        counts = build.apply(s, src(id="universe-x", family="universe"), REGISTRY["directory"], [good, bad], ["r#0", "r#1"])
    assert counts["directory"] == 1 and [r["board"] for r in s.directory_by_name("acme")] == ["greenhouse:acme"]
    s.close()


def test_a_bug_in_our_own_code_is_still_a_crash():
    class Broken:
        snapshot = "window"

        def parse(self, record, source):
            raise TypeError("a real bug")

    with pytest.raises(TypeError):
        build.apply(None, src(), Broken(), [rec("{}")], ["ref#0"])
