import json
import sqlite3

import pytest

from debrief.http import Fetched
from debrief.jobs import build, collect, config as jconfig, items, profile, watch
from debrief.jobs.adapters import REGISTRY, Ctx
from debrief.jobs.adapters.lists import clean_url, md_date
from debrief.jobs.store import JobsStore, state_path
from debrief.store import raw
from test_jobs_profile import BASE

AT = "2026-11-04T08:00:00Z"


def rec(body, url="https://x.test/", status=200, each=None, meta=None, fetched_at=AT, source_id="s", seq=0):
    if not isinstance(body, (bytes, str)):
        body = json.dumps(body)
    if isinstance(body, str):
        body = body.encode()
    f = Fetched(url=url, final_url=url, status=status, headers={}, content=body,
                error="" if 200 <= status < 400 else f"HTTP {status}")
    return raw.make_record(source_id, "run", seq, f, fetched_at, each=each, meta=meta)


def src(**kw):
    base = {"id": "s", "name": "S", "family": "list", "url": "https://x.test/"}
    return jconfig._source({**base, **kw}, set())


# ------------------------------------------------------------------------------- normalizing

def test_company_and_title_norm():
    assert items.company_norm("The Scale AI, Inc.") == "scale ai"
    assert items.company_norm("Hugging Face SAS") == "hugging face sas"
    assert items.company_norm("Ben & Jerry's LLC") == "ben and jerry s"
    assert items.title_norm("Software Engineer, New Grad (2026)") == "software engineer new grad 2026"
    assert items.title_norm("Café Engineer — C++/C#") == "cafe engineer c++ c#"
    assert items.title_norm("Software Engineer 1") == items.title_norm("Software Engineer I") == "software engineer i"
    assert items.title_norm("Software Engineer – University Graduate - US") == "software engineer university graduate"
    assert items.title_norm("Analyst, United States") == "analyst" and items.title_norm("US") == "us"
    assert items.title_norm("Engineer 2026") == "engineer 2026"


@pytest.mark.parametrize("a,b,ok", [
    (["San Francisco, CA"], ["San Francisco, CA, USA"], True), (["NYC"], [], True),
    (["New York, NY"], ["Seattle, WA"], False), (["Remote"], ["Remote - US"], True),
    (["Austin, TX; Remote"], ["Remote"], True),
])
def test_locations_overlap(a, b, ok):
    assert items.locations_overlap(a, b) is ok


def test_clean_url_and_md_date():
    assert clean_url("https://jobs.lever.co/a/1?utm_source=Simplify&ref=Simplify&lever-source=x") == "https://jobs.lever.co/a/1"
    assert clean_url("https://stripe.com/jobs/1?gh_jid=123&utm_medium=x") == "https://stripe.com/jobs/1?gh_jid=123"
    assert md_date("5d", AT) == "2026-10-30T00:00:00Z" and md_date("0d", AT) == "2026-11-04T00:00:00Z"
    assert md_date("Nov 04", AT) == "2026-11-04T00:00:00Z" and md_date("Dec 20", AT) == "2025-12-20T00:00:00Z"
    assert md_date("2mo", AT) == "2026-09-05T00:00:00Z" and md_date("soon", AT) is None


# ---------------------------------------------------------------------------------- parsers

MD = """# Jobs
| Company | Position | Location | Salary | Posting | Age |
|---|---|---|---|---|---|
| <a href="https://acme.com"><strong>Acme</strong></a> | SWE New Grad | San Francisco, CA +2 | $150k/yr | <a href="https://jobs.lever.co/acme/0f1e2d3c-1111-2222-3333-444455556666?utm_source=x"><img alt="Apply"/></a> | 5d |
| ↳ | ML Engineer | New York, NY</br>Remote |  | <a href="https://jobs.lever.co/acme/1f1e2d3c-1111-2222-3333-444455556666">Apply</a> | 0d |

| Company | Job Title | Location | Work Model | Date Posted |
| ----- | --------- | --------- | ---- | ------- |
| **[Beta](https://beta.com)** | **[Data Analyst](https://jobright.ai/jobs/info/abc?utm_source=1103)** | Austin, TX, United States | Remote | Nov 04 |
"""


