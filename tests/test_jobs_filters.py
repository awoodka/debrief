import copy
from datetime import datetime, timezone

import pytest

from debrief.jobs import filters
from test_jobs_profile import BASE

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
PROFILE = copy.deepcopy(BASE)
PROFILE["position_types"] = [
    {"name": "SWE", "titles": ["software engineer", "full stack engineer", "swe"]},
    {"name": "Research", "titles": ["member of technical staff", "research engineer"]},
]
PROFILE["filters"]["exclude_titles"] = ["senior", "staff", "intern", "lead"]
PROFILE["filters"]["new_grad_only"] = False          # the other stages, on plain titles; test_level covers it
PROFILE["filters"]["locations"]["places"] = {
    "NYC": ["New York, NY", "NYC"], "DC": ["Washington, DC", "Arlington, VA", "Columbia, MD"],
    "SF": ["San Francisco, CA", "Bay Area"]}


def job(title="Software Engineer", locations=("New York, NY",), published_at="2026-11-03T00:00:00Z", **data):
    return {"title": title, "company": "Acme", "locations": list(locations), "published_at": published_at,
            "first_seen_at": "2026-11-04T00:00:00Z", "data": data}


def stage(**kw):
    text = kw.pop("text", None)
    v = filters.check(job(**kw), PROFILE, NOW, text=text)
    return v.stage if not v.ok else "ok"


@pytest.mark.parametrize("title,expected", [
    ("Software Engineer, New Grad", "ok"),
    ("Full-Stack Engineer", "ok"),                         # punctuation doesn't matter
    ("Member of Technical Staff", "ok"),                   # "staff" is part of the matched type title
    ("Senior Member of Technical Staff", "title"),
    ("Staff Software Engineer", "title"),
    ("Staff+ Software Engineer", "title"),
    ("Software Engineer, C++", "ok"),
    ("Software Engineer Intern", "title"),
    ("Internal Tools Software Engineer", "ok"),            # "intern" is a whole word only
    ("Data Scientist", "title"),
])
def test_title(title, expected):
    assert stage(title=title) == expected


@pytest.mark.parametrize("locations,expected", [
    (["San Francisco, CA, United States"], "ok"),
    (["SF"], "ok"),
    (["NYC"], "ok"),
    (["Hybrid - New York, NY"], "ok"),
    (["Washington, D.C."], "ok"),
    (["Columbia, SC"], "place"),                           # same city name, another state
    (["Columbia, MD"], "ok"),
    (["Remote"], "ok"),
    (["Remote (US)"], "ok"),
    (["Remote - Canada"], "place"),
    (["United States"], "ok"),
    (["Austin, TX"], "place"),
    (["Austin, TX", "Remote in USA"], "ok"),
    (["Remote - Cleveland, OH"], "ok"),
    (["Remote in Canada"], "place"),
    (["Remote (US or Canada)"], "ok"),
    (["London, England, United Kingdom"], "place"),
    ([], "ok"),                                            # unknown: the model sees it
])
def test_place(locations, expected):
    assert stage(locations=locations) == expected


def test_place_remote_flag():
    assert filters.place_fit(["Atlanta, GA, United States"], PROFILE, remote_flag=True)[0] == "remote (US)"
    assert filters.place_fit(["Atlanta, GA, United States"], PROFILE, remote_flag=None)[0] is None
    assert filters.place_fit(["Perth, WA, Australia"], PROFILE, remote_flag=True)[0] is None
    assert filters.place_fit(["Europe,  USA"], PROFILE, remote_flag=True)[0] == "remote (US)"
    assert filters.place_fit(["Anywhere in the World"], PROFILE, remote_flag=True)[0] == "remote (US)"
    assert filters.place_fit(["London, UK"], PROFILE, remote_flag=True)[0] is None


