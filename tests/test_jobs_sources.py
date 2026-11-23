"""LinkedIn's guest job search, Getro's VC portfolio boards and Work at a Startup's
job pages. Every page here is synthetic, shaped like the live one."""
import json
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlsplit

from debrief.http import Fetched
from debrief.jobs import build, collect, probe
from debrief.jobs.adapters import REGISTRY, Ctx
from debrief.jobs.store import JobsStore, state_path
from test_jobs_alerts import INBOX, LINKEDIN
from test_jobs_collect import _cfg, rec, src

NOW = "2026-11-23T08:00:00Z"


class Pages:
    """Answers GETs and POSTs from `answer(url, body)`: a body (200) or (status, body, final URL). Records each ask."""
    user_agent = "t"

    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def _ask(self, url, body=None):
        self.asked.append((url, body))
        got = self.answer(url, body)
        status, content, final = (200, got, url) if not isinstance(got, tuple) else got
        content = content if isinstance(content, bytes) else (content if isinstance(content, str) else json.dumps(content)).encode()
        return Fetched(url=url, final_url=final, status=status, content=content, error="" if status == 200 else f"HTTP {status}")

    def get(self, url, params=None, headers=None, etag=None, last_modified=None):
        return self._ask(url)

    def post(self, url, json_body=None, headers=None):
        return self._ask(url, json_body)


# ------------------------------------------------------------------------------- LinkedIn search

LI = REGISTRY["linkedin_search"]


def li_card(job, title, company="Globex Corporation", place="Springfield, IL", posted="2026-11-23"):
    """One guest-search card as LinkedIn sends it, tracking ids and logo included."""
    return f'''<li><div class="base-card relative w-full base-search-card job-search-card" data-entity-urn="urn:li:jobPosting:{job}"
 data-impression-id="jobs-search-result-0" data-reference-id="AAAA" data-tracking-id="BBBB">
<a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/{title.lower().replace(" ", "-")}-at-globex-{job}?position=1&amp;pageNum=0&amp;refId=AAAA&amp;trackingId=BBBB">
<span class="sr-only">{title}</span></a>
<div class="search-entity-media"><img class="artdeco-entity-image" data-delayed-url="https://media.example/logo.png" alt></div>
<div class="base-search-card__info"><h3 class="base-search-card__title">
        {title}
      </h3>
<h4 class="base-search-card__subtitle"><a class="hidden-nested-link" href="https://www.linkedin.com/company/globex?trk=x">
            {company}
          </a></h4>
<div class="base-search-card__metadata"><span class="job-search-card__location">
            {place}
          </span>
{f'<time class="job-search-card__listdate--new" datetime="{posted}">3 hours ago</time>' if posted else ""}
</div></div></div></li>'''


def li_page(n, start=0):
    return "".join(li_card(5000000000 + start + i, f"Software Engineer {start + i}") for i in range(n))


def li_source(**options):
    base = {"endpoint": "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search", "max_requests": 50,
            "max_pages": 4, "us_wide": ["founding engineer"], "by_place": ["software engineer junior"],
            "places": ["Washington DC-Baltimore Area", "remote"]}
    return src(id="linkedin-search", family="api", adapter="linkedin_search", options={**base, **options})


def li_fetch(tmp_path, answer, **options):
    s = JobsStore(tmp_path / "j.sqlite")
    client = Pages(answer)
    ctx = Ctx(client, s, {}, "run", None, "", {})
    LI.fetch(ctx, li_source(**options))
    s.close()
    asked = [{k: v[0] for k, v in parse_qs(urlsplit(u).query).items()} for u, _ in client.asked]
    return ctx, asked


