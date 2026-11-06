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
from debrief.jobs import collect, coverage, profile, rank
from debrief.jobs.store import JobsStore, state_path
from debrief.jobs.web import data
from debrief.jobs.web.app import create_app
from debrief.jobs.web.auth import AccessVerifier
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

def test_profile_check_save_and_history(site):
    cfg, client, _ = site
    v0, doc = profile.latest(cfg)
    r = client.post("/profile", data={"profile": "{not json", "action": "save"}, headers={"Origin": ORIGIN})
    assert r.status_code == 400 and "Not valid JSON" in r.text and profile.latest(cfg)[0] == v0
    bad = dict(doc)
    del bad["ranking"]
    r = client.post("/profile", data={"profile": json.dumps(bad), "action": "save"}, headers={"Origin": ORIGIN})
    assert r.status_code == 400 and "ranking" in r.text and profile.latest(cfg)[0] == v0
    good = json.loads(json.dumps(doc))
    good["filters"]["max_age_days"] = 21
    r = client.post("/profile", data={"profile": json.dumps(good), "action": "check"}, headers={"Origin": ORIGIN})
    assert r.status_code == 200 and "No problems found" in r.text and profile.latest(cfg)[0] == v0
    r = client.post("/profile", data={"profile": json.dumps(good), "action": "save", "note": "three weeks"},
                    headers={"Origin": ORIGIN}, follow_redirects=False)
    assert r.status_code == 303 and profile.latest(cfg)[0] == v0 + 1
    r = client.get(f"/profile/v/{v0 + 1}")
    assert r.status_code == 200 and '+    &#34;max_age_days&#34;: 21' in r.text
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


def test_safe_url():
    assert data.safe_url("https://a.test/x") == "https://a.test/x"
    assert data.safe_url("javascript:alert(1)") is None and data.safe_url("//a.test") is None and data.safe_url(None) is None
