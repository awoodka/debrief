"""probe-sources (Milestone 0): hit every source once, politely, and report what works.

For each source in sources.yml the probe fetches its endpoint(s) through the polite client and
parses the response the way its adapter will (feed, page links, JSON, GraphQL). It records the
HTTP status, item count, a sample item, rate-limit headers, conditional-request support,
robots.txt, and any RSS a page advertises. Sources with `candidates` are tried in order until
one works.

Writes the full record to <data>/probe/probe-<stamp>.json and the review table to
docs/v2/SOURCES.md. Probing never writes to the stores M1 builds.
"""
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

import feedparser
from . import extract, urls
from .config import REPO
from .http import Client

ITUNES = "https://itunes.apple.com/search?entity=podcast&limit=5&term="


@dataclass
class Attempt:
    url: str
    kind: str
    status: str = "failed"          # ok | empty | failed | blocked | needs_key
    http_status: int = 0
    final_url: str = ""
    n_items: int = 0
    count: int | None = None        # a total the API reports (e.g. HN nbHits), when configured
    sample_title: str = ""
    sample_date: str = ""
    content_type: str = ""
    etag: bool = False
    last_modified: bool = False
    rate_headers: dict = field(default_factory=dict)
    robots: bool | None = None
    feeds: list = field(default_factory=list)   # RSS/Atom feeds the page advertises
    keys: list = field(default_factory=list)    # top-level JSON keys, to help locate items
    error: str = ""
    elapsed_s: float = 0.0
    each: str | None = None


@dataclass
class Result:
    id: str
    name: str
    roles: list
    tier: int
    enabled: bool
    status: str
    endpoint: str = ""
    attempts: list = field(default_factory=list)
    notes: str = ""
    summary: str = ""


def _path(obj, dotted):
    """obj['a']['b'] for 'a.b'; '' returns obj; missing returns None."""
    if dotted in (None, ""):
        return obj
    for part in dotted.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        else:
            return None
    return obj


def _short(s, n=90):
    s = re.sub(r"<[^>]+>", " ", str(s or ""))
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _parse_feed(att, r):
    d = feedparser.parse(r.content)
    if d.bozo and not d.entries:
        raise ValueError(f"not a feed ({type(d.bozo_exception).__name__})")
    att.n_items = len(d.entries)
    if d.entries:
        e = d.entries[0]
        att.sample_title = _short(e.get("title"))
        att.sample_date = e.get("published") or e.get("updated") or ""


def _parse_links(att, r, spec):
    base = r.final_url or att.url
    att.feeds = extract.advertised_feeds(r.content, base)
    found = extract.links(r.content, base, spec.get("link_pattern"), spec.get("selector"))
    att.n_items = len(found)
    if found:
        att.sample_title = _short(found[0][0], 110)


def _parse_json(att, data, spec):
    if spec.get("count"):
        c = _path(data, spec["count"])
        att.count = c if isinstance(c, int) else None
    if spec.get("single"):
        att.n_items = 1 if data else 0
        att.sample_title = _short(_path(data, spec.get("title")))
        if isinstance(data, dict):
            att.keys = sorted(data)[:25]
        return
    if "items" in spec:
        items = _path(data, spec["items"])
    else:  # no path given: take the first list we find, and say where it was
        items = data if isinstance(data, list) else None
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, list):
                    items, spec = v, {**spec, "items": k}
                    break
    if isinstance(data, dict):
        att.keys = sorted(data)[:25]
    if not isinstance(items, list):
        raise ValueError(f"no list at {spec.get('items')!r} (top-level keys: {att.keys})")
    att.n_items = len(items)
    if items:
        first = items[0]
        att.sample_title = _short(_path(first, spec.get("title")) if spec.get("title") else
                                  (sorted(first)[:8] if isinstance(first, dict) else first))
        att.sample_date = str(_path(first, spec.get("date")) or "") if spec.get("date") else ""


