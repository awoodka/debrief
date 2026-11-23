import copy
import dataclasses
import json
from datetime import datetime, timezone

import pytest

from debrief.jobs import build, collect, config as jconfig, prescore, profile, rank, score
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
                it("g", title="Software Engineer, Platform", company="Gee"),
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
    beyond = [c["title"] for c in rep["cuts"] if c["stage"] == "beyond N"]
    rep3 = rank.run(cfg, run_date="2026-11-05", runner=r, now=NOW, log=lambda *a: None)
    assert rep3["new"] == 0 and rep3["carried"] == 5 and rep3["sent"] == 1 and len(r.calls) == 2
    assert rep3["list"]["new"] == []
    assert {c["title"] for c in rank.below(rep3)} == {beyond[0], "Software Engineer, Platform"}
    assert {c["stage"] for c in rank.below(rep3)} == {"from an earlier night"}
    out = rank.today(cfg)
    assert "0 new tonight · 2 below" in out and "carried over" not in out
    rep4 = rank.run(cfg, run_date="2026-11-06", runner=r, now=NOW, log=lambda *a: None)
    assert rep4["list"]["new"] == [] and len(r.calls) == 2 and rep4["not_kept"] == 1
    assert {c["title"] for c in rank.below(rep4)} == {beyond[0], "Software Engineer, Platform"}


def test_score_run_no_cap_and_fallback(cfg):
    r = Runner(fail=2)
    rep = rank.run(cfg, run_date="2026-11-04", no_cap=True, runner=r, now=NOW, log=lambda *a: None)
    assert rep["status"] == "fallback" and rep["sent"] == 5                  # no cap: all five
    assert rep["list"]["new"] == []
    assert [c["stage"] for c in rank.below(rep)] == ["not scored"] * 5
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
    _, part = prescore.points({**p, "title": "Software Engineer I - Platform"}, prof, NOW, facts={"matched": "software engineer"})
    assert part["early"] == 20
    prof["ranking"] = {"threshold": 50, "max_candidates": 1, "top_n": 1}
    low = ({**p, "posting_id": "p2", "company": "Acme"}, 10)                # Acme is watched: skips the threshold
    other = ({**p, "posting_id": "p3", "company": "Other"}, 10)
    high = ({**p, "posting_id": "p4", "company": "Zed"}, 90)
    sent, cut = prescore.select([low, other, high], prof)
    assert [x[0]["posting_id"] for x in sent] == ["p2"]
    assert sorted(why.split(" (")[0] for _, why in cut) == ["below the threshold", "over the cap"]
    prof["ranking"] = {"threshold": 0, "max_candidates": 1, "top_n": 1}
    unread = ({**p, "posting_id": "p5", "company": "Zed"}, 90)
    read = ({**p, "posting_id": "p6", "company": "Zed"}, 40)
    sent, _ = prescore.select([unread, read], prof, first={"p5", "p6"}, read={"p6"})
    assert [x[0]["posting_id"] for x in sent] == ["p6"]


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


def test_place_puts_each_kept_job_in_one_place():
    ranking = profile.ranking({"ranking": {"threshold": 0, "max_candidates": 50, "top_n": 3, "per_company": 2}})
    def j(company, tonight=True, fresh=True, score=8):
        return rank.Job(tonight, fresh, score, company)
    jobs = [j("raydar"), j("raydar"), j("raydar"), j("beta", tonight=False, score=10), j("beta", fresh=False, score=10),
            j("beta", score=4), j("beta", score=None), j("beta"), j("gamma"), j("raydar")]
    assert rank.place(jobs, ranking) == [
        "new", "new", "company limit", "from an earlier night", "posted earlier", "below the floor", "not scored",
        "new", "beyond N", "company limit"]
    del ranking["per_company"]
    assert rank.place([j("raydar")] * 4, ranking) == ["new"] * 3 + ["beyond N"]


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
    assert "also kept at Zed: 1 more (below)" in out
    listed, kept_below = out.split(rank.BELOW)
    assert "AI Software Engineer, New Grad" not in listed
    assert "AI Software Engineer, New Grad — Zed" in kept_below and "Company limit: Zed already has 1 on the list." in kept_below
    assert "also kept at Zed: 1 more" in (c.data_dir / "runs" / "2026-11-04" / f"{rep['run_id']}.md").read_text()


# ------------------------------------------------- revision 5: the pool, the bonus, the catch-up

class ByTitle:
    """A fake claude that keeps every job and scores it by title (default 5)."""
    def __init__(self, scores=None):
        self.scores, self.calls, self.sent = scores or {}, [], []

    def __call__(self, argv, stdin, timeout):
        jobs = [json.loads(line) for line in stdin.split("<jobs>\n")[1].split("\n</jobs>")[0].splitlines()]
        self.calls.append([j["title"] for j in jobs])
        self.sent.append(stdin)
        return 0, claude_stdout([{"id": j["id"], "keep": True, "score": self.scores.get(j["title"], 5),
                                  "why": f"because {j['title']}", "mismatches": []} for j in jobs]), ""


