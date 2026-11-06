"""The settings page's model (src/debrief/jobs/web/profile_form.py): every schema field has a control
that fits it, and an untouched form reads back to the identical document."""
import json

import pytest

from browser_form import browser_submit
from debrief.jobs import collect, config as jconfig, profile
from debrief.jobs.web import profile_form as pf
from debrief.jobs.web.app import SECURITY_HEADERS, create_app
from debrief.jobs.web.auth import AccessVerifier
from starlette.testclient import TestClient
from test_jobs_profile import doc
from test_jobs_web import AUD, EMAILS, ORIGIN, TEAM, Certs, JWK, site, token  # noqa: F401


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


# -------------------------------------------------------------------------- through the real page

def _post(client, form, **extra):
    return client.post("/profile", data={**form, **extra}, headers={"Origin": ORIGIN}, follow_redirects=False)


def _form(client):
    return browser_submit(client.get("/profile").text, "/profile")


@pytest.mark.parametrize("changes", [{}, NASTY, BONUS], ids=["base", "nasty", "bonus"])
def test_an_untouched_page_saves_nothing(site, changes):
    cfg, client, _ = site
    profile.save(cfg, doc(**changes), note="seed")
    v, before = profile.latest(cfg)
    r = _post(client, _form(client), action="save")
    assert r.status_code == 303 and "No%20change" in r.headers["location"]
    n, after = profile.latest(cfg)
    assert n == v and profile.canonical(after) == profile.canonical(before)


def test_one_edit_changes_only_that_setting(site):
    cfg, client, _ = site
    v, before = profile.latest(cfg)
    r = _post(client, {**_form(client), "skills": "Python\r\nRust"}, action="save", note="learned rust")
    assert r.status_code == 303 and "Saved%20as%20version" in r.headers["location"]
    n, after = profile.latest(cfg)
    assert n == v + 1 and after == {**before, "skills": ["Python", "Rust"]}


def test_every_problem_is_listed_on_its_field_in_plain_words_with_typing_kept(site):
    cfg, client, _ = site
    v, _ = profile.latest(cfg)
    r = _post(client, _form(client) | {"filters.max_age_days": "120", "filters.min_hourly_pay": "twenty", "degree.graduation": "May 2027",
                                       "ranking.top_n": "", "wants": "", "note": "tighter"}, action="save")
    assert r.status_code == 400 and profile.latest(cfg)[0] == v
    page = r.text
    assert "Not saved. 5 things to fix" in page
    for words in ("Must be 90 or less.", "Enter a number, like 25.", "Use year and month, like 2027-05.", "Can&#39;t be blank.",
                  "Say what you&#39;re looking for."):
        assert words in page
    assert 'value="120"' in page and 'value="twenty"' in page and 'value="May 2027"' in page and 'value="tighter"' in page
    for schema_words in ("filters/", "ranking:", "is greater than", "does not match", "is not of type", "required property"):
        assert schema_words not in page


def test_a_field_error_sits_inside_that_fields_markup_and_opens_its_section(site):
    _, client, _ = site
    page = _post(client, {**_form(client), "filters.max_age_days": "120"}, action="save").text
    field = page.split('<label for="i-filters-max_age_days">')[1].split('class="hint"')[0]
    assert "Must be 90 or less." in field
    assert 'id="keep">\n    <details open>' in page
    assert 'id="where">\n    <details>' in page


def test_cross_field_rules_land_on_the_right_input(site):
    cfg, client, _ = site
    page = _post(client, {**_form(client), "filters.exclude_titles": "senior\r\nsoftware engineer"}, action="save").text
    titles = page.split('name="position_types.0.titles"')[1].split('class="hint"')[0]
    assert "“software engineer” is also in Words that rule out a title" in titles
    page = _post(client, {**_form(client), "filters.blocklist": "Acme"}, action="save").text
    blocklist = page.split('name="filters.blocklist"')[1].split('class="hint"')[0]
    assert "Acme is on both your Watchlist and Companies to hide" in blocklist
    max_candidates = profile.latest(cfg)[1]["ranking"]["max_candidates"]
    page = _post(client, {**_form(client), "ranking.top_n": "400"}, action="save").text
    assert f"Can&#39;t be more than Most jobs the model reads ({max_candidates})" in page.split('name="ranking.top_n"')[1].split('class="hint"')[0]
    form = _form(client)
    del form["filters.locations.remote_us"]
    r = _post(client, {**form, "filters.locations.places.0.remove": "on"}, action="save")
    assert r.status_code == 400 and "With neither, every job is filtered out." in r.text


def test_check_says_so_and_writes_nothing_and_a_failed_check_is_not_called_a_failed_save(site):
    cfg, client, _ = site
    v, _ = profile.latest(cfg)
    r = _post(client, {**_form(client), "filters.max_age_days": "21"}, action="check")
    assert r.status_code == 200 and "No problems" in r.text and 'value="21"' in r.text and profile.latest(cfg)[0] == v
    assert 'id="keep">\n    <details open>' in r.text
    r = _post(client, {**_form(client), "filters.max_age_days": "120"}, action="check")
    assert r.status_code == 400 and "Not ready to save." in r.text and "Not saved" not in r.text


