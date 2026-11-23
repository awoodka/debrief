import gzip
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from conftest import record
from debrief.http import Fetched
from debrief.jobs import boards, build, collect, profile
from debrief.jobs.adapters import REGISTRY
from debrief.jobs.store import JobsStore, state_path
from test_jobs_collect import FakeAdapter, _cfg, it, src
from test_jobs_finder import BoardClient, _list, _night
from test_jobs_jobright import CHALLENGE, JR, READ

LI = "https://www.linkedin.com/jobs/view/{}/"
LI_GUEST = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Answers:
    """Answers by URL: a body (200), or (status, body), or (status, body, final URL); 404 otherwise.
    Records what was asked."""
    user_agent = "t"

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url, params=None, headers=None, etag=None, last_modified=None):
        key = (url, params["offset"]) if params and "offset" in params else url
        self.asked.append(key)
        answer = self.pages.get(key, (404, ""))
        status, body, final = (200, answer, url) if not isinstance(answer, tuple) else (*answer, url)[:3]
        if not isinstance(body, (bytes, str)):
            body = json.dumps(body)
        return Fetched(url=url, final_url=final, status=status, content=body.encode() if isinstance(body, str) else body,
                       headers={"content-type": "text/html"}, error="" if status == 200 else f"HTTP {status}")

    def post(self, url, json_body=None, headers=None):
        return self.get(url)


def linkedin_page(description="<p>Build our APIs in Python.</p><p>Bachelor's degree in Computer Science.</p>",
                  seniority="Entry level", employment="Full-time"):
    """LinkedIn's guest job page, shaped like the live one, with the people a page can name."""
    criteria = "".join(
        f'<li class="description__job-criteria-item"><h3 class="description__job-criteria-subheader">{k}</h3>'
        f'<span class="description__job-criteria-text description__job-criteria-text--criteria">{v}</span></li>'
        for k, v in (("Seniority level", seniority), ("Employment type", employment),
                     ("Job function", "Engineering and Information Technology"), ("Industries", "Software Development")))
    return f'''<section class="top-card-layout"><div class="top-card-layout__card">
<a href="https://www.linkedin.com/company/acme"><img class="artdeco-entity-image" src="https://media.example/logo.png" alt="Acme"></a>
<h2 class="top-card-layout__title">Software Engineer</h2><span class="num-applicants__caption">Over 200 applicants</span></div></section>
<div class="decorated-job-posting__details">
<section class="core-section-container description"><div class="core-section-container__content">
<div class="description__text description__text--rich"><section class="show-more-less-html">
<div class="show-more-less-html__markup">{description}</div></section></div>
<ul class="description__job-criteria-list">{criteria}</ul></div></section>
<section class="core-section-container message-the-recruiter"><h2>Meet the hiring team</h2>
<img src="https://media.example/pat.jpg" alt="Pat Poster"><a href="https://www.linkedin.com/in/pat-poster">Pat Poster</a>
<span>Recruiter at Acme</span></section>
<section class="core-section-container find-a-referral"><div class="face-pile"><img alt="Riley Referral"
src="https://media.example/riley.jpg"></div><p>Riley Referral and 12 others work here</p></section></div>'''


PEOPLE = ("Pat Poster", "Riley Referral", "linkedin.com/in/", "recruiting@example.com", "media.example")


def nights(tmp_path, monkeypatch, listings, answers, options=None):
    """Postings from the alert inbox, newest first in `listings` order ([(key, url)]); night() runs describe
    once and returns (what it asked, its report entry)."""
    cfg = _cfg(tmp_path, monkeypatch, {"alert-inbox", "jobright-newgrad", "describe"})
    s = JobsStore(state_path(cfg))
    now = datetime.now(timezone.utc)
    for hours, (key, url, *more) in enumerate(listings, 1):
        with s.transaction():
            build.apply(s, src(id=more[0] if more else "alert-inbox"),
                        FakeAdapter({0: [it(key, title="Software Engineer", company=f"Co {key}", url=url,
                                            board=boards.board_from_url(url))]}),
                        [record("{}", fetched_at=_iso(now - timedelta(hours=hours)))], ["ref#0"])
    client = Answers(answers)
    source = cfg.source("describe")
    if options is not None:
        source = replace(source, options={**source.options, **options})
    _, prof = profile.latest(cfg)
    runs = iter(range(1, 10))

    def night():
        client.asked.clear()
        out = collect.collect_source(cfg, s, client, source, f"run-{next(runs)}", now.date().isoformat(), now, prof, {},
                                     lambda *a, **k: None)
        return list(client.asked), out
    return cfg, s, night


