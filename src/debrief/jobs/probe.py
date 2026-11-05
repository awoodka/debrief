"""`debrief jobs probe-sources` (jobs M0): check every job source once, politely, and measure the
company-board universe, so M1 is built on what actually works.

Sources (config/jobs/sources.yml): each endpoint is requested once through the polite client and read
the way M1's adapter will. For each source it records:
  - HTTP status, item count, a sample, and how full each field is;
  - the newest date and how many items are from the last 24 h;
  - whether a conditional request really returns a 304;
  - rate-limit headers and robots.txt.
A robots.txt-disallowed URL is never requested. A source whose key isn't in the environment is
"needs_key" and isn't requested either. The alert inbox is only logged into: messages are counted
per sender, and no bodies are read.

The universe: the company-board lists (family "universe") are merged per job-board system. Then a
fixed random sample of boards per system is listed once, which measures:
  - how many boards are alive;
  - latency, size and pages;
  - jobs per board, and new jobs in the last 24 h.
From that it extrapolates a full nightly poll: requests, minutes at each system's rate, megabytes,
and the new jobs a night would send toward scoring. Boards that the community lists link to but
the universe lacks are counted too ("discovery").

Writes the full record to <data>/jobs/probe/probe-<stamp>.json and the review tables to
docs/jobs/SOURCES.md. Nothing else is written.
"""
import csv
import imaplib
import io
import json
import os
import random
import re
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import feedparser

from .. import times, urls
from ..config import REPO
from ..http import Client
from ..probe import _path, _short
from . import boards

SEVERITY = ["ok", "partial", "empty", "needs_key", "blocked", "failed"]
URL_RE = re.compile(r"https?://[^\s)\"'<>|\]]+")
SEPARATOR = re.compile(r"^\|[\s:|-]*-[\s:|-]*$")   # | --- | :-- | --: |


@dataclass
class Attempt:
    url: str
    kind: str
    each: str | None = None
    status: str = "failed"          # ok | empty | failed | blocked | needs_key
    http_status: int = 0
    n_items: int = 0
    count: int | None = None        # a total the API reports
    sample_title: str = ""
    sample_date: str = ""
    newest: str | None = None       # newest item date (ISO UTC)
    last_24h: int | None = None     # items dated within 24 h of the probe
    fill: dict = field(default_factory=dict)       # field -> share of items with a value
    items_path: str | None = None   # where the items were found, when the spec didn't say
    keys: list = field(default_factory=list)
    content_type: str = ""
    bytes: int = 0
    etag: bool = False
    last_modified: bool = False
    conditional: str = ""           # "304" | "200 (validators ignored)" | ""
    rate_headers: dict = field(default_factory=dict)
    robots: bool | None = None
    extra: dict = field(default_factory=dict)
    error: str = ""
    elapsed_s: float = 0.0


@dataclass
class Result:
    id: str
    name: str
    family: str
    enabled: bool
    status: str
    endpoint: str = ""
    attempts: list = field(default_factory=list)
    summary: str = ""
    notes: str = ""
    terms: str = ""


# ------------------------------------------------------------------------------------- helpers

def _scrub(text, secrets):
    text = str(text or "")
    for s in secrets:
        if s and len(s) >= 4:
            text = text.replace(s, "<redacted>")
    return text


def _fill(items, fields):
    out = {}
    for name, path in (fields or {}).items():
        n = sum(1 for it in items if _path(it, path) not in (None, "", [], {}))
        out[name] = round(n / len(items), 2) if items else 0.0
    return out


_REL = re.compile(r"(today|just posted|yesterday|(\d+)\+?\s*(minute|hour|day|week|month)s?\s+ago)", re.I)