def _cfg(tmp_path, places=None, **ranking):
    c = jconfig.load(env={"DEBRIEF_DATA_DIR": str(tmp_path)})
    prof = copy.deepcopy(BASE)
    prof["position_types"] = [{"name": "SWE", "titles": ["software engineer", "ai engineer"]}]
    prof["filters"]["locations"]["places"] |= places or {}
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
    run = datetime(2026, 11, 11, 8, 17, tzinfo=timezone.utc)
    day = {"ranking": {"fresh_bonus": {"1": 1}}}
    at = lambda when: rank.fresh_bonus({"published_at": when}, day, run)
    assert [at(f"2026-11-{d:02d}T00:00:00Z") for d in (11, 10, 9)] == [1, 1, 0]
    assert at("2026-11-10T09:17:00Z") == 1 and at("2026-11-10T07:17:00Z") == 0
    week = {"ranking": {"fresh_bonus": {"3": 2, "7": 1}}}
    assert [rank.fresh_bonus({"published_at": f"2026-11-{d:02d}T00:00:00Z"}, week, run) for d in (8, 7, 4, 3)] == [2, 1, 1, 0]
    assert rank.fresh_bonus({"published_at": "2026-11-10T00:00:00Z"}, {"ranking": {}}, run) == 0


def test_a_job_dated_yesterday_gets_the_one_day_point(tmp_path):
    cfg = _cfg(tmp_path, fresh_bonus={"1": 1})
    _add(cfg, [it("y", title="Software Engineer, New Grad", company="Yesterday", published_at="2026-11-10T00:00:00Z"),
               it("t", title="Software Engineer, New Grad 2027", company="Timed", published_at="2026-11-10T07:00:00Z")],
         "2026-11-11T08:00:00Z")
    rep = rank.run(cfg, run_date="2026-11-11", runner=ByTitle(), now=datetime(2026, 11, 11, 8, 17, tzinfo=timezone.utc),
                   log=lambda *a: None)
    assert {e["company"]: e["bonus"] for e in rep["list"]["new"]} == {"Yesterday": 1, "Timed": 0}


def test_the_bonus_orders_fit_first_with_newness_added(tmp_path):
    cfg = _cfg(tmp_path, fresh_bonus={"3": 2, "7": 1}, fresh_days=14)
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


def _profile_sent(stdin):
    return json.loads(stdin.split("<profile>\n")[1].split("\n</profile>")[0].replace("<\\/", "</"))


def _jobs_sent(stdin):
    return [json.loads(line) for line in stdin.split("<jobs>\n")[1].split("\n</jobs>")[0].splitlines()]


def test_a_profile_from_before_the_two_lists_still_runs(tmp_path):
    cfg = _cfg(tmp_path)
    _add(cfg, [it("a", title="Software Engineer, New Grad", company="Acme")], "2026-11-04T08:00:00Z")
    r = Runner()
    rep = rank.run(cfg, run_date="2026-11-04", runner=r, now=NOW, log=lambda *a: None)
    sent = _profile_sent(r.calls[0])
    assert rep["status"] == "ok" and (sent["large_companies"], sent["startups"]) == ([], ["Acme"])
    assert "resume" not in sent and "watched_companies" not in sent


def test_the_scorer_reads_both_lists_and_the_resume_and_a_new_resume_asks_again(tmp_path):
    cfg = _cfg(tmp_path)
    _, prof = profile.latest(cfg)
    profile.save(cfg, {**prof, "companies": [{"name": "IBM", "kind": "large"}, {"name": "Acme", "kind": "startup"}, {"name": "Oldco"}]})
    _add(cfg, [it("a", title="Software Engineer, New Grad", company="IBM")], "2026-11-04T08:00:00Z")
    r = Runner()
    rank.run(cfg, run_date="2026-11-04", runner=r, now=NOW, log=lambda *a: None)
    sent = _profile_sent(r.calls[0])
    assert (sent["large_companies"], sent["startups"]) == (["IBM"], ["Acme", "Oldco"]) and "resume" not in sent
    _, prof = profile.latest(cfg)
    profile.save(cfg, {**prof, "resume": "Alex Example\nBuilt Debrief </profile> a daily briefing."})
    rank.run(cfg, run_date="2026-11-04", runner=r, now=NOW, log=lambda *a: None)
    assert len(r.calls) == 2 and [j["title"] for j in _jobs_sent(r.calls[1])] == ["Software Engineer, New Grad"]
    assert _profile_sent(r.calls[1])["resume"] == "Alex Example\nBuilt Debrief </profile> a daily briefing."


