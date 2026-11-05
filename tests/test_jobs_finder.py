"""The board finder (src/debrief/jobs/adapters/finder.py): the match rule, a night end to end
(found job -> job id, the company's link, a description; the list still decides closing), the
fetch-history rules that keep it from asking twice, and the markdown fixes found with it."""
import json
from datetime import datetime, timedelta, timezone

from debrief.http import Fetched
from debrief.jobs import build, collect, items
from debrief.jobs.adapters import finder
from debrief.jobs.adapters.lists import _cells, _links, _text
from debrief.jobs.store import JobsStore, state_path
from test_jobs_collect import _cfg, it, rec, src  # noqa: F401
from test_jobs_collect import FakeAdapter

U1, U2, U3 = ("0f1e2d3c-1111-2222-3333-44445555666" + c for c in "123")


def T(title, place="New York, NY", early=False):
    return {"title": title, "locations": [place], "early": early}


def J(id_, title, place="New York, NY", board="ashby:acme"):
    return (None, board, {"id": id_, "title": title, "locations": [place]})


# ------------------------------------------------------------------------------------- the match

def test_match_needs_the_same_core_title_and_place():
    pool = [J(U1, "Software Engineer (New Grad)"), J(U2, "Product Engineer")]
    assert finder.match(T("New Grad 2027: Software Engineer"), pool)[2]["id"] == U1
    assert finder.match(T("Software Engineer", "Austin, TX"), pool) is None          # another city
    assert finder.match(T("Senior Software Engineer"), pool) is None                 # another core title
    assert finder.match(T("New Grad"), pool) is None                                 # no core title at all


def test_match_tie_breaks_and_refuses_to_guess():
    twins = [J(U1, "Software Engineer – Query Engines"), J(U2, "Software Engineer, New Grad - Query Engines")]
    assert finder.match(T("Software Engineer, Query Engines", early=True), twins)[2]["id"] == U2
    assert finder.match(T("Software Engineer, Query Engines", early=False), twins) is None
    two_levels = [J(U1, "Software Engineer I"), J(U2, "Software Engineer I")]       # both early: can't tell
    assert finder.match(T("Software Engineer I", early=True), two_levels) is None
    wd = [J("R1", "Data Scientist", board="workday:hss|wd1|HSS_Careers"),
          J("R1", "Data Scientist", board="workday:hss|wd1|hss_careers")]           # one job, two spellings
    assert finder.match(T("Data Scientist"), wd)[2]["id"] == "R1"
    several = [J("R2", "Data Analyst", place="2 Locations", board="workday:acme|wd1|Ext")]
    assert finder.match(T("Data Analyst"), several)[2]["id"] == "R2"                # places unknown, not a city


def test_metro_names_overlap_their_city():
    assert items.locations_overlap(["SF"], ["San Francisco, CA"])
    assert items.locations_overlap(["SF", "NYC"], ["New York, NY"])
    assert not items.locations_overlap(["SF"], ["Seattle, WA"])


def test_early_careers_words():
    assert items.early_career("Software Engineer - Early Careers")
    assert items.core_title("Software Engineer - Early Careers") == items.core_title("New Grad 2027: Software Engineer")
    assert finder.search_text("New Grad 2027: AI/ML Engineer") == "ai ml engineer"


def test_company_boards_skip_excluded_and_unreadable_and_fold_workday_case(tmp_path):
    s = JobsStore(tmp_path / "j.sqlite")
    with s.transaction():
        for b in ("workday:hss|wd1|HSS_Careers", "workday:hss|wd1|hss_careers", "smartrecruiters:HSS", "greenhouse:hss"):
            s.save_directory(b, "HSS", "hss", "universe", "2026-11-01T00:00:00Z")
        s.save_directory("workable:amazon", "Amazon", "amazon", "universe", "2026-11-01T00:00:00Z")
    assert finder.company_boards(s, {"company": "HSS", "board": None}) == ["greenhouse:hss", "workday:hss|wd1|HSS_Careers"]
    assert finder.company_boards(s, {"company": "Amazon", "board": None}, exclude={"amazon"}) == []
    s.close()


def test_target_stays_out_of_the_content_hash():
    plain = it("k1", title="SWE")
    assert "target" not in plain.as_dict()
    assert items.content_hash(plain) != items.content_hash(it("k1", title="SWE", target="p:1"))


