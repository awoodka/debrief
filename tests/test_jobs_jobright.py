import gzip
import json

import pytest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from conftest import fixture, record
from debrief.http import Fetched
from debrief.jobs import build, collect, filters, profile, score
from debrief.jobs.adapters import REGISTRY
from debrief.jobs.adapters.describe import NEXT_DATA, jobright_kept, jobright_text
from debrief.jobs.store import JobsStore, state_path
from test_jobs_collect import FakeAdapter, _cfg, it, src
from test_jobs_profile import BASE

JR = "https://jobright.ai/jobs/info/{}"
DESCRIBE = src(id="describe", family="detail", url=None)


def page(job):
    data = {"props": {"pageProps": {"dataSource": {"jobResult": job}}}}
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>'


def texts(body, listing=("jobright-newgrad", "k1")):
    rec = record(body, meta={"system": "jobright", "listing": list(listing)})
    return REGISTRY["describe"].texts(rec, DESCRIBE)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_described_the_job_is_cut_for_what_its_title_hid():
    (ref, text, facts), = texts(fixture("jobright/job_info.html"))
    assert ref == ("jobright-newgrad", "k1") and facts == {"employment": "Full-time"}
    now = datetime.now(timezone.utc)
    job = {"title": "Associate Software Engineer", "company": "Boeing", "locations": ["New York, NY"],
           "published_at": _iso(now), "first_seen_at": _iso(now), "data": {}}
    assert filters.check(job, BASE, now).ok
    v = filters.check(job, BASE, now, text=text)
    assert (v.ok, v.stage) == (False, "clearance")
    assert filters.min_years(text) == 1


def test_the_scorers_excerpt_starts_at_the_requirements():
    (_, text, _), = texts(fixture("jobright/job_info.html"))
    excerpt = score.qualifications(text, 600)
    assert excerpt.startswith("Requirements") and "1+ years" in excerpt


def test_a_preferred_qualification_is_not_a_requirement():
    job = {"jobSummary": "Acme is hiring a Software Engineer.", "employmentType": "Full-time",
           "qualifications": {"mustHave": ["Bachelor's degree in Computer Science"],
                              "preferredHave": ["3+ years of experience with distributed systems"]}}
    (_, text, _), = texts(page(job))
    assert filters.min_years(text) is None
    job["qualifications"]["mustHave"].append("2+ years of experience with Python")
    (_, text, _), = texts(page(job))
    assert filters.min_years(text) == 2


def test_a_page_with_no_job_is_unreadable(tmp_path):
    s = JobsStore(tmp_path / "j.sqlite")
    rec = record("<html><body>Not found</body></html>", meta={"system": "jobright", "listing": ["jobright-newgrad", "k1"]})
    with s.transaction():
        c = build.apply(s, DESCRIBE, REGISTRY["describe"], [rec], ["ref#0"])
    assert c["unreadable"] == 1 and c["texts"] == 0 and rec["status"] == 0
    s.close()


class Pages:
    user_agent = "t"

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url, params=None, headers=None, etag=None, last_modified=None):
        self.asked.append(url)
        body = self.pages.get(url)
        if body is None:
            return Fetched(url=url, final_url=url, status=404, error="HTTP 404")
        return Fetched(url=url, final_url=url, status=200, content=body if isinstance(body, bytes) else json.dumps(body).encode())