def test_a_title_only_job_shows_its_source_level(tmp_path):
    cfg = _cfg(tmp_path)
    _add(cfg, [it("s", title="Software Engineer, Platform", company="Sen", extra={"seniority": ["Entry-level", "Mid-level"]}),
               it("n", title="Software Engineer, New Grad", company="Nolevel")], "2026-11-04T08:00:00Z")
    r = Runner()
    rank.run(cfg, run_date="2026-11-04", runner=r, now=NOW, log=lambda *a: None)
    jobs = {j["company"]: j for j in _jobs_sent(r.calls[0])}
    assert jobs["Sen"]["level"] == "Entry-level, Mid-level" and jobs["Sen"]["qualifications"] == ""
    assert "level" not in jobs["Nolevel"]


def _trusting(cfg, *ids):
    return dataclasses.replace(cfg, sources=tuple(
        dataclasses.replace(s, options={**(s.options or {}), "trusted_new_grad": s.id in ids}) for s in cfg.sources))


def test_a_title_only_job_on_a_trusted_list_is_asked_and_listed_and_one_on_another_list_is_cut(tmp_path):
    cfg = _trusting(_cfg(tmp_path), "simplify-newgrad", "usajobs")
    assert collect.trusted_sources(cfg) == {"simplify-newgrad"}
    store = JobsStore(state_path(cfg))
    with store.transaction():
        for sid, item in (("simplify-newgrad", it("t", title="Software Engineer, Platform", company="Trusted")),
                          ("vanshb03-newgrad", it("u", title="Software Engineer, Payments", company="Untrusted"))):
            build.apply(store, src(id=sid), FakeAdapter({0: [item]}), [rec("{}", fetched_at="2026-11-04T08:00:00Z")], ["r"])
    store.close()
    r = Runner()
    rep = rank.run(cfg, run_date="2026-11-04", runner=r, now=NOW, log=lambda *a: None)
    assert [j["company"] for j in _jobs_sent(r.calls[0])] == ["Trusted"]
    assert [e["company"] for e in rep["list"]["new"]] == ["Trusted"]
    assert [(c["company"], c["stage"], c["reason"]) for c in rep["cuts"] if c["company"] == "Untrusted"] == \
        [("Untrusted", "level", "nothing says it's for new grads (title only)")]


def test_level_no_years_sign_lets_a_read_plain_title_reach_the_scorer(tmp_path):
    cfg = _cfg(tmp_path)
    store = JobsStore(state_path(cfg))
    with store.transaction():
        build.apply(store, src(id="himalayas", family="api"),
                    FakeAdapter({0: [it("r", title="Software Engineer", company="Readco", text="Build our platform in Python.")]}),
                    [rec("{}", fetched_at="2026-11-04T08:00:00Z")], ["r"])
    store.close()
    assert cfg.settings["level"]["no_years_sign"] is False
    rep = rank.run(cfg, run_date="2026-11-04", runner=Runner(), now=NOW, log=lambda *a: None)
    assert rep["sent"] == 0 and [c["stage"] for c in rep["cuts"]] == ["level"]
    on = dataclasses.replace(cfg, settings={**cfg.settings, "level": {**cfg.settings["level"], "no_years_sign": True}})
    r = Runner()
    rep = rank.run(on, run_date="2026-11-04", runner=r, now=NOW, log=lambda *a: None)
    assert [j["company"] for j in _jobs_sent(r.calls[0])] == ["Readco"]


class Drops:
    """A fake claude that drops every job, giving its title's reason (and score 6)."""
    def __init__(self, reasons):
        self.reasons = reasons

    def __call__(self, argv, stdin, timeout):
        jobs = [json.loads(line) for line in stdin.split("<jobs>\n")[1].split("\n</jobs>")[0].splitlines()]
        return 0, claude_stdout([{"id": j["id"], "keep": False, "score": 6, "why": "x",
                                  "mismatches": [self.reasons[j["title"]]]} for j in jobs]), ""


def test_a_job_dropped_only_for_years_within_the_limit_is_kept_and_capped(tmp_path):
    cfg = _cfg(tmp_path)
    reasons = {"Software Engineer, New Grad A": "requires 1-3 years of professional experience",
               "Software Engineer, New Grad B": "requires 3+ years of experience",
               "Software Engineer, New Grad C": "requires an active Secret clearance"}
    _add(cfg, [it(k.lower(), title=t, company=k) for k, t in zip("abc", reasons)], "2026-11-04T08:00:00Z")
    rep = rank.run(cfg, run_date="2026-11-04", runner=Drops(reasons), now=NOW, log=lambda *a: None)
    assert [(e["company"], e["score"], e["model_score"]) for e in rep["list"]["new"]] == [("a", 5, 6)]
    assert sorted((c["company"], c["stage"]) for c in rep["cuts"] if c["company"] in "bc") == \
        [("b", "not kept"), ("c", "not kept")]


