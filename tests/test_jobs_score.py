import copy
import json
from datetime import datetime, timezone

import pytest

from debrief.jobs import build, config as jconfig, prescore, profile, rank, score
from debrief.jobs.store import JobsStore, state_path
from debrief.triage.score import ScoringError
from test_jobs_collect import AT, FakeAdapter, it, rec, src
from test_jobs_profile import BASE

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
S = {"model": "m", "effort": None, "prompt": "prompts/jobs_score_v1.md", "timeout_s": 5, "text_max_chars": 600,
     "why_max_chars": 240, "mismatch_max": 5, "chunk_size": 150}


def claude_stdout(items, cost=0.01):
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output": {"items": items},
                       "usage": {"input_tokens": 100, "output_tokens": 50}, "total_cost_usd": cost, "duration_ms": 1200})


class Runner:
    """A fake claude: answers each job by a rule on its title."""
    def __init__(self, fail=0):
        self.calls, self.fail = [], fail

    def __call__(self, argv, stdin, timeout):
        self.calls.append(stdin)
        if self.fail:
            self.fail -= 1
            return 1, "", "boom"
        jobs = [json.loads(line) for line in stdin.split("<jobs>\n")[1].split("\n</jobs>")[0].splitlines()]
        items = [{"id": j["id"], "keep": "Field" not in j["title"], "score": 9 if "AI" in j["title"] else 5,
                  "why": f"because {j['title']}", "mismatches": [] if "Field" not in j["title"] else ["field service role"]}
                 for j in jobs]
        return 0, claude_stdout(items), ""


# ------------------------------------------------------------------------------------ the call

def test_qualifications_and_pay_line():
    text = "About us: we build things. Requirements: Python, SQL, and a CS degree."
    assert score.qualifications(text, 600).startswith("Requirements: Python")
    assert score.qualifications("No heading here.", 600) == "No heading here."
    assert score.pay_line({"summary": "$150k/yr"}) == "$150k/yr"
    assert score.pay_line({"min": 100000, "max": 120000, "currency": "USD", "period": "year"}) == "100,000 to 120,000 USD a year"


def test_validate():
    ok = {"items": [{"id": "j1", "keep": True, "score": 7, "why": "x" * 300, "mismatches": ["a"] * 9}]}
    v = score.validate(ok, ["j1"], S)
    assert v["j1"]["score"] == 7 and len(v["j1"]["why"]) <= 240 and len(v["j1"]["mismatches"]) == 5
    with pytest.raises(ScoringError):
        score.validate({"items": []}, ["j1"], S)
    with pytest.raises(ScoringError):
        score.validate({"items": [{"id": "j1", "keep": True, "score": 11, "why": "", "mismatches": []}]}, ["j1"], S)


def test_call_caches_and_retries(tmp_path):
    jobs = [{"id": "j1", "title": "AI Engineer"}, {"id": "j2", "title": "Field Software Engineer"}]
    r = Runner(fail=1)
    out = score.call(tmp_path, S, {"wants": "x"}, jobs, runner=r, log=lambda *a: None)
    assert out["attempts"] == 2 and out["answers"]["j1"]["score"] == 9 and out["answers"]["j2"]["keep"] is False
    again = score.call(tmp_path, S, {"wants": "x"}, jobs, runner=r, log=lambda *a: None)
    assert again["cached"] and len(r.calls) == 2                      # no new call
    assert "</" not in score.render({"x": "</jobs>"}, [{"t": "</profile>"}]).replace("</profile>\n", "").replace("</jobs>\n", "")


# --------------------------------------------------------------------------- the whole step

