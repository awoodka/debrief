import json
import sqlite3
from dataclasses import replace

import pytest

from debrief.http import Fetched
from debrief.jobs import collect, prescore, profile
from debrief.jobs.adapters.yc import YcCompany, companies_from_hits, parse_title, targets
from debrief.jobs.store import JobsStore, state_path
from test_jobs_collect import AT, _cfg


@pytest.mark.parametrize("title,job,found", [
    ("Launch HN: Stack Auth (YC S24) – An Open-Source Auth0/Clerk Alternative", False, ("Stack Auth", "S24", None)),
    ("Show HN: Fume (YC W24) – bot to test all of your pull requests", False, ("Fume", "W24", None)),
    ("Show HN: Monoid – Open-Source Privacy Automation (YC S21)", False, ("Monoid", "S21", None)),
    ("Mozart Data (YC S20) Is Hiring a Senior Back End Engineer", False, ("Mozart Data", "S20", "Senior Back End Engineer")),
    ("Nango (YC W23, dev infra) is hiring staff back end engineers", True, ("Nango", "W23", "staff back end engineers")),
    ("OfOne (YC W23) – voice AI for drive-thrus – is hiring a founding engineer in SF", True,
     ("OfOne", "W23", "founding engineer in SF")),
    ("Opalite Health (YC W26) Is Hiring – Founding GTM", True, ("Opalite Health", "W26", "Founding GTM")),
    ("Tinfoil (YC P25) is hiring an applied AI security engineer", True, ("Tinfoil", "P25", "applied AI security engineer")),
    ("Evidence (YC S21) Is Hiring", True, ("Evidence", "S21", None)),
    ("9 Mothers (YC P26) Is Hiring in Austin, TX", True, ("9 Mothers", "P26", None)),
    ("DeepSource (YC W20) is looking for a Senior Front-end engineer", True, ("DeepSource", "W20", None)),
    ("Kalshi (YC W19) Paid Influencers to Target Polymarket CEO After FBI Raid", False, None),
    ("Justin Kan's Biggest Lessons from Founding Atrium (YC W18)", False, None),
    ("Show HN: Canary (YC) – Independent verification for AI code", False, None),
    ("Ask HN: Why was BeerMe selected by for YC F24?", False, None),
])
def test_a_title_names_a_yc_company_only_in_hns_forms_and_the_role_it_hires_for(title, job, found):
    assert parse_title(title, job=job) == found


def hit(title, at, url=None, job=False):
    return {"title": title, "created_at_i": at, "url": url, "_tags": ["job" if job else "story"]}


def test_one_company_per_name_with_the_boards_its_posts_link_to():
    hits = [hit("Widgets (YC W24) Is Hiring a Founding Engineer", 3, "https://jobs.lever.co/widgets/1f2e", job=True),
            hit("Launch HN: widgets (YC W24) – Widgets for everyone", 1, "https://widgets.example"),
            hit("Widgets (YC W24) is hiring designers", 2, "https://jobs.ashbyhq.com/widgets-inc/abc", job=True),
            hit("Coinbase (YC S12) just made a deal", 4)]
    assert companies_from_hits(hits) == [YcCompany("Widgets", "W24", ("ashby:widgets-inc", "lever:widgets"))]


def test_a_board_must_spell_the_name_or_be_linked():
    directory = {"stack auth": ["ashby:stack-auth", "lever:stackauthio"], "stackauth": ["greenhouse:stackauth"],
                 "laminar": ["lever:runlaminar"], "kalshi": ["smartrecruiters:Kalshi"], "dover": ["workday:dover|wd1|Dover"],
                 "beta": ["greenhouse:beta"], "acme": ["greenhouse:acme2"]}
    companies = [YcCompany("Stack Auth", "S24"), YcCompany("Laminar", "S24"), YcCompany("Kalshi", "W19"),
                 YcCompany("Dover", "S19"), YcCompany("Rivet", "W23", ("workday:rivet|wd1|Careers",)),
                 YcCompany("Fume", "W24", ("lever:fumeco",)), YcCompany("Beta", "S21"),
                 YcCompany("Acme", "S20", ("ashby:acme",))]
    found = targets(companies, lambda n: directory.get(n, []), watched_boards={"greenhouse:beta"}, watched_names={"acme"})
    assert {b: c.name for b, c in found.items()} == {
        "ashby:stack-auth": "Stack Auth", "greenhouse:stackauth": "Stack Auth", "workday:rivet|wd1|Careers": "Rivet",
        "lever:fumeco": "Fume"}


SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
WIDGETS = "https://boards-api.greenhouse.io/v1/boards/widgets/jobs?content=true&pay_transparency=true"
GIZMO = "https://api.ashbyhq.com/posting-api/job-board/gizmo?includeCompensation=true"
ACME = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true&pay_transparency=true"
TERRA = "https://api.lever.co/v0/postings/terra?mode=json"
BLANK = "https://api.lever.co/v0/postings/blank?mode=json"
DEAD = "https://api.lever.co/v0/postings/deadco?mode=json"
HITS = {"hits": [hit("Widgets (YC W24) Is Hiring a Founding Engineer", 1762200000, "https://www.ycombinator.com/companies/widgets", True),
                 hit("Gizmo (YC F25) – Founding Engineer", 1762100000, "https://jobs.ashbyhq.com/gizmo/9b1c", True),
                 hit("Acme (YC S20) is hiring", 1762000000, None, True),
                 hit("Terra (YC W21) Is Hiring AI Engineers", 1761900000, None, True),
                 hit("Blank (YC S22) Is Hiring", 1761800000, None, True),
                 hit("Deadco (YC W22) – Engineer", 1761700000, "https://jobs.lever.co/deadco/1f2e", True)]}