def test_a_linkedin_card_becomes_a_job_with_a_bare_link_and_its_posted_date():
    page = li_card(4000000001, "Junior Software Engineer") + li_card(4000000002, "Data Analyst", "Initech", "United States", None)
    r = rec(page, url="https://www.linkedin.com/jobs-guest/x", meta={"search": "software engineer @ remote"}, source_id="linkedin-search")
    got = LI.parse(r, li_source())
    assert [(i.item_key, i.title, i.company, i.locations, i.url, i.published_at, i.remote) for i in got] == [
        ("linkedin:4000000001", "Junior Software Engineer", "Globex Corporation", ("Springfield, IL",),
         "https://www.linkedin.com/jobs/view/4000000001", "2026-11-23T00:00:00Z", None),
        ("linkedin:4000000002", "Data Analyst", "Initech", ("United States",),
         "https://www.linkedin.com/jobs/view/4000000002", None, None)]
    assert "refId" not in json.dumps([i.as_dict() for i in got]) and "trackingId" not in json.dumps([i.as_dict() for i in got])


def test_linkedin_searches_us_wide_first_then_each_place(tmp_path):
    ctx, asked = li_fetch(tmp_path, lambda url, body: li_page(3))
    assert [(q["keywords"], q["location"], q.get("f_WT"), q["f_TPR"], q["start"]) for q in asked] == [
        ("founding engineer", "United States", None, "r86400", "0"),
        ("software engineer junior", "Washington DC-Baltimore Area", None, "r86400", "0"),
        ("software engineer junior", "United States", "2", "r86400", "0")]


def test_a_linkedin_search_pages_until_a_short_page_within_its_caps(tmp_path):
    full = lambda url, body: li_page(10, int(parse_qs(urlsplit(url).query)["start"][0]))
    _, asked = li_fetch(tmp_path, lambda url, body: li_page(10) if "start=0" in url else li_page(4, 10),
                        places=[])
    assert [q["start"] for q in asked] == ["0", "10"]
    _, asked = li_fetch(tmp_path, full, places=[], max_pages=3)
    assert [q["start"] for q in asked] == ["0", "10", "20"]
    ctx, asked = li_fetch(tmp_path, full, max_requests=5)
    assert len(asked) == 5 and any("stopped at 5 requests" in w for w in ctx.warnings)


def test_a_linkedin_block_ends_every_search_for_the_night(tmp_path):
    ctx, asked = li_fetch(tmp_path, lambda url, body: (999, "", url))
    assert len(asked) == 1 and ctx.blocked("https://www.linkedin.com/jobs/view/1").why == "HTTP 999"
    ctx, asked = li_fetch(tmp_path, lambda url, body: (200, "<html>sign in</html>", "https://www.linkedin.com/authwall?x=1"))
    assert len(asked) == 1 and ctx.blocked("https://www.linkedin.com/x").why == "a login wall"


def test_a_job_the_search_found_and_an_alert_email_names_is_one_posting(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"alert-inbox", "linkedin-search"})
    s = JobsStore(state_path(cfg))
    search = rec(li_card(4000000001, "Junior Software Engineer"), url="https://www.linkedin.com/jobs-guest/x", fetched_at=NOW,
                 source_id="linkedin-search")
    mail = rec(LINKEDIN, url="imaps://imap.gmail.com/INBOX;UIDVALIDITY=1;UID=1", meta={"site": "linkedin"}, fetched_at=NOW,
               source_id="alert-inbox")
    for source_id, adapter, r in (("linkedin-search", LI, search), ("alert-inbox", INBOX, mail)):
        with s.transaction():
            build.apply(s, cfg.source(source_id), adapter, [r], ["ref#0"], early_sources=collect.early_sources(cfg))
    (pid,) = {r[0] for r in s.q("SELECT posting_id FROM items WHERE item_key='linkedin:4000000001'")}
    assert {r[0] for r in s.q("SELECT source_id FROM items WHERE posting_id=?", (pid,))} == {"linkedin-search", "alert-inbox"}
    assert s.posting(pid)["published_at"] == "2026-11-23T00:00:00Z"
    s.close()


def test_the_html_probe_counts_the_jobs_on_a_page():
    class One:
        def robots_allowed(self, url):
            return True

        def request(self, method, url, **kw):
            return Fetched(url=url, final_url=url, status=200, content=li_page(3).encode(), headers={"content-type": "text/html"})
    spec = {"kind": "html", "items": 'data-entity-urn="urn:li:jobPosting:', "title": r'base-search-card__title">\s*([^<]+?)\s*</h3>'}
    att, _ = probe.probe_url(One(), "https://www.linkedin.com/jobs-guest/x", spec, conditional=False)
    assert (att.status, att.n_items, att.sample_title) == ("ok", 3, "Software Engineer 0")