def test_md_table_parse():
    out = REGISTRY["md_table"].parse(rec(MD, each="repo"), src())
    assert [(i.company, i.title) for i in out] == [("Acme", "SWE New Grad"), ("Acme", "ML Engineer"), ("Beta", "Data Analyst")]
    a, b, c = out
    assert a.item_key == "job:lever:0f1e2d3c-1111-2222-3333-444455556666" and a.board == "lever:acme"
    assert a.locations == ("San Francisco, CA",) and a.extra["more_locations"] == 2 and a.pay == {"summary": "$150k/yr"}
    assert a.published_at == "2026-10-30T00:00:00Z" and a.scope == "repo"
    assert b.locations == ("New York, NY", "Remote")
    assert c.url == "https://jobright.ai/jobs/info/abc" and c.remote is True and c.board is None


def test_simplify_parse_flags_and_urls():
    body = [{"id": "a1", "title": "SWE", "company_name": "Acme", "locations": ["NYC"], "active": True, "is_visible": True,
             "url": "https://boards.greenhouse.io/acme/jobs/123?utm_source=Simplify", "date_posted": 1762243200, "sponsorship": "Offers Sponsorship"},
            {"id": "a2", "title": "Old", "company_name": "Acme", "active": False, "url": "x"},
            {"id": "a3", "title": "Hidden", "company_name": "Acme", "active": True, "is_visible": False, "url": "x"}, "junk"]
    out = REGISTRY["simplify_json"].parse(rec(body), src())
    assert [(i.item_key, i.active) for i in out] == [("a1", True), ("a2", False), ("a3", False)]
    assert out[0].url == "https://boards.greenhouse.io/acme/jobs/123" and out[0].board == "greenhouse:acme"
    assert out[0].published_at == "2025-11-04T08:00:00Z" and out[0].extra == {"sponsorship": "Offers Sponsorship"}


def test_hn_parsers():
    tree = {"children": [
        {"id": 1, "author": "a", "created_at": "2026-11-02T16:01:43.000Z",
         "text": "Acme | Founding Engineer | Remote (US) | $150k<p>We build things. Apply: https:&#x2F;&#x2F;jobs.ashbyhq.com&#x2F;acme"},
        {"id": 2, "author": None, "text": "deleted"},
        {"id": 3, "author": "b", "text": "No pipes in this first line<p>body"}]}
    out = REGISTRY["hn_whoishiring"].parse(rec(tree, url="https://hn.algolia.com/api/v1/items/9", meta={"story": "9", "title": "t"}), src())
    (i,) = out
    assert (i.company, i.title, i.locations, i.board) == ("Acme", "Acme | Founding Engineer | Remote (US) | $150k",
                                                          ("Remote (US)",), "ashby:acme")
    assert i.url == "https://news.ycombinator.com/item?id=1" and "We build things" in i.text
    job = {"id": 77, "type": "job", "title": "Widgets (YC S24) Is Hiring a Founding Engineer", "time": 1762243200,
           "url": "https://www.ycombinator.com/companies/widgets/jobs/x"}
    (j,) = REGISTRY["hn_jobs"].parse(rec(job, url="https://hacker-news.firebaseio.com/v0/item/77.json"), src())
    assert j.company == "Widgets" and j.item_key == "77"
    assert REGISTRY["hn_jobs"].parse(rec([1, 2], url="https://hacker-news.firebaseio.com/v0/jobstories.json"), src()) == []


def test_watchlist_parse_greenhouse_and_workday():
    gh = {"jobs": [{"id": 4012345008, "title": "Research Engineer", "first_published": "2026-11-03T10:00:00-05:00",
                    "location": {"name": "San Francisco, CA"}, "absolute_url": "https://job-boards.greenhouse.io/anthropic/jobs/4012345008",
                    "content": "&lt;p&gt;Build &amp;amp; ship.&lt;/p&gt;"}]}
    (i,) = REGISTRY["watchlist"].parse(rec(gh, each="greenhouse:anthropic", meta={"board": "greenhouse:anthropic", "company": "Anthropic"}), src())
    assert (i.item_key, i.job_key, i.company, i.scope) == ("greenhouse:4012345008", "job:greenhouse:4012345008", "Anthropic", "greenhouse:anthropic")
    assert i.text == "Build & ship." and i.published_at == "2026-11-03T15:00:00Z"
    wd = {"total": 1, "jobPostings": [{"title": "Engineer", "externalPath": "/job/US-CA-Santa-Clara/Engineer_JR1", "postedOn": "Posted 3 Days Ago",
                                       "locationsText": "US, CA, Santa Clara"}]}
    board = "workday:nvidia|wd5|NVIDIAExternalCareerSite"
    (w,) = REGISTRY["watchlist"].parse(rec(wd, each=board, meta={"board": board, "company": "NVIDIA", "page": 0}), src())
    assert w.item_key == "workday:nvidia/JR1" and w.published_at == "2026-11-01T00:00:00Z"
    assert w.url == "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Engineer_JR1"
    assert items.job_key(w) == "job:" + items.boards.job_from_url(w.url)       # the URL gives the same key