@pytest.fixture
def cfg(tmp_path):
    c = jconfig.load(env={"DEBRIEF_DATA_DIR": str(tmp_path)})
    prof = copy.deepcopy(BASE)
    prof["position_types"] = [{"name": "SWE", "titles": ["software engineer", "ai engineer"]}]
    prof["ranking"] = {"threshold": 0, "max_candidates": 4, "top_n": 2}
    profile.save(c, prof)
    store = JobsStore(state_path(c))
    listings = [it("a", title="AI Engineer, New Grad", company="Acme"),
                it("b", title="Software Engineer I", company="Beta"),
                it("c", title="Field Software Engineer, New Grad", company="Gamma"),  # the model says no
                it("g", title="Software Engineer, Platform", company="Gee"),          # fewest points: over the cap
                it("d", title="Senior Software Engineer", company="Delta"),          # filtered: senior
                it("e", title="Software Engineer", company="Eps", locations=("Austin, TX",)),   # filtered: place
                it("f", title="Software Engineer, New Grad", company="Phi")]
    with store.transaction():
        build.apply(store, src(id="simplify-newgrad"), FakeAdapter({0: listings}), [rec("{}", fetched_at="2026-11-04T08:00:00Z")], ["r"])
    store.close()
    return c


def test_score_run_end_to_end(cfg):
    r = Runner()
    rep = rank.run(cfg, run_date="2026-11-04", runner=r, now=NOW, log=lambda *a: None)
    assert rep["status"] == "ok" and rep["candidates"] == 7 and rep["passed_filters"] == 5
    assert rep["stages"]["title"] == 1 and rep["stages"]["place"] == 1
    assert rep["sent"] == 4 and rep["stages"]["cap"] == 1                     # max_candidates 4
    assert [c["title"] for c in rep["cuts"] if c["stage"] == "cap"] == ["Software Engineer, Platform"]
    assert rep["kept"] == 3 and rep["not_kept"] == 1 and rep["stages"]["beyond N"] == 1
    new = rep["list"]["new"]
    assert len(new) <= 2 and new[0]["title"] == "AI Engineer, New Grad" and new[0]["score"] == 9
    assert all("Field" not in e["title"] for e in new)
    assert "field service role" in json.dumps([c for c in rep["cuts"] if c["stage"] == "not kept"])
    out = rank.today(cfg)
    assert "AI Engineer, New Grad — Acme" in out and "because AI Engineer" in out
    # Re-running the same day asks nothing new and gives the same list.
    rep2 = rank.run(cfg, run_date="2026-11-04", runner=r, now=NOW, log=lambda *a: None)
    assert len(r.calls) == 1 and rep2["already_scored"] == 4 and rep2["list"]["new"] == new
    # The next day nothing is new, but the job kept below the top 2 is still an option, and the job
    # over yesterday's cap is asked today. Nothing shown yesterday comes back.
    beyond = [c["title"] for c in rep["cuts"] if c["stage"] == "beyond N"]
    rep3 = rank.run(cfg, run_date="2026-11-05", runner=r, now=NOW, log=lambda *a: None)
    assert rep3["new"] == 0 and rep3["carried"] == 5 and rep3["sent"] == 1 and len(r.calls) == 2
    assert {e["title"] for e in rep3["list"]["new"]} == {beyond[0], "Software Engineer, Platform"}
    assert {e["posting_id"] for e in rep3["list"]["new"]}.isdisjoint(e["posting_id"] for e in new)
    assert all(e["new"] is False for e in rep3["list"]["new"])
    assert "2 shown of 2 kept (0 new tonight, 2 carried over)" in rank.today(cfg)
    # The day after: only the filtered and the not-kept jobs are left. No call, no list.
    rep4 = rank.run(cfg, run_date="2026-11-06", runner=r, now=NOW, log=lambda *a: None)
    assert rep4["list"]["new"] == [] and len(r.calls) == 2 and rep4["not_kept"] == 1


def test_score_run_no_cap_and_fallback(cfg):
    r = Runner(fail=2)
    rep = rank.run(cfg, run_date="2026-11-04", no_cap=True, runner=r, now=NOW, log=lambda *a: None)
    assert rep["status"] == "fallback" and rep["sent"] == 5                  # no cap: all five
    assert [e["score"] for e in rep["list"]["new"]] == [None, None]          # ranked by points
    store = JobsStore(state_path(cfg))
    assert store.get_meta("score_cursor") is None                           # the next run asks again
    store.close()