def age_days(value, now):
    """Age in days of a date in any form a job source uses: ISO strings, RFC 2822, epoch seconds or
    milliseconds, digit strings, and relative text ("Posted Today", "3 days ago", "2 hours ago").
    None if unreadable."""
    if value is None or isinstance(value, bool) or value == "":
        return None
    if isinstance(value, (int, float)):
        when = boards.when(value)
    elif isinstance(value, str) and value.strip().isdigit():
        return age_days(int(value.strip()), now)
    elif isinstance(value, str):
        m = _REL.search(value)
        if m and not re.match(r"^\d{4}-\d{2}-\d{2}", value.strip()):
            word = m.group(1).lower()
            if word in ("today", "just posted"):
                return 0.0
            if word == "yesterday":
                return 1.0
            n, unit = int(m.group(2)), m.group(3).lower()
            return {"minute": n / 1440, "hour": n / 24, "day": n, "week": 7 * n, "month": 30 * n}[unit]
        when = boards.when(value)
    else:
        return None
    if not when:
        return None
    dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
    return (now - dt).total_seconds() / 86400


def _dates(att, items, date_path, now):
    if not date_path:
        return
    ages = [age_days(_path(it, date_path), now) for it in items]
    ages = [a for a in ages if a is not None and a > -2]      # a little clock skew is fine
    if ages:
        att.last_24h = sum(1 for a in ages if a <= 1)
        newest = min(ages)
        att.newest = datetime.fromtimestamp(now.timestamp() - newest * 86400, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _find_list(data, prefix="", depth=0):
    """(path, list) of the longest list of objects within 3 levels, for sources whose shape isn't known."""
    best = (None, None)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        best = (prefix, data)
    if isinstance(data, dict) and depth < 3:
        for k, v in data.items():
            p, lst = _find_list(v, f"{prefix}.{k}" if prefix else k, depth + 1)
            if lst is not None and (best[1] is None or len(lst) > len(best[1])):
                best = (p, lst)
    return best


def md_rows(text):
    """Data rows of every markdown table in the text (header and separator rows dropped)."""
    lines = text.splitlines()
    rows = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not s.startswith("|") or SEPARATOR.match(s):
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if SEPARATOR.match(nxt):
            continue                                   # a header row
        rows.append(s)
    return rows


def _cell_text(cell):
    cell = re.sub(r"<[^>]+>", " ", cell)
    cell = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", cell)
    return " ".join(cell.replace("*", "").split())


def board_links(texts):
    """{board: n} for every job-board URL in the texts."""
    out = {}
    for t in texts:
        for u in URL_RE.findall(t or ""):
            b = boards.board_from_url(u)
            if b:
                out[b] = out.get(b, 0) + 1
    return out


# ------------------------------------------------------------------------------- one request

def probe_url(client, url, spec, *, method="GET", body=None, headers=None, params=None, each=None,
              secrets=(), now=None, conditional=True):
    now = now or datetime.now(timezone.utc)
    kind = spec.get("kind", "json")
    att = Attempt(url=url, kind=kind, each=each)
    att.robots = client.robots_allowed(url)
    if att.robots is not True:
        att.status = "blocked"
        att.error = ("robots.txt disallows this URL for our User-Agent" if att.robots is False
                     else "robots.txt unreachable (RFC 9309: treat as disallowed)")
        return att, None
    r = client.request(method, url, params=params, json_body=body, headers=headers)
    att.http_status, att.elapsed_s = r.status, r.elapsed_s
    att.content_type = r.headers.get("content-type", "")[:60]
    att.etag, att.last_modified = "etag" in r.headers, "last-modified" in r.headers
    att.rate_headers = r.rate_headers()
    att.bytes = len(r.content)
    if not r.ok:
        att.error = _scrub(r.error or f"HTTP {r.status}", secrets) + (" (refused)" if r.status in (401, 403) else "")
        return att, None
    try:
        if kind == "json":
            data = r.json()
            if spec.get("count"):
                c = _path(data, spec["count"])
                att.count = c if isinstance(c, int) else None
            if spec.get("single"):
                items = [data]
            elif spec.get("items") is not None:
                items = _path(data, spec["items"])
                if not isinstance(items, list):
                    raise ValueError(f"no list at {spec['items']!r} (top-level keys: "
                                     f"{sorted(data)[:20] if isinstance(data, dict) else type(data).__name__})")
            else:
                att.items_path, items = _find_list(data)
                if items is None:
                    raise ValueError(f"no list of objects found (top-level keys: "
                                     f"{sorted(data)[:20] if isinstance(data, dict) else type(data).__name__})")
            if isinstance(data, dict):
                att.keys = sorted(data)[:25]
            att.n_items = len(items)
            first = items[0] if items else None
            if first is not None:
                att.sample_title = _short(_path(first, spec["title"]) if spec.get("title") else
                                          (sorted(first)[:12] if isinstance(first, dict) else first), 110)
                att.sample_date = str(_path(first, spec.get("date")) or "") if spec.get("date") else ""
            objs = [it for it in items if isinstance(it, dict)]
            att.fill = _fill(objs, spec.get("fields"))
            _dates(att, objs, spec.get("date"), now)
        elif kind == "feed":
            d = feedparser.parse(r.content)
            if d.bozo and not d.entries:
                raise ValueError(f"not a feed ({type(d.bozo_exception).__name__})")
            att.n_items = len(d.entries)
            if d.entries:
                att.sample_title = _short(d.entries[0].get("title"), 110)
                att.sample_date = d.entries[0].get("published") or ""
            ages = [age_days(e.get("published") or e.get("updated"), now) for e in d.entries]
            ages = [a for a in ages if a is not None]
            if ages:
                att.last_24h = sum(1 for a in ages if a <= 1)
        elif kind == "md_table":
            rows = md_rows(r.text)
            att.n_items = len(rows)
            if rows:
                cells = [_cell_text(c) for c in rows[0].strip("|").split("|")]
                att.sample_title = _short(" · ".join(c for c in cells[:3] if c), 110)
            links = board_links(rows)
            att.extra = {"rows_with_board_link": sum(1 for row in rows if board_links([row])),
                         "boards_linked": len(links)}
        elif kind == "csv":
            rows = list(csv.DictReader(io.StringIO(r.text)))
            att.n_items = len(rows)
            att.sample_title = _short(rows[0] if rows else "", 110)
        else:
            raise ValueError(f"unknown probe kind {kind!r}")
    except Exception as e:  # noqa: BLE001 — a parse failure is a probe result, not a crash
        att.error = _scrub(f"{type(e).__name__}: {e}", secrets)[:240]
        return att, r.content
    att.status = "ok" if att.n_items else "empty"
    if conditional and method == "GET" and (att.etag or att.last_modified):
        r2 = client.request(method, url, params=params, headers=headers,
                            etag=r.headers.get("etag"), last_modified=r.headers.get("last-modified"))
        att.conditional = "304" if r2.status == 304 else f"{r2.status} (validators ignored)"
    return att, r.content


def probe_imap(spec, user, password, host="imap.gmail.com", port=993, connect=imaplib.IMAP4_SSL):
    """Log in, count messages per alert sender in INBOX (read-only), log out. No bodies are read."""
    att = Attempt(url=f"imaps://{host}:{port}", kind="imap")
    att.robots = True
    try:
        m = connect(host, port, timeout=30)
        try:
            m.login(user, password)
            typ, data = m.select("INBOX", readonly=True)
            total = int(data[0]) if typ == "OK" and data and data[0] else 0
            counts = {}
            for name, domain in (spec.get("senders") or {}).items():
                typ, found = m.search(None, "FROM", f'"{domain}"')
                counts[name] = len(found[0].split()) if typ == "OK" and found and found[0] else 0
        finally:
            try:
                m.logout()
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        att.error = _scrub(f"{type(e).__name__}: {e}", (user, password))[:240]
        return att
    att.extra = {"inbox_total": total, "by_sender": counts}
    att.n_items = sum(counts.values())
    att.status = "ok" if att.n_items else "empty"
    if att.status == "empty":
        att.error = "logged in, but no alert emails yet (set up the saved-search alerts)"
    return att


# ------------------------------------------------------------------------------- one source

def probe_source(client, src, env=os.environ, now=None, bodies=None, contact=""):
    res = Result(id=src.id, name=src.name, family=src.family, enabled=src.enabled, status="ok",
                 notes=src.notes, terms=src.terms)
    missing = src.missing_keys(env)
    if missing:
        res.status, res.summary = "needs_key", "set " + ", ".join(missing) + " in .env"
        res.endpoint = src.url or ""
        return res
    secrets = [env.get(n, "") for n in src.auth_env()]
    if (src.probe or {}).get("kind") == "imap":
        a = src.auth or {}
        user, password = (env[n].strip() for n in a["env"])
        att = probe_imap(src.probe, user, password)
        res.attempts, res.status, res.endpoint = [att], att.status, att.url
        return res
    headers, params = src.auth_parts(env)
    headers = {**{k: v.replace("{contact}", contact) for k, v in (src.headers or {}).items()}, **headers}
    conditional = src.family != "check" and (src.probe or {}).get("conditional", True)
    if src.url:
        groups = [[(src.probe or {}, u, e) for e, u in urls.expand(src.url, src.each)]]
    else:
        groups = [[({**(src.probe or {}), **(c.get("probe") or {})}, urls.fill(c["url"]), None)]
                  for c in src.candidates]
    for group in groups:
        atts = []
        for spec, u, e in group:
            att, content = probe_url(client, u, spec, method=src.method, body=src.body, headers=headers or None,
                                     params=params or None, each=e, secrets=secrets, now=now,
                                     conditional=conditional)
            atts.append(att)
            if bodies is not None and content is not None and src.family in ("list", "universe"):
                bodies.setdefault(src.id, {})[e] = content
        res.attempts.extend(atts)
        if any(a.status == "ok" for a in atts):
            break
    ok = [a for a in res.attempts if a.status == "ok"]
    if ok and src.each:
        res.status = "ok" if len(ok) == len(res.attempts) else "partial"
        res.summary = f"{len(ok)}/{len(res.attempts)} ok"
        res.endpoint = src.url
    elif ok:
        res.status, res.endpoint = "ok", ok[0].url
    else:
        res.status = min((a.status for a in res.attempts), key=SEVERITY.index, default="failed")
        res.endpoint = res.attempts[-1].url if res.attempts else ""
    return res


# ------------------------------------------------------------------------------- the universe

def universe_from_bodies(bodies, sources):
    """{system: set(board keys)} from the fetched universe datasets, plus {system: {source: n}}."""
    out = {s: set() for s in boards.SYSTEMS}
    per_source = {s: {} for s in boards.SYSTEMS}
    for src in sources:
        if src.family != "universe":
            continue
        for system, content in (bodies.get(src.id) or {}).items():
            if system not in out or content is None:
                continue
            text = content.decode("utf-8", "replace")
            rows = (list(csv.DictReader(io.StringIO(text))) if src.probe.get("kind") == "csv" else json.loads(text))
            got = boards.universe_rows(system, rows)
            per_source[system][src.id] = len(set(got))
            out[system].update(got)
    return out, per_source


def discovery(bodies, sources, universe):
    """What the community lists link to: active listings, how many point at a job-board system,
    and the boards the universe doesn't have yet."""
    linked, listings, on_board = {}, 0, 0
    for src in sources:
        if src.family != "list":
            continue
        for content in (bodies.get(src.id) or {}).values():
            text = content.decode("utf-8", "replace")
            if src.probe.get("kind") == "json":
                try:
                    items = json.loads(text)
                except ValueError:
                    continue
                for it in items if isinstance(items, list) else []:
                    if not isinstance(it, dict) or it.get("active") is False or it.get("is_visible") is False:
                        continue
                    listings += 1
                    b = boards.board_from_url(it.get("url") or "")
                    if b:
                        on_board += 1
                        linked[b] = linked.get(b, 0) + 1
            else:
                rows = md_rows(text)
                listings += len(rows)
                for row in rows:
                    found = board_links([row])
                    if found:
                        on_board += 1
                    for b, n in found.items():
                        linked[b] = linked.get(b, 0) + n
    by_system = {}
    for b in linked:
        system = boards.split(b)[0]
        d = by_system.setdefault(system, {"linked": 0, "not_in_universe": 0})
        d["linked"] += 1
        if b not in universe.get(system, set()):
            d["not_in_universe"] += 1
    return {"listings": listings, "listings_on_a_board_system": on_board,
            "share_on_a_board_system": round(on_board / listings, 3) if listings else 0.0,
            "by_system": by_system, "boards": sorted(linked)}


def _pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))]