def test_api_parsers():
    muse = {"results": [{"id": 5, "name": "SWE I", "company": {"name": "Acme"}, "locations": [{"name": "New York, NY"}],
                         "publication_date": "2026-11-03T18:37:12Z", "refs": {"landing_page": "https://themuse.com/j/5?utm_x=1"},
                         "levels": [{"name": "Entry Level"}], "contents": "<p>Hi</p>"}]}
    (m,) = REGISTRY["themuse"].parse(rec(muse), src())
    assert (m.title, m.company, m.locations, m.text) == ("SWE I", "Acme", ("New York, NY",), "Hi")
    rok = [{"legal": "notice"}, {"id": "9", "position": "Backend Dev", "company": "R", "location": "Worldwide", "epoch": 1762243200,
                                 "url": "https://remoteok.com/remote-jobs/9", "salary_min": 100000}]
    (r,) = REGISTRY["remoteok"].parse(rec(rok), src())
    assert r.item_key == "9" and r.remote is True and r.pay["min"] == 100000
    rss = b"""<?xml version="1.0"?><rss version="2.0"><channel><item><title>Globex: Data Engineer</title>
    <link>https://weworkremotely.com/remote-jobs/globex-data</link><guid>g1</guid><pubDate>Wed, 04 Nov 2026 18:30:56 +0000</pubDate></item></channel></rss>"""
    (w,) = REGISTRY["wwr_rss"].parse(rec(rss), src())
    assert (w.company, w.title, w.item_key) == ("Globex", "Data Engineer", "g1")
    adz = {"results": [{"id": 11, "title": "<strong>Software</strong> Engineer", "company": {"display_name": "Initech"},
                        "location": {"display_name": "Washington, DC"}, "created": "2026-11-03T12:00:00Z",
                        "redirect_url": "https://www.adzuna.com/land/ad/11", "salary_min": 90000, "salary_is_predicted": "1"}]}
    (a,) = REGISTRY["adzuna"].parse(rec(adz), src())
    assert a.title == "Software Engineer" and a.pay["predicted"] is True
    usa = {"SearchResult": {"SearchResultItems": [{"MatchedObjectId": "8", "MatchedObjectDescriptor": {
        "PositionTitle": "IT Specialist", "OrganizationName": "NASA", "PositionLocation": [{"LocationName": "Washington, District of Columbia"}],
        "PublicationStartDate": "2026-11-03", "PositionURI": "https://www.usajobs.gov/job/8",
        "PositionRemuneration": [{"MinimumRange": "80000", "MaximumRange": "100000", "RateIntervalCode": "PA"}],
        "UserArea": {"Details": {"JobSummary": "Summary."}}}}]}}
    (u,) = REGISTRY["usajobs"].parse(rec(usa), src())
    assert (u.company, u.pay["period"], u.text) == ("NASA", "PA", "Summary.")


def test_profile_searches():
    from debrief.jobs.adapters.apis import searches
    p = json.loads(json.dumps(BASE))
    assert searches(p, src(options={})) == [("software engineer", "New York, NY"), ("software engineer", None)]
    assert searches(p, src(options={"places": ["Washington, DC"]})) == [("software engineer", "Washington, DC"), ("software engineer", None)]
    assert searches(None, src()) == []


# ---------------------------------------------------------------------------------- building

def it(key, title="SWE", company="Acme", locations=("New York, NY",), url="", active=True, scope="", **kw):
    return items.JobItem(item_key=key, title=title, company=company, locations=locations, url=url, active=active, scope=scope, **kw)


class FakeAdapter:
    def __init__(self, by_record, snapshot="window"):
        self.by_record, self.snapshot = by_record, snapshot

    def parse(self, record, source):
        return self.by_record[record["seq"]]

    def directory(self, record, source):
        return []

    def texts(self, record, source):
        return []


@pytest.fixture
def store(tmp_path):
    s = JobsStore(tmp_path / "jobs.sqlite")
    yield s
    s.close()


