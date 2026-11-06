"""The settings page's model (src/debrief/jobs/web/profile_form.py): every schema field has a control
that fits it, and an untouched form reads back to the identical document."""
import json

import pytest

from debrief.jobs import profile
from debrief.jobs.web import profile_form as pf
from test_jobs_profile import doc


def _schema():
    return json.loads(profile.SCHEMA.read_text())


def _resolve(node, schema):
    if "$ref" in node:
        return {**schema["$defs"][node["$ref"].rsplit("/", 1)[1]], **{k: v for k, v in node.items() if k != "$ref"}}
    return node


def _schema_nodes(node, schema, path=""):
    """{slot: schema node} for every settable place: "filters.max_years", "companies[].note",
    "filters.locations.places{}"."""
    node = _resolve(node, schema)
    if "patternProperties" in node or isinstance(node.get("additionalProperties"), dict):
        return {path + "{}": node}
    if "properties" in node:
        out = {}
        for key, child in node["properties"].items():
            out |= _schema_nodes(child, schema, f"{path}.{key}" if path else key)
        return out
    items = _resolve(node.get("items", {}), schema)
    if items.get("type") == "object":
        return {path: node} | _schema_nodes(items, schema, path + "[]")
    return {path: node}


SLOTS = _schema_nodes(_schema(), _schema())
LEAVES = dict(pf.leaves())


def test_tables_place_every_schema_field_exactly_once():
    placed = [slot for slot, _ in pf.leaves()]
    assert sorted(placed) == sorted(SLOTS)
    assert len(placed) == len(set(placed))


@pytest.mark.parametrize("slot", sorted(SLOTS))
def test_every_control_fits_its_schema_node(slot):
    node, control = SLOTS[slot], LEAVES[slot]
    if "enum" in node:
        assert isinstance(control, pf.Choice) and sorted(control.labels) == sorted(node["enum"])
    elif node.get("type") == "boolean":
        assert isinstance(control, pf.Toggle)
    elif node.get("type") == "integer":
        assert isinstance(control, pf.Number) and control.whole
    elif node.get("type") == "number":
        assert isinstance(control, pf.Number) and not control.whole
    elif slot.endswith("{}"):
        assert isinstance(control, pf.MapRows)
    elif node.get("type") == "array" and node["items"].get("type") == "object":
        assert isinstance(control, pf.Rows)
    elif node.get("type") == "array":
        assert isinstance(control, pf.Lines)
    else:
        assert isinstance(control, (pf.Text, pf.Area))


def test_board_is_the_one_field_that_accepts_a_url_and_its_pattern_has_wording():
    assert LEAVES["companies[].board"].accept is not None
    assert "pattern" in LEAVES["companies[].board"].messages
    assert "pattern" in LEAVES["degree.graduation"].messages


def _posted_by_a_browser(raw, reading):
    """Multi-line inputs post CRLF; a single-line input never holds a newline."""
    out = {}
    for name, value in raw.items():
        field = reading.fields.get(name)
        if isinstance(field, (pf.Lines, pf.Area)):
            out[name] = value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        elif isinstance(field, pf.Toggle) and not value:
            continue
        else:
            out[name] = value.replace("\r", "").replace("\n", "")
    return out


NASTY = dict(
    filters__min_hourly_pay=25.0,
    ranking={"threshold": 40, "max_candidates": 150, "top_n": 30, "per_company": 3, "fresh_bonus": {}},
    companies=[{"name": "Acme"}, {"name": "B & <C>", "board": None}, {"name": "D", "board": "lever:d", "note": ""},
               {"name": "  Padded ", "note": "two\nlines"}],
    wants="\nLeading newline, CRLF\r\nand a trailing newline\n",
    skills=["C++", "  padded  ", "Ünïcode", "a, b"],
    filters__locations={"remote_us": False, "places": {"Z": ["New York, NY", "Hoboken"], "A": ["Boston"]}},
    projects=[{"name": "Same", "summary": " one ", "skills": []}, {"name": "Same", "summary": "two\n", "skills": ["x y"]}],
    position_types=[{"name": "  SWE", "titles": ["z title", "a title"]}, {"name": "ML", "titles": ["ml"]}],
)
BONUS = dict(ranking={"threshold": 40.0, "max_candidates": 150, "top_n": 30, "fresh_bonus": {"7": 1, "3": 2}})