# ------------------------------------------------------------------------------------- Getro

GETRO = REGISTRY["getro"]
GETRO_URL = "https://api.getro.com/api/v2/collections/{}/search/jobs"
DAY = 86400
T0 = int(datetime(2026, 11, 23, 8, tzinfo=timezone.utc).timestamp())


def getro_job(n, created, title="Software Engineer", org=None, **kw):
    org = {"id": 7, "name": "Kestrel Labs", "slug": "kestrel-labs", "stage": "series_a", "head_count": 2,
           "logo_url": "https://cdn.example/logo", "topics": [], "industry_tags": ["Software"], **(org or {})}
    return {"id": n, "title": title, "created_at": created, "locations": ["New York, NY, USA"], "work_mode": "on_site",
            "url": f"https://job-boards.greenhouse.io/kestrel/jobs/{n}?gh_src=getro&utm_source=getro", "seniority": "entry_level",
            "organization": org, "compensation_public": True, "compensation_amount_min_cents": 12000000,
            "compensation_amount_max_cents": 15000000, "compensation_currency": "USD", "compensation_period": "year",
            "featured": False, "has_description": True, "slug": f"{n}-software-engineer", "source": "career_page", **kw}


def getro_page(jobs, count=500):
    return {"results": {"count": count, "jobs": jobs}}


def getro_source(**options):
    base = {"endpoint": "https://api.getro.com/api/v2/collections/{network}/search/jobs", "job_functions": ["Software Engineering"],
            "first_pages": 2, "max_pages": 5, "networks": {"Fund A": 1, "Fund B": 2}}
    return src(id="getro", family="api", adapter="getro", method="POST", url=GETRO_URL.format(1),
               headers={"Accept": "application/json"}, options={**base, **options})


def test_a_getro_job_keeps_its_employer_link_and_its_company_size():
    r = rec(getro_page([getro_job(101, T0, work_mode="remote", locations=["Remote"]),
                        getro_job(102, T0 - DAY, title="Data Analyst", org={"name": "Bigco", "stage": "ipo", "head_count": 6}),
                        getro_job(103, T0, org={"name": "Smallco", "stage": "other", "head_count": None}, compensation_public=False)]),
            meta={"network": "Fund A"}, source_id="getro")
    a, b, c = GETRO.parse(r, getro_source())
    assert (a.item_key, a.title, a.company, a.locations, a.url, a.published_at, a.remote, a.scope) == (
        "getro:101", "Software Engineer", "Kestrel Labs", ("Remote",), "https://job-boards.greenhouse.io/kestrel/jobs/101",
        "2026-11-23T08:00:00Z", True, "Fund A")
    assert build.job_key(a) == "job:greenhouse:101" and a.board == "greenhouse:kestrel"
    assert a.extra["company_size"] == "11-50 people, series a" and a.extra["seniority"] == "entry_level"
    assert a.pay == {"min": 120000.0, "max": 150000.0, "currency": "USD", "period": "year"}
    assert (b.extra["company_size"], b.remote) == ("5,001+ people, ipo", None)
    assert c.extra["company_size"] is None and c.pay is None


def getro_answers(pages):
    """pages: {network id: [page 0 jobs, page 1 jobs, ...]}; a page past the end is empty."""
    def answer(url, body):
        nid = int(url.split("/collections/")[1].split("/")[0])
        got = pages.get(nid, [])
        return getro_page(got[body["page"]] if body["page"] < len(got) else [])
    return answer


def getro_fetch(store, answer, **options):
    client = Pages(answer)
    ctx = Ctx(client, store, {}, "run", None, "", {})
    GETRO.fetch(ctx, getro_source(**options))
    return ctx, [(int(u.split("/collections/")[1].split("/")[0]), b["page"]) for u, b in client.asked]


def full(start, newest):
    """A full page of 20 jobs, ids from `start`, created one hour apart from `newest` back."""
    return [getro_job(start + i, newest - i * 3600) for i in range(20)]


