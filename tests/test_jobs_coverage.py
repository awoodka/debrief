"""The coverage check (src/debrief/jobs/coverage.py) and its Google sample
(src/debrief/jobs/adapters/serpapi.py): budget and rotation, eligibility, exact / same-job / near
matching, pending then miss with a cause, the report and the morning list's summary line."""
import json
from datetime import date, datetime, timezone

from debrief.http import Fetched
from debrief.jobs import collect, coverage, profile, rank
from debrief.jobs.adapters import serpapi
from debrief.jobs.store import JobsStore, state_path
from debrief.store import raw
from test_jobs_collect import _cfg, it, rec  # noqa: F401
from test_jobs_finder import _list

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
D1, D2, D3 = "2026-11-05", "2026-11-06", "2026-11-07"


def test_posted_from():
    at = "2026-11-05T12:00:00Z"
    assert coverage.posted_from("3 days ago", at) == "2026-11-02T12:00:00Z"
    assert coverage.posted_from("21 hours ago", at) == "2026-11-04T15:00:00Z"
    assert coverage.posted_from("Just posted", at) == at
    assert coverage.posted_from("30+ days ago", at) == "2026-10-06T12:00:00Z"
    assert coverage.posted_from(None, at) is None


def test_budget_and_rotation():
    assert serpapi.budget(250, 8, date(2026, 11, 1)) == 8                     # 250 over 30 days: the cap
    assert serpapi.budget(20, 8, date(2026, 11, 26)) == 4                     # 20 over the last 5 days
    assert serpapi.budget(0, 8, date(2026, 11, 26)) == 0
    qs = serpapi.queries({"position_types": [{"titles": ["software engineer"]}, {"titles": ["data scientist"]}]},
                         {"suffix": "new grad", "locations": [{"google": "New York, New York, United States"},
                                                               {"google": "United States", "remote": True}]})
    assert qs == [("software engineer new grad", "New York, New York, United States"),
                  ("data scientist new grad", "New York, New York, United States"),
                  ("software engineer new grad remote", "United States"), ("data scientist new grad remote", "United States")]
    d = date(2026, 11, 5)
    a, b = serpapi.todays(qs, 3, d), serpapi.todays(qs, 3, date(2026, 11, 6))
    assert len(a) == 3 and len(set(a)) == 3 and b[0] == qs[(d.toordinal() * 3 + 3) % 4]   # continues the next day


class SerpClient:
    user_agent = "t"

    def __init__(self, left=250):
        self.left, self.calls = left, []

    def robots_allowed(self, url):
        return True

    def get(self, url, params=None, headers=None, etag=None, last_modified=None):
        self.calls.append((url, dict(params or {})))
        final = url + "?" + "&".join(f"{k}={v}" for k, v in (params or {}).items())
        body = {"total_searches_left": self.left} if "account" in url else {"jobs_results": []}
        return Fetched(url=url, final_url=final, status=200, headers={"content-type": "application/json"},
                       content=json.dumps(body).encode())


def test_the_sample_spends_its_budget_and_keeps_the_key_out_of_raw(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"serpapi-google-jobs"})
    s = JobsStore(state_path(cfg))
    _, prof = profile.latest(cfg)
    client = SerpClient(left=250)
    out = collect.collect_source(cfg, s, client, cfg.source("serpapi-google-jobs"), "run-1", D1, NOW, prof,
                                 {"SERPAPI_KEY": "secret-serp-key"}, lambda *a, **k: None)
    searches = [c for c in client.calls if "search.json" in c[0]]
    assert out["status"] == "ok" and 1 <= len(searches) <= 8
    assert all(p["engine"] == "google_jobs" and p["api_key"] == "secret-serp-key" for _, p in searches)
    stored = raw.runs_for_day(cfg.data_dir, D1)["run-1"]["serpapi-google-jobs"][1]
    assert "secret-serp-key" not in json.dumps(stored)
    s.close()


def job(title, company, location="New York, NY", apply=(), posted="2 days ago", description="New grad role. Python."):
    return {"title": title, "company_name": company, "location": location, "via": "LinkedIn",
            "description": description, "detected_extensions": {"posted_at": posted},
            "apply_options": [{"title": "Apply", "link": u} for u in apply], "job_id": f"id-{title}-{company}"}


def _sample(cfg, run_date, jobs, seq=0):
    r = rec({"jobs_results": jobs}, url="https://serpapi.com/search.json", source_id=coverage.SOURCE, seq=seq,
            meta={"kind": "search", "q": "software engineer new grad", "location": "New York, New York, United States"},
            fetched_at=f"{run_date}T08:00:00Z")
    raw.write(cfg.data_dir, run_date, coverage.SOURCE, f"run-{run_date}", [r])