def sample_boards(client, universe, n, seed, include, exclude, conditional_k, now=None, log=print):
    """List a fixed random sample of boards per system once; measure and extrapolate."""
    now = now or datetime.now(timezone.utc)
    inc, exc = re.compile(include, re.I), re.compile(exclude, re.I)
    rng = random.Random(seed)
    out = {}
    for system in boards.SYSTEMS:
        keys = sorted(universe.get(system) or ())
        picked = rng.sample(keys, min(n, len(keys)))
        rows, cond_tried, cond_304 = [], 0, 0
        log(f"  universe {system}: {len(keys)} boards, sampling {len(picked)}", flush=True)
        for b in picked:
            method, url, body = boards.list_request(b)
            row = {"board": b, "status": "", "http": 0}
            allowed = client.robots_allowed(url)
            if allowed is not True:
                row["status"] = "blocked" if allowed is False else "robots_unreachable"
                rows.append(row)
                continue
            r = client.request(method, url, json_body=body)
            row.update(http=r.status, elapsed_s=r.elapsed_s, bytes=len(r.content))
            if r.status in (404, 410) or (system == "workday" and r.status in (400, 422)):
                row["status"] = "dead"
            elif not r.ok:
                row["status"], row["error"] = "error", r.error
            else:
                try:
                    jobs, total = boards.parse_list(system, r.json())
                except Exception as e:  # noqa: BLE001
                    row["status"], row["error"] = "unreadable", f"{type(e).__name__}: {e}"[:160]
                    rows.append(row)
                    continue
                new = [j for j in jobs if (j.get("age_days") is not None and j["age_days"] <= 1)
                       or (j.get("published") and age_days(j["published"], now) is not None
                           and age_days(j["published"], now) <= 1)]
                row.update(status="ok", jobs=total if total is not None else len(jobs),
                           pages=boards.pages_needed(system, total), new_24h=len(new),
                           new_24h_rough=sum(1 for j in new if inc.search(j.get("title") or "")
                                             and not exc.search(j.get("title") or "")),
                           validators="etag" in r.headers or "last-modified" in r.headers)
                if row["validators"] and cond_tried < conditional_k and method == "GET":
                    cond_tried += 1
                    r2 = client.request(method, url, etag=r.headers.get("etag"),
                                        last_modified=r.headers.get("last-modified"))
                    cond_304 += r2.status == 304
            rows.append(row)
        out[system] = summarize(system, len(keys), rows, client, cond_tried, cond_304)
        out[system]["rows"] = rows
    return out