@pytest.mark.parametrize("table", [pf.TABLE, pf.WATCH_TABLE], ids=["settings", "watchlist"])
@pytest.mark.parametrize("changes", [{}, NASTY, BONUS], ids=["base", "nasty", "bonus"])
def test_an_untouched_form_reads_back_identically(table, changes):
    stored = doc(**changes)
    raw = pf.fill_all(stored, table)
    rd, read = pf.read_all(raw, stored, table)
    assert not rd.failed and profile.canonical(read) == profile.canonical(stored)
    rd, read = pf.read_all(_posted_by_a_browser(raw, rd), stored, table)
    assert not rd.failed and profile.canonical(read) == profile.canonical(stored)
    assert profile.profile_id(read) == profile.profile_id(stored)


@pytest.mark.parametrize("stored,typed,expected", [
    (25, "25", 25), (25, "25.0", 25), (25, " 25 ", 25), (25, "$25", 25), (25.0, "25", 25.0), (25.0, "25.0", 25.0),
    (25, "27.50", 27.5), (25, "30", 30),
])
def test_retyping_the_same_number_keeps_its_stored_form(stored, typed, expected):
    d = doc(filters__min_hourly_pay=stored)
    _, read = pf.read_all({**pf.fill_all(d), "filters.min_hourly_pay": typed}, d)
    assert repr(read["filters"]["min_hourly_pay"]) == repr(expected)


@pytest.mark.parametrize("typed", ["twenty", "1e9", "nan", "inf", "14.0", "1_000", "٣"])
def test_a_whole_number_input_refuses_anything_else_and_keeps_the_stored_value(typed):
    d = doc()
    rd, read = pf.read_all({**pf.fill_all(d), "filters.max_age_days": typed}, d)
    assert rd.failed == {"filters.max_age_days": ["Enter a whole number, like 14."]}
    assert read["filters"]["max_age_days"] == 14


def test_emptying_a_required_number_is_an_error_and_an_optional_one_removes_the_key():
    d = doc(ranking={"threshold": 40, "max_candidates": 150, "top_n": 30, "per_company": 3})
    rd, read = pf.read_all({**pf.fill_all(d), "ranking.top_n": "", "ranking.per_company": ""}, d)
    assert rd.failed == {"ranking.top_n": ["Can't be blank."]}
    assert "per_company" not in read["ranking"]


def test_a_changed_list_is_tidied_and_a_repeat_is_dropped_keeping_order():
    d = doc(skills=["Python", "Go"])
    rd, read = pf.read_all({**pf.fill_all(d), "skills": " Go \r\n\r\nPython\r\nGo\r\nRust\r\n"}, d)
    assert not rd.failed and read["skills"] == ["Go", "Python", "Rust"]


def test_a_trailing_line_alone_changes_nothing():
    d = doc(skills=["Python", "Go"])
    _, read = pf.read_all({**pf.fill_all(d), "skills": "Python\r\nGo\r\n\r\n"}, d)
    assert read["skills"] == ["Python", "Go"]


def test_toggles_read_as_a_browser_posts_them():
    d = doc()
    raw = pf.fill_all(d)
    del raw["filters.full_time_only"]
    _, read = pf.read_all(raw, d)
    assert read["filters"]["full_time_only"] is False
    assert read["filters"]["new_grad_only"] is True


def test_a_spare_row_adds_a_blank_one_does_not_and_remove_removes():
    d = doc(position_types=[{"name": "A", "titles": ["a"]}, {"name": "B", "titles": ["b"]}])
    raw = {**pf.fill_all(d), "position_types.2.name": "", "position_types.2.titles": ""}
    _, read = pf.read_all(raw, d)
    assert read["position_types"] == d["position_types"]
    raw |= {"position_types.2.name": "C", "position_types.2.titles": "c1\r\nc2"}
    _, read = pf.read_all(raw, d)
    assert read["position_types"][2] == {"name": "C", "titles": ["c1", "c2"]}
    raw["position_types.0.remove"] = "on"
    _, read = pf.read_all(raw, d)
    assert [p["name"] for p in read["position_types"]] == ["B", "C"]