def test_age_degree_blocklist():
    assert stage(published_at="2026-10-01T00:00:00Z") == "age"
    assert stage(published_at=None) == "ok"                # first seen today
    assert stage(degrees=["PhD"]) == "degree"
    assert stage(degrees=["Master's", "PhD"]) == "degree"
    assert stage(degrees=["Bachelor's", "Master's"]) == "ok"
    assert stage(title="Software Engineer, PhD New College Grad") == "degree"
    assert stage(title="Software Engineer (BS/MS/PhD)") == "ok"
    assert stage(title="Software Engineer, Media Engine (Master's)") == "degree"
    assert stage(title="Software Engineer (BS/MS)") == "ok"
    p = copy.deepcopy(PROFILE)
    p["filters"]["blocklist"] = ["ACME Inc."]
    assert filters.check(job(), p, NOW).stage == "blocklist"


@pytest.mark.parametrize("title,employment,expected", [
    ("Software Engineer", "FullTime", "ok"),
    ("Software Engineer", "Full-time", "ok"),
    ("Software Engineer", "Contract", "employment"),
    ("Software Engineer", "PartTime", "employment"),
    ("Software Engineer", "Intern", "employment"),
    ("Software Engineer (Contract)", None, "employment"),
    ("Part-Time Software Engineer", None, "employment"),
    ("Smart Contract Software Engineer", None, "ok"),
])
def test_employment(title, employment, expected):
    assert stage(title=title, employment=employment) == expected


@pytest.mark.parametrize("pay,top", [
    ({"summary": "$172k/yr"}, 82.69),
    ({"summary": "$180K • Offers Equity"}, 86.54),
    ({"summary": "$22/hr"}, 22.0),
    ({"min": 40000, "max": 50000, "currency": "USD", "period": "year"}, 24.04),
    ({"min": 190000.0, "max": 230000.0, "currency": "USD", "period": None}, 110.58),
    ({"min": 65, "max": 75, "currency": "USD", "period": "year"}, None),           # mislabeled: unclear
    ({"min": 4000, "max": 4000, "currency": "USD", "period": "year"}, None),
    ({"min": 155000, "max": 205000, "currency": "EUR", "period": None}, None),
    ({"min": 30, "max": 35, "currency": "USD", "period": "per-hour-wage"}, 35.0),
])
def test_hourly_top(pay, top):
    assert filters.hourly_top(pay) == top


def test_pay_filter():
    assert stage(pay={"summary": "$22/hr"}) == "pay"
    assert stage(pay={"min": 40000, "max": 50000, "currency": "USD", "period": "year"}) == "pay"
    assert stage(pay={"min": 65, "max": 75, "currency": "USD", "period": "year"}) == "ok"         # unclear stays
    assert stage(pay=None) == "ok"


def test_clearance():
    assert stage(title="Software Engineer - TS/SCI") == "clearance"
    assert stage(title="Cleared Software Engineer") == "clearance"
    assert stage(title="Software Engineer (Clearance Eligible)") == "ok"
    assert stage(text="Must hold an active Secret clearance.") == "clearance"
    assert stage(text="TS/SCI with full scope polygraph required.") == "clearance"
    assert stage(text="Ability to obtain a Secret clearance is required.") == "ok"
    assert stage(text="Candidates with an ACTIVE PUBLIC TRUST or SUITABILITY are preferred.") == "ok"
    assert stage(text="A final Top Secret/SCI clearance with a Counterintelligence Polygraph Post-Start is required.") == "ok"
    assert stage(text="Required Qualifications Active TS/SCI clearance (required)") == "clearance"
    p = copy.deepcopy(PROFILE)
    p["filters"]["clearance"]["has"] = "top_secret"
    assert filters.check(job(), p, NOW, text="Requires an active Secret clearance.").ok