def test_a_read_job_at_a_small_company_reaches_the_scorer_only_with_the_startup_sign(tmp_path):
    cfg = _cfg(tmp_path)
    store = JobsStore(state_path(cfg))
    with store.transaction():
        build.apply(store, src(id="himalayas", family="api"),
                    FakeAdapter({0: [it("s", title="Software Engineer", company="Smallco", text="Build our agents in Python.",
                                        extra={"company_size": "11-50 people, seed"}),
                                     it("b", title="Software Engineer", company="Bigco", text="Build our agents in Python.",
                                        extra={"company_size": "5,001+ people"})]}),
                    [rec("{}", fetched_at="2026-11-04T08:00:00Z")], ["r"])
    store.close()
    assert cfg.settings["level"]["startup_sign"] is True
    r = Runner()
    rep = rank.run(cfg, run_date="2026-11-04", runner=r, now=NOW, log=lambda *a: None)
    assert [j["company"] for j in _jobs_sent(r.calls[0])] == ["Smallco"]
    assert [(c["company"], c["stage"]) for c in rep["cuts"] if c["company"] == "Bigco"] == [("Bigco", "level")]
    off = dataclasses.replace(cfg, settings={**cfg.settings, "level": {**cfg.settings["level"], "startup_sign": False}})
    rep = rank.run(off, run_date="2026-11-05", runner=Runner(), now=NOW, log=lambda *a: None)
    assert rep["sent"] == 0


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
    _add(cfg, [it("c", title="Junior Software Engineer, Payments", company="C")], "2026-11-05T08:00:00Z")
    rep2 = rank.run(cfg, run_date="2026-11-05", runner=r, now=_day(1), log=lambda *a: None)
    assert r.calls[-1] == ["Junior Software Engineer, Payments"] and [e["company"] for e in rep2["list"]["new"]] == ["C"]
    rep3 = rank.run(cfg, run_date="2026-11-06", runner=r, now=_day(2), log=lambda *a: None)
    left = ({"A", "B"} - {first}).pop()
    assert len(r.calls) == 3 and rep3["list"]["new"] == []
    assert [(c["company"], c["stage"]) for c in rank.below(rep3)] == [(left, "from an earlier night")]


def test_a_job_from_an_earlier_night_never_makes_the_list_however_it_scores(tmp_path):
    cfg = _cfg(tmp_path, max_candidates=1, top_n=1)
    _add(cfg, [it("a", title="Software Engineer, New Grad", company="A"),
               it("b", title="AI Engineer, Payments, New Grad", company="B")], "2026-11-04T08:00:00Z")
    r = ByTitle({"AI Engineer, Payments, New Grad": 9})
    first = rank.run(cfg, run_date="2026-11-04", runner=r, now=_day(0), log=lambda *a: None)
    assert [e["company"] for e in first["list"]["new"]] == ["A"] and first["stages"]["cap"] == 1
    nxt = rank.run(cfg, run_date="2026-11-05", runner=r, now=_day(1), log=lambda *a: None)
    assert r.calls[-1] == ["AI Engineer, Payments, New Grad"] and nxt["list"]["new"] == []
    assert [(c["company"], c["score"], c["reason"]) for c in rank.below(nxt)] == \
        [("B", 9, "From an earlier night: first found Nov 4.")]
    assert rank.today(cfg).startswith("Jobs for 2026-11-05 · 0 new tonight · 1 below")


def test_today_for_a_date_with_no_list_says_there_is_none(tmp_path):
    cfg = _cfg(tmp_path)
    assert rank.today(cfg, list_date="2026-11-03") == rank.today(cfg) == "no list yet (debrief jobs score)"