NEW_GRAD = {"jobs": [{"id": 501, "title": "Software Engineer, New Grad", "first_published": "2026-11-03T10:00:00Z",
                      "location": {"name": "New York, NY"}, "absolute_url": "https://job-boards.greenhouse.io/widgets/jobs/501",
                      "content": "Build widgets. Backed by Y Combinator."}]}
GIZMO_JOBS = {"jobs": [{"id": "9b1c", "title": "Founding Engineer", "location": "San Francisco", "publishedAt": "2026-11-03T10:00:00Z",
                        "jobUrl": "https://jobs.ashbyhq.com/gizmo/9b1c", "descriptionPlain": "Build gizmos."}]}
TERRA_JOBS = [{"id": "77aa", "text": "Video Game Localizer", "createdAt": 1762160000000, "hostedUrl": "https://jobs.lever.co/terra/77aa",
               "categories": {"location": "Worldwide"}, "descriptionPlain": "Translate games."}]


class Answers:
    user_agent = "t"

    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, params=None, headers=None, etag=None, last_modified=None):
        self.calls.append(url)
        body = self.pages.get(SEARCH if url.startswith(SEARCH + "?") else url)
        if body is None:
            return Fetched(url=url, final_url=url, status=404, error="HTTP 404")
        return Fetched(url=url, final_url=url, status=200, headers={"content-type": "application/json"},
                       content=json.dumps(body).encode())

    def searches(self):
        return sum(u.startswith(SEARCH) for u in self.calls)

    def boards(self):
        return sorted(u for u in self.calls if not u.startswith(SEARCH))


def night(cfg, client, refresh_days=7):
    source = replace(cfg.source("yc-boards"), options={**cfg.source("yc-boards").options, "refresh_days": refresh_days})
    cfg = replace(cfg, sources=(source,))
    return collect.run(cfg, client=client, run_date="2026-11-04", env={}, log=lambda *a, **k: None)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"yc-boards"})
    s = JobsStore(state_path(cfg))
    with s.transaction():
        s.save_directory("greenhouse:widgets", "Widgets", "widgets", "universe-kalil", AT)
        s.save_directory("greenhouse:acme", "Acme", "acme", "universe-kalil", AT)
        s.save_directory("lever:terra", "Terra", "terra", "universe-kalil", AT)
        s.save_directory("lever:blank", "Blank", "blank", "universe-kalil", AT)
    s.close()
    return cfg


def test_only_confirmed_boards_are_read_and_a_dead_one_waits_for_the_refresh(cfg):
    pages = {SEARCH: HITS, WIDGETS: NEW_GRAD, GIZMO: GIZMO_JOBS, TERRA: TERRA_JOBS, BLANK: [], ACME: NEW_GRAD}
    client = Answers(pages)
    report = night(cfg, client)
    assert report["status"] == "ok", report
    assert client.searches() == 1 and client.boards() == sorted([WIDGETS, GIZMO, TERRA, BLANK, DEAD])
    s = JobsStore(state_path(cfg))
    assert {key: s.posting(s.find_key(key))["data"].get("yc") for key in ("job:greenhouse:501", "job:ashby:9b1c")} == {
        "job:greenhouse:501": "W24", "job:ashby:9b1c": "F25"}
    assert s.find_key("job:lever:77aa") is None
    widgets = s.posting(s.find_key("job:greenhouse:501"))
    assert widgets["company"] == "Widgets" and not prescore.watched(widgets, profile.latest(cfg)[1])
    s.close()

    client = Answers(pages)
    night(cfg, client)
    assert client.searches() == 0 and client.boards() == sorted([WIDGETS, GIZMO, BLANK])
    client = Answers(pages)
    night(cfg, client, refresh_days=0)
    assert client.searches() == 1 and client.boards() == sorted([WIDGETS, GIZMO, BLANK, DEAD])


def test_the_list_is_refreshed_only_when_due_and_a_failed_refresh_keeps_it(cfg):
    night(cfg, Answers({SEARCH: HITS, WIDGETS: NEW_GRAD}))
    client = Answers({WIDGETS: {"jobs": []}})
    report = night(cfg, client)
    assert client.searches() == 0 and WIDGETS in client.calls and report["totals"].get("closed") == 1
    client = Answers({WIDGETS: NEW_GRAD})
    report = night(cfg, client, refresh_days=0)
    assert client.searches() == 1 and WIDGETS in client.calls and GIZMO not in client.calls
    assert any("wasn't refreshed" in w for s in report["sources"] for w in s.get("warnings", []))


def test_replay_rebuilds_the_same_postings(cfg, tmp_path):
    night(cfg, Answers({SEARCH: HITS, WIDGETS: NEW_GRAD}))
    into = tmp_path / "replay.sqlite"
    collect.replay(cfg, "2026-11-04", into, log=lambda *a: None)

    def dump(path):
        con = sqlite3.connect(path)
        out = {t: con.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall() for t in ("postings", "posting_keys", "items")}
        con.close()
        return out
    assert dump(state_path(cfg)) == dump(into) and dump(into)["postings"]