def test_check_renders_a_fresh_spare_row_and_keeps_what_was_typed_in_the_old_one(site):
    _, client, _ = site
    page = _post(client, {**_form(client), "projects.1.name": "New one", "projects.1.summary": "Words.", "projects.1.skills": ""}, action="check").text
    assert 'name="projects.1.name" value="New one"' in page
    assert 'name="projects.2.name" value=""' in page and "projects.3.name" not in page


def test_enter_means_check_because_check_comes_first(site):
    _, client, _ = site
    page = client.get("/profile").text
    assert page.index('value="check"') < page.index('value="save"')
    assert page.count('type="submit"') == 2


def test_a_rebuild_refuses_save_with_the_form_kept_but_not_check(site):
    cfg, client, _ = site
    v, _ = profile.latest(cfg)
    form = {**_form(client), "filters.max_age_days": "21", "note": "keep me"}
    collect.rebuilding_marker(cfg).write_text("now")
    r = _post(client, form, action="save")
    assert r.status_code == 503 and "nothing was saved" in r.text and 'value="21"' in r.text and 'value="keep me"' in r.text
    assert profile.latest(cfg)[0] == v
    assert _post(client, form, action="check").status_code == 200
    collect.rebuilding_marker(cfg).unlink()
    assert _post(client, browser_submit(r.text, "/profile"), action="save").status_code == 303
    assert profile.latest(cfg)[1]["filters"]["max_age_days"] == 21