def test_matching_waiting_and_causes(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"simplify-newgrad"})
    s = JobsStore(state_path(cfg))
    gh = "https://job-boards.greenhouse.io/globex/jobs/{}"
    _list(s, [it("a", title="Software Engineer, New Grad", company="Globex", url=gh.format(1), job_key="job:greenhouse:1",
                 board="greenhouse:globex"),
              it("b", title="New Grad Software Engineer", company="Initech"),
              it("c", title="Software Engineer, Platform, New Grad", company="Palantir")],
          "2026-11-04T08:00:00Z", source_id="simplify-newgrad", snapshot="window")
    s.close()
    _sample(cfg, D1, [
        job("Software Engineer (New Grad)", "Globex Inc", apply=[gh.format(1)]),             # job id
        job("Software Engineer - New Grad", "Initech"),                                     # same job
        job("Software Engineer, Platform Infrastructure, New Grad", "Palantir Technologies"),  # near
        job("Senior Software Engineer", "Hooli"),                                           # ineligible: title
        job("Software Engineer, New Grad", "Umbrella", apply=["https://jobs.lever.co/umbrella/0f1e2d3c-1111-2222-3333-444455556666"]),
        job("Software Engineer, New Grad", "Stark", apply=["https://www.linkedin.com/jobs/view/123", "https://www.indeed.com/viewjob?jk=9"]),
        job("Software Engineer, New Grad", "Wayne", apply=["https://careers.wayne.com/jobs/7"]),
    ])
    r1 = coverage.run(cfg, run_date=D1, now=NOW, log=lambda *a: None)
    st = JobsStore(state_path(cfg))
    status = dict(st.q("SELECT company, status FROM coverage_samples"))
    assert status == {"Globex Inc": "matched", "Initech": "matched", "Palantir Technologies": "near", "Hooli": "ineligible",
                      "Umbrella": "pending", "Stark": "pending", "Wayne": "pending"}
    assert dict(st.q("SELECT company, how FROM coverage_samples WHERE status='matched'")) == {"Globex Inc": "job id",
                                                                                               "Initech": "same job"}
    st.close()
    assert r1["google"]["week"]["recall"] == 1.0 and r1["google"]["week"]["pending"] == 3
    # Rerunning the same day doesn't count as another check; two more days make the misses final.
    coverage.run(cfg, run_date=D1, now=NOW, log=lambda *a: None)
    coverage.run(cfg, run_date=D2, now=NOW, log=lambda *a: None)
    r3 = coverage.run(cfg, run_date=D3, now=NOW, log=lambda *a: None)
    causes = {m["company"]: m["cause"] for m in r3["misses"]}
    assert causes == {"Umbrella": "not watched", "Stark": "closed site only", "Wayne": "company site only"}
    umbrella = next(m for m in r3["misses"] if m["company"] == "Umbrella")
    assert 'debrief jobs watch add "Umbrella" --kind large|startup --url https://jobs.lever.co/umbrella/' in umbrella["suggestion"]
    assert r3["google"]["week"]["recall"] == 0.5 and r3["near"][0]["near_company"] == "Palantir"
    assert r3["summary"].startswith("Coverage (7 days): 50% of 6 sampled jobs found; 3 missed:")
    assert "Hooli" not in json.dumps(r3["misses"]) and r3["ineligible_stages"] == {"title": 1}


def test_the_summary_line_tops_the_morning_list(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"simplify-newgrad"})
    _sample(cfg, D1, [job("Software Engineer, New Grad", "Umbrella")])
    coverage.run(cfg, run_date=D1, now=NOW, log=lambda *a: None)
    s = JobsStore(state_path(cfg))
    assert coverage.summary_line(s, D1) == "Coverage: no Google sample decided yet (1 pending)"
    assert coverage.summary_line(s, "2026-11-04") is None
    s.close()
    _list_store = JobsStore(state_path(cfg))
    _list(_list_store, [it("x", title="Software Engineer, New Grad", company="Zed")], "2026-11-05T08:00:00Z",
          source_id="simplify-newgrad", snapshot="window")
    _list_store.close()
    rank.run(cfg, run_date=D1, dry_run=False, runner=lambda *a: (1, "", "no call in this test"), now=NOW, log=lambda *a: None)
    assert "Coverage: no Google sample decided yet (1 pending)" in rank.today(cfg, list_date=D1).splitlines()[1]