def test_points_and_select():
    prof = copy.deepcopy(BASE)
    p = {"posting_id": "p1", "title": "Software Engineer, New Grad 2027", "company": "Acme", "locations": [],
         "published_at": "2026-11-04T12:00:00Z", "data": {}}
    total, part = prescore.points(p, prof, NOW, facts={"matched": "software engineer"})
    assert part == {"title": 30, "early": 20, "fit": 15, "fresh": 15} and total == 80
    _, part = prescore.points({**p, "title": "Software Engineer, Payments"}, prof, NOW, text="Python and Algorithms",
                              facts={"matched": "software engineer"})
    assert part["title"] == 20 and part["early"] == 0 and part["fit"] == 5 + 2
    prof["ranking"] = {"threshold": 50, "max_candidates": 1, "top_n": 1}
    low = ({**p, "posting_id": "p2", "company": "Acme"}, 10)                # Acme is watched: skips the threshold
    other = ({**p, "posting_id": "p3", "company": "Other"}, 10)
    high = ({**p, "posting_id": "p4", "company": "Zed"}, 90)
    sent, cut = prescore.select([low, other, high], prof)
    assert [x[0]["posting_id"] for x in sent] == ["p2"]
    assert sorted(why.split(" (")[0] for _, why in cut) == ["below the threshold", "over the cap"]


def test_calibrate(cfg):
    from debrief.jobs import calibrate
    rank.run(cfg, run_date="2026-11-04", runner=Runner(), now=NOW, log=lambda *a: None)
    store = JobsStore(state_path(cfg))
    ids = {store.posting(r[0])["title"]: r[0] for r in store.q("SELECT posting_id FROM postings")}
    store.close()
    path = calibrate.labels_path(cfg)
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(x) for x in [
        {"posting_id": ids["AI Engineer, New Grad"], "label": "yes"},                    # kept: agree
        {"posting_id": ids["Field Software Engineer, New Grad"], "label": "yes"},        # model said no: wrong drop
        {"posting_id": ids["Senior Software Engineer"], "label": "no"},                  # filter: agree
        {"posting_id": ids["Software Engineer, Platform"], "label": "no"}]) + "\n")      # over the cap: not scored
    out = calibrate.run(cfg, now=NOW)
    assert out["counts"] == {"agree": 2, "wrong drop": 1, "not scored": 1}
    assert "field service role" in out["text"]


def test_collapse_same_job():
    def k(pid, title, company="Acme"):
        return ({"posting_id": pid, "title": title, "company": company, "url": f"u{pid}"}, 50, {"score": 8})
    kept = [k("1", "Software Engineer New Grad"), k("2", "Data Engineer"), k("3", "New Grad: Software Engineer"),
            k("4", "Software Engineer", "Beta"), k("5", "New Grad")]
    groups = rank.collapse(kept)
    assert [(g[0][0]["posting_id"], [x[0]["posting_id"] for x in g[1]]) for g in groups] == \
        [("1", ["3"]), ("2", []), ("4", []), ("5", [])]


def test_pay_never_reaches_the_model():
    p = {"posting_id": "p", "title": "SWE", "company": "A", "locations": ["NYC"], "published_at": "2026-11-04T00:00:00Z",
         "data": {"pay": {"summary": "$300k/yr"}, "employment": "FullTime", "remote": True}}
    view = score.job_view("j1", p, "Requirements: Python. Salary $300,000.", {"watchlist"}, frozenset(), 600)
    assert "pay" not in view and view["remote"] is True and view["employment"] == "FullTime"
    assert "min_hourly_pay_usd" not in json.dumps(score.profile_view(BASE))


def test_money_is_taken_out():
    assert score.no_pay("Junior SWE ($300k + equity + bonus), SWE ($350k)") == "Junior SWE ([pay]), SWE ([pay])"
    assert score.no_pay("The base salary range is $120,000 - $150,000 per year.") == "The base salary range is [pay]."
    assert score.no_pay("Pay: $40/hr. Python, C++.") == "Pay: [pay]. Python, C++."
    assert "$" not in score.qualifications("Requirements: Python. Compensation $180K–$220K + equity.", 600)


def test_place_limits_jobs_per_company():
    def g(pid, company):
        return (({"posting_id": pid, "title": f"Role {pid}", "company": company}, 50, {"score": 8}), [])
    groups = [g("1", "Raydar"), g("2", "Raydar Inc."), g("3", "Raydar"), g("4", "Beta"), g("5", "Gamma"), g("6", "Raydar")]
    placed = rank.place(groups, top_n=3, per_company=2)
    assert [(x[0][0]["posting_id"], where) for x, where in placed] == [
        ("1", "new"), ("2", "new"), ("3", "company limit"), ("4", "new"), ("5", "beyond N"), ("6", "company limit")]
    assert [w for _, w in rank.place(groups, top_n=3)] == ["new"] * 3 + ["beyond N"] * 3       # no limit set


