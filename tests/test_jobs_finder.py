"""The board finder (src/debrief/jobs/adapters/finder.py): the match rule, a night end to end
(found job -> job id, the company's link, a description; the list still decides closing), the
fetch-history rules that keep it from asking twice, and the markdown fixes found with it. Also
describe's board guess for "?gh_jid=" jobs (src/debrief/jobs/adapters/describe.py)."""
import json
from datetime import datetime, timedelta, timezone

from debrief.http import Fetched
from debrief.jobs import boards, build, collect, items
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


# ----------------------------------------------------- describe: the Greenhouse board for ?gh_jid= jobs

def test_greenhouse_guesses():
    g = boards.greenhouse_guesses
    assert g("SambaNova Systems", "https://sambanova.ai/sambanova-available-positions/?gh_jid=6240725004") == \
        ["sambanovasystems", "sambanova"]
    assert g("Blue River Technology", "https://www.bluerivertechnology.com/job/?gh_jid=8337794") == \
        ["bluerivertechnology", "blueriver", "bluerivertech"]
    assert g("Precisely", "https://www.precisely.com/careers-and-culture/us-jobs/job/4750854005?gh_jid=4750854005") == \
        ["precisely"]
    assert g("Acme AI Labs", "https://careers.acme-corp.co.uk/?gh_jid=1") == ["acmeailabs", "acme", "acmecorp"]
    assert g("Ben & Jerry\u2019s", None) == ["benjerrys"]
    assert g("", None) == []


GH = "https://boards-api.greenhouse.io/v1/boards/{}/jobs/{}?pay_transparency=true"


def _describe_night(cfg, store, client):
    source = next(s for s in cfg.sources if s.id == "describe")
    from debrief.jobs import profile
    _, prof = profile.latest(cfg)
    NIGHTS.append(f"run-describe-{len(NIGHTS)}")
    return collect.collect_source(cfg, store, client, source, NIGHTS[-1], "2026-11-05", datetime.now(timezone.utc),
                                  prof, {}, lambda *a, **k: None)


def test_describe_guesses_the_greenhouse_board_once(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"describe", "simplify-newgrad"})
    s = JobsStore(state_path(cfg))
    br = "https://www.bluerivertechnology.com/job/?gh_jid={}"
    _list(s, [it("s1", title="Software Engineer", company="Blue River Technology", url=br.format(11)),
              it("s2", title="Software Engineer, New Grad", company="Blue River Technology", url=br.format(12)),
              it("s3", title="Software Engineer", company="Precisely", url="https://www.precisely.com/job/13?gh_jid=13")],
          _iso(datetime.now(timezone.utc) - timedelta(hours=1)), source_id="simplify-newgrad", snapshot="window")
    client = BoardClient({GH.format("bluerivertech", 11): {"id": 11, "title": "Software Engineer", "content": "Cameras"},
                          GH.format("bluerivertech", 12): {"id": 12, "title": "Software Engineer, New Grad", "content": "CI"}})
    out = _describe_night(cfg, s, client)
    assert out["status"] == "ok"
    asked = [c[1] for c in client.calls]
    assert len(asked) == 5 and sorted(asked).count(GH.format("precisely", 13)) == 1
    assert {GH.format("bluerivertech", 11), GH.format("bluerivertech", 12)} <= set(asked)
    assert sum("/bluerivertechnology/" in u or "/blueriver/" in u for u in asked) == 2      # guessed for one job only
    assert s.texts_for(s.find_key("job:greenhouse:11")) == "Cameras"
    assert s.texts_for(s.find_key("job:greenhouse:12")) == "CI"
    assert [r["board"] for r in s.directory_by_name(items.company_norm("Blue River Technology"))] == ["greenhouse:bluerivertech"]
    assert s.directory_by_name(items.company_norm("Precisely")) == []

    # The next night: Blue River is described, and Precisely's 404 isn't asked again.
    client.calls.clear()
    _describe_night(cfg, s, client)
    assert client.calls == []

    # A new Blue River job: its board is in the directory now, so one request and no guessing.
    _list(s, [it("s4", title="Software Engineer, Cameras", company="Blue River Technology", url=br.format(14))],
          _iso(datetime.now(timezone.utc)), source_id="simplify-newgrad", snapshot="window")
    client.pages[GH.format("bluerivertech", 14)] = {"id": 14, "title": "Software Engineer, Cameras", "content": "Optics"}
    _describe_night(cfg, s, client)
    assert client.calls == [("GET", GH.format("bluerivertech", 14))]
    s.close()