def stored(cfg):
    return b"".join(gzip.open(f).read() for f in (cfg.data_dir / "raw").rglob("*.jsonl.gz")).decode()


# ------------------------------------------------------------------------------------- LinkedIn

def test_a_linkedin_job_is_described_from_its_guest_page_without_its_people(tmp_path, monkeypatch):
    text = ("<p>Build our APIs in Python.</p><p>Bachelor's degree in Computer Science.</p>"
            "<p>Questions: recruiting@example.com or https://www.linkedin.com/in/pat-poster</p>")
    cfg, s, night = nights(tmp_path, monkeypatch, [("linkedin:4000000001", LI.format(4000000001))],
                           {LI_GUEST.format(4000000001): linkedin_page(text)})
    assert all(p in linkedin_page(text) for p in PEOPLE)
    asked, out = night()
    assert asked == [LI_GUEST.format(4000000001)] and out["counts"]["texts"] == 1
    pid = s.item("alert-inbox", "linkedin:4000000001")["posting_id"]
    described = s.texts_for(pid)
    assert "Build our APIs in Python." in described and "Bachelor's degree" in described
    assert s.posting(pid)["data"]["employment"] == "Full-time" and s.posting(pid)["data"]["seniority"] == "Entry level"
    raw_text = stored(cfg)
    assert "Engineering and Information Technology" in raw_text
    assert [p for p in PEOPLE if p in raw_text + described] == []
    assert "Over 200 applicants" not in raw_text
    s.close()


@pytest.mark.parametrize("answer,why", [((999, ""), "HTTP 999"), ((429, ""), "HTTP 429 after retries"),
                                        ((200, "<html>Sign in</html>", "https://www.linkedin.com/authwall?trk=x"),
                                         "a login wall")])
def test_a_linkedin_block_stops_linkedin_for_the_night_but_not_other_sites(tmp_path, monkeypatch, answer, why):
    cfg, s, night = nights(tmp_path, monkeypatch, [("linkedin:4000000001", LI.format(4000000001)),
                                                   ("linkedin:4000000002", LI.format(4000000002)),
                                                   ("jr", JR.format("aa"), "jobright-newgrad")],
                           {LI_GUEST.format(4000000001): answer, LI_GUEST.format(4000000002): linkedin_page(),
                            JR.format("aa"): READ[1]})
    asked, out = night()
    assert asked == [LI_GUEST.format(4000000001), JR.format("aa")]
    assert f"www.linkedin.com blocked us ({why}); not asked again this run" in out["warnings"]
    assert out["blocked"] == [{"host": "www.linkedin.com", "why": why, "by": "describe", "skipped": 0}]
    asked, _ = night()
    assert asked == [LI_GUEST.format(4000000001)]
    s.close()


def test_linkedin_pages_stop_at_the_nights_cap(tmp_path, monkeypatch):
    _, s, night = nights(tmp_path, monkeypatch, [("linkedin:4000000001", LI.format(4000000001)),
                                                 ("linkedin:4000000002", LI.format(4000000002))],
                         {LI_GUEST.format(4000000001): linkedin_page(), LI_GUEST.format(4000000002): linkedin_page()},
                         options={"max_pages": {"jobright": 60, "linkedin": 1}})
    asked, out = night()
    assert asked == [LI_GUEST.format(4000000001)] and "stopped at 1 linkedin pages; 1 wait for the next run" in out["warnings"]
    asked, _ = night()
    assert asked == [LI_GUEST.format(4000000002)]
    s.close()


def test_a_job_whose_jobright_page_is_blocked_is_read_on_linkedin(tmp_path, monkeypatch):
    cfg, s, night = nights(tmp_path, monkeypatch, [("jr", JR.format("aa"), "jobright-newgrad")],
                           {JR.format("aa"): CHALLENGE, LI_GUEST.format(4000000001): linkedin_page()})
    pid = s.item("jobright-newgrad", "jr")["posting_id"]
    with s.transaction():
        build.apply(s, src(id="alert-inbox"), FakeAdapter({0: [it("linkedin:4000000001", title="Software Engineer",
                                                                  company="Co jr", url=LI.format(4000000001))]}),
                    [record("{}", fetched_at=_iso(datetime.now(timezone.utc)))], ["ref#0"])
    assert s.item("alert-inbox", "linkedin:4000000001")["posting_id"] == pid
    asked, _ = night()
    assert asked == [JR.format("aa"), LI_GUEST.format(4000000001)] and "Build our APIs" in s.texts_for(pid)
    s.close()


# ------------------------------------------------------------------------------- SmartRecruiters

SR_JOB = "https://api.smartrecruiters.com/v1/companies/Acme/postings/{}"


