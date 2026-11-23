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
    (["Tokyo", "Remote"], "place"),
    (["Remote", "London, UK"], "place"),
    (["Remote", "Atlanta, GA"], "ok"),
    (["Remote", "3 Locations"], "ok"),
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
    assert filters.min_years("Minimum of one year of professional software development experience.") == 1
    assert filters.min_years("Requirements One (1) year of experience in the job offered") == 1
    assert filters.min_years("ONE (1) or more years of experience developing data analytics solutions") == 1
    assert filters.min_years("Degree and typically 4 or more years of relevant professional experience.") == 4
    assert filters.min_years("Recent graduate with up to ~1 year of experience.") is None
    assert filters.min_years("Bachelor's degree OR Associate's degree and two (2) years of relevant experience") is None


@pytest.mark.parametrize("text,years", [
    ("Bachelor's degree OR 2+ years of professional experience building software in lieu of a degree", None),
    ("4 years of software engineering experience may be substituted for the degree.", None),
    ("Bachelor's degree and 1+ year of experience with embedded development or 4+ years of experience with "
     "embedded development in lieu of a degree", 1),
    ("1+ years of software development experience (internship and/or project experience is acceptable)", None),
    ("1–3 years of experience — or equivalent academic, project, or internship work", None),
    ("2-12+ years of industry software engineering experience (does not include internships or co-ops)", 2),
    ("3-10 years of full-time experience exclusive of internships in Data Analyst roles", 3),
    ("0–2 years of professional experience (or equivalent internship/academic project experience). "
     "2+ years of professional experience with Go.", 2),
    ("Minimum 5 years of experience in software engineering, plus a BS from an accredited school", 5),
    ("3+ years of experience in an academic or industry setting", 3),
    ("4 years of experience in software engineering (Internship experience does not apply)", 4),
    ("1-3 years of experience in software engineering (internships included)", None),
    ("1+ years of experience with software development, application support, internships, academic projects, or "
     "technical work", None),
])
def test_years_a_degree_or_school_can_stand_in_for_dont_count(text, years):
    assert filters.min_years(text) == years
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


def test_a_trusted_list_alone_passes_a_title_only_job_but_never_a_level_two_title():
    p = copy.deepcopy(PROFILE)
    p["filters"]["new_grad_only"] = True
    v = filters.check(job(title="Software Engineer, Platform"), p, NOW, listed_early=True, listed_trusted=True)
    assert v.ok and v.facts["new_grad_sign"] == "a trusted new-grad list"
    v = filters.check(job(title="Software Engineer II"), p, NOW, listed_early=True, listed_trusted=True)
    assert (v.ok, v.stage) == (False, "level")


def test_a_read_description_asking_no_years_is_a_sign_only_when_switched_on():
    p = copy.deepcopy(PROFILE)
    p["filters"]["new_grad_only"] = True
    def lv(title="Software Engineer", text="Build our platform in Python.", **kw):
        v = filters.check(job(title=title), p, NOW, text=text, **kw)
        return v.facts.get("new_grad_sign") if v.ok else v.stage
    assert lv() == "level"
    assert lv(no_years=True) == "the description asks for no years of experience"
    assert lv(text="1+ years of professional software experience.", no_years=True) == "level"
    assert lv(text=None, no_years=True) == "level"
    assert lv("Software Engineer II", no_years=True) == "level"


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


@pytest.mark.parametrize("title,verdict", [
    ("Software Engineer II", "level"),
    ("Software Dev Engineer II", "level"),
    ("Software Engineer II - Reliability", "level"),
    ("Associate Software Engineer III", "level"),
    ("Software Engineer (Level 3/4)", "level"),
    ("Software Engineer I/II", "title"),
    ("Web Developer 1 or 2", "title"),
    ("Software Engineer 2 – Early Career", "title"),
    ("Graduate Software Engineer 2", "title"),
    ("Software Dev Engineer I - AMZ10587268.2", "title"),
    ("Software Dev Engineer II - AMZ27610.1", "level"),
    ("Machine Learning Engineer 4 (IC)", "level"),
    ("Machine Learning Engineer, Level 5", "level"),
    ("AVP, Backend Software Engineer", "level"),
])
def test_a_title_naming_level_two_or_more_fails_level_whatever_else_says_new_grad(title, verdict):
    p = copy.deepcopy(PROFILE)
    p["filters"]["new_grad_only"] = True
    p["position_types"] += [{"name": "Other", "titles": ["engineer", "developer"]}]
    v = filters.check(job(title=title, levels=["Entry Level"]), p, NOW, text="We welcome new grads.", listed_early=True)
    assert (v.facts.get("new_grad_sign") if v.ok else v.stage) == verdict
    if verdict == "level":
        assert v.reason.startswith("the title names level ") and v.reason.endswith(", above entry")
    assert filters.title_level(title) == (verdict == "title")