def apply(store, source_id, listings, snapshot="window", records=None, fetched_at=AT):
    records = records or [rec("{}", seq=0, fetched_at=fetched_at, each=listings[0].scope if listings else None)]
    with store.transaction():
        return build.apply(store, src(id=source_id), FakeAdapter({0: listings}, snapshot), records, ["ref#0"] * len(records))


def test_merge_by_job_key_then_fuzzy(store):
    c = apply(store, "watchlist", [it("greenhouse:123", url="https://job-boards.greenhouse.io/acme/jobs/123",
                                      job_key="job:greenhouse:123", board="greenhouse:acme")])
    assert c["postings_new"] == 1
    c = apply(store, "simplify", [it("s1", title="Software Engineer", url="https://acme.com/careers?gh_jid=123"),   # same job id
                                  it("s2", title="swe", locations=("New York",))])                                 # fuzzy match
    assert c["merged"] == 2 and c["postings_new"] == 0
    (pid,) = [r[0] for r in store.q("SELECT posting_id FROM postings")]
    p = store.posting(pid)
    assert p["url"] == "https://job-boards.greenhouse.io/acme/jobs/123" and p["title"] == "SWE"
    assert set(p["locations"]) == {"New York, NY", "New York"}


def _only_posting(store):
    (pid,) = [r[0] for r in store.q("SELECT posting_id FROM postings")]
    return store.posting(pid)


def test_link_prefers_the_companys_own_listing(store):
    # An aggregator's page first, then the company's own careers page (not a known board): the company wins.
    apply(store, "jobright", [it("j1", url="https://jobright.ai/jobs/info/abc")])
    apply(store, "simplify", [it("s1", url="https://apply.careers.microsoft.com/careers/job/197")])
    assert _only_posting(store)["url"] == "https://apply.careers.microsoft.com/careers/job/197"
    # A job-board page beats the company's other pages; the board's own listing (watchlist) beats both.
    gh = "https://job-boards.greenhouse.io/acme/jobs/123"
    apply(store, "speedyapply", [it("m1", url=gh + "/application?embed=true", job_key="job:greenhouse:123")])
    assert _only_posting(store)["url"] == gh + "/application?embed=true"
    apply(store, "watchlist", [it("greenhouse:123", url=gh, job_key="job:greenhouse:123", board="greenhouse:acme")])
    assert _only_posting(store)["url"] == gh
    # The board's listing goes away while the others stay open: the best open listing's link is used.
    apply(store, "watchlist", [it("greenhouse:123", url=gh, job_key="job:greenhouse:123", active=False)],
          fetched_at="2026-11-05T08:00:00Z")
    p = _only_posting(store)
    assert p["url"] == gh + "/application?embed=true" and p["closed_at"] is None


def test_link_ranks():
    r = build.link_rank
    assert r("watchlist", "https://x.test/", True) < r("s", "https://jobs.lever.co/a/0f1e2d3c-1111-2222-3333-444455556666", True)
    assert r("s", "https://careers.acme.com/job/1", True) < r("s", "https://go.vansh.nyc/P4", True)
    assert r("s", "https://www.linkedin.com/jobs/view/1", True)[1] == 3
    assert r("s", "https://go.vansh.nyc/P4", True) < r("watchlist", "https://x.test/", False)              # open beats gone
    newer, older = r("s", "https://t.test/2", True, "2026-08-04T00:00:00Z"), r("s", "https://t.test/1", True, "2026-08-03T00:00:00Z")
    assert newer < older                                                                                    # newest repost wins


def test_application_form_links_become_job_pages():
    u = "0f1e2d3c-1111-2222-3333-444455556666"
    assert clean_url(f"https://jobs.ashbyhq.com/acme/{u}/application?embed=true&utm_source=x") == f"https://jobs.ashbyhq.com/acme/{u}"
    assert clean_url(f"https://jobs.ashbyhq.com/acme/{u}/application") == f"https://jobs.ashbyhq.com/acme/{u}"
    assert clean_url(f"https://jobs.lever.co/acme/{u}/apply") == f"https://jobs.lever.co/acme/{u}"
    assert clean_url(f"https://jobs.eu.lever.co/acme/{u}/apply") == f"https://jobs.eu.lever.co/acme/{u}"
    assert clean_url("https://apply.workable.com/acme/j/912979C2F0/apply") == "https://apply.workable.com/acme/j/912979C2F0/"
    for same in ("https://www.amazon.jobs/jobs/10514428/apply", "https://x.applytojob.com/apply/RrI9QanYDY/Junior-Software-Engineer",
                 f"https://jobs.ashbyhq.com/acme/{u}"):
        assert clean_url(same) == same


