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
2026-11-05); an email that links straight to a job board gets that board's id from the link. The
parsers are written from real sample emails (PLAN.md "Revision 6", step 4): until a sender has
one, its emails are kept and counted, not parsed.
"""
import email
import email.policy
import imaplib
import re
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime

from ...http import Fetched
from ...store import raw
from . import Adapter

# Sender domain -> site. A Gmail forwarding filter (Handshake's alerts go to the school address)
# keeps the original From, so forwarded alerts are recognized the same way.
SENDERS = {"linkedin.com": "linkedin", "indeed.com": "indeed", "indeedemail.com": "indeed",
           "glassdoor.com": "glassdoor", "wellfound.com": "wellfound", "angel.co": "wellfound",
           "joinhandshake.com": "handshake", "dice.com": "dice", "builtin.com": "builtin"}

# site -> parse(message, record, source) -> [JobItem]. Filled in from real samples.
PARSERS = {}

_UIDVALIDITY = re.compile(rb"UIDVALIDITY\s+(\d+)")


def sender_site(address):
    """The site an alert email comes from, by its sender's domain, or None."""
    domain = (parseaddr(address or "")[1].rsplit("@", 1)[-1] or "").lower()
    for d, site in SENDERS.items():
        if domain == d or domain.endswith("." + d):
            return site
    return None


def headers_of(raw_bytes):
    """(site, sender domain, subject, date ISO) from a raw message's headers."""
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)
    sender = str(msg.get("From") or "")
    try:
        when = parsedate_to_datetime(str(msg.get("Date"))).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        when = None
    domain = (parseaddr(sender)[1].rsplit("@", 1)[-1] or "").lower()
    return sender_site(sender), domain, str(msg.get("Subject") or "")[:200], when


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
        parser = PARSERS.get(site)
        if not parser:
            return []
        msg = email.message_from_bytes(raw.body(record), policy=email.policy.default)
        return parser(msg, record, source)


ADAPTERS = [AlertInbox()]