def test_getro_reads_first_pages_its_first_night_then_stops_at_what_it_already_has(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"getro"})
    s = JobsStore(state_path(cfg))
    night1 = {1: [full(1, T0), full(21, T0 - 20 * 3600), full(41, T0 - 40 * 3600)], 2: [full(100, T0)[:5]]}
    ctx, asked = getro_fetch(s, getro_answers(night1))
    assert asked == [(1, 0), (1, 1), (2, 0)] and ctx.warnings == []
    with s.transaction():
        build.apply(s, getro_source(), GETRO, ctx.records, [f"ref#{i}" for i in range(len(ctx.records))])
    assert s.newest_published("getro", "Fund A") == "2026-11-23T08:00:00Z"
    later = T0 + DAY
    night2 = {1: [full(200, later), [getro_job(220 + i, later - (20 + i) * 3600) for i in range(5)] + full(1, T0)[:15]],
              2: [[getro_job(300, later)] + full(100, T0)[:5]]}
    ctx, asked = getro_fetch(s, getro_answers(night2))
    assert asked == [(1, 0), (1, 1), (2, 0)] and ctx.warnings == []
    endless = {1: [[getro_job(400 + 20 * i + k, later - (20 * i + k) * 60) for k in range(20)] for i in range(9)]}
    ctx, asked = getro_fetch(s, getro_answers(endless), networks={"Fund A": 1})
    assert asked == [(1, p) for p in range(5)]
    assert ctx.warnings == ["Fund A: stopped at 5 pages before reaching the jobs it had last night"]
    s.close()


def test_getro_asks_for_the_job_functions_newest_first_and_stops_on_a_block(tmp_path):
    s = JobsStore(tmp_path / "j.sqlite")
    client = Pages(lambda url, body: (403, "<html>blocked</html>", url))
    ctx = Ctx(client, s, {}, "run", None, "", {})
    GETRO.fetch(ctx, getro_source())
    assert [b for _, b in client.asked] == [{"hitsPerPage": 20, "query": "", "filters": {"job_functions": ["Software Engineering"]},
                                             "page": 0}]
    assert ctx.blocked(GETRO_URL.format(2)).why == "HTTP 403"
    s.close()


def test_the_scoring_call_sees_the_company_size_a_source_gives():
    from debrief.jobs import score
    p = {"title": "Software Engineer", "company": "Kestrel Labs", "locations": ["New York, NY"], "published_at": NOW,
         "data": {"company_size": "11-50 people, series a", "seniority": "entry_level"}}
    view = score.job_view("j1", p, None, {"getro"}, frozenset(), 2000)
    assert view["company_size"] == "11-50 people, series a" and view["level"] == "entry_level"
    assert "company_size" not in score.job_view("j2", {**p, "data": {}}, None, {"getro"}, frozenset(), 2000)


# ---------------------------------------------------------------------------- Work at a Startup

WAAS = REGISTRY["waas_jobs"]
SEED = {"seed": {"id": 1000, "date": "2026-11-20"}, "ids_per_day": 10, "first_days": 2, "stop_after": 3, "max_requests": 50}
RUN_DATE = "2026-11-23"
FIRST_ID = SEED["seed"]["id"] + SEED["ids_per_day"] * (
    (date.fromisoformat(RUN_DATE) - date.fromisoformat(SEED["seed"]["date"])).days - SEED["first_days"])