def test_early_career_words():
    assert items.core_title("New Grad 2027: AI Security Engineer") == items.core_title("AI Security Engineer - 2027 New Grad")
    assert items.core_title("Software Engineer I") == items.core_title("Software Engineer, Early Career")
    assert items.core_title("Software Engineer II") != items.core_title("Software Engineer")
    assert items.early_career("Graduate Software Engineer") and items.early_career("Software Engineer 1")
    assert items.early_career("Data Scientist (2026)") and not items.early_career("AI Engineer")


EARLY = frozenset({"simplify", "vansh"})


def early_apply(store, source_id, listings):
    with store.transaction():
        return build.apply(store, src(id=source_id), FakeAdapter({0: listings}), [rec("{}", seq=0)], ["ref#0"],
                           early_sources=EARLY)


def test_early_career_second_chance(store):
    gh = "https://job-boards.greenhouse.io/acme/jobs/{}"
    early_apply(store, "simplify", [it("s1", title="AI Engineer New Grad", url=gh.format(1)),
                                    it("s2", title="Data Engineer", url=gh.format(2))])
    early_apply(store, "watchlist", [it("w1", title="Platform Engineer", job_key="job:greenhouse:3")])   # not early-career
    c = early_apply(store, "vansh", [it("v1", title="New Grad 2027: AI Engineer", url="https://go.vansh.nyc/A"),
                                     it("v2", title="New Grad: Data Engineer", url="https://go.vansh.nyc/B"),      # list => early
                                     it("v3", title="New Grad: Platform Engineer", url="https://go.vansh.nyc/C"),  # experienced posting
                                     it("v4", title="New Grad: AI Engineer", locations=("Austin, TX",))])         # another city
    assert c["merged"] == 2 and c["postings_new"] == 2
    p = store.posting(store.item("vansh", "v1")["posting_id"])
    assert p["title"] == "AI Engineer New Grad" and p["url"] == gh.format(1)
    # A job API (not a list) with no early-career words in the title gets no second chance.
    c = early_apply(store, "remoteok", [it("r1", title="Engineer, AI")])
    assert c["postings_new"] == 1
    # One candidate, but its job id differs: a different job.
    c = early_apply(store, "speedy", [it("m1", title="AI Engineer, Early Career", url=gh.format(9))])
    assert c["postings_new"] == 1


def test_early_career_second_chance_needs_exactly_one(store):
    early_apply(store, "simplify", [it("s1", title="Software Engineer New Grad", url="https://jobs.lever.co/a/0f1e2d3c-1111-2222-3333-444455556666"),
                                    it("s2", title="Software Engineer I", url="https://jobs.lever.co/a/9f1e2d3c-1111-2222-3333-444455556666")])
    c = early_apply(store, "vansh", [it("v1", title="New Grad: Software Engineer", url="https://go.vansh.nyc/A")])
    assert c["postings_new"] == 1                                                    # two candidates: left alone
    # A third similar role: still more than one candidate, so left alone.
    c = early_apply(store, "speedy", [it("m1", title="Software Engineer, Early Career",
                                         url="https://jobs.lever.co/a/1f1e2d3c-1111-2222-3333-444455556666")])
    assert c["postings_new"] == 1


def test_same_title_different_job_or_city_stays_apart(store):
    apply(store, "w", [it("1", url="https://jobs.lever.co/acme/0f1e2d3c-1111-2222-3333-444455556666")])
    c = apply(store, "l", [it("2", url="https://jobs.lever.co/acme/9f1e2d3c-1111-2222-3333-444455556666"),     # another job id
                           it("3", locations=("Seattle, WA",))])                                                # another city
    assert c["postings_new"] == 2 and store.one("SELECT COUNT(*) FROM postings")[0] == 3


def test_flag_closure_skip_and_reopen(store):
    c = apply(store, "simplify", [it("a"), it("old", title="Old", active=False)], snapshot="flag")
    assert c["new"] == 1 and c["skipped_inactive"] == 1
    c = apply(store, "simplify", [it("a", active=False)], snapshot="flag", fetched_at="2026-11-05T08:00:00Z")
    assert c["closed"] == 1
    (p,) = [store.posting(r[0]) for r in store.q("SELECT posting_id FROM postings")]
    assert p["closed_at"] == "2026-11-05T08:00:00Z"
    c = apply(store, "simplify", [it("a")], snapshot="flag", fetched_at="2026-11-06T08:00:00Z")
    assert c["reopened"] == 1