def probe_url(client, url, spec, headers=None, each=None):
    kind = spec.get("kind", "feed")
    att = Attempt(url=url, kind=kind, each=each)
    att.robots = client.robots_allowed(url)
    if kind == "links" and att.robots is False:
        att.status, att.error = "blocked", "robots.txt disallows this page for our User-Agent"
        return att
    if kind == "graphql":
        r = client.post(url, json_body={"query": spec["query"]}, headers=headers)
    else:
        r = client.get(url, headers=headers)
    att.http_status, att.final_url, att.elapsed_s = r.status, r.final_url, r.elapsed_s
    att.content_type = r.headers.get("content-type", "")[:60]
    att.etag, att.last_modified = "etag" in r.headers, "last-modified" in r.headers
    att.rate_headers = r.rate_headers()
    if kind == "status":
        expect = spec.get("expect", [200])
        if r.status not in expect:
            att.error = r.error or f"HTTP {r.status}"
            return att
        needle = spec.get("contains")
        if needle and r.status == 200 and needle.lower() not in r.text.lower():
            att.status, att.error = "empty", f"200 but no {needle!r} in the body"
            return att
        att.status, att.n_items, att.sample_title = "ok", 1, f"HTTP {r.status}"
        return att
    if r.status in (401, 403):
        att.status = "needs_key" if not headers and spec.get("_auth_env") else "failed"
        att.error = r.error + (" (key not set)" if att.status == "needs_key" else " (refused)")
        return att
    if not r.ok:
        att.error = r.error or f"HTTP {r.status}"
        return att
    try:
        if kind == "feed":
            _parse_feed(att, r)
        elif kind == "links":
            _parse_links(att, r, spec)
        elif kind in ("json", "graphql"):
            data = r.json()
            if kind == "graphql" and isinstance(data, dict) and data.get("errors"):
                raise ValueError(f"GraphQL errors: {_short(data['errors'], 160)}")
            _parse_json(att, data, spec)
        else:
            raise ValueError(f"unknown probe kind {kind!r}")
    except Exception as e:  # noqa: BLE001 — a parse failure is a probe result, not a crash
        att.error = f"{type(e).__name__}: {e}"[:200]
        return att
    att.status = "ok" if att.n_items else "empty"
    if att.status == "empty" and att.feeds:
        att.error = "no matching links; page advertises a feed"
    return att


def _probe_itunes(client, term):
    """Find a podcast's feed through the iTunes search API, then probe the feed itself."""
    r = client.get(ITUNES + quote_plus(term))
    att = Attempt(url=ITUNES + quote_plus(term), kind="itunes", each=term, http_status=r.status)
    if not r.ok:
        att.error = r.error
        return att
    results = r.json().get("results", [])
    match = next((x for x in results if term.lower() in (x.get("collectionName") or "").lower()), None)
    if not match or not match.get("feedUrl"):
        att.error = f"no podcast named like {term!r} with a feedUrl ({len(results)} results)"
        return att
    feed = probe_url(client, match["feedUrl"], {"kind": "feed"}, each=term)
    feed.kind = "itunes→feed"
    return feed


SEVERITY = ["ok", "empty", "needs_key", "blocked", "failed"]


def _targets(source):
    """[(spec, url, each)] groups to try. Plain url/each → one group; candidates → one per candidate."""
    base = dict(source.probe or {})
    if source.auth.get("env"):
        base["_auth_env"] = source.auth["env"]
    if source.url:
        return [[(base, u, e) for e, u in urls.expand(source.url, source.each)]]
    groups = []
    for c in source.candidates:
        if isinstance(c, str):
            groups.append([(base, urls.fill(c), None)])
        else:
            spec = {**base, **(c.get("probe") or {})}
            groups.append([(spec, urls.fill(c["url"]), None)])
    return groups


def probe_source(client, source, env=os.environ):
    res = Result(id=source.id, name=source.name, roles=list(source.roles), tier=source.tier,
                 enabled=source.enabled, status="not_probed", notes=source.notes)
    if (source.probe or {}).get("kind") == "itunes":
        res.attempts = [_probe_itunes(client, t) for t in source.each]
    else:
        groups = _targets(source)
        if not groups:
            res.summary = "nothing to probe (no url)"
            return res
        headers = source.auth_headers(env)
        for group in groups:
            atts = [probe_url(client, u, spec, headers or None, each=e) for spec, u, e in group]
            res.attempts.extend(atts)
            if any(a.status == "ok" for a in atts):
                break
    ok = [a for a in res.attempts if a.status == "ok"]
    if ok and source.each:
        # One attempt per list entry: ok only if every entry worked.
        res.status = "ok" if len(ok) == len(res.attempts) else "partial"
        res.summary = f"{len(ok)}/{len(res.attempts)} ok"
        res.endpoint = source.url or "; ".join(a.url for a in ok)   # itunes: the feeds it found
    elif ok:
        # Candidates stop at the first that works; that one is the endpoint.
        res.status, res.endpoint = "ok", ok[0].url
    else:
        res.status = min((a.status for a in res.attempts), key=SEVERITY.index, default="failed")
        res.endpoint = res.attempts[-1].url if res.attempts else ""
    return res