def waas_page(jid, title="Founding Engineer", experience="Any (new grads ok)", location="San Francisco, CA, US / Remote (US)",
              links_to=1):
    """A Work at a Startup job page as the site sends it: Inertia props in an HTML-escaped data-page attribute,
    the founders (name, photo, bio, LinkedIn) included."""
    import html as h
    props = {"job": {"id": jid, "title": title, "salaryRange": "$120K - $160K", "equityRange": "0.5% - 1.0%", "location": location,
                     "jobType": "Full-time", "sponsorsVisa": "Will sponsor", "minExperience": experience, "skills": ["Python"],
                     "descriptionHtml": "<p>Build agents with Pat Founder and Sam.</p><p>Questions: hiring@kestrel.example "
                                        "or https://www.linkedin.com/in/pat-founder</p>",
                     "interviewProcessHtml": "<p>Call with Pat Founder</p>"},
             "company": {"name": "Kestrel", "slug": "kestrel", "batch": "F25", "teamSize": 4, "url": "https://kestrel.example",
                         "location": "San Francisco", "industry": "B2B -> Engineering", "description": "Agents for ops",
                         "hiringDescriptionHtml": "<p>Pat Founder hires.</p>", "logoUrl": "https://img.example/k.png",
                         "founders": [{"name": "Pat Founder", "avatarUrl": "https://img.example/pat.png", "bio": "Ex-Hooli",
                                       "linkedin": "https://www.linkedin.com/in/pat-founder"},
                                      {"name": "Sam Cofounder", "avatarUrl": "https://img.example/sam.png", "bio": "PhD",
                                       "linkedin": "https://www.linkedin.com/in/sam-cofounder"}],
                         "jobs": [{"id": jid - 1, "title": "Designer"}]},
             "similarJobs": [{"id": links_to, "companyName": "Other"}], "applyUrl": "https://account.ycombinator.com/authenticate?x=1"}
    return f'<html><body><div id="app" data-page="{h.escape(json.dumps({"component": "JobDetailV2Page", "props": props}))}"></div></body></html>'


NOT_FOUND = (404, "<html><title>Y Combinator | Page not found</title></html>", None)


def waas_answers(live, other=None):
    """Live job ids answer a job page; other {id: answer}; every other id a 404."""
    def answer(url, body):
        jid = int(url.rsplit("/", 1)[1])
        got = (other or {}).get(jid) or (waas_page(jid) if jid in live else NOT_FOUND)
        return got if not isinstance(got, tuple) else (got[0], got[1], got[2] or url)
    return answer


def waas_fetch(store, answer, run_date=RUN_DATE, **options):
    client = Pages(answer)
    ctx = Ctx(client, store, {}, "run", None, "", {}, run_date=run_date)
    source = src(id="waas-jobs", family="api", adapter="waas_jobs", url="https://www.workatastartup.com/jobs",
                 options={**SEED, **options})
    WAAS.fetch(ctx, source)
    return ctx, [int(u.rsplit("/", 1)[1]) for u, _ in client.asked], source


def test_a_work_at_a_startup_page_keeps_the_job_and_never_its_people(tmp_path):
    s = JobsStore(tmp_path / "j.sqlite")
    ctx, _, source = waas_fetch(s, waas_answers({FIRST_ID}), max_requests=1)
    stored = json.dumps(ctx.records[0], ensure_ascii=False)
    for person in ("Pat", "Founder", "Sam", "Cofounder", "linkedin.com/in", "hiring@kestrel.example", "avatarUrl", "Ex-Hooli",
                   "founders", "similarJobs", "account.ycombinator.com"):
        assert person not in json.loads(stored)["body"], person
    (it,) = WAAS.parse(ctx.records[0], source)
    assert (it.item_key, it.title, it.company, it.locations, it.url, it.remote, it.pay) == (
        f"waas:{FIRST_ID}", "Founding Engineer", "Kestrel", ("San Francisco, CA, US", "Remote (US)"),
        f"https://www.workatastartup.com/jobs/{FIRST_ID}", True, {"summary": "$120K - $160K"})
    assert it.text == "Build agents with [name] and [name].\nQuestions:  or"
    assert it.extra == {"experience": "Any (new grads ok)", "employment": "Full-time", "yc": "F25", "company_size": "4 people, YC F25",
                        "via": "Work at a Startup"}
    s.close()