def test_full_snapshot_closes_missing_but_not_on_304_or_empty(store):
    apply(store, "md", [it("a", scope="r"), it("b", title="B", scope="r")], snapshot="full")
    c = apply(store, "md", [it("a", scope="r")], snapshot="full", records=[rec("x", status=304, each="r")])
    assert c.get("closed", 0) == 0                                  # a 304 says nothing
    c = apply(store, "md", [it("a", scope="r")], snapshot="full", records=[rec("x", each="r")])
    assert c["closed"] == 1 and c["gone"] == 1
    many = [it(str(i), title=f"T{i}", scope="r") for i in range(10)]
    apply(store, "md", many, snapshot="full")
    warnings = []
    with store.transaction():
        c = build.apply(store, src(id="md"), FakeAdapter({0: []}, "full"), [rec("x", each="r")], ["ref"], warn=warnings.append)
    assert c["close_guard"] == 1 and "nothing closed" in warnings[0]


# --------------------------------------------------------------------------- context safety

class StubClient:
    def __init__(self, robots=True, final="https://api.test/x?app_key=SECRETKEY1"):
        self.robots, self.final, self.calls = robots, final, []

    def robots_allowed(self, url):
        return self.robots

    def get(self, url, params=None, headers=None, etag=None, last_modified=None):
        self.calls.append((url, params, headers))
        return Fetched(url=url, final_url=self.final, status=500, error=f"HTTP 500 at {self.final}", content=b"")

    post = get


def test_ctx_redacts_keys_and_refuses_disallowed(tmp_path):
    s = JobsStore(tmp_path / "j.sqlite")
    source = src(auth={"params": {"app_key": "K"}}, headers={"User-Agent": "{contact}"})
    ctx = Ctx(StubClient(), s, {"K": "SECRETKEY1"}, "run", None, "me@example.com", {})
    r = ctx.get(source, "https://api.test/x")
    assert "SECRETKEY1" not in json.dumps(r) and "<redacted>" in r["final_url"]
    assert ctx.client.calls[0][1] == {"app_key": "SECRETKEY1"} and ctx.client.calls[0][2]["User-Agent"] == "me@example.com"
    ctx = Ctx(StubClient(robots=False), s, {}, "run", None, "", {})
    r = ctx.get(src(), "https://x.test/")
    assert ctx.client.calls == [] and r["status"] == 0 and "robots.txt disallows" in r["error"]
    s.close()


# --------------------------------------------------------------------- a whole run, and replay

class SiteClient:
    """Answers by URL from a dict; everything is allowed by robots.txt."""
    user_agent = "t"

    def __init__(self, pages):
        self.pages = pages

    def robots_allowed(self, url):
        return True

    def _answer(self, url):
        body = self.pages.get(url)
        if body is None:
            return Fetched(url=url, final_url=url, status=404, error="HTTP 404")
        return Fetched(url=url, final_url=url, status=200, headers={"content-type": "application/json"},
                       content=body if isinstance(body, bytes) else json.dumps(body).encode())

    def get(self, url, params=None, headers=None, etag=None, last_modified=None):
        return self._answer(url)

    def post(self, url, json_body=None, headers=None):
        return self._answer(url)

    def request(self, method, url, json_body=None, **kw):
        return self._answer(url)

    def bucket(self, url):
        return "x", 0


def _cfg(tmp_path, monkeypatch, sources):
    cfg = jconfig.load(env={"DEBRIEF_DATA_DIR": str(tmp_path)})
    picked = tuple(s for s in cfg.sources if s.id in sources)
    cfg = jconfig.JobsConfig(settings=cfg.settings, sources=picked, data_dir=cfg.data_dir, contact="me@example.com")
    prof = json.loads(json.dumps(BASE))
    prof["companies"] = [{"name": "Acme", "board": "greenhouse:acme"}]
    profile.save(cfg, prof)
    return cfg