def test_a_night_reads_the_newest_pages_no_board_reaches(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"jobright-newgrad", "describe"})
    s = JobsStore(state_path(cfg))
    now = datetime.now(timezone.utc)
    for hours, listing in ((3, it("a", title="Software Engineer", company="Alpha", url=JR.format("aa"))),
                           (2, it("b", title="Software Engineer", company="Beta", url=JR.format("bb"))),
                           (1, it("c", title="Software Engineer", company="Gamma", url=JR.format("cc"))),
                           (1, it("d", title="Software Engineer", company="Acme", url=JR.format("dd"),
                                  job_key="job:greenhouse:7", board="greenhouse:acme"))):
        with s.transaction():
            build.apply(s, src(id="jobright-newgrad"), FakeAdapter({0: [listing]}),
                        [record("{}", fetched_at=_iso(now - timedelta(hours=hours)))], ["ref#0"])
    pid = {k: s.item("jobright-newgrad", k)["posting_id"] for k in "abcd"}
    gh = "https://boards-api.greenhouse.io/v1/boards/acme/jobs/7?pay_transparency=true"
    client = Pages({gh: {"id": 7, "title": "Software Engineer", "content": "Python and SQL"},
                    JR.format("aa"): fixture("jobright/job_info.html"), JR.format("cc"): fixture("jobright/job_info.html")})
    source = replace(cfg.source("describe"), options={"max_requests": 300, "max_pages": {"jobright": 2}})
    _, prof = profile.latest(cfg)

    def night(run_id):
        client.asked.clear()
        return collect.collect_source(cfg, s, client, source, run_id, now.date().isoformat(), now, prof, {},
                                      lambda *a, **k: None)

    out = night("run-1")
    assert sorted(client.asked) == sorted([gh, JR.format("cc"), JR.format("bb")])
    assert out["status"] == "ok" and any("stopped at 2 jobright pages" in w for w in out["warnings"])
    assert "1+ years" in s.texts_for(pid["c"]) and s.texts_for(pid["d"]) == "Python and SQL"
    assert s.texts_for(pid["b"]) is None and "gone" not in s.posting(pid["b"])["data"]
    assert s.posting(pid["c"])["data"]["employment"] == "Full-time"

    night("run-2")
    assert client.asked == [JR.format("aa")]
    assert "1+ years" in s.texts_for(pid["a"])
    s.close()


def with_people(html):
    data = json.loads(NEXT_DATA.search(html).group(1))
    job = data["props"]["pageProps"]["dataSource"]["jobResult"]
    job["socialConnections"] = [{"firstName": "Jane", "fullName": "Jane Example", "jobTitle": "Engineer",
                                 "linkedinUrl": "https://www.linkedin.com/in/jane-example/"}]
    job["jobRecruiter"], job["jobRecruiterProfileUrl"] = "Rob Recruiter", "https://www.linkedin.com/in/rob-recruiter"
    data["props"]["pageProps"]["dataSource"]["companyResult"] = {
        "companyName": "Boeing", "leadership": [{"pname": "Lee Leader", "plinkedinUrl": "https://www.linkedin.com/in/lee-leader"}]}
    blob = json.dumps(data)
    return html.replace(NEXT_DATA.search(html).group(1), blob).replace(
        "<body>", f'<body><script id="jobright-helper-job-detail-info" type="application/json">{blob}</script>'
                  '<div class="leaderName">Lee Leader</div><a href="https://www.linkedin.com/in/lee-leader">in</a>')


PEOPLE_SEEN = ("Jane Example", "Rob Recruiter", "Lee Leader", "linkedin.com/in/")


def test_a_stored_page_keeps_the_job_and_none_of_its_people(tmp_path, monkeypatch):
    clean = fixture("jobright/job_info.html").decode()
    served = with_people(clean)
    assert all(p in served for p in PEOPLE_SEEN)
    assert jobright_text(jobright_kept(clean)) == jobright_text(jobright_kept(served)) == jobright_text(clean)

    cfg = _cfg(tmp_path, monkeypatch, {"jobright-newgrad", "describe"})
    s = JobsStore(state_path(cfg))
    now = datetime.now(timezone.utc)
    with s.transaction():
        build.apply(s, src(id="jobright-newgrad"), FakeAdapter({0: [it("a", title="Software Engineer", url=JR.format("aa"))]}),
                    [record("{}", fetched_at=_iso(now))], ["ref#0"])
    _, prof = profile.latest(cfg)
    out = collect.collect_source(cfg, s, Pages({JR.format("aa"): served.encode()}), cfg.source("describe"), "run-1",
                                 now.date().isoformat(), now, prof, {}, lambda *a, **k: None)
    assert out["counts"]["texts"] == 1
    assert s.texts_for(s.item("jobright-newgrad", "a")["posting_id"]) == jobright_text(clean)[0]
    stored = b"".join(gzip.open(f).read() for f in (cfg.data_dir / "raw").rglob("*.jsonl.gz")).decode()
    assert "1+ years" in stored
    assert not [p for p in PEOPLE_SEEN if p in stored]
    s.close()