def summarize(system, universe_n, rows, client, cond_tried=0, cond_304=0):
    ok = [r for r in rows if r["status"] == "ok"]
    n = len(rows)
    alive = len(ok) / n if n else 0.0
    _, url, _ = boards.list_request(boards.key(system, "x|wd1|x" if system == "workday" else "x"))
    interval = client.bucket(url)[1]
    med = statistics.median([r["elapsed_s"] for r in ok]) if ok else None
    pages = statistics.mean([r["pages"] for r in ok]) if ok else 1
    est_alive = round(universe_n * alive)
    requests = round(est_alive * pages)
    # Workday and Recruitee have a host per tenant, so robots.txt is one more request per board.
    robots = est_alive if system in ("workday", "recruitee") else 1
    per_req = max(interval, med or interval)
    s = {"universe": universe_n, "sampled": n,
         "ok": len(ok), "dead": sum(r["status"] == "dead" for r in rows),
         "errors": sum(r["status"] in ("error", "unreadable") for r in rows),
         "blocked": sum(r["status"] in ("blocked", "robots_unreachable") for r in rows),
         "alive_share": round(alive, 2),
         "median_s": round(med, 2) if med is not None else None,
         "p90_s": _pct([r["elapsed_s"] for r in ok], 0.9),
         "median_kb": round(statistics.median([r["bytes"] for r in ok]) / 1024, 1) if ok else None,
         "jobs_per_board": round(statistics.mean([r["jobs"] for r in ok]), 1) if ok else None,
         "pages_per_board": round(pages, 2),
         "new_24h_per_board": round(statistics.mean([r["new_24h"] for r in ok]), 2) if ok else None,
         "validators_share": round(sum(bool(r.get("validators")) for r in ok) / len(ok), 2) if ok else None,
         "conditional": f"{cond_304}/{cond_tried} returned 304" if cond_tried else "",
         "interval_s": interval,
         "est_alive_boards": est_alive,
         "est_requests": requests + robots,
         "est_minutes": round((requests + robots) * per_req / 60, 1),
         "est_mb": round(est_alive * pages * (statistics.median([r["bytes"] for r in ok]) if ok else 0) / 1e6, 1),
         "est_new_24h": round(est_alive * (statistics.mean([r["new_24h"] for r in ok]) if ok else 0)),
         "est_new_24h_rough": round(est_alive * (statistics.mean([r["new_24h_rough"] for r in ok]) if ok else 0))}
    if system == "workday":
        s["note"] = ("Workday's dates are relative, so \"new\" here is Posted Today or Yesterday (up to 48 h), "
                     "counted on each board's first page only: a rough figure either way")
    return s


