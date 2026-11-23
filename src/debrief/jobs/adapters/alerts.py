"""The job-alert inbox (docs/jobs/PLAN.md, "Revision 6"): saved-search alert emails from LinkedIn,
Indeed, Handshake, Wellfound, Glassdoor, Dice and Built In, read from a Gmail account used only for
them (IMAP, an app password in JOBS_IMAP_USER / JOBS_IMAP_PASSWORD).

fetch() never changes the mailbox: INBOX is opened read-only and messages are fetched with
BODY.PEEK[], so nothing is marked read, moved or deleted. Messages from the last `days` days are
fetched once each: one whose URL (imaps://<host>/INBOX;UIDVALIDITY=v;UID=u, no username) is already
in the fetch history is skipped. Each message is kept raw like an HTTP response, so parse() is pure
and a replay rebuilds the same items.

parse() picks a parser by the sender's domain. A site's own listing id (LinkedIn's, Indeed's) isn't
the employer's job id, so items carry no job key and copies merge by company, title and place (Alex,
2026-11-05).
"""
import email
import email.policy
import imaplib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

from ...http import Fetched
from ...store import raw
from ..items import JobItem, fuzzy_key
from . import Adapter

# Sender domain -> site. A Gmail forwarding filter (Handshake's alerts go to the school address)
# keeps the original From, so forwarded alerts are recognized the same way.
SENDERS = {"linkedin.com": "linkedin", "indeed.com": "indeed", "indeedemail.com": "indeed",
           "glassdoor.com": "glassdoor", "wellfound.com": "wellfound", "angel.co": "wellfound",
           "joinhandshake.com": "handshake", "dice.com": "dice", "builtin.com": "builtin"}

_UIDVALIDITY = re.compile(rb"UIDVALIDITY\s+(\d+)")
ISO = "%Y-%m-%dT%H:%M:%SZ"


def sender_site(address):
    """The site an alert email comes from, by its sender's domain, or None."""
    domain = (parseaddr(address or "")[1].rsplit("@", 1)[-1] or "").lower()
    for d, site in SENDERS.items():
        if domain == d or domain.endswith("." + d):
            return site
    return None


def sent_at(msg):
    try:
        return parsedate_to_datetime(str(msg.get("Date"))).astimezone(timezone.utc).strftime(ISO)
    except (TypeError, ValueError):
        return None


def headers_of(raw_bytes):
    """(site, sender domain, subject, date ISO) from a raw message's headers."""
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)
    sender = str(msg.get("From") or "")
    domain = (parseaddr(sender)[1].rsplit("@", 1)[-1] or "").lower()
    return sender_site(sender), domain, str(msg.get("Subject") or "")[:200], sent_at(msg)


class _Runs(HTMLParser):
    HIDDEN = ("head", "style", "script")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.runs, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.HIDDEN:
            self.hidden += 1
        elif tag == "a":
            self.runs.append(("a", dict(attrs).get("href") or ""))

    def handle_endtag(self, tag):
        if tag in self.HIDDEN:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        text = " ".join(data.split())
        if text and not self.hidden:
            self.runs.append(("t", text))


def html_runs(msg):
    part = msg.get_body(preferencelist=("html",))
    html = part.get_content() if part else ""
    parser = _Runs()
    parser.feed(html)
    parser.close()
    return parser.runs, html


def cards(runs, job_of):
    out, job = {}, None
    for kind, value in runs:
        if kind == "a":
            job = job_of(value)
            if job is not None:
                out.setdefault(job, [])
        elif job is not None:
            out[job].append(value)
    return out


@dataclass(frozen=True)
class Digest:
    marker: str
    job: Callable[[str], str | None]
    item: Callable[[str, list, str], JobItem | None]


def read(digest, msg, record):
    rs, html = html_runs(msg)
    if digest.marker not in html:
        return []
    sent = sent_at(msg) or record["fetched_at"]
    out = [it for job, lines in cards(rs, digest.job).items() if (it := digest.item(job, lines, sent))]
    if not out:
        raise ValueError(f"a {(record.get('meta') or {}).get('site')} job-alert digest with no jobs we can read")
    return out


INDEED_LINK = re.compile(r"^https://www\.indeed\.com/(?:rc|pagead)/clk/")
INDEED_JK = re.compile(r"[?&]jk=([0-9a-f]{16})(?![0-9a-f])")
INDEED_JK_ONLY = re.compile(r"[0-9a-f]{16}")
AGE = re.compile(r"^(?:just posted|today|(\d+)\+? (day|hour)s? ago)$", re.I)
STEP = {"day": timedelta(days=1), "hour": timedelta(hours=1)}
PAY = re.compile(r"^(?:from |up to )?\$\d", re.I)


def _indeed_job(href):
    if not INDEED_LINK.match(href):
        return None
    m = INDEED_JK.search(href)
    return m.group(1) if m else href


def posted_from_age(sent, age):
    m = AGE.match(age or "")
    if not m:
        return None
    n, unit = m.groups()
    at = datetime.fromisoformat(sent.replace("Z", "+00:00"))
    return (at - int(n) * STEP[unit] if unit else at).strftime(ISO)