def test_a_new_job_under_the_floor_or_posted_earlier_waits_below_with_its_reason(tmp_path):
    cfg = _cfg(tmp_path)
    _add(cfg, [it("a", title="AI Engineer, New Grad", company="Ai"),
               it("l", title="Software Engineer, New Grad", company="Low"),
               it("o", title="Software Engineer I", company="Old", published_at="2026-10-25T00:00:00Z")],
         "2026-11-04T08:00:00Z")
    r = ByTitle({"AI Engineer, New Grad": 8, "Software Engineer, New Grad": 4, "Software Engineer I": 9})
    rep = rank.run(cfg, run_date="2026-11-04", runner=r, now=NOW, log=lambda *a: None)
    assert [e["company"] for e in rep["list"]["new"]] == ["Ai"]
    assert [(c["company"], c["reason"]) for c in rank.below(rep)] == [
        ("Old", "Posted earlier: found tonight, posted Oct 25."), ("Low", "Below the floor: scored 4, under 5.")]
    out = rank.today(cfg)
    assert out.startswith("Jobs for 2026-11-04 · 1 new tonight · 2 below")
    assert "Below the floor: scored 4, under 5." in out.split(rank.BELOW)[1]
    _, prof = profile.latest(cfg)
    profile.save(cfg, {**prof, "ranking": {**prof["ranking"], "min_score": 4, "fresh_days": 14}})
    again = rank.run(cfg, run_date="2026-11-04", runner=r, now=NOW, log=lambda *a: None)
    assert [e["company"] for e in again["list"]["new"]] == ["Old", "Ai", "Low"] and rank.below(again) == []


def test_tonights_fresh_jobs_are_asked_before_one_posted_earlier(tmp_path):
    cfg = _cfg(tmp_path, max_candidates=1, top_n=1)
    _add(cfg, [it("s", title="Software Engineer, New Grad", company="Stale", published_at="2026-10-30T00:00:00Z"),
               it("f", title="Junior Software Engineer, Payments", company="Fresh")], "2026-11-04T08:00:00Z")
    r = ByTitle()
    rep = rank.run(cfg, run_date="2026-11-04", runner=r, now=NOW, log=lambda *a: None)
    assert r.calls == [["Junior Software Engineer, Payments"]]
    assert [c["company"] for c in rep["cuts"] if c["stage"] == "cap"] == ["Stale"]


def test_a_job_the_watchlist_section_shows_is_shown_once(tmp_path):
    cfg = _cfg(tmp_path)
    _add(cfg, [it("w", title="Software Engineer, New Grad", company="Acme"),
               it("o", title="AI Engineer, New Grad", company="Acme", published_at="2026-10-30T00:00:00Z")],
         "2026-11-04T08:00:00Z")
    r = ByTitle({"Software Engineer, New Grad": 4})
    rep = rank.run(cfg, run_date="2026-11-04", runner=r, now=NOW, log=lambda *a: None)
    assert rep["list"]["new"] == [] and [e["title"] for e in rep["list"]["watchlist"]] == ["Software Engineer, New Grad"]
    assert [c["title"] for c in rank.below(rep)] == ["AI Engineer, New Grad"]
    assert rank.today(cfg).startswith("Jobs for 2026-11-04 · 0 new tonight · 1 below")
    nxt = rank.run(cfg, run_date="2026-11-05", runner=r, now=_day(1), log=lambda *a: None)
    assert nxt["list"]["watchlist"] == []
    assert [(c["title"], c["stage"]) for c in rank.below(nxt)] == [("AI Engineer, New Grad", "from an earlier night")]


def test_name_variants_of_one_company_take_one_slot_and_one_entry(tmp_path):
    cfg = _cfg(tmp_path, per_company=1)
    _add(cfg, [it("h", title="Software Engineer, New Grad", company="Bask Health"),
               it("b", title="New Grad: Software Engineer", company="Bask"),
               it("a", title="AI Engineer, New Grad", company="Bask"),
               it("o", title="Software Engineer I", company="Other")], "2026-11-04T08:00:00Z")
    rep = rank.run(cfg, run_date="2026-11-04", runner=ByTitle({"AI Engineer, New Grad": 8}), now=NOW, log=lambda *a: None)
    assert [(e["company"], e["title"], e["more"]) for e in rep["list"]["new"]] == [("Bask", "AI Engineer, New Grad", 1),
                                                                                  ("Other", "Software Engineer I", 0)]
    (held,) = rank.below(rep)
    assert held["stage"] == "company limit" and held["company"] in ("Bask", "Bask Health")
    assert [c["stage"] for c in rep["cuts"] if c["company"] in ("Bask", "Bask Health")].count("same job") == 1


def test_repeat_days_holds_a_company_listed_this_week_unless_the_job_scores_7(tmp_path):
    cfg = _cfg(tmp_path, repeat_days=7)
    r = ByTitle({"AI Engineer, New Grad": 7, "Junior Software Engineer, Payments": 6})
    _add(cfg, [it("h", title="Software Engineer, New Grad", company="Bask Health")], "2026-11-04T08:00:00Z")
    assert [e["company"] for e in rank.run(cfg, run_date="2026-11-04", runner=r, now=_day(0), log=lambda *a: None)
            ["list"]["new"]] == ["Bask Health"]
    _add(cfg, [it("p", title="Junior Software Engineer, Payments", company="Bask"),
               it("a", title="AI Engineer, New Grad", company="Bask")], "2026-11-07T08:00:00Z")
    rep = rank.run(cfg, run_date="2026-11-07", runner=r, now=_day(3), log=lambda *a: None)
    assert [e["title"] for e in rep["list"]["new"]] == ["AI Engineer, New Grad"]
    assert [(c["title"], c["reason"]) for c in rank.below(rep)] == [
        ("Junior Software Engineer, Payments", "Listed this week: Bask was on the list in the last 7 nights, and this scored under 7.")]
    _, prof = profile.latest(cfg)
    profile.save(cfg, {**prof, "ranking": {k: v for k, v in prof["ranking"].items() if k != "repeat_days"}})
    again = rank.run(cfg, run_date="2026-11-07", runner=r, now=_day(3), log=lambda *a: None)
    assert [e["title"] for e in again["list"]["new"]] == ["AI Engineer, New Grad", "Junior Software Engineer, Payments"]


