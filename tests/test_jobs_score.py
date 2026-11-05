import copy
import json
from datetime import datetime, timezone

import pytest

from debrief.jobs import build, config as jconfig, prescore, profile, rank, score
from debrief.jobs.store import JobsStore, state_path
from debrief.triage.score import ScoringError
from test_jobs_collect import FakeAdapter, it, rec, src
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
    # The next day, nothing is new.
    rep3 = rank.run(cfg, run_date="2026-11-05", runner=r, now=NOW, log=lambda *a: None)
    assert rep3["candidates"] == 0 and rep3["list"]["new"] == []


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