def sr_posting(job_id, title="Software Engineer"):
    return {"id": job_id, "name": title, "company": {"identifier": "Acme", "name": "Acme"},
            "location": {"city": "New York", "region": "NY", "country": "us"},
            "typeOfEmployment": {"label": "Full-time"}, "experienceLevel": {"label": "Entry Level"},
            "creator": {"name": "Casey Creator", "avatarUrl": "https://media.example/casey.jpg"},
            "jobAd": {"sections": {"companyDescription": {"title": "Company Description", "text": "<p>Acme makes tools.</p>"},
                                   "jobDescription": {"title": "Job Description", "text": "<p>Ship Python services.</p>"},
                                   "qualifications": {"title": "Qualifications", "text": "<ul><li>BS in CS</li></ul>"}}}}


def test_a_smartrecruiters_job_is_described_from_its_posting_without_its_creator(tmp_path, monkeypatch):
    url = "https://jobs.smartrecruiters.com/Acme/744000000000001-software-engineer"
    cfg, s, night = nights(tmp_path, monkeypatch, [("sr", url, "jobright-newgrad")],
                           {SR_JOB.format("744000000000001"): sr_posting("744000000000001")})
    asked, out = night()
    assert asked == [SR_JOB.format("744000000000001")]
    pid = s.item("jobright-newgrad", "sr")["posting_id"]
    text, data = s.texts_for(pid), s.posting(pid)["data"]
    assert text.startswith("Job Description\nShip Python services.") and "BS in CS" in text and "Acme makes tools" not in text
    assert (data["employment"], data["experience"], data["board_country"]) == ("Full-time", "Entry Level", "us")
    assert "Casey" not in stored(cfg) and "media.example" not in stored(cfg)
    s.close()


def test_a_watched_smartrecruiters_board_is_read_page_by_page_and_closes_only_after_a_full_read(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"watchlist"})
    prof = profile.latest(cfg)[1]
    prof["companies"] = [{"name": "Acme", "board": "smartrecruiters:Acme"}]
    profile.save(cfg, prof)
    page_url = "https://api.smartrecruiters.com/v1/companies/Acme/postings?limit=100&offset={}"

    def board(n, total=None):
        jobs = [{"id": str(744000000000000 + i), "name": f"Engineer {i}", "company": {"identifier": "Acme"},
                 "releasedDate": "2026-11-22T10:00:00Z", "location": {"city": "New York", "region": "NY", "country": "us"}}
                for i in range(n)]
        return {page_url.format(0): {"totalFound": total or n, "content": jobs[:100]},
                page_url.format(100): {"totalFound": total or n, "content": jobs[100:]}}

    def run(pages, day):
        client = Answers(pages)
        report = collect.run(cfg, client=client, run_date=day, env={}, log=lambda *a, **k: None)
        return client.asked, report["sources"][0]

    asked, out = run(board(150), "2026-11-22")
    assert asked == [page_url.format(0), page_url.format(100)] and out["counts"]["items"] == 150
    s = JobsStore(state_path(cfg))
    first = s.item("watchlist", "smartrecruiters:744000000000000")
    assert json.loads(first["data"])["url"] == "https://jobs.smartrecruiters.com/Acme/744000000000000"
    s.close()
    lost = {**board(149, 150), page_url.format(100): (429, "")}
    asked, out = run(lost, "2026-11-23")
    assert out["counts"].get("closed", 0) == 0 and out["status"] == "partial"
    asked, out = run(board(149), "2026-11-23")
    assert out["counts"]["closed"] == 1


def test_the_board_finder_lists_a_smartrecruiters_board(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {build.FINDER, "alert-inbox"})
    s = JobsStore(state_path(cfg))
    now = datetime.now(timezone.utc)
    with s.transaction():
        s.save_directory("smartrecruiters:Acme", "Acme", "acme", "universe", _iso(now - timedelta(hours=2)))
    _list(s, [it("linkedin:4000000001", title="Software Engineer", company="Acme", url=LI.format(4000000001))],
          _iso(now - timedelta(hours=1)), source_id="alert-inbox", snapshot="window")
    listing = "https://api.smartrecruiters.com/v1/companies/Acme/postings?limit=100&offset=0"
    client = BoardClient({listing: {"totalFound": 1, "content": [sr_posting("744000000000001")]}})
    _night(cfg, s, client, _iso(now))
    assert client.calls == [("GET", listing)]
    pid = s.item("alert-inbox", "linkedin:4000000001")["posting_id"]
    assert s.find_key("job:smartrecruiters:744000000000001") == pid
    s.close()


# ----------------------------------------------------------------------------------- Workday