def test_a_near_job_under_the_floor_is_shown_nearby_sharing_the_company_limit():
    ranking = profile.ranking({"ranking": {"threshold": 0, "max_candidates": 50, "top_n": 2, "per_company": 1}})
    def j(company, score, near=True, fresh=True):
        return rank.Job(True, fresh, score, company, near)
    jobs = [j("a", 8), j("b", 4), j("a", 4), j("c", 2), j("d", 4, near=False), j("e", 3), j("b", 3), j("f", 4, fresh=False),
            j("g", 3)]
    assert rank.place(jobs, ranking) == ["new", "nearby", "company limit", "below the floor", "below the floor", "nearby",
                                         "company limit", "posted earlier", "beyond N"]


DC = {"DC": ["Washington, DC", "Reston, VA"]}
NEAR_SCORES = {"AI Engineer, New Grad": 8, "Software Engineer, New Grad": 4, "Software Engineer I": 4,
               "Junior Software Engineer": 4, "Software Engineer, Payments, New Grad": 2, "Software Engineer, Platform, New Grad": 4}


def _near_night(cfg):
    _add(cfg, [it("l", title="AI Engineer, New Grad", company="Listed", locations=("Reston, VA",)),
               it("x", title="Software Engineer I", company="Listed", locations=("Washington, DC",)),
               it("n", title="Software Engineer, New Grad", company="Near", locations=("Seattle, WA; Washington, DC",)),
               it("f", title="Junior Software Engineer", company="Far"),
               it("w", title="Software Engineer, Payments, New Grad", company="Weak", locations=("Washington, DC",)),
               it("o", title="Software Engineer, Platform, New Grad", company="Older", locations=("Washington, DC",),
                  published_at="2026-10-30T00:00:00Z")], "2026-11-04T08:00:00Z")
    return rank.run(cfg, run_date="2026-11-04", runner=ByTitle(NEAR_SCORES), now=NOW, log=lambda *a: None)


def test_a_new_weaker_job_in_a_nearby_area_is_shown_after_the_list_and_not_again(tmp_path):
    cfg = _cfg(tmp_path, places=DC, per_company=1, nearby=["DC"])
    rep = _near_night(cfg)
    assert [(e["company"], e["more"]) for e in rep["list"]["new"]] == [("Listed", 1)]
    assert [e["company"] for e in rep["list"]["nearby"]] == ["Near"]
    assert {c["company"]: c["stage"] for c in rank.below(rep)} == {
        "Listed": "company limit", "Far": "below the floor", "Weak": "below the floor", "Older": "posted earlier"}
    out = rank.today(cfg)
    assert out.startswith("Jobs for 2026-11-04 · 1 new tonight · 1 nearby · 4 below")
    listed, rest = out.split(f"\n{rank.NEARBY}:\n")
    nearby, below = rest.split(rank.BELOW)
    assert "AI Engineer, New Grad — Listed" in listed and "also kept at Listed: 1 more (below)" in listed
    assert "Software Engineer, New Grad — Near · Seattle, WA; Washington, DC" in nearby and rank.NOTES["nearby"] in nearby
    assert "Near" not in listed + below and "Listed" not in nearby
    md = (cfg.data_dir / "runs" / "2026-11-04" / f"{rep['run_id']}.md").read_text()
    assert f"## {rank.NEARBY}\n\n1. **4** Software Engineer, New Grad — Near" in md
    nxt = rank.run(cfg, run_date="2026-11-05", runner=ByTitle(NEAR_SCORES), now=_day(1), log=lambda *a: None)
    assert nxt["list"] == {"new": [], "nearby": [], "watchlist": []}
    assert "Near" not in {c["company"] for c in rank.below(nxt)} and "Far" in {c["company"] for c in rank.below(nxt)}