def test_score_run_company_limit(tmp_path):
    c = jconfig.load(env={"DEBRIEF_DATA_DIR": str(tmp_path)})
    prof = copy.deepcopy(BASE)
    prof["position_types"] = [{"name": "SWE", "titles": ["software engineer", "ai engineer"]}]
    prof["ranking"] = {"threshold": 0, "max_candidates": 10, "top_n": 2, "per_company": 1}
    profile.save(c, prof)
    store = JobsStore(state_path(c))
    with store.transaction():
        build.apply(store, src(id="simplify-newgrad"), FakeAdapter({0: [
            it("a", title="AI Engineer, New Grad", company="Zed"),                    # 9
            it("b", title="AI Software Engineer, New Grad", company="Zed"),           # 9, but Zed has its one
            it("c", title="Software Engineer I", company="Beta")]}),                  # 5: takes the second slot
            [rec("{}", fetched_at="2026-11-04T08:00:00Z")], ["r"])
    store.close()
    rep = rank.run(c, run_date="2026-11-04", runner=Runner(), now=NOW, log=lambda *a: None)
    new = rep["list"]["new"]
    assert [(e["company"], e["title"], e["more"]) for e in new] == [("Zed", "AI Engineer, New Grad", 1),
                                                                   ("Beta", "Software Engineer I", 0)]
    held, = [x for x in rep["cuts"] if x["stage"] == "company limit"]
    assert held["title"] == "AI Software Engineer, New Grad" and "Zed already has 1" in held["reason"]
    out = rank.today(c)
    assert "also kept at Zed: 1 more (jobs today --all)" in out
    assert "AI Software Engineer, New Grad" not in out
    assert "AI Software Engineer, New Grad" in rank.today(c, show_all=True)
    assert "also kept at Zed: 1 more" in (c.data_dir / "runs" / "2026-11-04" / f"{rep['run_id']}.md").read_text()


# ------------------------------------------------- revision 5: the pool, the bonus, the catch-up

class ByTitle:
    """A fake claude that keeps every job and scores it by title (default 5)."""
    def __init__(self, scores=None):
        self.scores, self.calls = scores or {}, []

    def __call__(self, argv, stdin, timeout):
        jobs = [json.loads(line) for line in stdin.split("<jobs>\n")[1].split("\n</jobs>")[0].splitlines()]
        self.calls.append([j["title"] for j in jobs])
        return 0, claude_stdout([{"id": j["id"], "keep": True, "score": self.scores.get(j["title"], 5),
                                  "why": f"because {j['title']}", "mismatches": []} for j in jobs]), ""


def _cfg(tmp_path, **ranking):
    c = jconfig.load(env={"DEBRIEF_DATA_DIR": str(tmp_path)})
    prof = copy.deepcopy(BASE)
    prof["position_types"] = [{"name": "SWE", "titles": ["software engineer", "ai engineer"]}]
    prof["ranking"] = {"threshold": 0, "max_candidates": 50, "top_n": 20, **ranking}
    profile.save(c, prof)
    return c


def _add(cfg, listings, at):
    store = JobsStore(state_path(cfg))
    with store.transaction():
        build.apply(store, src(id="simplify-newgrad"), FakeAdapter({0: listings}), [rec("{}", fetched_at=at)], ["r"])
    store.close()


def _day(n):
    return datetime(2026, 11, 4 + n, 12, 0, tzinfo=timezone.utc)


def test_fresh_bonus():
    prof = {"ranking": {"fresh_bonus": {"3": 2, "7": 1}}}
    p = lambda days: {"published_at": f"2026-11-{4 - days:02d}T12:00:00Z" if days < 4 else f"2026-10-{31 - (days - 4):02d}T12:00:00Z"}
    assert [rank.fresh_bonus(p(d), prof, NOW) for d in (1, 3, 5, 7, 10)] == [2, 2, 1, 1, 0]
    assert rank.fresh_bonus(p(1), {"ranking": {}}, NOW) == 0


