"""The jobs site (src/debrief/jobs/web/): the Cloudflare Access gate, the pages, the Tracked mark,
profile and watchlist edits, and the guards (same-site forms only, no writes during a rebuild,
nothing but http(s) links, everything escaped, no scripts)."""
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from debrief.http import Fetched
from debrief.jobs import collect, config as jconfig, coverage, profile, rank
from debrief.jobs.store import JobsStore, state_path
from debrief.jobs.web import data
from debrief.jobs.web.app import create_app
from debrief.jobs.web.auth import AccessVerifier
from browser_form import browser_submit
from test_jobs_coverage import _sample, job
from test_jobs_score import NOW, ByTitle, _add, _cfg
from test_jobs_collect import it

TEAM, AUD = "testteam", "aud-123"
ISS = f"https://{TEAM}.cloudflareaccess.com"
EMAILS = ["alexwoodka@gmail.com", "alexwoodka@startupshell.org"]
ORIGIN = "http://testserver"


def _key(kid):
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(k.public_key()))
    jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return pem, jwk


PEM, JWK = _key("k1")
OTHER_PEM, OTHER_JWK = _key("k2")


def token(email="alexwoodka@gmail.com", aud=AUD, iss=ISS, exp_in=600, pem=PEM, kid="k1", alg="RS256", key=None):
    now = int(time.time())
    claims = {"email": email, "aud": [aud], "iss": iss, "iat": now, "exp": now + exp_in, "type": "app"}
    return jwt.encode(claims, key or pem, algorithm=alg, headers={"kid": kid})


class Certs:
    def __init__(self, keys):
        self.keys, self.calls = keys, 0

    def __call__(self, url):
        self.calls += 1
        assert url == f"{ISS}/cdn-cgi/access/certs"
        return {"keys": self.keys}


@pytest.fixture
def site(tmp_path):
    cfg = _cfg(tmp_path, top_n=2, fresh_bonus={"3": 2, "7": 1})
    _add(cfg, [it("a", title="Software Engineer, New Grad", company="Initech <script>alert(1)</script>",
                  url="https://job-boards.greenhouse.io/initech/jobs/1", published_at="2026-11-03T12:00:00Z"),
               it("b", title="AI Engineer, New Grad", company="Globex", url="javascript:alert(1)"),
               it("c", title="Software Engineer I", company="Hooli"),
               it("w", title="Software Engineer, New Grad", company="Acme")], "2026-11-04T08:00:00Z")
    rank.run(cfg, run_date="2026-11-04", runner=ByTitle({"AI Engineer, New Grad": 8}), now=NOW, log=lambda *a: None)
    _add(cfg, [it("o", title="Software Engineer, New Grad", company="Oldco", published_at="2026-10-02T00:00:00Z")],
         "2026-11-04T09:00:00Z")
    rank.catchup(cfg, run_date="2026-11-04", runner=ByTitle(), now=NOW, log=lambda *a: None, since="2026-10-01", name="older")
    _sample(cfg, "2026-11-04", [job("Software Engineer, New Grad", "Umbrella")])
    coverage.run(cfg, run_date="2026-11-04", now=NOW, log=lambda *a: None)
    certs = Certs([JWK])
    verifier = AccessVerifier(TEAM, AUD, EMAILS, fetch=certs)
    app = create_app(cfg=cfg, verifier=verifier, origin=ORIGIN, env={})
    client = TestClient(app, base_url=ORIGIN)
    client.headers["Cf-Access-Jwt-Assertion"] = token()
    return cfg, client, certs


def ids(cfg):
    s = JobsStore(state_path(cfg))
    out = {s.posting(r[0])["company"]: r[0] for r in s.q("SELECT posting_id FROM postings")}
    s.close()
    return out


# ------------------------------------------------------------------------------------ the gate

