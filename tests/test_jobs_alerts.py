"""The alert inbox (src/debrief/jobs/adapters/alerts.py): read-only IMAP, each message fetched once
and kept raw, senders recognized, and a site's own listing ids never used as job keys, so a
LinkedIn copy merges into the job the lists already have."""
from datetime import datetime, timezone

from debrief.jobs import build, collect, items
from debrief.jobs.adapters import REGISTRY, alerts
from debrief.jobs.collect import early_sources
from debrief.jobs.store import JobsStore, state_path
from debrief.store import raw
from test_jobs_collect import _cfg, it, rec, src  # noqa: F401
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


def test_the_inbox_counts_as_a_new_grad_source(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"alert-inbox", "simplify-newgrad"})
    assert {"alert-inbox", "simplify-newgrad"} <= early_sources(cfg)


def test_a_linkedin_copy_merges_into_the_job_the_lists_have(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, {"alert-inbox", "simplify-newgrad"})
    s = JobsStore(state_path(cfg))
    now = _iso(datetime.now(timezone.utc))
    _list(s, [it("s1", title="Software Engineer, New Grad", company="Globex", url="https://job-boards.greenhouse.io/globex/jobs/5",
                 job_key="job:greenhouse:5", board="greenhouse:globex")], now, source_id="simplify-newgrad", snapshot="window")

    def linkedin(msg, record, source):
        return [items.JobItem(item_key="linkedin:4100", title="New Grad: Software Engineer", company="Globex",
                              locations=("New York, NY",), url="https://www.linkedin.com/jobs/view/4100/",
                              published_at=record["fetched_at"], extra={"via": "LinkedIn"})]
    monkeypatch.setitem(alerts.PARSERS, "linkedin", linkedin)
    r = rec(mail("jobalerts-noreply@linkedin.com").decode(), url="imaps://imap.gmail.com/INBOX;UIDVALIDITY=1;UID=1",
            meta={"site": "linkedin"}, fetched_at=now, source_id="alert-inbox")
    with s.transaction():
        c = build.apply(s, cfg.source("alert-inbox"), REGISTRY["alert_inbox"], [r], ["ref#0"], early_sources=early_sources(cfg))
    assert c["merged"] == 1 and c.get("postings_new", 0) == 0
    pid = s.find_key("job:greenhouse:5")
    assert s.posting_sources(pid) == {"simplify-newgrad", "alert-inbox"}
    assert s.posting(pid)["url"] == "https://job-boards.greenhouse.io/globex/jobs/5"     # the board's link beats LinkedIn's
    assert s.job_keys(pid) == ["job:greenhouse:5"]                                       # no "linkedin:" job key
    s.close()


def test_no_parser_yet_means_nothing_parsed():
    r = rec(mail("jobs@indeed.com").decode(), meta={"site": "indeed"}, source_id="alert-inbox")
    assert REGISTRY["alert_inbox"].parse(r, src(id="alert-inbox", family="email")) == []
    assert alerts.sender_site("Indeed <alert@indeedemail.com>") == "indeed"
    assert alerts.sender_site("x@notlinkedin.com") is None