# ------------------------------------------------------------------------------------- the run

def latest(cfg):
    """The newest saved probe record, or None."""
    files = sorted((cfg.data_dir / "probe").glob("probe-*.json"))
    return json.loads(files[-1].read_text()) if files else None


def run(cfg, only=None, skip=(), env=os.environ, universe=True, sample=None, log=print):
    """Probe every source (or only `only`). Sources in `skip` aren't requested: their result is
    carried over from the newest saved probe, marked `carried_from`, for sources whose terms limit
    how often we may call (Jobicy: once an hour) or that spend a quota (SerpAPI searches)."""
    now = datetime.now(timezone.utc)
    client = Client(cfg.user_agent, cfg.settings["http"])
    results, bodies = [], {}
    prev = {r["id"]: r for r in ((latest(cfg) or {}).get("sources") or [])} if skip else {}
    for src in cfg.sources:
        if only and src.id not in only:
            continue
        if src.id in skip:
            old = prev.get(src.id)
            if old:
                results.append(Result(**{**{k: v for k, v in old.items() if k in Result.__dataclass_fields__},
                                         "summary": (old.get("summary") or "") + f" (carried from the probe at {(latest(cfg) or {}).get('generated_at')})"}))
                log(f"[{src.id}] skipped; carried over: {old['status']}")
            else:
                log(f"[{src.id}] skipped; no earlier probe to carry over")
            continue
        log(f"[{src.id}]", end=" ", flush=True)
        if not src.enabled:
            results.append(Result(id=src.id, name=src.name, family=src.family, enabled=False,
                                  status="disabled", notes=src.notes, terms=src.terms))
            log("disabled (not requested)")
            continue
        if src.family in ("watchlist", "detail"):
            log("works from the profile and stored postings (not probed)")
            continue
        r = probe_source(client, src, env=env, now=now, bodies=bodies, contact=cfg.contact)
        log(r.status + (f" ({r.summary})" if r.summary else ""))
        results.append(r)
    report = {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "user_agent": cfg.user_agent,
              "sources": [asdict(r) for r in results]}
    if universe and not only:
        uni, per_source = universe_from_bodies(bodies, cfg.sources)
        p = cfg.settings["probe"]
        report["universe"] = {
            "by_source": per_source,
            "discovery": discovery(bodies, cfg.sources, uni),
            "sample": sample_boards(client, uni, sample or p["universe_sample"], p["seed"],
                                    p["rough_title"]["include"], p["rough_title"]["exclude"],
                                    p["conditional_check"], now=now, log=log),
        }
        report["universe"]["discovery"]["not_in_universe"] = [
            b for b in report["universe"]["discovery"].pop("boards")
            if b not in uni.get(boards.split(b)[0], set())]
    return report