@pytest.mark.parametrize("make", [
    lambda: None,
    lambda: "not-a-token",
    lambda: token(exp_in=-60),                                  # expired
    lambda: token(aud="another-app"),
    lambda: token(iss="https://evil.cloudflareaccess.com"),
    lambda: token(email="someone@example.com"),                  # not an allowed email
    lambda: token(pem=OTHER_PEM, kid="k1"),                      # signed by another key
    lambda: token(alg="HS256", key="a-shared-secret-of-forty-bytes-length!!"),   # algorithm confusion
])
def test_the_gate_refuses(site, make):
    _, client, _ = site
    t = make()
    headers = {"Cf-Access-Jwt-Assertion": t} if t else {}
    if not t:
        client.headers.pop("Cf-Access-Jwt-Assertion")
    r = client.get("/", headers=headers)
    assert r.status_code == 403 and "Initech" not in r.text


def test_both_emails_get_in_and_an_unknown_key_is_fetched_once(site):
    _, client, certs = site
    for email in EMAILS:
        assert client.get("/", headers={"Cf-Access-Jwt-Assertion": token(email=email.upper())}).status_code == 200
    before = certs.calls
    assert client.get("/", headers={"Cf-Access-Jwt-Assertion": token(pem=OTHER_PEM, kid="k2")}).status_code == 403
    assert client.get("/", headers={"Cf-Access-Jwt-Assertion": token(pem=OTHER_PEM, kid="k2")}).status_code == 403
    assert certs.calls == before + 1                             # one refetch, then a minute's wait
    certs.keys.append(OTHER_JWK)                                  # Access rotated its keys
    v = AccessVerifier(TEAM, AUD, EMAILS, fetch=certs)
    assert v.email_for(token(pem=OTHER_PEM, kid="k2")) == "alexwoodka@gmail.com"


# ----------------------------------------------------------------------------------- the pages

def test_today_escapes_links_only_http_and_runs_no_script(site):
    cfg, client, _ = site
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "<script" not in body and "&lt;script&gt;" in body
    assert "javascript:" not in body
    assert 'href="https://job-boards.greenhouse.io/initech/jobs/1"' in body and 'rel="noopener noreferrer"' in body
    assert "AI Engineer, New Grad" in body and "Also kept, still options for later days" in body
    assert "default-src 'none'" in r.headers["content-security-policy"] and r.headers["x-content-type-options"] == "nosniff"
    assert "alexwoodka@gmail.com" in body                        # who's signed in


def test_catchup_and_coverage_and_unknown_pages(site):
    _, client, _ = site
    r = client.get("/catchup/older")
    assert r.status_code == 200 and "Older roles" in r.text and "Oldco" in r.text and "posted since Oct 1" in r.text
    assert client.get("/catchup/nope").status_code == 404
    r = client.get("/coverage")
    assert r.status_code == 200 and "Google sample" in r.text and "pending" in r.text


# ------------------------------------------------------------------------------ the Tracked mark

