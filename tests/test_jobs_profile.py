import copy
import json

import pytest

from debrief.jobs import config as jconfig, profile

BASE = {
    "degree": {"level": "BS", "field": "Computer Science", "school": "Example U", "graduation": "2027-05"},
    "coursework": ["Algorithms"], "skills": ["Python"],
    "position_types": [{"name": "Software Engineer", "titles": ["software engineer"]}],
    "projects": [{"name": "P", "summary": "A thing.", "skills": ["Python"]}],
    "interests": ["AI"], "companies": [{"name": "Acme", "board": "greenhouse:acme"}],
    "wants": "Entry-level engineering.",
    "filters": {"locations": {"remote_us": True, "places": {"NYC": ["New York, NY"]}}, "max_years": 2,
                "max_age_days": 14, "work_authorization": "us_citizen", "clearance": {"has": "none", "eligible": True},
                "exclude_titles": ["senior"], "blocklist": [], "full_time_only": True, "min_hourly_pay": 25,
                "new_grad_only": True},
    "ranking": {"threshold": 40, "max_candidates": 150, "top_n": 30},
}


def doc(**changes):
    d = copy.deepcopy(BASE)
    for path, value in changes.items():
        *parents, last = path.split("__")
        target = d
        for p in parents:
            target = target[p]
        target[last] = value
    return d


def test_valid_profile_has_no_problems():
    assert profile.problems(doc()) == []


@pytest.mark.parametrize("changes,needle", [
    ({"degree__graduation": "May 2027"}, "degree/graduation"),
    ({"filters__max_years": -1}, "filters/max_years"),
    ({"companies": [{"name": "Acme", "board": "monster:acme"}]}, "companies/0/board"),
    ({"skills": ["Python", "Python"]}, "skills"),
    ({"surprise": 1}, "(top)"),
    ({"position_types": [{"name": "SWE", "titles": ["senior"]}]}, "also in filters/exclude_titles"),
    ({"position_types": [{"name": "A", "titles": ["x"]}, {"name": "a", "titles": ["y"]}]}, "appears twice"),
    ({"companies": [{"name": "Acme"}, {"name": "acme"}]}, "listed twice"),
    ({"filters__blocklist": ["Acme"]}, "also on filters/blocklist"),
    ({"ranking__top_n": 200}, "can't be filled"),
    ({"filters__locations": {"remote_us": False, "places": {}}}, "every job would be filtered out"),
])
def test_problems_are_reported(changes, needle):
    assert any(needle in p for p in profile.problems(doc(**changes)))


def _without(*keys):
    d = doc()
    for k in keys:
        del d[k]
    return d


@pytest.mark.parametrize("bad,lines", [
    (doc(degree__graduation="May 2027"), ["degree/graduation: 'May 2027' does not match '^\\\\d{4}-(0[1-9]|1[0-2])$'"]),
    (doc(filters__max_years=-1), ["filters/max_years: -1 is less than the minimum of 0"]),
    (doc(companies=[{"name": "Acme", "board": "monster:acme"}]),
     ["companies/0/board: 'monster:acme' does not match "
      "'^(greenhouse|lever|ashby|workable|smartrecruiters|recruitee|workday):[^\\\\s]+$'"]),
    (doc(skills=["Python", "Python"]), ["skills: ['Python', 'Python'] has non-unique elements"]),
    ({**doc(), "surprise": 1}, ["(top): Additional properties are not allowed ('surprise' was unexpected)"]),
    (doc(position_types=[{"name": "SWE", "titles": ["senior"]}]), ["position_types/SWE: 'senior' is also in filters/exclude_titles"]),
    (doc(position_types=[{"name": "A", "titles": ["x"]}, {"name": "a", "titles": ["y"]}]), ["position_types: 'a' appears twice"]),
    (doc(companies=[{"name": "Acme"}, {"name": "acme"}]), ["companies: 'acme' is listed twice"]),
    (doc(filters__blocklist=["Acme"]), ["companies: 'Acme' is also on filters/blocklist"]),
    (doc(ranking__top_n=200), ["ranking: top_n (200) is more than max_candidates (150), so it can't be filled"]),
    (doc(filters__locations={"remote_us": False, "places": {}}), ["filters/locations: no remote and no places, so every job would be filtered out"]),
    (_without("ranking", "wants"), ["(top): 'wants' is a required property", "(top): 'ranking' is a required property"]),
    (doc(filters__max_age_days=0, ranking={"threshold": 400, "max_candidates": 1, "top_n": 1, "fresh_bonus": {"x": 9}}),
     ["filters/max_age_days: 0 is less than the minimum of 1",
      "ranking/fresh_bonus: 'x' does not match any of the regexes: '^[1-9][0-9]{0,2}$'",
      "ranking/threshold: 400 is greater than the maximum of 100"]),
])
def test_problem_lines_keep_their_exact_text(bad, lines):
    assert profile.problems(bad) == lines