def _indeed_item(job, lines, sent):
    if len(lines) < 3:
        return None
    title, company, place, *rest = lines
    jk = job if INDEED_JK_ONLY.fullmatch(job) else None
    fk = fuzzy_key(company, title)
    if not (jk or fk):
        return None
    pay = next((x for x in rest if PAY.match(x)), None)
    return JobItem(item_key=f"indeed:{jk or fk[3:]}", title=title, company=company, locations=(place,),
                   url=f"https://www.indeed.com/viewjob?jk={jk}" if jk else "",
                   published_at=posted_from_age(sent, next((x for x in rest if AGE.match(x)), None)),
                   extra={"via": "Indeed", **({"pay_hint": pay} if pay else {})})


LINKEDIN_LINK = re.compile(r"^https://www\.linkedin\.com/comm/jobs/view/(\d+)")
WORKPLACE = re.compile(r"\s*\((On-site|Hybrid|Remote)\)$")
REMOTE = {"On-site": False, "Hybrid": False, "Remote": True}


def _linkedin_job(href):
    m = LINKEDIN_LINK.match(href)
    return m.group(1) if m else None


def _linkedin_item(job, lines, sent):
    if len(lines) < 2:
        return None
    title, where = lines[0], lines[1]
    company, sep, place = where.rpartition(" · ")
    if not sep:
        company, place = place, ""
    m = WORKPLACE.search(place)
    place = place[:m.start()] if m else place
    return JobItem(item_key=f"linkedin:{job}", title=title, company=company, locations=(place,) if place else (),
                   url=f"https://www.linkedin.com/jobs/view/{job}/", remote=REMOTE[m.group(1)] if m else None,
                   extra={"via": "LinkedIn"})


PARSERS = {"indeed": Digest("utm_campaign=job_alerts", _indeed_job, _indeed_item),
           "linkedin": Digest("email_job_alert_digest", _linkedin_job, _linkedin_item)}


class AlertInbox(Adapter):
    name = "alert_inbox"
    quiet_ok = True                       # a night with no new alerts is normal
    connect = staticmethod(imaplib.IMAP4_SSL)

    def fetch(self, ctx, source):
        user, password = (ctx.env[n].strip() for n in source.auth["env"])
        opts = source.options or {}
        host, port = opts.get("host", "imap.gmail.com"), int(opts.get("port", 993))
        days, cap = int(opts.get("days", 4)), int(opts.get("max_messages", 300))
        secrets = (user, password)
        seen = {r[0] for r in ctx.store.q("SELECT DISTINCT url FROM fetches WHERE source_id=?", (source.id,))}
        base = f"imaps://{host}/INBOX"
        try:
            conn = self.connect(host, port, timeout=60)
        except Exception as e:  # noqa: BLE001
            ctx.record(source, Fetched(url=base, final_url=base, status=0, error=f"connect: {type(e).__name__}: {e}"),
                       secrets=secrets)
            return
        try:
            try:
                conn.login(user, password)
            except Exception as e:  # noqa: BLE001
                ctx.record(source, Fetched(url=base, final_url=base, status=0, error=f"login: {type(e).__name__}: {e}"[:300]),
                           secrets=secrets)
                return
            conn.select("INBOX", readonly=True)
            typ, data = conn.status("INBOX", "(UIDVALIDITY)")
            m = _UIDVALIDITY.search(data[0] if typ == "OK" and data and data[0] else b"")
            validity = m.group(1).decode() if m else "0"
            since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
            typ, found = conn.uid("SEARCH", None, "SINCE", since)
            uids = found[0].split() if typ == "OK" and found and found[0] else []
            n = 0
            for uid in uids:
                url = f"{base};UIDVALIDITY={validity};UID={uid.decode()}"
                if url in seen:
                    continue
                if n >= cap:
                    ctx.warn(f"stopped at {cap} messages; the rest wait for the next run")
                    break
                typ, parts = conn.uid("FETCH", uid, "(BODY.PEEK[])")
                body = next((p[1] for p in parts or () if isinstance(p, tuple) and len(p) > 1), b"") if typ == "OK" else b""
                if not body:
                    ctx.record(source, Fetched(url=url, final_url=url, status=0, error="fetch returned no message"),
                               secrets=secrets)
                    continue
                site, domain, subject, when = headers_of(body)
                ctx.record(source, Fetched(url=url, final_url=url, status=200, headers={"content-type": "message/rfc822"},
                                           content=body),
                           each=site or "other", secrets=secrets,
                           meta={"site": site, "from_domain": domain, "subject": subject, "date": when})
                n += 1
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass

    def parse(self, record, source):
        if not raw.ok(record):
            return []
        site = (record.get("meta") or {}).get("site")
        digest = PARSERS.get(site)
        if not digest:
            return []
        return read(digest, email.message_from_bytes(raw.body(record), policy=email.policy.default), record)


ADAPTERS = [AlertInbox()]