# ----------------------------------------------------------------------------- a night, end to end

class BoardClient:
    """Answers GETs by URL and Workday searches by (URL, searchText); counts requests."""
    user_agent = "t"

    def __init__(self, pages, searches=None):
        self.pages, self.searches, self.calls = pages, searches or {}, []

    def robots_allowed(self, url):
        return True

    def _answer(self, url, body):
        if body is None:
            return Fetched(url=url, final_url=url, status=404, error="HTTP 404")
        return Fetched(url=url, final_url=url, status=200, headers={"content-type": "application/json"},
                       content=json.dumps(body).encode())

    def get(self, url, params=None, headers=None, etag=None, last_modified=None):
        self.calls.append(("GET", url))
        return self._answer(url, self.pages.get(url))

    def post(self, url, json_body=None, headers=None):
        self.calls.append(("POST", url, json_body.get("searchText")))
        return self._answer(url, self.searches.get((url, json_body.get("searchText"))))


ASHBY = "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"
LEVER = "https://api.lever.co/v0/postings/acme?mode=json"
WD = "https://globex.wd1.myworkdayjobs.com/wday/cxs/globex/Ext/jobs"


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NIGHTS = []


def _night(cfg, store, client, when):
    """One run of the finder alone; returns its report entry (run id: run-<when>-<n>)."""
    source = next(s for s in cfg.sources if s.id == build.FINDER)
    from debrief.jobs import profile
    _, prof = profile.latest(cfg)
    NIGHTS.append(f"run-{when}-{len(NIGHTS)}")
    return collect.collect_source(cfg, store, client, source, NIGHTS[-1], when[:10], datetime.now(timezone.utc),
                                  prof, {}, lambda *a, **k: None)


def _list(store, listings, at, source_id="jobright-newgrad", snapshot="full"):
    with store.transaction():
        return build.apply(store, src(id=source_id), FakeAdapter({0: listings}, snapshot),
                           [rec("{}", seq=0, fetched_at=at, each="list")], ["ref#0"])


def test_a_night_finds_the_job_links_it_and_the_list_still_closes_it(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {build.FINDER, "jobright-newgrad"})
    s = JobsStore(state_path(cfg))
    earlier = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    with s.transaction():
        s.save_directory("ashby:acme", "Acme", "acme", "universe", earlier)
        s.save_directory("lever:acme", "Acme", "acme", "universe", earlier)
        s.save_directory("workday:globex|wd1|Ext", "Globex", "globex", "universe", earlier)
    jr = "https://jobright.ai/jobs/info/{}"
    _list(s, [it("a", title="New Grad: Software Engineer", url=jr.format(1), scope="list"),
              it("b", title="Software Engineer I", company="Globex", url=jr.format(2), scope="list"),
              it("c", title="Product Manager", url=jr.format(3), scope="list")], earlier)
    pages = {ASHBY: {"jobs": [{"id": U1, "title": "Software Engineer (New Grad)", "location": "New York, NY",
                               "jobUrl": f"https://jobs.ashbyhq.com/acme/{U1}", "descriptionPlain": "Python. New grads welcome."},
                              {"id": U2, "title": "Software Engineer", "location": "New York, NY",
                               "jobUrl": f"https://jobs.ashbyhq.com/acme/{U2}", "descriptionPlain": "5+ years."}]},
             LEVER: None}                                                    # a dead board: 404
    path = "/job/New-York/Software-Engineer-I_R77"
    searches = {(WD, "software engineer"): {"total": 1, "jobPostings": [
        {"title": "Software Engineer I", "externalPath": path, "locationsText": "New York, NY"}]}}
    client = BoardClient(pages, searches)
    out = _night(cfg, s, client, _iso(datetime.now(timezone.utc)))
    assert out["status"] == "ok" and out["counts"]["merged"] == 2 and out["counts"].get("postings_new", 0) == 0
    assert sorted(c[0] for c in client.calls) == ["GET", "GET", "POST"]

    acme = s.find_key(f"job:ashby:{U1}")
    assert acme and s.posting(acme)["title"] == "New Grad: Software Engineer"     # the list's posting, now with an id
    assert s.posting(acme)["url"] == f"https://jobs.ashbyhq.com/acme/{U1}"        # the company's page beats jobright
    assert s.texts_for(acme) == "Python. New grads welcome."                      # the new-grad twin, not "5+ years"
    globex = s.find_key("job:workday:globex/R77")
    assert s.posting(globex)["url"] == "https://globex.wd1.myworkdayjobs.com/Ext" + path
    assert s.posting(globex)["board"] == "workday:globex|wd1|Ext"                 # so describe can read it

    # The same night again: every board was answered after these postings appeared, so nothing is asked.
    client.calls.clear()
    _night(cfg, s, client, _iso(datetime.now(timezone.utc)))
    assert client.calls == []

    # The list drops the job: the posting closes, though the finder's listing never went inactive.
    _list(s, [it("b", title="Software Engineer I", company="Globex", url=jr.format(2), scope="list"),
              it("c", title="Product Manager", url=jr.format(3), scope="list")], _iso(datetime.now(timezone.utc)))
    assert s.posting(acme)["closed_at"] is not None and s.posting(globex)["closed_at"] is None
    s.close()