def test_an_edit_only_list_keeps_a_blanked_row_for_the_schema_to_refuse():
    d = doc(companies=[{"name": "Acme", "note": "n"}])
    raw = {"companies.0.name": "", "companies.0.board": "", "companies.0.note": ""}
    _, read = pf.read_all(raw, d, pf.WATCH_TABLE)
    assert read["companies"] == [{}]


def test_map_rows_add_rename_refuse_a_repeat_and_keep_absent_absent():
    d = doc()
    assert "fresh_bonus" not in pf.read_all(pf.fill_all(d), d)[1]["ranking"]
    raw = {**pf.fill_all(d), "filters.locations.places.1.area": "DC", "filters.locations.places.1.places": "Washington, DC"}
    _, read = pf.read_all(raw, d)
    assert read["filters"]["locations"]["places"] == {"NYC": ["New York, NY"], "DC": ["Washington, DC"]}
    raw["filters.locations.places.1.area"] = "NYC"
    rd, _ = pf.read_all(raw, d)
    assert rd.failed == {"filters.locations.places.1.area": ["NYC is listed twice."]}
    raw = {**pf.fill_all(d), "filters.locations.places.0.area": ""}
    rd, _ = pf.read_all(raw, d)
    assert rd.failed == {"filters.locations.places.0.area": ["Give this area a name."]}


def test_a_new_map_row_with_no_value_says_so():
    d = doc()
    raw = {**pf.fill_all(d), "ranking.fresh_bonus.0.days": "3", "ranking.fresh_bonus.0.bonus": ""}
    rd, _ = pf.read_all(raw, d)
    assert rd.failed == {"ranking.fresh_bonus.0.bonus": ["Enter the points to add."]}


def test_clearing_every_bonus_removes_the_key_but_an_empty_one_stays():
    d = doc(ranking={"threshold": 40, "max_candidates": 150, "top_n": 30, "fresh_bonus": {"3": 2}})
    raw = {**pf.fill_all(d), "ranking.fresh_bonus.0.days": "", "ranking.fresh_bonus.0.bonus": ""}
    assert "fresh_bonus" not in pf.read_all(raw, d)[1]["ranking"]
    d = doc(ranking={"threshold": 40, "max_candidates": 150, "top_n": 30, "fresh_bonus": {}})
    assert pf.read_all(pf.fill_all(d), d)[1]["ranking"]["fresh_bonus"] == {}


@pytest.mark.parametrize("before,typed,after", [
    ({"name": "Acme"}, "lever:acme", {"name": "Acme", "board": "lever:acme"}),
    ({"name": "Acme"}, "https://jobs.lever.co/acme/123", {"name": "Acme", "board": "lever:acme"}),
    ({"name": "Acme", "board": "lever:acme"}, "", {"name": "Acme", "board": None}),
    ({"name": "Acme", "board": None}, "", {"name": "Acme", "board": None}),
    ({"name": "Acme"}, "", {"name": "Acme"}),
])
def test_board_forms(before, typed, after):
    d = doc(companies=[before])
    _, read = pf.read_all({**pf.fill_all(d, pf.WATCH_TABLE), "companies.0.board": typed}, d, pf.WATCH_TABLE)
    assert read["companies"] == [after]


def test_an_unreadable_board_url_is_refused_in_plain_words():
    d = doc()
    rd, read = pf.read_all({**pf.fill_all(d, pf.WATCH_TABLE), "companies.0.board": "https://example.com/careers"}, d, pf.WATCH_TABLE)
    assert "isn't a job board we can read" in rd.failed["companies.0.board"][0]
    assert read["companies"] == d["companies"]


def test_a_table_never_touches_what_it_does_not_place():
    d = doc()
    _, read = pf.read_all(pf.fill_all(d, pf.WATCH_TABLE) | {"companies.0.note": "hello"}, d, pf.WATCH_TABLE)
    assert read == {**d, "companies": [{**d["companies"][0], "note": "hello"}]}
    _, read = pf.read_all(pf.fill_all(d) | {"wants": "New."}, d)
    assert read == {**d, "wants": "New."}


def test_a_blank_first_profile_is_complete_apart_from_what_only_a_person_can_say():
    missing = {i.path for i in profile.issues(pf.blank_doc()) if i.rule == "required"}
    assert ("degree", "level") in missing and ("ranking", "top_n") in missing and ("filters", "max_years") in missing
    assert ("skills",) not in missing and ("companies",) not in missing and ("ranking", "per_company") not in missing
