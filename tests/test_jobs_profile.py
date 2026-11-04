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
                "exclude_titles": ["senior"], "blocklist": []},
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