def test_every_write_keeps_the_guards(site):
    cfg, client, _ = site
    form = _form(client)
    v, _ = profile.latest(cfg)
    r = client.post("/profile", data={**form, "action": "save"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert client.post("/profile", data={**form, "action": "save"}).status_code == 403
    client.headers.pop("Cf-Access-Jwt-Assertion")
    assert client.post("/profile", data={**form, "action": "save"}, headers={"Origin": ORIGIN}).status_code == 403
    assert profile.latest(cfg)[0] == v


def test_a_page_from_before_this_form_existed_is_refreshed_not_saved(site):
    cfg, client, _ = site
    v, _ = profile.latest(cfg)
    r = client.post("/profile", data={"profile": "{}", "action": "save"}, headers={"Origin": ORIGIN})
    assert r.status_code == 400 and "out of date" in r.text and profile.latest(cfg)[0] == v


def test_the_page_has_no_json_no_scripts_and_the_same_csp(site):
    _, client, _ = site
    r = client.get("/profile")
    for banned in ("<script", " onclick", " onchange", "javascript:", '{"', "{&#34;", 'name="profile"'):
        assert banned not in r.text
    assert r.headers["content-security-policy"] == ("default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                                                    "base-uri 'none'; frame-ancestors 'none'")
    assert r.headers["content-security-policy"] == SECURITY_HEADERS["Content-Security-Policy"]


def test_sections_start_closed_and_summarize_their_values(site):
    _, client, _ = site
    page = client.get("/profile").text
    assert "<details open" not in page
    for words in ("Remote in the US · NYC", "$25 an hour or more · last 14 days · up to 2 years", "1 skill · 1 interest",
                  "BS Computer Science, Example U, May 2027 · 1 course", "US citizen · no clearance, could obtain one"):
        assert words in page
    assert 'href="/watchlist"' in page and "Acme. Boards and notes are set on the Watchlist page." in page


def test_a_closed_section_still_posts(site):
    _, client, _ = site
    page = client.get("/profile").text
    assert 'name="skills"' in page.split('id="skills">')[1] and "<details>" in page.split('id="skills">')[1][:30]
    assert "skills" in browser_submit(page, "/profile")


def test_a_first_profile_can_be_made_from_blank(tmp_path):
    cfg = jconfig.load(env={"DEBRIEF_DATA_DIR": str(tmp_path)})
    app = create_app(cfg=cfg, verifier=AccessVerifier(TEAM, AUD, EMAILS, fetch=Certs([JWK])), origin=ORIGIN, env={})
    client = TestClient(app, base_url=ORIGIN)
    client.headers["Cf-Access-Jwt-Assertion"] = token()
    r = _post(client, _form(client), action="save")
    assert r.status_code == 400 and profile.latest(cfg) == (0, None)
    assert "Can&#39;t be blank." in r.text and "required property" not in r.text
    assert "Pick one of the options." in r.text and "Say what you&#39;re looking for" in r.text
    assert "Keep at least one kind of role" in r.text and "With neither" not in r.text
    filled = _form(client) | {
        "degree.level": "BS", "degree.field": "CS", "degree.graduation": "2027-05", "wants": "Engineering.",
        "position_types.0.name": "SWE", "position_types.0.titles": "software engineer",
        "filters.locations.remote_us": "on", "filters.max_years": "2", "filters.max_age_days": "14",
        "filters.work_authorization": "us_citizen", "filters.clearance.has": "none", "filters.min_hourly_pay": "25",
        "ranking.threshold": "40", "ranking.max_candidates": "150", "ranking.top_n": "30",
    }
    r = _post(client, filled, action="save")
    assert r.status_code == 303, r.text
    n, saved = profile.latest(cfg)
    assert n == 1 and profile.problems(saved) == [] and saved["companies"] == [] and saved["degree"]["school"] == ""
    assert "per_company" not in saved["ranking"] and "fresh_bonus" not in saved["ranking"]


# --------------------------------------------------------------------------------- a stale page

def _add_elsewhere(cfg, name="Globex"):
    _, d = profile.latest(cfg)
    return profile.save(cfg, {**d, "companies": d["companies"] + [{"name": name, "board": None}]}, note="other tab")[0]


def test_a_stale_page_keeps_a_watchlist_add(site):
    cfg, client, _ = site
    form = _form(client)
    v = _add_elsewhere(cfg)
    r = _post(client, {**form, "filters.max_age_days": "21"}, action="save")
    n, now = profile.latest(cfg)
    assert r.status_code == 303 and n == v + 1 and now["filters"]["max_age_days"] == 21
    assert "Globex" in [c["name"] for c in now["companies"]]
    assert "change%20made%20elsewhere%20to%20Companies%20was%20kept" in r.headers["location"]


def test_an_untouched_stale_page_writes_nothing(site):
    cfg, client, _ = site
    form = _form(client)
    v = _add_elsewhere(cfg)
    r = _post(client, form, action="save")
    assert r.status_code == 303 and "No%20change" in r.headers["location"] and profile.latest(cfg)[0] == v


def test_a_different_setting_changed_elsewhere_is_taken_up_and_shown(site):
    cfg, client, _ = site
    form = _form(client)
    _, d = profile.latest(cfg)
    profile.save(cfg, {**d, "skills": ["Python", "Go"]}, note="other tab")
    r = _post(client, {**form, "wants": "Mine."}, action="check")
    assert r.status_code == 200 and "Python\nGo</textarea>" in r.text.replace("\r", "") and "Mine." in r.text
    r = _post(client, browser_submit(r.text, "/profile"), action="save")
    assert r.status_code == 303
    now = profile.latest(cfg)[1]
    assert now["skills"] == ["Python", "Go"] and now["wants"] == "Mine."


def _other_tab_sets_age(cfg, days):
    _, d = profile.latest(cfg)
    return profile.save(cfg, {**d, "filters": {**d["filters"], "max_age_days": days}}, note="other tab")[0]


def test_the_same_setting_changed_twice_stops_explains_and_a_second_save_wins(site):
    cfg, client, _ = site
    form = {**_form(client), "filters.max_age_days": "21", "note": "mine"}
    v = _other_tab_sets_age(cfg, 30)
    r = _post(client, form, action="save")
    assert r.status_code == 409 and profile.latest(cfg)[0] == v
    assert "Changed elsewhere while this page was open" in r.text.split('name="filters.max_age_days"')[1].split('class="hint"')[0]
    assert 'name="filters.max_age_days" value="21"' in r.text and 'value="mine"' in r.text
    r = _post(client, browser_submit(r.text, "/profile"), action="save")
    assert r.status_code == 303 and profile.latest(cfg)[0] == v + 1
    assert profile.latest(cfg)[1]["filters"]["max_age_days"] == 21


def test_a_conflict_stops_check_too(site):
    cfg, client, _ = site
    form = {**_form(client), "filters.max_age_days": "21"}
    _other_tab_sets_age(cfg, 30)
    r = _post(client, form, action="check")
    assert r.status_code == 409 and "Not ready to save." in r.text


def test_two_changes_that_agree_are_no_conflict(site):
    cfg, client, _ = site
    form = {**_form(client), "filters.max_age_days": "21"}
    v = _other_tab_sets_age(cfg, 21)
    r = _post(client, form, action="save")
    assert r.status_code == 303 and "No%20change" in r.headers["location"] and profile.latest(cfg)[0] == v


def test_a_stale_row_list_merges_as_one_setting(site):
    cfg, client, _ = site
    form = {**_form(client), "position_types.0.name": "Renamed"}
    _, d = profile.latest(cfg)
    profile.save(cfg, {**d, "position_types": d["position_types"] + [{"name": "Other", "titles": ["other"]}]}, note="other tab")
    r = _post(client, form, action="save")
    assert r.status_code == 409 and 'value="Renamed"' in r.text
    assert "Changed elsewhere" in r.text.split('id="i-position_types"')[1].split('class="items"')[0]


def test_a_blank_page_saved_after_a_first_profile_appeared_changes_nothing(tmp_path):
    cfg = jconfig.load(env={"DEBRIEF_DATA_DIR": str(tmp_path)})
    app = create_app(cfg=cfg, verifier=AccessVerifier(TEAM, AUD, EMAILS, fetch=Certs([JWK])), origin=ORIGIN, env={})
    client = TestClient(app, base_url=ORIGIN)
    client.headers["Cf-Access-Jwt-Assertion"] = token()
    form = _form(client)
    profile.save(cfg, doc())
    assert _post(client, form, action="save").status_code == 303
    assert profile.latest(cfg) == (1, doc())