WD_PAGE = "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/Careers/job/New-York-NY/Software-Engineer_{}"
WD_LINK = "https://acme.wd1.myworkdayjobs.com/en-US/Careers/job/New-York-NY/Software-Engineer_{}"
WITHHELD = json.dumps({"errorCode": "S22", "errorCaseId": "X", "httpStatus": 403, "message": "permission denied"})
WD_JOB = {"jobPostingInfo": {"jobDescription": "<p>Python and SQL.</p>", "timeType": "Full time",
                             "country": {"descriptor": "United States of America"}}}


@pytest.mark.parametrize("first,blocks", [((403, WITHHELD), False), ((403, "<html>Access Denied</html>"), True)])
def test_workdays_own_403_for_one_job_is_not_a_block(tmp_path, monkeypatch, first, blocks):
    _, s, night = nights(tmp_path, monkeypatch, [("wd1", WD_LINK.format("R1"), "jobright-newgrad"),
                                                 ("wd2", WD_LINK.format("R2"), "jobright-newgrad")],
                         {WD_PAGE.format("R1"): first, WD_PAGE.format("R2"): WD_JOB})
    asked, out = night()
    assert asked == ([WD_PAGE.format("R1")] if blocks else [WD_PAGE.format("R1"), WD_PAGE.format("R2")])
    assert bool(out.get("blocked")) is blocks
    asked, _ = night()
    assert asked == ([WD_PAGE.format("R1")] if blocks else [])
    s.close()


# -------------------------------------------------------------------------------------- Amazon

AMAZON = "https://www.amazon.jobs/en/search.json"


def amazon_job(n, posted, title=None):
    title = title or ("Software Development Engineer I" if n == 1 else f"Software Development Engineer I, Team {n}")
    return {"id_icims": str(3100000 + n), "title": title, "posted_date": posted, "job_schedule_type": "full-time",
            "job_path": f"/en/jobs/{3100000 + n}/software-development-engineer",
            "normalized_location": "Arlington, Virginia, USA",
            "locations": [json.dumps({"normalizedLocation": "Arlington, Virginia, USA"}),
                          json.dumps({"normalizedLocation": "Seattle, Washington, USA"})],
            "description": "Build services for customers.<br/>Work with a team.",
            "basic_qualifications": "- Bachelor's degree in computer science<br/>- Experience programming in Java",
            "preferred_qualifications": "- Experience with AWS"}


def test_amazon_reads_the_newest_jobs_page_by_page_until_they_get_old(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"amazon"})
    source = replace(cfg.source("amazon"), options={**cfg.source("amazon").options, "page_size": 2})
    today = datetime.now(timezone.utc).strftime("%B %d, %Y").replace(" 0", " ")
    old = (datetime.now(timezone.utc) - timedelta(days=6)).strftime("%B %d, %Y")
    client = Answers({(AMAZON, 0): {"hits": 9, "jobs": [amazon_job(1, today), amazon_job(2, today)]},
                      (AMAZON, 2): {"hits": 9, "jobs": [amazon_job(3, today), amazon_job(4, old)]},
                      (AMAZON, 4): {"hits": 9, "jobs": [amazon_job(5, old), amazon_job(6, old)]}})
    s = JobsStore(state_path(cfg))
    now = datetime.now(timezone.utc)
    out = collect.collect_source(cfg, s, client, source, "run-1", now.date().isoformat(), now, None, {}, lambda *a, **k: None)
    assert client.asked == [(AMAZON, 0), (AMAZON, 2)] and out["counts"]["postings_new"] == 4
    row = s.item("amazon", "amazon:3100001")
    p = s.posting(row["posting_id"])
    assert (p["company"], p["title"]) == ("Amazon", "Software Development Engineer I")
    assert p["locations"] == ["Arlington, Virginia, USA", "Seattle, Washington, USA"]
    assert json.loads(row["data"])["url"] == "https://www.amazon.jobs/en/jobs/3100001/software-development-engineer"
    assert p["published_at"] == now.strftime("%Y-%m-%dT00:00:00Z") and p["data"]["employment"] == "full-time"
    assert s.texts_for(p["posting_id"]).startswith("Build services for customers.\nWork with a team.\nBasic qualifications\n")
    s.close()


def test_amazon_asks_for_its_categories_in_the_us_newest_first(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"amazon"})
    (url, params, _), = REGISTRY["amazon"].requests(None, cfg.source("amazon"))
    assert url == AMAZON and (params["sort"], params["country"], params["offset"]) == ("recent", "USA", 0)
    assert params["category[]"] == ["software-development", "machine-learning-science", "data-science", "business-intelligence"]