def test_a_known_board_beats_a_greenhouse_guess(tmp_path):
    from debrief.jobs.adapters import describe
    from test_jobs_collect import BASE
    s = JobsStore(tmp_path / "j.sqlite")
    _list(s, [it("s1", title="Software Engineer", company="Acme", url="https://acme.com/jobs?gh_jid=5",
                 job_key="job:greenhouse:5")], _iso(datetime.now(timezone.utc)), source_id="simplify-newgrad",
          snapshot="window")
    found = describe.targets(s, json.loads(json.dumps(BASE)), datetime.now(timezone.utc))
    assert [(sy, b, k) for sy, b, k, _ in found] == [("greenhouse", None, "greenhouse:5")]      # to guess
    with s.transaction():
        s.save_directory("greenhouse:acme", "Acme", "acme", "universe", _iso(datetime.now(timezone.utc)))
    found = describe.targets(s, json.loads(json.dumps(BASE)), datetime.now(timezone.utc))
    assert [(sy, b, k) for sy, b, k, _ in found] == [("greenhouse", "greenhouse:acme", "greenhouse:5")]
    s.close()


# ------------------------------------------- revision 6: a job its own board says is gone

def test_describe_gone_per_job_and_per_board():
    from debrief.jobs.adapters import REGISTRY
    d, s = REGISTRY["describe"], src(id="describe")
    meta = {"system": "greenhouse", "board": "greenhouse:acme", "wanted": ["greenhouse:1"]}
    assert d.gone(rec("", status=404, meta=meta), s) == {"greenhouse:1": True}
    assert d.gone(rec({"id": 1, "title": "SWE", "content": "x"}, meta=meta), s) == {"greenhouse:1": False}
    assert d.gone(rec("", status=404, meta={**meta, "guessed": True}), s) == {}          # a wrong board-name guess
    assert d.gone(rec("", status=500, meta=meta), s) == {}                               # an error proves nothing
    ash = {"system": "ashby", "board": "ashby:acme", "wanted": [f"ashby:{U1}", f"ashby:{U2}"]}
    listing = {"jobs": [{"id": U1, "title": "SWE", "location": "New York, NY", "jobUrl": "u"}]}
    assert d.gone(rec(listing, meta=ash), s) == {f"ashby:{U1}": False, f"ashby:{U2}": True}
    assert d.gone(rec({"jobs": []}, meta=ash), s) == {}                                  # empty: maybe a format change
    assert d.gone(rec("", status=503, meta=ash), s) == {}


def test_a_gone_job_is_cut_and_a_reopened_one_comes_back(tmp_path):
    import copy as _copy
    from debrief.jobs import filters
    from debrief.jobs.adapters import REGISTRY
    from test_jobs_collect import BASE
    s = JobsStore(tmp_path / "j.sqlite")
    now = datetime.now(timezone.utc)
    _list(s, [it("s1", title="Software Engineer", url="https://job-boards.greenhouse.io/acme/jobs/1", job_key="job:greenhouse:1",
                 board="greenhouse:acme")], _iso(now), source_id="simplify-newgrad", snapshot="window")
    pid = s.find_key("job:greenhouse:1")
    meta = {"system": "greenhouse", "board": "greenhouse:acme", "wanted": ["greenhouse:1"]}
    with s.transaction():
        c = build.apply(s, src(id="describe", family="detail", url=None), REGISTRY["describe"],
                        [rec("", status=404, meta=meta, fetched_at=_iso(now))], ["ref#0"])
    assert c["gone_on_board"] == 1 and s.posting(pid)["data"]["gone"]["status"] == 404
    prof = _copy.deepcopy(BASE)
    v = filters.check(s.posting(pid), prof, now)
    assert v.stage == "gone"
    assert s.posting(pid)["closed_at"] is None                    # the list still says open; the board outranks it
    with s.transaction():
        build.apply(s, src(id="describe", family="detail", url=None), REGISTRY["describe"],
                    [rec({"id": 1, "title": "Software Engineer", "content": "Python"}, meta=meta, fetched_at=_iso(now))],
                    ["ref#1"])
    assert "gone" not in s.posting(pid)["data"] and filters.check(s.posting(pid), prof, now, listed_early=True).ok
    s.close()


def test_collect_since_widens_only_the_detail_steps():
    from debrief.jobs.adapters import describe
    from test_jobs_collect import BASE
    prof = json.loads(json.dumps(BASE))
    wide = collect.widened(prof, "2026-10-01", datetime(2026, 11, 5, 12, tzinfo=timezone.utc))
    assert wide["filters"]["max_age_days"] == 36 and prof["filters"]["max_age_days"] == 14
    assert collect.widened(prof, "2026-11-01", datetime(2026, 11, 5, tzinfo=timezone.utc))["filters"]["max_age_days"] == 14