def test_a_night_reads_what_can_still_make_tonights_list_first(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"jobright-newgrad", "describe"})
    s = JobsStore(state_path(cfg))
    now = datetime.now(timezone.utc)
    yesterday = (now - timedelta(days=1)).date().isoformat()
    lever = "https://api.lever.co/v0/postings/acme/{}?mode=json"
    ashby = "https://api.ashbyhq.com/posting-api/job-board/gizmo?includeCompensation=true"
    for key, company, seen, posted, kw in (
            ("old", "Alpha", 30, 0, {"job_key": "job:lever:old", "board": "lever:acme"}),
            ("shown", "Beta", 10, 0, {"job_key": "job:lever:shown", "board": "lever:acme"}),
            ("stale", "Gamma", 2, 10, {"job_key": "job:lever:stale", "board": "lever:acme"}),
            ("new1", "Delta", 3, 0, {"job_key": "job:lever:new1", "board": "lever:acme"}),
            ("new2", "Eps", 4, 0, {"job_key": "job:ashby:new2", "board": "ashby:gizmo"}),
            ("shownjr", "Zeta", 5, 0, {"url": JR.format("ee")})):
        listing = it(key, title="Software Engineer", company=company, published_at=_iso(now - timedelta(days=posted)), **kw)
        with s.transaction():
            build.apply(s, src(id="jobright-newgrad"), FakeAdapter({0: [listing]}),
                        [record("{}", fetched_at=_iso(now - timedelta(hours=seen)))], ["ref#0"])
    with s.transaction():
        s.save_highlighted(yesterday, "score-1", [(s.item("jobright-newgrad", k)["posting_id"], "new") for k in ("shown", "shownjr")],
                           ("new",))
        s.set_meta("score_cursor", _iso(now - timedelta(hours=20)))
        s.set_meta("score_cursor_date", yesterday)
    client = Pages({})
    source = replace(cfg.source("describe"), options={"max_requests": 3, "max_pages": {"jobright": 60}})
    _, prof = profile.latest(cfg)
    out = collect.collect_source(cfg, s, client, source, "run-1", now.date().isoformat(), now, prof, {}, lambda *a, **k: None)
    assert client.asked == [lever.format("new1"), ashby, lever.format("stale")]
    assert any("stopped at 3 requests" in w for w in out["warnings"])
    s.close()


class Answers(Pages):
    def get(self, url, params=None, headers=None, etag=None, last_modified=None):
        self.asked.append(url)
        status, body = self.pages.get(url, (404, ""))
        return Fetched(url=url, final_url=url, status=status, content=body.encode(), error="" if status == 200 else f"HTTP {status}")


CHALLENGE = (200, "<html><body>Checking your browser</body></html>")
READ = (200, fixture("jobright/job_info.html").decode())