def test_a_later_posting_asks_again_and_a_dead_board_rests(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {build.FINDER, "jobright-newgrad"})
    s = JobsStore(state_path(cfg))
    now = datetime.now(timezone.utc)
    with s.transaction():
        s.save_directory("ashby:acme", "Acme", "acme", "universe", _iso(now))
        s.save_directory("lever:acme", "Acme", "acme", "universe", _iso(now))
    _list(s, [it("a", title="Software Engineer, Data", url="https://jobright.ai/jobs/info/1", scope="list")],
          _iso(now - timedelta(hours=1)))
    client = BoardClient({ASHBY: {"jobs": []}, LEVER: None})
    _night(cfg, s, client, _iso(now))
    assert sorted(client.calls) == [("GET", ASHBY), ("GET", LEVER)]
    client.calls.clear()
    _list(s, [it("a", title="Software Engineer, Data", url="https://jobright.ai/jobs/info/1", scope="list"),
              it("d", title="Software Engineer, Platform", url="https://jobright.ai/jobs/info/4", scope="list")],
          _iso(now + timedelta(hours=1)))                                         # first seen after the last look
    _night(cfg, s, client, _iso(now))
    assert client.calls == [("GET", ASHBY)]                                       # lever:acme 404'd: it rests
    s.close()


def test_replay_rebuilds_the_finders_links(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {build.FINDER, "jobright-newgrad"})
    s = JobsStore(state_path(cfg))
    with s.transaction():
        s.save_directory("ashby:acme", "Acme", "acme", "universe", _iso(datetime.now(timezone.utc)))
    _list(s, [it("a", title="Software Engineer", url="https://jobright.ai/jobs/info/1", scope="list")],
          _iso(datetime.now(timezone.utc) - timedelta(hours=1)))
    client = BoardClient({ASHBY: {"jobs": [{"id": U3, "title": "Software Engineer", "location": "New York, NY",
                                            "jobUrl": f"https://jobs.ashbyhq.com/acme/{U3}"}]}})
    when = _iso(datetime.now(timezone.utc))
    _night(cfg, s, client, when)
    from debrief.store import raw
    rel, records = raw.runs_for_day(cfg.data_dir, when[:10])[NIGHTS[-1]][build.FINDER]
    assert records[0]["meta"]["found"] == {f"ashby:{U3}": {"posting": s.find_key(f"job:ashby:{U3}"), "company": "Acme"}}
    (item,) = finder.BoardFinder().parse(records[0], src(id=build.FINDER))
    assert item.target == s.find_key(f"job:ashby:{U3}") and item.job_key == f"job:ashby:{U3}"
    s.close()


# ------------------------------------------------------------------------------ markdown table fixes

def test_markdown_cells_keep_pipes_and_brackets_inside_links():
    row = "| [ISS | Institutional Shareholder Services](http://iss.test/) | **[[2027] Software Engineer](https://jobright.ai/jobs/info/1)** | NYC |"
    cells = _cells(row)
    assert [_text(c) for c in cells] == ["ISS | Institutional Shareholder Services", "[2027] Software Engineer", "NYC"]
    assert _links(cells[1]) == ["https://jobright.ai/jobs/info/1"]
    assert _cells("| A \\| B | Eng |") == ["A | B", "Eng"]
    assert _cells("| Acme | Unbalanced [ title | NYC |") == ["Acme", "Unbalanced [ title", "NYC"]