def test_run_twice_no_duplicates_and_replay_matches(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"watchlist", "simplify-newgrad", "speedyapply-ai"})
    gh = {"jobs": [{"id": 123, "title": "SWE New Grad", "first_published": "2026-11-03T10:00:00Z", "location": {"name": "New York, NY"},
                    "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/123"}]}
    simplify = [{"id": "s1", "title": "Software Engineer New Grad", "company_name": "Acme", "locations": ["New York, NY"],
                 "url": "https://job-boards.greenhouse.io/acme/jobs/123?utm_source=Simplify", "active": True, "date_posted": 1762243200},
                {"id": "s2", "title": "Data Analyst", "company_name": "Beta", "locations": ["Remote"], "url": "https://beta.com/j/1",
                 "active": True, "date_posted": 1762243200}]
    pages = {"https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true&pay_transparency=true": gh,
             cfg.source("simplify-newgrad").url: simplify,
             cfg.source("speedyapply-ai").url: MD.encode()}
    logs = []
    r1 = collect.run(cfg, client=SiteClient(pages), run_date="2026-11-04", env={}, log=lambda *a, **k: logs.append(a))
    assert r1["status"] == "ok", r1
    r2 = collect.run(cfg, client=SiteClient(pages), run_date="2026-11-04", env={}, log=lambda *a, **k: None)
    assert r2["totals"].get("new", 0) == 0 and r2["totals"].get("postings_new", 0) == 0
    live = JobsStore(state_path(cfg))
    n_postings = live.one("SELECT COUNT(*) FROM postings")[0]
    assert n_postings == 5      # the greenhouse job (+ Simplify's copy), Beta, and 3 markdown rows
    pid = live.find_key("job:greenhouse:123")
    assert {r[0] for r in live.q("SELECT source_id FROM items WHERE posting_id=?", (pid,))} == {"watchlist", "simplify-newgrad"}
    live.close()
    into = tmp_path / "replay.sqlite"
    collect.replay(cfg, "2026-11-04", into, log=lambda *a: None)

    def dump(path):
        con = sqlite3.connect(path)
        out = {t: con.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall() for t in collect.TABLES}
        con.close()
        return out
    assert dump(state_path(cfg)) == dump(into)


def test_skipped_sources_are_reported(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"adzuna", "alert-inbox", "board-greenhouse", "remotive"})
    r = collect.run(cfg, client=SiteClient({}), run_date="2026-11-04", env={}, log=lambda *a, **k: None)
    assert {s["id"]: s["status"] for s in r["sources"]} == {
        "adzuna": "needs_key", "alert-inbox": "not_built", "board-greenhouse": "probe_only", "remotive": "disabled"}


# ---------------------------------------------------------------------------------- watchlist

def test_watch_add_picks_the_live_board_and_saves_a_version(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, set())
    s = JobsStore(state_path(cfg))
    with s.transaction():
        for b in ("greenhouse:globex", "lever:globex", "ashby:globex"):
            s.save_directory(b, "Globex", "globex", "universe-kalil", AT)
        s.save_directory("greenhouse:globexcorp", "Globex Corp", "globex corp", "universe-kalil", AT)
    pages = {"https://boards-api.greenhouse.io/v1/boards/globex/jobs": {"jobs": [{"id": 1, "title": "a"}]},
             "https://api.lever.co/v0/postings/globex?mode=json": [{"id": "x", "text": "a"}, {"id": "y", "text": "b"}]}
    entry, notes, version = watch.add(cfg, "Globex", client=SiteClient(pages), store=s)
    assert entry["board"] == "lever:globex" and version == 2
    assert any("ashby:globex: HTTP 404" in n for n in notes)
    with pytest.raises(watch.WatchError, match="already"):
        watch.add(cfg, "globex", client=SiteClient(pages), store=s)
    entry, notes, _ = watch.add(cfg, "Initech", client=SiteClient(pages), store=s)
    assert entry["board"] is None and "LinkedIn alert" in entry["note"]
    entry, _, _ = watch.add(cfg, "Hooli", url="https://boards.greenhouse.io/globex/jobs/1", client=SiteClient(pages), store=s)
    assert entry["board"] == "greenhouse:globex"
    assert watch.remove(cfg, "Initech") == 5
    assert [c["name"] for c in profile.latest(cfg)[1]["companies"]] == ["Acme", "Globex", "Hooli"]
    s.close()


