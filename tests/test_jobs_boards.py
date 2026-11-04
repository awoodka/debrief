import pytest

from debrief.jobs import boards


@pytest.mark.parametrize("url,board", [
    ("https://boards.greenhouse.io/anthropic/jobs/4012345008", "greenhouse:anthropic"),
    ("https://job-boards.greenhouse.io/Stripe/jobs/123?gh_src=x", "greenhouse:stripe"),
    ("https://boards-api.greenhouse.io/v1/boards/figma/jobs", "greenhouse:figma"),
    ("https://boards.greenhouse.io/embed/job_app?for=Databricks&token=7012345", "greenhouse:databricks"),
    ("https://boards.greenhouse.io/embed/job_app?token=7012345", None),
    ("https://wd5.myworkdaysite.com/recruiting/acme/Careers/job/US-Remote/Engineer_R1", "workday:acme|wd5|Careers"),
    ("https://wd5.myworkdaysite.com/en-US/other", None),
    ("https://jobs.lever.co/palantir/0f1e2d3c", "lever:palantir"),
    ("https://jobs.ashbyhq.com/openai/abc-123/application", "ashby:openai"),
    ("https://apply.workable.com/huggingface/j/ABC123/", "workable:huggingface"),
    ("https://apply.workable.com/j/ABC123", None),
    ("https://jobs.smartrecruiters.com/Visa/744000012345678", "smartrecruiters:Visa"),
    ("https://1x.recruitee.com/o/software-engineer", "recruitee:1x"),
    ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Engineer_JR1",
     "workday:nvidia|wd5|NVIDIAExternalCareerSite"),
    ("https://Acme.WD1.myworkdayjobs.com/External", "workday:acme|wd1|External"),
    ("https://careers.google.com/jobs/results/123", None),
    ("not a url", None),
])
def test_board_from_url(url, board):
    assert boards.board_from_url(url) == board


def test_universe_rows_both_dataset_shapes():
    kalil = [{"name": "0x", "slug": "0x", "url": "https://jobs.ashbyhq.com/0x"}, {"name": "bad"}]
    assert boards.universe_rows("ashby", kalil) == ["ashby:0x"]
    wd_kalil = [{"name": "23andMe", "slug": "23andme/23", "url": "https://23andme.wd5.myworkdayjobs.com/23"}]
    assert boards.universe_rows("workday", wd_kalil) == ["workday:23andme|wd5|23"]
    assert boards.universe_rows("workday", ["2020companies|wd1|external_careers"]) == ["workday:2020companies|wd1|external_careers"]
    assert boards.universe_rows("greenhouse", ["Figma"]) == ["greenhouse:figma"]


def test_list_requests():
    assert boards.list_request("greenhouse:anthropic") == ("GET", "https://boards-api.greenhouse.io/v1/boards/anthropic/jobs", None)
    m, u, body = boards.list_request("workday:nvidia|wd5|NVIDIAExternalCareerSite", page=2)
    assert m == "POST" and u == "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs"
    assert body["offset"] == 40 and body["limit"] == 20
    assert boards.list_request("smartrecruiters:Visa", 1)[1].endswith("limit=100&offset=100")
    assert boards.pages_needed("workday", 41) == 3 and boards.pages_needed("workday", 0) == 1
    assert boards.pages_needed("greenhouse", None) == 1


@pytest.mark.parametrize("text,age", [("Posted Today", 0), ("Posted Yesterday", 1), ("Posted 3 Days Ago", 3),
                                      ("Posted 30+ Days Ago", 30), ("", None)])
def test_workday_age(text, age):
    assert boards.workday_age_days(text) == age


def test_parse_list_each_system():
    gh = {"jobs": [{"title": "SWE", "first_published": "2026-11-03T10:00:00-05:00", "location": {"name": "NYC"}}],
          "meta": {"total": 1}}
    (g,), total = boards.parse_list("greenhouse", gh)
    assert total is None and g["id"] is None
    assert {k: g[k] for k in ("title", "published", "location", "locations")} == {
        "title": "SWE", "published": "2026-11-03T15:00:00Z", "location": "NYC", "locations": ["NYC"]}
    lever = [{"text": "ML Eng", "createdAt": 1762200000000, "categories": {"location": "SF"}}]
    jobs, _ = boards.parse_list("lever", lever)
    assert jobs[0]["title"] == "ML Eng" and jobs[0]["published"].startswith("2025-11-03") and jobs[0]["location"] == "SF"
    ashby = {"jobs": [{"title": "Founding Eng", "publishedAt": "2026-11-02T00:00:00Z", "location": None, "isRemote": True}]}
    assert boards.parse_list("ashby", ashby)[0][0]["location"] == "Remote"
    wk = {"jobs": [{"title": "Data", "published_on": "2026-11-01", "city": "Austin", "country": "United States",
                    "telecommuting": True}]}
    assert boards.parse_list("workable", wk)[0][0]["location"] == "Austin, United States (remote)"
    sr = {"totalFound": 250, "content": [{"name": "PM", "releasedDate": "2026-11-01T09:00:00.000Z",
                                          "location": {"city": "Austin", "country": "us"}}]}
    jobs, total = boards.parse_list("smartrecruiters", sr)
    assert total == 250 and jobs[0]["published"] == "2026-11-01T09:00:00Z"
    rc = {"offers": [{"title": "Eng", "published_at": "2026-11-01 12:00:00 UTC", "city": "Oslo", "remote": False}]}
    r = boards.parse_list("recruitee", rc)[0][0]
    assert {k: r[k] for k in ("title", "published", "location")} == {"title": "Eng", "published": "2026-11-01T12:00:00Z", "location": "Oslo"}
    wd = {"total": 41, "jobPostings": [{"title": "Engineer", "postedOn": "Posted Today", "locationsText": "US, CA"}]}
    jobs, total = boards.parse_list("workday", wd)
    assert total == 41 and jobs[0]["age_days"] == 0 and jobs[0]["published"] is None
    with pytest.raises((KeyError, ValueError)):
        boards.parse_list("greenhouse", {"error": "not found"})


@pytest.mark.parametrize("value,iso", [
    ("2026-10-29 08:16:53 UTC", "2026-10-29T08:16:53Z"), ("2026-10-29T08:16:53.000+00:00", "2026-10-29T08:16:53Z"),
    ("2026-05-29", "2026-05-29T00:00:00Z"), (1762200000000, "2025-11-03T20:00:00Z"), (1762200000, "2025-11-03T20:00:00Z"),
    ("Wed, 04 Nov 2026 18:30:56 GMT", "2026-11-04T18:30:56Z"), (None, None), ("Posted Today", None),
])
def test_when(value, iso):
    assert boards.when(value) == iso