def jobright_nights(tmp_path, monkeypatch, answers):
    """Postings listed only by jobright, newest first in `answers` order; night() runs describe once and
    returns the pages it asked."""
    cfg = _cfg(tmp_path, monkeypatch, {"jobright-newgrad", "describe"})
    s = JobsStore(state_path(cfg))
    now = datetime.now(timezone.utc)
    for hours, key in enumerate(answers, 1):
        with s.transaction():
            build.apply(s, src(id="jobright-newgrad"),
                        FakeAdapter({0: [it(key, title="Software Engineer", company=f"Co {key}", url=JR.format(key))]}),
                        [record("{}", fetched_at=_iso(now - timedelta(hours=hours)))], ["ref#0"])
    client = Answers({JR.format(k): v for k, v in answers.items()})
    _, prof = profile.latest(cfg)
    runs = iter(range(1, 10))

    def night():
        client.asked.clear()
        out = collect.collect_source(cfg, s, client, cfg.source("describe"), f"run-{next(runs)}", now.date().isoformat(),
                                     now, prof, {}, lambda *a, **k: None)
        return [u.rsplit("/", 1)[1] for u in client.asked], out
    return s, night


@pytest.mark.parametrize("answer,why", [(CHALLENGE, "a page with no job data (jobright's challenge)"),
                                        ((429, "Too Many Requests"), "HTTP 429 after retries")])
def test_a_challenge_or_block_ends_the_nights_jobright_reads(tmp_path, monkeypatch, answer, why):
    s, night = jobright_nights(tmp_path, monkeypatch, {"aa": READ, "bb": answer, "cc": READ})
    asked, out = night()
    assert asked == ["aa", "bb"]
    assert f"jobright.ai blocked us ({why}); not asked again this run" in out["warnings"]
    asked, _ = night()
    assert asked == ["bb"]
    s.close()


def test_an_empty_job_result_is_a_job_jobright_no_longer_has(tmp_path, monkeypatch):
    s, night = jobright_nights(tmp_path, monkeypatch, {"aa": (200, page({})), "bb": (200, page(None)), "cc": READ})
    asked, out = night()
    assert asked == ["aa", "bb", "cc"] and out["warnings"] == []
    assert [e for e in out["errors"] if JR.format("aa") in e or JR.format("bb") in e] == []
    asked, _ = night()
    assert asked == []
    s.close()


def test_a_greenhouse_id_no_guess_finds_a_board_for_gets_its_jobright_page(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"jobright-newgrad", "describe"})
    s = JobsStore(state_path(cfg))
    now = datetime.now(timezone.utc)
    for source_id, listings in (
            ("simplify-newgrad", [it("p", title="Software Engineer", company="Precisely", url="https://www.precisely.com/job/?gh_jid=13"),
                                  it("b", title="Software Engineer", company="Blueriver", url="https://www.blueriver.com/job/?gh_jid=11")]),
            ("jobright-newgrad", [it("pj", title="Software Engineer", company="Precisely", url=JR.format("cc")),
                                  it("bj", title="Software Engineer", company="Blueriver", url=JR.format("dd"))])):
        with s.transaction():
            build.apply(s, src(id=source_id), FakeAdapter({0: listings}), [record("{}", fetched_at=_iso(now - timedelta(hours=1)))],
                        ["ref#0"])
    pid = {k: s.item("simplify-newgrad", k)["posting_id"] for k in "pb"}
    assert pid == {"p": s.item("jobright-newgrad", "pj")["posting_id"], "b": s.item("jobright-newgrad", "bj")["posting_id"]}
    gh = "https://boards-api.greenhouse.io/v1/boards/{}/jobs/{}?pay_transparency=true"
    client = Pages({gh.format("blueriver", 11): {"id": 11, "title": "Software Engineer", "content": "Cameras"},
                    JR.format("cc"): fixture("jobright/job_info.html"), JR.format("dd"): fixture("jobright/job_info.html")})
    _, prof = profile.latest(cfg)
    collect.collect_source(cfg, s, client, cfg.source("describe"), "run-1", now.date().isoformat(), now, prof, {},
                           lambda *a, **k: None)
    assert gh.format("precisely", 13) in client.asked and JR.format("cc") in client.asked
    assert JR.format("dd") not in client.asked
    assert "1+ years" in s.texts_for(pid["p"]) and s.texts_for(pid["b"]) == "Cameras"
    s.close()