def test_new_grads_ok_is_the_new_grad_sign_and_years_are_not(tmp_path, monkeypatch):
    from debrief.jobs import filters
    from test_jobs_profile import BASE
    cfg = _cfg(tmp_path, monkeypatch, {"waas-jobs"})
    s = JobsStore(state_path(cfg))
    other = {FIRST_ID + 1: waas_page(FIRST_ID + 1, title="Software Engineer", experience="3+ years")}
    ctx, _, source = waas_fetch(s, waas_answers({FIRST_ID}, other), max_requests=2)
    with s.transaction():
        build.apply(s, source, WAAS, ctx.records, ["ref#0", "ref#1"])
    prof = json.loads(json.dumps(BASE))
    prof["filters"]["new_grad_only"] = True
    prof["position_types"] += [{"name": "Startup", "titles": ["founding engineer", "software engineer"]}]
    now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    verdicts, new_grads_ok, three_years = {}, f"waas:{FIRST_ID}", f"waas:{FIRST_ID + 1}"
    for key in (new_grads_ok, three_years):
        p = s.posting(s.item("waas-jobs", key)["posting_id"])
        verdicts[key] = filters.check(p, prof, now, text=s.texts_for(p["posting_id"]))
    assert verdicts[new_grads_ok].facts.get("new_grad_sign") == "the source's level (experience)"
    assert (verdicts[three_years].ok, verdicts[three_years].stage) == (False, "level")
    s.close()


def test_the_id_walk_starts_back_from_the_seed_then_goes_on_from_the_highest_job_it_holds(tmp_path):
    s = JobsStore(tmp_path / "j.sqlite")
    live = {FIRST_ID + k for k in (0, 2, 3, 10, 20, 21)}
    ctx, asked, source = waas_fetch(s, waas_answers(live), max_requests=6)
    assert asked == list(range(FIRST_ID, FIRST_ID + 6))
    assert ctx.warnings == [f"stopped at 6 job pages (at id {FIRST_ID + 6}); the walk goes on next night"]
    with s.transaction():
        build.apply(s, source, WAAS, ctx.records, [f"ref#{i}" for i in range(len(ctx.records))])
    assert s.top_item_number("waas-jobs") == FIRST_ID + 3
    ctx, asked, _ = waas_fetch(s, waas_answers(live), run_date="2026-11-24")
    assert asked == [FIRST_ID + 4, FIRST_ID + 5, FIRST_ID + 6]
    assert ctx.warnings == [] and all(r["status"] == 404 for r in ctx.records)
    s.close()


def test_a_run_of_misses_below_a_job_a_page_links_to_is_a_gap_not_the_top(tmp_path):
    s = JobsStore(tmp_path / "j.sqlite")
    live = {FIRST_ID, FIRST_ID + 10}
    ctx, asked, _ = waas_fetch(s, waas_answers(live, {FIRST_ID: waas_page(FIRST_ID, links_to=FIRST_ID + 10)}))
    assert asked == list(range(FIRST_ID, FIRST_ID + 14)) and [r["status"] for r in ctx.records].count(200) == 2
    ctx, asked, _ = waas_fetch(s, waas_answers(live))
    assert asked == list(range(FIRST_ID, FIRST_ID + 4))
    s.close()


def test_a_page_without_a_job_is_a_miss_and_a_page_without_job_data_is_a_block(tmp_path):
    s = JobsStore(tmp_path / "j.sqlite")
    no_job = '<div id="app" data-page="{&quot;props&quot;: {&quot;company&quot;: {&quot;name&quot;: &quot;X&quot;}}}"></div>'
    ctx, asked, _ = waas_fetch(s, waas_answers({FIRST_ID + 2}, {FIRST_ID: no_job}))
    assert asked[:3] == list(range(FIRST_ID, FIRST_ID + 3)) and ctx.records[0]["status"] == 404 and ctx.blocked(WAAS_URL) is None
    ctx, asked, _ = waas_fetch(s, waas_answers(set(), {FIRST_ID: "<html><title>Just a moment...</title></html>"}))
    assert asked == [FIRST_ID] and ctx.blocked(WAAS_URL).why == "a page with no job data (a challenge?)"
    ctx, asked, _ = waas_fetch(s, waas_answers(set(), {FIRST_ID: (200, "<html>sign in</html>", "https://account.ycombinator.com/?continue=x")}))
    assert asked == [FIRST_ID] and ctx.blocked(WAAS_URL).why == "a login wall"
    s.close()


WAAS_URL = "https://www.workatastartup.com/jobs/1"