def test_years():
    assert filters.min_years("3+ years of professional software experience; 5 years of Python experience") == 3
    assert filters.min_years("0-2 years of experience") == 0
    assert filters.min_years("Must be 18 years of age") is None
    assert filters.min_years("Up to 5 years of experience, with no degree needed") is None
    assert filters.min_years("Between new graduate level and 5 years of engineering experience.") is None
    assert filters.min_years("Between 0 and 3 years of experience") == 0
    assert filters.min_years("Preferred qualifications\n- 5+ years of experience in data engineering") is None
    assert filters.min_years("Preferably 5+ years of experience developing models") is None
    assert filters.min_years("5+ years of experience preferred") is None
    assert filters.min_years("Requirements: 3+ years of experience. Preferred: 5+ years of experience in Go") == 3
    assert filters.min_years("Preferred: Go. Requirements: 4+ years of experience") == 4
    assert stage(text="We need 3+ years of relevant work experience.") == "years"
    assert stage(text="1+ year of experience with Python.") == "ok"
    assert stage() == "ok"                                 # no text: not checked


def test_level():
    p = copy.deepcopy(PROFILE)
    p["filters"]["new_grad_only"] = True
    def lv(title="Software Engineer", text=None, listed=False, **data):
        v = filters.check(job(title=title, **data), p, NOW, text=text, listed_early=listed)
        return v.facts.get("new_grad_sign") if v.ok else v.stage
    assert lv() == "level"
    assert lv("Software Engineer, New Grad") == "title"
    assert lv("Associate Software Engineer") == "title"
    assert lv("Software Engineer I") == "title"
    assert lv("Software Engineer (2027)") == "title"
    assert lv(listed=True) == "level"
    assert lv(text="Build our platform.", listed=True) == "a new-grad list"
    assert lv(levels=["Entry Level"]) == "the source's level (levels)"
    assert lv(levels=["Entry Level"], listed=True) == "the source's level (levels)"
    assert lv(seniority=["Mid-level"]) == "level"
    assert lv(text="We welcome new grads and recent graduates.").startswith("the description")
    assert lv(text="Between new graduate level and 5 years of engineering experience.").startswith("the description")
    assert lv(text="0-2 years of experience with Python").startswith("the description")
    assert lv(text="5+ years building distributed systems") == "level"
    assert filters.check(job(), p, NOW, skip=("level",)).ok


def test_a_list_alone_cuts_a_title_only_job_and_says_so():
    p = copy.deepcopy(PROFILE)
    p["filters"]["new_grad_only"] = True
    v = filters.check(job(title="Software Engineer, Platform"), p, NOW, listed_early=True)
    assert (v.ok, v.stage, v.reason) == (False, "level", "nothing says it's for new grads (title only)")


@pytest.mark.parametrize("title,new_grad", [
    ("Software Engineer I - Engagement Growth", True),
    ("Software Engineer 1 - Cloud Infrastructure", True),
    ("Software Dev Engineer I, Graviton Software", True),
    ("Software Developer Engineer 1 - Graviton", True),
    ("Full Stack Software Engineer I/II", True),
    ("Software Engineer Embedded Systems I (Full Time)", True),
    ("SDE I", True),
    ("Data Scientist I", True),
    ("Software Engineer Level 0", True),
    ("Software Engineer II - Payments", False),
    ("Software Engineer III", False),
    ("Software Engineer, I/O Systems", False),
    ("Layer-1 Engineer", False),
    ("Software Engineer, Layer-1 Protocol", False),
    ("Application Developer Levels 1 - 5", False),
    ("Software Engineer 1.5", False),
    ("Software Engineer (I'm hiring)", False),
    ("Software Engineer, Team 12", False),
])
def test_a_level_one_anywhere_in_the_title_is_new_grad(title, new_grad):
    p = copy.deepcopy(PROFILE)
    p["filters"]["new_grad_only"] = True
    p["position_types"] += [{"name": "Other", "titles": ["sde", "data scientist", "engineer", "developer"]}]
    v = filters.check(job(title=title), p, NOW, listed_early=True)
    assert (v.facts.get("new_grad_sign") if v.ok else v.stage) == ("title" if new_grad else "level")