def test_a_profile_without_nearby_areas_keeps_near_jobs_below_the_floor(tmp_path):
    cfg = _cfg(tmp_path, places=DC, per_company=1)
    rep = _near_night(cfg)
    assert list(rep["list"]) == ["new", "watchlist"] and [e["company"] for e in rep["list"]["new"]] == ["Listed"]
    assert ("Near", "Below the floor: scored 4, under 5.") in [(c["company"], c["reason"]) for c in rank.below(rep)]
    out = rank.today(cfg)
    assert out.startswith("Jobs for 2026-11-04 · 1 new tonight · 5 below") and rank.NEARBY not in out


ONE_YEAR = "Requirements: 1+ year of professional software engineering experience. Python."


def test_a_job_requiring_a_year_ranks_as_a_5_and_says_why_and_two_years_is_still_cut(tmp_path):
    cfg = _cfg(tmp_path)
    _, prof = profile.latest(cfg)
    profile.save(cfg, {**prof, "filters": {**prof["filters"], "max_years": 1}})
    _add(cfg, [it("y", title="AI Engineer, New Grad", company="Yearco", text=ONE_YEAR),
               it("n", title="Software Engineer, New Grad", company="Nonco", text="Requirements: Python and SQL."),
               it("t", title="Software Engineer I", company="Twoco", text="Requirements: 2+ years of professional experience.")],
         "2026-11-04T08:00:00Z")
    r = ByTitle({"AI Engineer, New Grad": 8, "Software Engineer, New Grad": 6, "Software Engineer I": 9})
    rep = rank.run(cfg, run_date="2026-11-04", runner=r, now=NOW, log=lambda *a: None)
    assert {j["company"]: j["years_required"] for j in _jobs_sent(r.sent[0])} == {"Yearco": 1, "Nonco": None}
    assert [(c["company"], c["stage"]) for c in rep["cuts"] if c["company"] == "Twoco"] == [("Twoco", "years")]
    new = rep["list"]["new"]
    assert [(e["company"], e["score"]) for e in new] == [("Nonco", 6), ("Yearco", 5)]
    assert new[1]["model_score"] == 8 and new[1]["years_note"] == "Requires 1 year of experience: ranked as 5 at most."
    assert "model_score" not in new[0] and "years_note" not in new[0]
    out = rank.today(cfg)
    assert "[ 5] AI Engineer, New Grad — Yearco" in out and "Requires 1 year of experience: ranked as 5 at most." in out
    assert "The model scored 8." in (cfg.data_dir / "runs" / "2026-11-04" / f"{rep['run_id']}.md").read_text()


def test_a_capped_job_waits_below_a_higher_floor_with_both_reasons(tmp_path):
    cfg = _cfg(tmp_path, min_score=6)
    _add(cfg, [it("y", title="AI Engineer, New Grad", company="Yearco", text=ONE_YEAR)], "2026-11-04T08:00:00Z")
    rep = rank.run(cfg, run_date="2026-11-04", runner=ByTitle({"AI Engineer, New Grad": 9}), now=NOW, log=lambda *a: None)
    assert rep["list"]["new"] == []
    (c,) = rank.below(rep)
    assert (c["stage"], c["score"], c["model_score"], c["reason"]) == ("below the floor", 5, 9, "Below the floor: scored 5, under 6.")
    assert "Requires 1 year of experience: ranked as 5 at most." in rank.today(cfg).split(rank.BELOW)[1]


def _other_us(cfg, **ranking):
    _, prof = profile.latest(cfg)
    f = prof["filters"]
    profile.save(cfg, {**prof, "filters": {**f, "locations": {**f["locations"], "other_us": True}},
                       "ranking": {**prof["ranking"], **ranking}})


def test_a_job_outside_the_places_ranks_the_penalty_lower_and_says_where(tmp_path):
    cfg = _cfg(tmp_path)
    _other_us(cfg)
    _add(cfg, [it("a", title="AI Engineer, New Grad", company="Atlco", locations=("Atlanta, GA",)),
               it("n", title="Software Engineer, New Grad", company="Nyco", locations=("New York, NY",)),
               it("w", title="Software Engineer I", company="Weakco", locations=("Denver, CO",)),
               it("l", title="Software Engineer, Platform, New Grad", company="Lonco", locations=("London, UK",))],
         "2026-11-04T08:00:00Z")
    r = ByTitle({"AI Engineer, New Grad": 8, "Software Engineer, New Grad": 7, "Software Engineer I": 6})
    rep = rank.run(cfg, run_date="2026-11-04", runner=r, now=NOW, log=lambda *a: None)
    assert _profile_sent(r.sent[0])["constraints"]["other_us_places_ok"] is True
    assert [c["stage"] for c in rep["cuts"] if c["company"] == "Lonco"] == ["place"]
    new = rep["list"]["new"]
    assert [(e["company"], e["score"], e.get("model_score")) for e in new] == [("Nyco", 7, None), ("Atlco", 6, 8)]
    assert new[1]["place_note"] == "Outside your places (Atlanta, GA): ranked 2 lower."
    (c,) = rank.below(rep)
    assert (c["company"], c["score"], c["model_score"], c["reason"]) == ("Weakco", 4, 6, "Below the floor: scored 4, under 5.")
    out = rank.today(cfg)
    assert "[ 6] AI Engineer, New Grad — Atlco" in out and "Outside your places (Atlanta, GA): ranked 2 lower." in out
    assert "Outside your places (Denver, CO): ranked 2 lower." in out.split(rank.BELOW)[1]
    assert "ranked 2 lower. The model scored 8." in (cfg.data_dir / "runs" / "2026-11-04" / f"{rep['run_id']}.md").read_text()