def test_track_toggles_and_shows_everywhere(site):
    cfg, client, _ = site
    pid = ids(cfg)["Globex"]
    r = client.post("/track", data={"posting_id": pid, "on": "1", "back": "/"}, headers={"Origin": ORIGIN},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/#{data.anchor(pid)}"
    s = JobsStore(state_path(cfg))
    assert pid in s.tracked()
    s.close()
    body = client.get("/").text
    assert f'class="job tracked" id="{data.anchor(pid)}"' in body and "Untrack" in body
    client.post("/track", data={"posting_id": pid, "on": "0", "back": "//evil.example/x"}, headers={"Origin": ORIGIN},
                follow_redirects=False)
    s = JobsStore(state_path(cfg))
    assert pid not in s.tracked()
    s.close()


def test_track_refuses_cross_site_get_and_rebuilds(site):
    cfg, client, _ = site
    pid = ids(cfg)["Globex"]
    r = client.post("/track", data={"posting_id": pid, "on": "1"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/track", data={"posting_id": pid, "on": "1"})                      # no Origin, no Sec-Fetch-Site
    assert r.status_code == 403
    assert client.get("/track").status_code == 405
    collect.rebuilding_marker(cfg).write_text("now")
    r = client.post("/track", data={"posting_id": pid, "on": "1"}, headers={"Origin": ORIGIN})
    assert r.status_code == 503
    collect.rebuilding_marker(cfg).unlink()
    r = client.post("/track", data={"posting_id": pid, "on": "1"}, headers={"Sec-Fetch-Site": "same-origin"},
                    follow_redirects=False)
    assert r.status_code == 303
    s = JobsStore(state_path(cfg))
    assert list(s.tracked()) == [pid]
    s.close()


# --------------------------------------------------------------------------------- the profile

def test_profile_save_and_history(site):
    cfg, client, _ = site
    v0, _ = profile.latest(cfg)
    assert client.post("/profile", data={"action": "save"}, headers={"Origin": ORIGIN}).status_code == 400
    assert profile.latest(cfg)[0] == v0
    form = browser_submit(client.get("/profile").text, "/profile")
    form |= {"filters.max_age_days": "21", "action": "save", "note": "three weeks"}
    r = client.post("/profile", data=form, headers={"Origin": ORIGIN}, follow_redirects=False)
    assert r.status_code == 303 and profile.latest(cfg)[0] == v0 + 1
    assert profile.latest(cfg)[1]["filters"]["max_age_days"] == 21
    assert "three weeks" in client.get("/profile").text
    assert client.get("/profile/v/99").status_code == 404


# ------------------------------------------------------------------------------- the watchlist

class Boards:
    user_agent = "t"

    def robots_allowed(self, url):
        return True

    def request(self, method, url, params=None, json_body=None, headers=None):
        body = {"jobs": [{"id": 1, "title": "SWE"}, {"id": 2, "title": "MLE"}]} if "globex" in url else None
        if body is None:
            return Fetched(url=url, final_url=url, status=404, error="HTTP 404")
        return Fetched(url=url, final_url=url, status=200, headers={"content-type": "application/json"},
                       content=json.dumps(body).encode())

    def get(self, url, **kw):
        return self.request("GET", url)


def test_watchlist_add_and_remove(site):
    cfg, _, certs = site
    app = create_app(cfg=cfg, verifier=AccessVerifier(TEAM, AUD, EMAILS, fetch=certs), origin=ORIGIN, env={}, client=Boards())
    client = TestClient(app, base_url=ORIGIN)
    client.headers["Cf-Access-Jwt-Assertion"] = token()
    v0 = profile.latest(cfg)[0]
    r = client.post("/watchlist/add", data={"name": "Globex", "url": "https://job-boards.greenhouse.io/globex"},
                    headers={"Origin": ORIGIN})
    assert r.status_code == 200 and "Added Globex (board greenhouse:globex" in r.text
    assert profile.latest(cfg)[0] == v0 + 1 and "greenhouse:globex" in r.text
    r = client.post("/watchlist/add", data={"name": "globex"}, headers={"Origin": ORIGIN})
    assert "already on the watchlist" in r.text
    r = client.get("/watchlist/remove?name=Globex")
    assert r.status_code == 200 and "Remove Globex?" in r.text and profile.latest(cfg)[0] == v0 + 1   # asking changes nothing
    r = client.post("/watchlist/remove", data={"name": "Globex"}, headers={"Origin": ORIGIN})
    assert "Removed Globex" in r.text and "greenhouse:globex" not in r.text.split("</nav>")[1].split("Removed")[0]


def _edit(client, *form, **changes):
    fields = browser_submit(client.get("/watchlist").text, "/watchlist/edit") if not form else form[0]
    return client.post("/watchlist/edit", data={"action": "save", **fields, **changes}, headers={"Origin": ORIGIN}, follow_redirects=False)


def _company(cfg, i=0):
    return profile.latest(cfg)[1]["companies"][i]


def _with_companies(cfg, companies):
    _, d = profile.latest(cfg)
    return profile.save(cfg, {**d, "companies": companies})[0]


def test_watchlist_board_and_note_can_be_set_and_cleared(site):
    cfg, client, _ = site
    _with_companies(cfg, [{"name": "Acme"}, {"name": "Globex", "board": None}])
    v = profile.latest(cfg)[0]
    r = _edit(client, **{"companies.0.board": "https://jobs.lever.co/acme/123"})
    assert r.status_code == 303 and "/watchlist?msg=Saved" in r.headers["location"]
    assert _company(cfg) == {"name": "Acme", "board": "lever:acme"} and _company(cfg, 1) == {"name": "Globex", "board": None}
    assert profile.latest(cfg)[0] == v + 1
    _edit(client, **{"companies.0.board": "ashby:acme", "companies.0.note": "dream job"})
    assert _company(cfg) == {"name": "Acme", "board": "ashby:acme", "note": "dream job"}
    assert "dream job" in client.get("/watchlist").text
    _edit(client, **{"companies.0.note": "", "companies.0.board": ""})
    assert _company(cfg) == {"name": "Acme", "board": None}


def test_an_untouched_watchlist_form_is_a_no_change_with_every_shape_of_company(site):
    cfg, client, _ = site
    v = _with_companies(cfg, [{"name": "Acme"}, {"name": "B & <C>", "board": None}, {"name": "D", "board": "lever:d", "note": ""},
                              {"name": " Padded ", "note": "two\nlines"}])
    before = profile.profile_id(profile.latest(cfg)[1])
    r = _edit(client)
    assert r.status_code == 303 and "No%20change" in r.headers["location"]
    assert profile.latest(cfg)[0] == v and profile.profile_id(profile.latest(cfg)[1]) == before


def test_the_watchlist_form_has_one_way_to_add_and_one_way_to_remove(site):
    cfg, client, _ = site
    page = client.get("/watchlist").text
    fields = browser_submit(page, "/watchlist/edit")
    assert sorted(k for k in fields if k.startswith("companies")) == ["companies.0.board", "companies.0.name", "companies.0.note"]
    assert "companies.0.remove" not in page and "companies.1" not in page
    assert 'action="/watchlist/add"' in page and "/watchlist/remove?name=Acme" in page


def test_a_bad_board_or_name_is_refused_on_its_field_in_plain_words(site):
    cfg, client, _ = site
    v = profile.latest(cfg)[0]
    r = _edit(client, **{"companies.0.board": "monster:acme", "companies.0.note": "kept"})
    assert r.status_code == 400 and "Systems: greenhouse" in r.text.split('name="companies.0.board"')[1].split('class="hint"')[0]
    assert 'value="monster:acme"' in r.text and 'value="kept"' in r.text and "does not match" not in r.text
    r = _edit(client, **{"companies.0.board": "https://example.com/careers"})
    assert r.status_code == 400 and "isn&#39;t a job board we can read" in r.text
    r = _edit(client, **{"companies.0.name": ""})
    assert r.status_code == 400 and "To take it off, use Remove." in r.text
    assert profile.latest(cfg)[0] == v


def test_renaming_onto_a_hidden_company_lands_on_the_name(site):
    cfg, client, _ = site
    _, d = profile.latest(cfg)
    profile.save(cfg, {**d, "filters": {**d["filters"], "blocklist": ["Hidden"]}})
    r = _edit(client, **{"companies.0.name": "Hidden"})
    assert r.status_code == 400 and "Hidden is on both" in r.text.split('name="companies.0.name"')[1].split('class="hint"')[0]


def test_watchlist_edit_keeps_the_guards(site):
    cfg, client, _ = site
    fields = browser_submit(client.get("/watchlist").text, "/watchlist/edit") | {"companies.0.note": "n", "action": "save", "note": "mine"}
    v = profile.latest(cfg)[0]
    assert client.post("/watchlist/edit", data=fields, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/watchlist/edit", data=fields).status_code == 403
    collect.rebuilding_marker(cfg).write_text("now")
    r = client.post("/watchlist/edit", data=fields, headers={"Origin": ORIGIN})
    assert r.status_code == 503 and 'name="companies.0.note" value="n"' in r.text and 'value="mine"' in r.text
    assert client.post("/watchlist/edit", data={**fields, "action": "check"}, headers={"Origin": ORIGIN}).status_code == 200
    collect.rebuilding_marker(cfg).unlink()
    assert profile.latest(cfg)[0] == v


def test_a_stale_watchlist_form_keeps_an_add_when_untouched_and_explains_when_not(site):
    cfg, client, _ = site
    page = client.get("/watchlist").text
    _, d = profile.latest(cfg)
    v, _ = profile.save(cfg, {**d, "companies": d["companies"] + [{"name": "Globex", "board": None}]})
    r = _edit(client, browser_submit(page, "/watchlist/edit"))
    assert r.status_code == 303 and "No%20change" in r.headers["location"] and profile.latest(cfg)[0] == v
    r = _edit(client, browser_submit(page, "/watchlist/edit"), **{"companies.0.note": "mine"})
    assert r.status_code == 409 and "added Globex" in r.text and 'value="mine"' in r.text and profile.latest(cfg)[0] == v


def test_a_stale_settings_page_and_the_watchlist_form_change_only_their_own_settings(site):
    cfg, client, _ = site
    page = client.get("/watchlist").text
    _, d = profile.latest(cfg)
    profile.save(cfg, {**d, "wants": "Newer."})
    r = _edit(client, browser_submit(page, "/watchlist/edit"), **{"companies.0.note": "mine"})
    assert r.status_code == 303 and "elsewhere%20to%20In%20your%20own%20words" in r.headers["location"]
    _, now = profile.latest(cfg)
    assert now["wants"] == "Newer." and now["companies"][0]["note"] == "mine"


def test_the_watchlist_has_no_edit_form_before_a_profile_exists(tmp_path):
    cfg = jconfig.load(env={"DEBRIEF_DATA_DIR": str(tmp_path)})
    app = create_app(cfg=cfg, verifier=AccessVerifier(TEAM, AUD, EMAILS, fetch=Certs([JWK])), origin=ORIGIN, env={})
    client = TestClient(app, base_url=ORIGIN)
    client.headers["Cf-Access-Jwt-Assertion"] = token()
    page = client.get("/watchlist")
    assert page.status_code == 200 and "/watchlist/edit" not in page.text and "No companies yet" in page.text
    assert client.post("/watchlist/edit", data={"action": "save"}, headers={"Origin": ORIGIN}, follow_redirects=False).status_code == 303


def _watch_client(cfg, certs, boards=None, **kw):
    app = create_app(cfg=cfg, verifier=AccessVerifier(TEAM, AUD, EMAILS, fetch=certs), origin=ORIGIN, env={}, client=boards or Boards())
    client = TestClient(app, base_url=ORIGIN, **kw)
    client.headers["Cf-Access-Jwt-Assertion"] = token()
    return client


def test_adding_a_hidden_company_is_explained_not_a_500(site):
    cfg, _, certs = site
    _, d = profile.latest(cfg)
    v, _ = profile.save(cfg, {**d, "filters": {**d["filters"], "blocklist": ["Globex"]}})
    client = _watch_client(cfg, certs, raise_server_exceptions=False)
    r = client.post("/watchlist/add", data={"name": "Globex", "url": "https://job-boards.greenhouse.io/globex"}, headers={"Origin": ORIGIN})
    assert r.status_code == 200 and "Companies to hide" in r.text and profile.latest(cfg)[0] == v


def test_a_save_during_the_board_lookup_is_not_reverted(site):
    cfg, _, certs = site

    class SavesDuringLookup(Boards):
        def request(self, method, url, params=None, json_body=None, headers=None):
            _, d = profile.latest(cfg)
            profile.save(cfg, {**d, "wants": "Changed during the lookup."})
            return super().request(method, url, params, json_body, headers)

    client = _watch_client(cfg, certs, boards=SavesDuringLookup())
    r = client.post("/watchlist/add", data={"name": "Globex", "url": "https://job-boards.greenhouse.io/globex"}, headers={"Origin": ORIGIN})
    assert "Added Globex" in r.text
    _, now = profile.latest(cfg)
    assert now["wants"] == "Changed during the lookup." and "Globex" in [c["name"] for c in now["companies"]]


def test_safe_url():
    assert data.safe_url("https://a.test/x") == "https://a.test/x"
    assert data.safe_url("javascript:alert(1)") is None and data.safe_url("//a.test") is None and data.safe_url(None) is None