def test_the_bonus_orders_fit_first_with_newness_added(tmp_path):
    cfg = _cfg(tmp_path, fresh_bonus={"3": 2, "7": 1})
    _add(cfg, [it("f", title="Software Engineer, New Grad", company="Fresh", published_at="2026-11-03T12:00:00Z"),
               it("o", title="Software Engineer I", company="Old", published_at="2026-10-22T12:00:00Z"),
               it("s", title="AI Engineer, New Grad", company="Seven", published_at="2026-10-22T12:00:00Z")],
         "2026-11-04T08:00:00Z")
    r = ByTitle({"Software Engineer, New Grad": 6, "Software Engineer I": 8, "AI Engineer, New Grad": 7})
    rep = rank.run(cfg, run_date="2026-11-04", runner=r, now=NOW, log=lambda *a: None)
    # Fresh: 6 + 2 = 8 ties Old's 8 and loses on the score itself; Seven's 7 has no bonus.
    assert [(e["company"], e["score"], e["bonus"]) for e in rep["list"]["new"]] == \
        [("Old", 8, 0), ("Fresh", 6, 2), ("Seven", 7, 0)]
    assert "posted Nov 3" in rank.today(cfg)


def test_the_nightly_cap_asks_new_jobs_first_and_carried_ones_later(tmp_path):
    cfg = _cfg(tmp_path, max_candidates=1, top_n=1)
    _add(cfg, [it("a", title="Software Engineer, New Grad", company="A"),
               it("b", title="Software Engineer, New Grad", company="B")], "2026-11-04T08:00:00Z")
    r = ByTitle()
    rep = rank.run(cfg, run_date="2026-11-04", runner=r, now=_day(0), log=lambda *a: None)
    assert len(r.calls) == 1 and len(rep["list"]["new"]) == 1 and rep["stages"]["cap"] == 1
    first = rep["list"]["new"][0]["company"]
    # A rerun the same day: the night's budget is spent, so nothing is asked and the list is the same.
    again = rank.run(cfg, run_date="2026-11-04", runner=r, now=_day(0), log=lambda *a: None)
    assert len(r.calls) == 1 and again["list"]["new"] == rep["list"]["new"]
    # The next night a new job arrives with fewer points; it's asked before the one left waiting.
    _add(cfg, [it("c", title="Software Engineer, Payments", company="C")], "2026-11-05T08:00:00Z")
    rep2 = rank.run(cfg, run_date="2026-11-05", runner=r, now=_day(1), log=lambda *a: None)
    assert r.calls[-1] == ["Software Engineer, Payments"] and [e["company"] for e in rep2["list"]["new"]] == ["C"]
    rep3 = rank.run(cfg, run_date="2026-11-06", runner=r, now=_day(2), log=lambda *a: None)
    left = ({"A", "B"} - {first}).pop()
    assert [e["company"] for e in rep3["list"]["new"]] == [left] and rep3["list"]["new"][0]["new"] is False


def test_catchup_lists_every_kept_job_once(tmp_path):
    cfg = _cfg(tmp_path, max_candidates=1, top_n=1)               # the catch-up ignores both
    _add(cfg, [it("w", title="Software Engineer, New Grad", company="Acme", published_at="2026-09-04T00:00:00Z"),
               it("x", title="Software Engineer, New Grad", company="Delta", published_at="2026-09-04T00:00:00Z"),
               it("b", title="Software Engineer I", company="Beta"),
               it("z1", title="New Grad: Software Engineer", company="Zed"),
               it("z2", title="Software Engineer, New Grad 2027", company="Zed", locations=("NYC",))],
         "2026-11-04T08:00:00Z")
    r = ByTitle()
    rep = rank.catchup(cfg, run_date="2026-11-04", runner=r, now=NOW, log=lambda *a: None)
    titles = {(e["company"], e["title"]) for e in rep["list"]["catchup"]}
    assert ("Acme", "Software Engineer, New Grad") in titles                 # watchlist: no age limit
    assert not any(c == "Delta" for c, _ in titles) and rep["stages"]["age"] == 1
    assert len(rep["list"]["catchup"]) == 3 and len(r.calls) == 1           # no top_n, no cap
    zed, = [e for e in rep["list"]["catchup"] if e["company"] == "Zed"]
    assert len(zed["also"]) == 1                                             # the copy, shown under it
    out = rank.today(cfg, catchup=True)
    assert out.startswith("Catch-up for 2026-11-04 · 3 kept jobs") and "posted Sep 4" in out
    # The morning list the same day: everything kept was shown in the catch-up, copies included.
    day = rank.run(cfg, run_date="2026-11-04", runner=r, now=NOW, log=lambda *a: None)
    assert day["list"]["new"] == [] and day["list"]["watchlist"] == []
    assert rank.today(cfg, catchup=True) == out                              # the catch-up rows survive
    # The next night only a new job is listed.
    _add(cfg, [it("e", title="Software Engineer, New Grad", company="Eps")], "2026-11-05T08:00:00Z")
    nxt = rank.run(cfg, run_date="2026-11-05", runner=r, now=_day(1), log=lambda *a: None)
    assert [e["company"] for e in nxt["list"]["new"]] == ["Eps"]