def test_the_years_cap_applies_on_top_of_the_place_penalty(tmp_path):
    cfg = _cfg(tmp_path)
    _other_us(cfg)
    _add(cfg, [it("y", title="AI Engineer, New Grad", company="Highco", locations=("Atlanta, GA",), text=ONE_YEAR),
               it("s", title="Software Engineer, New Grad", company="Lowco", locations=("Atlanta, GA",), text=ONE_YEAR)],
         "2026-11-04T08:00:00Z")
    rep = rank.run(cfg, run_date="2026-11-04", runner=ByTitle({"AI Engineer, New Grad": 9, "Software Engineer, New Grad": 6}),
                   now=NOW, log=lambda *a: None)
    (e,) = rep["list"]["new"]
    assert (e["company"], e["score"], e["model_score"]) == ("Highco", 5, 9)
    assert rank.score_notes(e) == ["Outside your places (Atlanta, GA): ranked 2 lower.",
                                   "Requires 1 year of experience: ranked as 5 at most."]
    assert [(c["company"], c["score"], c["model_score"]) for c in rank.below(rep)] == [("Lowco", 4, 6)]


def test_a_zero_penalty_ranks_a_job_outside_the_places_as_the_model_scored_it(tmp_path):
    cfg = _cfg(tmp_path)
    _other_us(cfg, other_us_penalty=0)
    _add(cfg, [it("a", title="AI Engineer, New Grad", company="Atlco", locations=("Atlanta, GA",))], "2026-11-04T08:00:00Z")
    rep = rank.run(cfg, run_date="2026-11-04", runner=ByTitle({"AI Engineer, New Grad": 8}), now=NOW, log=lambda *a: None)
    (e,) = rep["list"]["new"]
    assert e["score"] == 8 and rank.score_notes(e) == [] and "model_score" not in e


def test_a_job_requiring_a_year_ranks_below_one_that_doesnt_with_the_same_score(tmp_path):
    cfg = _cfg(tmp_path)
    _add(cfg, [it("y", title="Software Engineer, New Grad", company="Yearco", text=ONE_YEAR),
               it("n", title="Software Engineer, New Grad", company="Nonco", text="Requirements: SQL.")],
         "2026-11-04T08:00:00Z")
    rep = rank.run(cfg, run_date="2026-11-04", runner=ByTitle(), now=NOW, log=lambda *a: None)
    new = rep["list"]["new"]
    assert [(e["company"], e["score"]) for e in new] == [("Nonco", 5), ("Yearco", 5)] and new[0]["points"] < new[1]["points"]


def test_an_unread_job_ranks_below_a_read_one_with_the_same_score(tmp_path):
    cfg = _cfg(tmp_path)
    _add(cfg, [it("u", title="Software Engineer, New Grad", company="Unreadco"),
               it("r", title="Software Engineer, New Grad", company="Readco", text="We build tools for farmers.")],
         "2026-11-04T08:00:00Z")
    rep = rank.run(cfg, run_date="2026-11-04", runner=ByTitle({"Software Engineer, New Grad": 6}), now=NOW, log=lambda *a: None)
    new = rep["list"]["new"]
    assert [e["company"] for e in new] == ["Readco", "Unreadco"] and new[0]["points"] < new[1]["points"]


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


def test_display_title_shows_an_hn_role():
    prof = copy.deepcopy(BASE)
    t = "Mechanize | San Francisco, CA (Onsite) | Junior Software Engineer | Full-time | $150k"
    assert rank.display_title(t, prof) == "Junior Software Engineer"
    assert rank.display_title("River | Staff Engineers (Elixir) | NYC") == "Staff Engineers (Elixir)"
    assert rank.display_title("Software Engineer, New Grad", prof) == "Software Engineer, New Grad"
    assert rank.display_title("Software Engineer (Semiconductors & AI) | Graduates Considered", prof) == \
        "Software Engineer (Semiconductors & AI)"
    assert rank.display_title("") == ""