@pytest.mark.parametrize("bad,path,rule", [
    (doc(filters__max_years=-1), ("filters", "max_years"), "minimum"),
    (_without("wants"), ("wants",), "required"),
    (doc(position_types=[{"name": "A", "titles": ["x"]}, {"name": "a", "titles": ["y"]}]), ("position_types", 1, "name"), "type_twice"),
    (doc(position_types=[{"name": "A", "titles": ["x"]}, {"name": "B", "titles": ["senior"]}]), ("position_types", 1, "titles"), "title_excluded"),
    (doc(companies=[{"name": "Acme"}, {"name": "acme"}]), ("companies", 1, "name"), "company_twice"),
    (doc(filters__blocklist=["Acme"]), ("companies", 0, "name"), "company_blocked"),
    (doc(ranking__top_n=200), ("ranking", "top_n"), "top_n_over"),
    (doc(filters__locations={"remote_us": False, "places": {}}), ("filters", "locations", "remote_us"), "nowhere"),
])
def test_issues_point_at_the_setting_at_fault(bad, path, rule):
    found = profile.issues(bad)
    assert [(i.path, i.rule) for i in found] == [(path, rule)]
    assert [i.line for i in found] == profile.problems(bad)


def test_version_reads_any_saved_version(tmp_path):
    cfg = jconfig.load(env={"DEBRIEF_DATA_DIR": str(tmp_path)})
    assert profile.version(cfg, 1) is None
    profile.save(cfg, doc())
    profile.save(cfg, doc(ranking__top_n=20))
    assert profile.version(cfg, 1) == doc() and profile.version(cfg, 2) == doc(ranking__top_n=20)
    assert profile.version(cfg, 3) is None


def test_save_versions_and_skips_unchanged(tmp_path):
    cfg = jconfig.load(env={"DEBRIEF_DATA_DIR": str(tmp_path)})
    assert profile.latest(cfg) == (0, None)
    assert profile.save(cfg, doc(), note="first") == (1, True)
    assert profile.save(cfg, doc(), note="same") == (1, False)
    assert profile.save(cfg, doc(ranking__top_n=20)) == (2, True)
    n, latest = profile.latest(cfg)
    assert n == 2 and latest["ranking"]["top_n"] == 20
    d = cfg.data_dir / "profile"
    assert sorted(p.name for p in d.glob("profile-*.json")) == ["profile-0001.json", "profile-0002.json"]
    assert (d / "profile-0001.json").stat().st_mode & 0o777 == 0o600
    assert (d / "history.jsonl").stat().st_mode & 0o777 == 0o600
    hist = [json.loads(x) for x in (d / "history.jsonl").read_text().splitlines()]
    assert [h["version"] for h in hist] == [1, 2] and hist[0]["note"] == "first"
    assert hist[1]["id"] == profile.profile_id(latest) != hist[0]["id"]


def test_bad_profile_is_not_saved(tmp_path):
    cfg = jconfig.load(env={"DEBRIEF_DATA_DIR": str(tmp_path)})
    with pytest.raises(profile.ProfileError, match="filters/max_age_days"):
        profile.save(cfg, doc(filters__max_age_days=0))
    assert profile.latest(cfg) == (0, None)


def test_id_ignores_key_order():
    a = doc()
    b = json.loads(json.dumps(a, sort_keys=True))
    assert profile.profile_id(a) == profile.profile_id(b)


def test_a_title_may_contain_an_excluded_word():
    # 'member of technical staff' is matched whole; 'staff' only excludes what's left over (M2's filter).
    d = doc(position_types=[{"name": "Research", "titles": ["member of technical staff"]}], filters__exclude_titles=["staff"])
    assert profile.problems(d) == []
