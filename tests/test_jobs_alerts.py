"""The alert inbox (src/debrief/jobs/adapters/alerts.py): read-only IMAP, each message fetched once
and kept raw, senders recognized, and a site's own listing ids never used as job keys, so a
LinkedIn copy merges into the job the lists already have."""
import sqlite3
from datetime import datetime, timezone

from conftest import fixture
from debrief.jobs import build, collect, items
from debrief.jobs.adapters import REGISTRY, alerts
from debrief.jobs.collect import early_sources
from debrief.jobs.store import JobsStore, state_path
from debrief.store import raw
from test_jobs_collect import _cfg, it, rec, src
from test_jobs_finder import _iso, _list

ENV = {"JOBS_IMAP_USER": "jobs.alerts@example.com", "JOBS_IMAP_PASSWORD": "app-password-123"}


def mail(sender, subject="New jobs for you", body="<html><body>jobs</body></html>"):
    return (f"From: {sender}\r\nTo: {ENV['JOBS_IMAP_USER']}\r\nSubject: {subject}\r\n"
            "Date: Thu, 05 Nov 2026 08:00:00 +0000\r\nContent-Type: text/html; charset=utf-8\r\n\r\n" + body).encode()


class FakeIMAP:
    """Records every command; answers UID SEARCH and UID FETCH from `messages` {uid: bytes}."""
    def __init__(self, messages, fail_login=False):
        self.messages, self.fail_login, self.calls = messages, fail_login, []

    def __call__(self, host, port, timeout=None):
        self.calls.append(("connect", host, port))
        return self

    def login(self, user, password):
        self.calls.append(("login", user))
        if self.fail_login:
            raise OSError(f"[AUTHENTICATIONFAILED] Invalid credentials for {user} / {password}")

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [str(len(self.messages)).encode()]

    def status(self, mailbox, what):
        return "OK", [b'"INBOX" (UIDVALIDITY 777)']

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [b" ".join(str(u).encode() for u in sorted(self.messages))]
        uid = int(args[0])
        return "OK", [(f"{uid} (UID {uid} BODY[] {{1}}".encode(), self.messages[uid]), b")"]

    def logout(self):
        self.calls.append(("logout",))


def _night(cfg, store, monkeypatch, fake):
    monkeypatch.setattr(alerts.AlertInbox, "connect", staticmethod(fake))
    source = cfg.source("alert-inbox")
    from debrief.jobs import profile
    _, prof = profile.latest(cfg)
    return collect.collect_source(cfg, store, None, source, f"run-{len(fake.calls)}", "2026-11-05",
                                  datetime.now(timezone.utc), prof, ENV, lambda *a, **k: None)


def test_reads_read_only_once_each_and_keeps_no_username(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"alert-inbox"})
    s = JobsStore(state_path(cfg))
    fake = FakeIMAP({1: mail("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>"),
                     2: mail("Handshake <noreply@m.joinhandshake.com>", subject="Fwd: jobs"),
                     3: mail("Someone <hello@example.org>", subject="Welcome")})
    out = _night(cfg, s, monkeypatch, fake)
    assert out["status"] == "ok" and out["requests"] == 3
    assert ("select", "INBOX", True) in fake.calls                                   # read-only
    assert all(c[3] == "(BODY.PEEK[])" for c in fake.calls if c[:2] == ("uid", "FETCH"))
    urls = [r["url"] for r in s.q("SELECT url FROM fetches WHERE source_id='alert-inbox' ORDER BY seq")]
    assert urls == [f"imaps://imap.gmail.com/INBOX;UIDVALIDITY=777;UID={u}" for u in (1, 2, 3)]
    assert not any("jobs.alerts" in u for u in urls)
    day = raw.runs_for_day(cfg.data_dir, "2026-11-05")
    records = next(iter(day.values()))["alert-inbox"][1]
    assert [(r["meta"]["site"], r["each"]) for r in records] == [("linkedin", "linkedin"), ("handshake", "handshake"),
                                                                 (None, "other")]
    # The next night: every message was fetched once already, so none is fetched again.
    fake.calls.clear()
    out = _night(cfg, s, monkeypatch, fake)
    assert out["requests"] == 0 and not [c for c in fake.calls if c[:2] == ("uid", "FETCH")]
    s.close()


def test_a_failed_login_fails_the_source_and_hides_the_password(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"alert-inbox"})
    s = JobsStore(state_path(cfg))
    out = _night(cfg, s, monkeypatch, FakeIMAP({}, fail_login=True))
    assert out["status"] == "failed"
    err = " ".join(out["errors"])
    assert "login" in err and ENV["JOBS_IMAP_PASSWORD"] not in err and ENV["JOBS_IMAP_USER"] not in err
    s.close()


def test_the_inbox_is_not_a_new_grad_source(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"alert-inbox", "simplify-newgrad"})
    assert early_sources(cfg) == {"simplify-newgrad"}


def test_senders_are_recognized_by_domain():
    assert alerts.sender_site("Indeed <alert@jobalert.indeed.com>") == "indeed"
    assert alerts.sender_site("Indeed <alert@indeedemail.com>") == "indeed"
    assert alerts.sender_site("x@notlinkedin.com") is None


INDEED, LINKEDIN = fixture("alerts/indeed-alert.eml"), fixture("alerts/linkedin-digest.eml")
INBOX = REGISTRY["alert_inbox"]


def parsed(body, site):
    r = rec(body, url="imaps://imap.gmail.com/INBOX;UIDVALIDITY=1;UID=1", meta={"site": site}, source_id="alert-inbox")
    return INBOX.parse(r, src(id="alert-inbox", family="email"))