def test_save_list_replaces_only_its_sections(tmp_path):
    s = JobsStore(tmp_path / "j.sqlite")
    with s.transaction():
        for n in "123":
            s.save_posting({"posting_id": f"p:{n}", "company": "A", "title": "SWE", "locations": [], "url": None, "board": None,
                            "published_at": None, "first_seen_at": AT, "last_seen_at": AT, "closed_at": None, "data": {}})
        s.save_list("2026-11-04", "c1", [("catchup", 1, "p:1", 7, 50)], sections=("catchup",))
        s.save_list("2026-11-04", "s1", [("new", 1, "p:2", 6, 40)])
        s.save_list("2026-11-04", "s2", [("new", 1, "p:3", 6, 40)])
    assert sorted((r["section"], r["posting_id"]) for r in s.q("SELECT * FROM daily_list")) == \
        [("catchup", "p:1"), ("new", "p:3")]
    s.close()


# ------------------------------------------------ revision 6: a named catch-up ("older roles")

def test_named_catchup_since_a_date_keeps_the_first_catchup(tmp_path):
    cfg = _cfg(tmp_path, threshold=100)                           # the catch-up skips the threshold
    _add(cfg, [it("n", title="Software Engineer, New Grad", company="Nu", published_at="2026-11-03T00:00:00Z")],
         "2026-11-04T08:00:00Z")
    r = ByTitle()
    first = rank.catchup(cfg, run_date="2026-11-04", runner=r, now=NOW, log=lambda *a: None)
    assert [e["company"] for e in first["list"]["catchup"]] == ["Nu"]
    _add(cfg, [it("o", title="Software Engineer, New Grad", company="Oct", published_at="2026-10-02T00:00:00Z"),
               it("s", title="Software Engineer, New Grad", company="Sep", published_at="2026-09-20T00:00:00Z"),
               it("w", title="Software Engineer, New Grad", company="Acme", published_at="2026-08-01T00:00:00Z")],
         "2026-11-04T09:00:00Z")
    older = rank.catchup(cfg, run_date="2026-11-04", runner=r, now=NOW, log=lambda *a: None, since="2026-10-01", name="older")
    assert {e["company"] for e in older["list"]["older"]} == {"Oct", "Acme"}       # Sep is before Oct 1; Acme is watched
    assert any(c["company"] == "Sep" and "before 2026-10-01" in c["reason"] for c in older["cuts"])
    # The first catch-up's rows and marks are untouched, and nothing it listed is listed again.
    out = rank.today(cfg, catchup=True)
    assert "Catch-up for 2026-11-04 · 1 kept jobs" in out and "— Nu" in out
    old = rank.today(cfg, catchup="older")
    assert old.startswith("Older roles for 2026-11-04 · 2 kept jobs, best first · posted since Oct 1")
    store = JobsStore(state_path(cfg))
    marks = dict(store.q("SELECT section, COUNT(*) FROM highlighted GROUP BY section"))
    store.close()
    assert marks == {"catchup": 1, "older": 2}
    with pytest.raises(SystemExit):
        rank.catchup(cfg, name="new")