def write(report, cfg, md_path=None):
    stamp = report["generated_at"].replace("-", "").replace(":", "")
    out_dir = cfg.data_dir / "probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    jpath = out_dir / f"probe-{stamp}.json"
    jpath.write_text(json.dumps(report, indent=1, sort_keys=True))
    md = md_path or (REPO / "docs" / "jobs" / "SOURCES.md")
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(render_markdown(report, jpath))
    return jpath, md


# ------------------------------------------------------------------------------------ the report

FAMILY_TITLES = [("list", "Community lists"), ("board", "Job-board systems (one example board each)"),
                 ("bigtech", "Big companies' own careers APIs"), ("api", "Job APIs"),
                 ("discussion", "Hacker News"), ("email", "The alert inbox"),
                 ("check", "The miss-check sample (Google's job results)"),
                 ("universe", "Board universe lists")]


def _cell(s):
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")


def _caching(atts):
    bits = set()
    for a in atts:
        if a.get("etag"):
            bits.add("ETag")
        if a.get("last_modified"):
            bits.add("Last-Modified")
        if a.get("conditional"):
            bits.add("conditional: " + a["conditional"])
        for k, v in (a.get("rate_headers") or {}).items():
            bits.add(f"{k}: {v}")
        if a.get("robots") is False:
            bits.add("robots: disallowed")
    return ", ".join(sorted(bits))