def run(cfg, only=None, include_disabled=False, log=print):
    """Probe every source. Disabled sources are listed but not requested again, unless named in
    `only` or include_disabled is set: their notes already say why they're off."""
    client = Client(cfg.user_agent, cfg.settings["http"])
    results = []
    for s in cfg.sources:
        if only and s.id not in only:
            continue
        log(f"[{s.id}]", end=" ", flush=True)
        if not s.enabled and not (include_disabled or only):
            r = Result(id=s.id, name=s.name, roles=list(s.roles), tier=s.tier, enabled=False,
                       status="disabled", notes=s.notes)
            log("disabled (not requested)")
            results.append(r)
            continue
        r = probe_source(client, s)
        log(r.status + (f" ({r.summary})" if r.summary else ""))
        results.append(r)
    return results


# ------------------------------------------------------------------------------------- reporting

def _cell(s):
    return str(s or "").replace("|", "\\|").replace("\n", " ")


def _limits(atts):
    bits = set()
    for a in atts:
        for k, v in a.rate_headers.items():
            bits.add(f"{k}: {v}")
        if a.etag:
            bits.add("ETag")
        if a.last_modified:
            bits.add("Last-Modified")
        if a.robots is False:
            bits.add("robots: disallowed")
        if a.robots is None:
            bits.add("robots: unreachable")
    return ", ".join(sorted(bits))


def _detail(r):
    ok = [a for a in r.attempts if a.status == "ok"]
    shown = ok[0] if ok else (r.attempts[-1] if r.attempts else None)
    if not shown:
        return "", ""
    items = f"{shown.n_items}" + (f" (total {shown.count})" if shown.count is not None else "")
    sample = shown.sample_title + (f" · {shown.sample_date[:25]}" if shown.sample_date else "")
    return items, sample


def render_markdown(results, meta):
    by_status = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    L = ["# Debrief v2 source probe (Milestone 0)", "",
         f"Generated {meta['generated_at']} by `debrief probe-sources`, with User-Agent "
         f"`{meta['user_agent']}`. Each endpoint was requested once, through the same rate buckets "
         "the collector uses. Full record: `" + meta["json_path"] + "`.", "",
         "Status counts: " + ", ".join(f"{k} {v}" for k, v in sorted(by_status.items())), "",
         "| Source | Roles · tier | Status | Endpoint | Items | Sample | Limits / caching / robots | Notes |",
         "|---|---|---|---|---|---|---|---|"]
    for r in results:
        items, sample = _detail(r)
        status = r.status + ("" if r.enabled or r.status == "disabled" else " (disabled)") \
            + (f" {r.summary}" if r.summary else "")
        failed = [a for a in r.attempts if a.status != "ok"]
        notes = r.notes
        if failed:
            errs = "; ".join(f"{a.each or a.url}: {a.error or a.status}" for a in failed[:4])
            notes = (notes + " · " if notes else "") + "not ok: " + errs
        feeds = sorted({f for a in r.attempts for f in a.feeds})
        if feeds:
            notes += " · advertised feeds: " + ", ".join(feeds[:3])
        L.append("| " + " | ".join(_cell(x) for x in [
            f"{r.name} (`{r.id}`)", f"{'+'.join(r.roles)} · {r.tier}", status, r.endpoint,
            items, sample, _limits(r.attempts), notes]) + " |")
    return "\n".join(L) + "\n"


def _load(path):
    d = json.loads(Path(path).read_text())
    return [Result(**{**r, "attempts": [Attempt(**a) for a in r["attempts"]]}) for r in d["results"]]


def merge_with_latest(results, cfg):
    """Re-probing a few sources: carry every other source over from the latest full probe, in
    sources.yml order, so the table stays complete."""
    previous = sorted((cfg.data_dir / "probe").glob("probe-*.json"))
    if not previous:
        return results
    fresh = {r.id: r for r in results}
    old = {r.id: r for r in _load(previous[-1])}
    merged = []
    for s in cfg.sources:
        r = fresh.get(s.id) or old.get(s.id)
        if r is None:
            continue
        if s.id not in fresh:   # carried over: config may have changed since that probe
            r.notes, r.enabled = s.notes, s.enabled
            if not s.enabled:
                r.status, r.summary = "disabled", ""
        merged.append(r)
    return merged


def write(results, cfg, md_path=None):
    stamp = datetime.now().astimezone()
    out_dir = cfg.data_dir / "probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    jpath = out_dir / f"probe-{stamp:%Y%m%d-%H%M%S}.json"
    meta = {"generated_at": stamp.isoformat(timespec="seconds"), "user_agent": cfg.user_agent,
            "json_path": f"$DEBRIEF_DATA_DIR/probe/{jpath.name}"}
    jpath.write_text(json.dumps({"meta": meta, "results": [asdict(r) for r in results]}, indent=2))
    md_path = Path(md_path or REPO / "docs" / "v2" / "SOURCES.md")
    md_path.write_text(render_markdown(results, meta))
    return jpath, md_path