def test_an_indeed_alert_gives_each_job_dated_back_from_the_email():
    got = parsed(INDEED, "indeed")
    assert [(i.title, i.company, i.locations, i.url, i.published_at) for i in got] == [
        ("Junior Software Engineer", "Globex Corporation", ("Springfield, IL",),
         "https://www.indeed.com/viewjob?jk=0a1b2c3d4e5f6a7b", "2026-11-12T13:00:00Z"),
        ("Software Engineer I - Platform", "Initech - Systems Group", ("Shelbyville, IL",),
         "https://www.indeed.com/viewjob?jk=1b2c3d4e5f6a7b8c", "2026-11-09T13:00:00Z"),
        ("Software Developer", "Hooli", ("Springfield, IL",), "", "2026-11-11T13:00:00Z"),
        ("Data Engineer", "Vandelay Industries", ("United States",),
         "https://www.indeed.com/viewjob?jk=2c3d4e5f6a7b8c9d", "2026-10-13T13:00:00Z")]
    assert got[0].pay is None and got[0].extra == {"via": "Indeed", "pay_hint": "$70,000 - $90,000 a year"}
    assert all(items.job_key(i) is None for i in got)
    again = parsed(INDEED.replace(b"tk=x", b"tk=y").replace(b"ad=x", b"ad=y"), "indeed")
    assert [i.item_key for i in again] == [i.item_key for i in got] and len({i.item_key for i in got}) == 4


def test_a_linkedin_digest_gives_each_job_once_with_its_workplace():
    got = parsed(LINKEDIN, "linkedin")
    view = "https://www.linkedin.com/jobs/view/{}/"
    assert [(i.title, i.company, i.locations, i.remote, i.url) for i in got] == [
        ("Junior Software Engineer", "Globex Corporation", ("Springfield, IL",), False, view.format(4000000001)),
        ("New Grad Software Engineer", "Initech", ("United States",), True, view.format(4000000002)),
        ("Software Engineer, Early Career", "Hooli", ("Shelbyville, IL",), None, view.format(4000000003))]
    assert all(i.published_at is None and items.job_key(i) is None for i in got)


def test_only_the_daily_digests_are_read():
    created = LINKEDIN.replace(b"email_email_job_alert_digest_01", b"email_email_job_alert_confirmation_01")
    active = mail("Indeed <alert@jobalert.indeed.com>", subject="Your job alert is now active",
                  body="<p>Your job alert is active</p><p>4 new entry level software engineer jobs in Springfield, IL</p>"
                       '<a href="https://engage.indeed.com/f/a/x?from=jawelcometop">Junior Software Engineer</a>'
                       "<p>Globex Corporation</p><p>- Springfield, IL</p>")
    account = mail("Indeed <no-reply@indeed.com>", subject="Confirm your new email address", body="<p>Your code</p>")
    assert parsed(created, "linkedin") == parsed(active, "indeed") == parsed(account, "indeed") == []
    assert parsed(LINKEDIN, "linkedin") and parsed(INDEED, "indeed")


def test_a_digest_we_cannot_read_makes_the_night_partial(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"alert-inbox"})
    s = JobsStore(state_path(cfg))
    moved = LINKEDIN.replace(b"https://www.linkedin.com/comm/jobs/view/", b"https://www.linkedin.com/comm/jobs/v2/")
    out = _night(cfg, s, monkeypatch, FakeIMAP({1: INDEED, 2: moved}))
    assert out["status"] == "partial" and out["counts"]["items"] == 4
    assert "linkedin job-alert digest with no jobs we can read" in " ".join(out["errors"])
    s.close()


def test_alert_copies_join_the_job_the_lists_have(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"alert-inbox", "simplify-newgrad"})
    s = JobsStore(state_path(cfg))
    now = _iso(datetime.now(timezone.utc))
    _list(s, [it("s1", title="Junior Software Engineer", company="Globex", locations=("Springfield, IL",),
                 url="https://job-boards.greenhouse.io/globex/jobs/5", job_key="job:greenhouse:5", board="greenhouse:globex")],
          now, source_id="simplify-newgrad", snapshot="window")
    records = [rec(body, url=f"imaps://imap.gmail.com/INBOX;UIDVALIDITY=1;UID={n}", meta={"site": site}, fetched_at=now,
                   source_id="alert-inbox", seq=n) for n, (body, site) in enumerate([(INDEED, "indeed"), (LINKEDIN, "linkedin")])]
    with s.transaction():
        c = build.apply(s, cfg.source("alert-inbox"), INBOX, records, ["ref#0", "ref#1"], early_sources=early_sources(cfg))
    assert c["items"] == 7 and c["merged"] == 2
    pid = s.find_key("job:greenhouse:5")
    assert {r[0] for r in s.q("SELECT item_key FROM items WHERE posting_id=? AND source_id='alert-inbox'", (pid,))} == {
        "indeed:0a1b2c3d4e5f6a7b", "linkedin:4000000001"}
    assert s.posting(pid)["url"] == "https://job-boards.greenhouse.io/globex/jobs/5"
    assert s.job_keys(pid) == ["job:greenhouse:5"]
    s.close()


def test_a_replay_rebuilds_the_same_listings(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"alert-inbox"})
    s = JobsStore(state_path(cfg))
    out = _night(cfg, s, monkeypatch, FakeIMAP({1: INDEED, 2: LINKEDIN}))
    assert out["status"] == "ok" and out["counts"]["items"] == 7
    s.close()
    into = tmp_path / "replay.sqlite"
    collect.replay(cfg, "2026-11-05", into, log=lambda *a: None)

    def dump(path):
        con = sqlite3.connect(path)
        rows = {t: con.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall() for t in collect.TABLES}
        con.close()
        return rows
    assert dump(state_path(cfg)) == dump(into) and len(dump(into)["items"]) == 7