def _source_row(r):
    atts = r["attempts"]
    ok = [a for a in atts if a["status"] == "ok"]
    shown = ok[0] if ok else (atts[-1] if atts else None)
    items = fresh = sample = fill = ""
    if shown:
        items = str(sum(a["n_items"] for a in ok) if ok and len(ok) > 1 else shown["n_items"])
        if shown.get("count") is not None:
            items += f" (total {shown['count']})"
        if shown.get("newest") or shown.get("last_24h") is not None:
            fresh = f"{shown.get('newest') or '?'} · {shown.get('last_24h')} in 24 h"
        sample = shown["sample_title"] + (f" · {str(shown['sample_date'])[:25]}" if shown.get("sample_date") else "")
        fill = ", ".join(f"{k} {int(v * 100)}%" for k, v in (shown.get("fill") or {}).items())
        if shown.get("items_path"):
            fill = (fill + "; " if fill else "") + f"items at `{shown['items_path']}`"
        if shown.get("extra"):
            fill = (fill + "; " if fill else "") + ", ".join(f"{k}: {v}" for k, v in shown["extra"].items())
    status = r["status"] + (f" {r['summary']}" if r.get("summary") else "")
    notes = " · ".join(x for x in (r.get("notes"), r.get("terms")) if x)
    bad = [a for a in atts if a["status"] != "ok"]
    if bad:
        notes = (notes + " · " if notes else "") + "not ok: " + "; ".join(
            f"{a.get('each') or a['url']}: {a['error'] or a['status']}" for a in bad[:4])
    return (f"| {_cell(r['name'])} | {_cell(status)} | {_cell(r.get('endpoint'))} | {_cell(items)} | "
            f"{_cell(fresh)} | {_cell(sample)} | {_cell(fill)} | {_cell(_caching(atts))} | {_cell(notes)} |")