def test_facts():
    v = filters.check(job(title="Software Engineer", locations=["SF"], pay={"summary": "$150k/yr"}), PROFILE, NOW)
    assert v.ok and v.facts["position_type"] == "SWE" and v.facts["area"] == "SF" and v.facts["hourly_top"] == 72.12


# ------------------------------------------------------------------- revision 5: the audit fixes

@pytest.mark.parametrize("locations,expected", [
    (["Arlington County, Arlington, VA"], "ok"),             # the city is the second part
    (["New York City Metropolitan Area, United States"], "ok"),
    (["Greater Boston Area"], "place"),                      # read as Boston: still outside the places
    (["San Francisco Bay Area"], "ok"),
    (["Arlington, Texas"], "place"),                         # another state
])
def test_place_reads_every_part_and_metro_names(locations, expected):
    assert stage(locations=locations) == expected


def test_a_foreign_namesake_never_matches_a_us_town():
    prof = copy.deepcopy(PROFILE)
    prof["filters"]["locations"]["places"]["DC"] += ["Vienna, VA", "Hanover, MD"]
    assert filters.place_fit(["Vienna, VA, United States"], prof)[0] == "DC"
    assert filters.place_fit(["Vienna, Austria"], prof)[0] is None
    assert filters.place_fit(["Hanover, Germany"], prof)[0] is None
    assert filters.place_fit(["Hanover, United States"], prof)[0] == "DC"      # no state given: the name decides


def test_skip_age():
    old = job(published_at="2026-08-01T00:00:00Z")
    assert filters.check(old, PROFILE, NOW).stage == "age"
    assert filters.check(old, PROFILE, NOW, skip=("age",)).ok


@pytest.mark.parametrize("title,expected", [
    ("Software Development Engineer", "ok"),
    ("Software Dev Engineer I, Security Platform", "ok"),
    ("Engineer, Software", "ok"),
    ("Software Engineering Full-Time Campus Hire", "ok"),
    ("Software Engineering Manager", "title"),               # "manager" is excluded
    ("Full Stack Developer", "ok"),
    ("Site Reliability Engineer", "ok"),
    ("Associate Product Manager", "ok"),                     # "manager" is part of the matched type title
    ("Software Test Engineer", "title"),                     # words out of order aren't a match
])
def test_profile_v6_titles(title, expected):
    prof = copy.deepcopy(PROFILE)
    prof["position_types"][0]["titles"] += ["software development engineer", "software dev engineer", "engineer software",
                                            "software engineering", "full stack developer"]
    prof["position_types"] += [{"name": "DevOps / SRE", "titles": ["devops engineer", "site reliability engineer", "sre"]},
                               {"name": "Product", "titles": ["associate product manager", "product manager"]}]
    prof["filters"]["exclude_titles"] = prof["filters"]["exclude_titles"] + ["manager"]
    v = filters.check(job(title=title), prof, NOW)
    assert (v.stage if not v.ok else "ok") == expected


# ------------------------------------------------- F1 (11-05): the job's own board decides its country

@pytest.mark.parametrize("data,expected", [
    ({}, "ok"),                                                                    # no board answer: as before
    ({"board_country": "United Kingdom", "board_more_places": False}, "place"),
    ({"board_country": "United States of America", "board_more_places": False}, "ok"),
    ({"board_country": "US", "board_more_places": False}, "ok"),
    ({"board_country": "USA", "board_more_places": False}, "ok"),
    ({"board_country": "United Kingdom", "board_more_places": True}, "ok"),       # several places: the lists decide
])
def test_the_board_country_outranks_a_list(data, expected):
    j = job(title="Software Engineer Analyst", locations=("London, UK", "San Francisco, CA"), **data)
    v = filters.check(j, PROFILE, NOW)
    assert (v.stage if not v.ok else "ok") == expected
    if expected == "place":
        assert v.reason == "its own job board places it in United Kingdom"