def test_rebuild_reproduces_the_live_tables(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"simplify-newgrad"})
    simplify = [{"id": "s1", "title": "SWE", "company_name": "Acme", "url": "https://x.test/1", "active": True}]
    collect.run(cfg, client=SiteClient({cfg.source("simplify-newgrad").url: simplify}), run_date="2026-11-04", env={},
                log=lambda *a, **k: None)
    s = JobsStore(state_path(cfg))
    with s.transaction():                                   # a job already shown must stay shown
        s.save_highlighted("2026-11-04", "score-1", [(s.q("SELECT posting_id FROM postings")[0][0], "new")], ("new",))
    s.close()
    con = sqlite3.connect(state_path(cfg))
    before = {t: con.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall() for t in collect.TABLES + collect.STATE_TABLES}
    assert len(before["highlighted"]) == 1
    con.close()
    out = collect.rebuild(cfg, log=lambda *a: None)
    assert out["dates"] == ["2026-11-04"] and (state_path(cfg).parent / out["backup"]).exists()
    con = sqlite3.connect(state_path(cfg))
    after = {t: con.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall() for t in collect.TABLES + collect.STATE_TABLES}
    con.close()
    assert after == before


# ----------------------------------------------------------------------------- describe (M2)

from debrief.jobs.adapters import describe as describe_mod  # noqa: E402


def test_describe_texts_per_system():
    d = REGISTRY["describe"]
    gh = rec({"id": 123, "title": "SWE", "content": "&lt;p&gt;Build things. 1+ year of experience.&lt;/p&gt;",
              "pay_input_ranges": [{"min_cents": 10000000, "max_cents": 12000000, "currency_type": "USD"}]},
             meta={"system": "greenhouse", "board": "greenhouse:acme", "wanted": ["greenhouse:123"]})
    (key, text, facts), = d.texts(gh, src(id="describe"))
    assert key == "greenhouse:123" and "Build things" in text and facts["pay"]["max"] == 120000
    u = "0f1e2d3c-1111-2222-3333-444455556666"
    ashby = rec({"jobs": [{"id": u, "title": "SWE", "descriptionPlain": "Python", "employmentType": "FullTime"},
                          {"id": "9f1e2d3c-1111-2222-3333-444455556666", "title": "Other", "descriptionPlain": "x"}]},
                meta={"system": "ashby", "board": "ashby:acme", "wanted": [f"ashby:{u}"]})
    out = d.texts(ashby, src(id="describe"))
    assert out == [(f"ashby:{u}", "Python", {"employment": "FullTime", "pay": None})]       # only the wanted job
    wd = rec({"jobPostingInfo": {"jobDescription": "<p>Design chips</p>", "timeType": "Full time"}},
             meta={"system": "workday", "board": "workday:acme|wd1|Ext", "wanted": ["workday:acme/R1"]})
    assert d.texts(wd, src(id="describe")) == [("workday:acme/R1", "Design chips", {"employment": "Full time"})]
    assert d.texts(rec("", status=404, meta={"system": "lever"}), src(id="describe")) == []


def test_describe_workday_path():
    assert describe_mod._workday_path(["https://jobright.ai/x", "https://acme.wd1.myworkdayjobs.com/en-US/Ext/job/US-NY/SWE_R1"]) \
        == "/job/US-NY/SWE_R1"


def test_describe_targets_and_store(store, tmp_path):
    import copy
    prof = copy.deepcopy(BASE)
    gh = "https://job-boards.greenhouse.io/acme/jobs/{}"
    apply(store, "simplify", [it("s1", title="Software Engineer", url=gh.format(1), job_key="job:greenhouse:1",
                                 board="greenhouse:acme"),
                              it("s2", title="Senior Software Engineer", url=gh.format(2), job_key="job:greenhouse:2",
                                 board="greenhouse:acme"),                                   # filtered out: senior
                              it("s3", title="Software Engineer", company="Beta", url="https://beta.com/jobs/9")])  # no id
    from datetime import datetime, timezone
    found = describe_mod.targets(store, prof, datetime(2026, 11, 4, 12, tzinfo=timezone.utc))
    assert [(s, b, k) for s, b, k, _ in found] == [("greenhouse", "greenhouse:acme", "greenhouse:1")]
    r = rec({"id": 1, "title": "Software Engineer", "content": "Python and SQL"},
            meta={"system": "greenhouse", "board": "greenhouse:acme", "wanted": ["greenhouse:1"]})
    with store.transaction():
        c = build.apply(store, src(id="describe", family="detail", url=None), REGISTRY["describe"], [r], ["ref#0"])
    assert c["texts"] == 1
    pid = store.find_key("job:greenhouse:1")
    assert store.one("SELECT text FROM posting_text WHERE posting_id=?", (pid,))[0] == "Python and SQL"
    assert describe_mod.targets(store, prof, datetime(2026, 11, 4, 12, tzinfo=timezone.utc)) == []    # described now