def render_markdown(report, jpath):
    srcs = report["sources"]
    counts = {}
    for r in srcs:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    L = ["# Jobs source probe (M0)", "",
         f"Generated {report['generated_at']} by `debrief jobs probe-sources`, with User-Agent "
         f"`{report['user_agent']}`. Each endpoint was requested once (plus one conditional request "
         f"where it sent validators), through the rate buckets in config/jobs/settings.yml. "
         f"Full record: `{jpath}`.", "",
         "Status counts: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())), ""]
    for fam, title in FAMILY_TITLES:
        rows = [r for r in srcs if r["family"] == fam]
        if not rows:
            continue
        L += [f"## {title}", "",
              "| Source | Status | Endpoint | Items | Newest · last 24 h | Sample | Fields filled | Caching / limits | Notes |",
              "|---|---|---|---|---|---|---|---|---|"]
        L += [_source_row(r) for r in rows]
        L.append("")
    need = [r for r in srcs if r["status"] == "needs_key"]
    if need:
        L += ["## Waiting on keys", ""] + [f"- **{r['name']}**: {r['summary']}" for r in need] + [""]
    uni = report.get("universe")
    if uni:
        L += ["## The board universe", "",
              "Boards per system, from each list (after merging both lists, duplicates removed):", "",
              "| System | " + " | ".join(sorted({s for d in uni["by_source"].values() for s in d})) + " |",
              "|---|" + "---|" * len({s for d in uni["by_source"].values() for s in d})]
        names = sorted({s for d in uni["by_source"].values() for s in d})
        for system, d in uni["by_source"].items():
            L.append(f"| {system} | " + " | ".join(str(d.get(n, "")) for n in names) + " |")
        L += ["", "### A full nightly poll, extrapolated from a sample", "",
              "Each system's sample was listed once. Minutes assume one request at a time per system, at the "
              "larger of the bucket's interval and the median response time. Robots.txt is one extra "
              "request per tenant host on Workday and Recruitee.", "",
              "| System | Universe | Sampled | Alive | Dead | Errors | Blocked | Median s | p90 s | Median KB | Jobs/board | Pages/board | New 24 h/board | 304s | Est. requests | Est. min | Est. MB | Est. new jobs/night | …rough title match |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        tot = {"req": 0, "min": [], "mb": 0, "new": 0, "rough": 0}
        for system, s in uni["sample"].items():
            L.append(f"| {system} | {s['universe']} | {s['sampled']} | {s['ok']} ({int(s['alive_share'] * 100)}%) | "
                     f"{s['dead']} | {s['errors']} | {s['blocked']} | {s['median_s']} | {s['p90_s']} | "
                     f"{s['median_kb']} | {s['jobs_per_board']} | {s['pages_per_board']} | {s['new_24h_per_board']} | "
                     f"{s['conditional'] or '—'} | {s['est_requests']} | {s['est_minutes']} | {s['est_mb']} | "
                     f"{s['est_new_24h']} | {s['est_new_24h_rough']} |")
            tot["req"] += s["est_requests"]
            tot["min"].append(s["est_minutes"])
            tot["mb"] += s["est_mb"]
            tot["new"] += s["est_new_24h"]
            tot["rough"] += s["est_new_24h_rough"]
        L += ["", f"Totals: about {tot['req']} requests and {round(tot['mb'])} MB a night; "
                  f"{round(sum(tot['min']))} min one system after another, or {round(max(tot['min'] or [0]))} min "
                  f"with the systems in parallel (one request at a time each). About {tot['new']} new jobs a "
                  f"night, {tot['rough']} of them past the rough title filter (before location and level).", ""]
        notes = [f"- {system}: {s['note']}" for system, s in uni["sample"].items() if s.get("note")]
        if notes:
            L += notes + [""]
        d = uni["discovery"]
        L += ["### What the community lists link to", "",
              f"{d['listings']} active listings across the lists. {d['listings_on_a_board_system']} of them "
              f"({int(d['share_on_a_board_system'] * 100)}%) link to a known job-board system; the rest are "
              "company-hosted pages (big tech, custom sites) or the list's own short links.", "",
              "| System | Boards linked | Not in the universe yet |", "|---|---|---|"]
        for system, x in sorted(d["by_system"].items()):
            L.append(f"| {system} | {x['linked']} | {x['not_in_universe']} |")
        L.append("")
    return "\n".join(L) + "\n"