@pytest.mark.parametrize("title,vetoed", [
    ("Software Engineer - Python 3", False),
    ("Software Engineer, Backend (Node 2)", False),
    ("Software Engineer - Team 2", False),
    ("Data Analyst, Product 3", False),
    ("Software Engineer 2", True),
    ("SDE III", True),
    ("Software Engineer, Level 2", True),
    ("Software Engineer Data & AI II", True),
    ("Software Engineer - 5G Core", False),
    ("Software Engineer (L2)", False),                     # company L-numbers differ: Google's entry level is L3
])
def test_a_level_number_counts_right_after_the_role_word_or_level(title, vetoed):
    p = copy.deepcopy(PROFILE)
    p["filters"]["new_grad_only"] = True
    p["position_types"] += [{"name": "Other", "titles": ["engineer", "analyst", "sde"]}]
    v = filters.check(job(title=title, levels=["Entry Level"]), p, NOW)
    assert (v.ok, v.stage) == ((False, "level") if vetoed else (True, ""))


def test_a_list_of_place_counts_is_an_unknown_place():
    assert stage(locations=["3 Locations"]) == "ok"
    assert stage(locations=["14 Locations", "2 Locations"]) == "ok"
    assert filters.place_fit(["3 Locations"], PROFILE) == ("unknown", None)
    assert stage(locations=["3 Locations", "London, UK"]) == "place"


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


def test_areas_names_every_profile_area_a_job_is_in():
    prof = {"filters": {"locations": {"places": {"DC": ["Washington, DC", "Reston, VA"], "NYC": ["New York, NY"]}}}}
    assert filters.areas(["Seattle, WA; Reston, VA"], prof) == {"DC"}
    assert filters.areas(["New York, NY", "Washington DC-Baltimore Area"], prof) == {"DC", "NYC"}
    assert filters.areas(["Reston, VA, United States"], prof) == {"DC"}
    assert filters.areas(["Remote, US"], prof) == filters.areas(["Vienna, Austria"], prof) == filters.areas([], prof) == set()


OTHER_US = copy.deepcopy(PROFILE)
OTHER_US["filters"]["locations"]["other_us"] = True


@pytest.mark.parametrize("locations,area,outside", [
    (["Atlanta, GA"], "other US", ["Atlanta, GA"]),
    (["Hybrid - Austin, TX"], "other US", ["Hybrid - Austin, TX"]),
    (["Atlanta, GA, United States", "London, UK"], "other US", ["Atlanta, GA, United States"]),
    (["Atlanta, GA", "New York, NY"], "NYC", None),
    (["Atlanta, GA", "Remote in USA"], "remote (US)", None),
    (["London, UK"], None, None),
    (["Perth, WA, Australia"], None, None),
    (["Vienna, Austria"], None, None),
    (["Remote - Canada"], None, None),
    (["Tokyo", "Remote"], None, None),
    ([], "unknown", None),
    (["3 Locations"], "unknown", None),
])
def test_other_us_keeps_a_us_job_outside_the_areas_and_names_its_places(locations, area, outside):
    v = filters.check(job(locations=locations), OTHER_US, NOW)
    assert (v.facts.get("area") if v.ok else None, v.facts.get("outside")) == (area, outside)
    assert v.ok or v.stage == "place"


def test_without_other_us_a_us_job_outside_the_areas_is_cut_as_before():
    assert stage(locations=["Atlanta, GA"]) == "place"
    off = copy.deepcopy(OTHER_US)
    off["filters"]["locations"]["other_us"] = False
    assert filters.check(job(locations=["Atlanta, GA"]), off, NOW).stage == "place"


def test_other_us_never_overrides_the_board_or_a_remote_ban():
    assert filters.check(job(locations=["Atlanta, GA"], board_country="United Kingdom"), OTHER_US, NOW).stage == "place"
    no_remote = copy.deepcopy(OTHER_US)
    no_remote["filters"]["locations"]["remote_us"] = False
    assert filters.check(job(locations=["Remote (US)"]), no_remote, NOW).stage == "place"
    assert filters.check(job(locations=["Remote - Cleveland, OH"]), no_remote, NOW).stage == "place"
    assert filters.check(job(locations=["Cleveland, OH"]), no_remote, NOW).facts["area"] == "other US"


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


def test_a_read_startup_role_asking_no_more_years_than_allowed_passes_the_level_check():
    p = copy.deepcopy(PROFILE)
    p["filters"]["new_grad_only"] = True
    def lv(text="Build our agent platform in Python.", startup=True):
        v = filters.check(job(title="Software Engineer"), p, NOW, text=text, startup=startup)
        return v.facts.get("new_grad_sign") if v.ok else v.stage
    assert lv() == "a startup role asking for no years of experience"
    assert lv(text="1+ years of experience with Python.") == "a startup role asking for 1 year of experience"
    assert lv(text="2+ years of experience with Python.") == "a startup role asking for 2 years of experience"
    assert lv(text="3+ years of professional software experience.") == "level"
    assert lv(text=None) == "level"
    assert lv(startup=False) == "level"


def test_small_company_reads_the_size_labels_sources_give():
    assert filters.small_company("11-50 people, series a")
    assert filters.small_company("25 people, YC W24")
    assert filters.small_company("201-1,000 people")
    assert not filters.small_company("1,001-5,000 people")
    assert not filters.small_company("5,001+ people, public")
    assert not filters.small_company("series b")
    assert not filters.small_company(None)
