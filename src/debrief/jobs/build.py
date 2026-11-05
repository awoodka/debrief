"""From one source's raw records to items and postings, inside the caller's transaction.

Everything here reads times from the records (fetched_at), never the clock, so a replay of the
same raw files rebuilds the same rows.

  - A listing (item) seen for the first time joins a posting by job key, then by fuzzy key
    (items.py), then by the early-career second chance (early_match), or starts a new posting. An inactive listing never seen before is skipped: it's a
    job that closed before we started watching. A board-finder listing (JobItem.target) that no
    job key places joins the posting it was found for.
  - A posting closes when every listing of it is inactive, and reopens when one is active again.
    Board-finder listings (FOLLOWERS) don't count: the finder reads a board once, not every night,
    so the sources that found the job still decide when it's gone.
  - A posting's link is chosen from all its listings (link_rank): the company's own listing of the
    job first, an aggregator's page only when nothing better exists.
  - "full" snapshot sources: a listing missing from a complete response for its scope (every
    request ok, none a 304) is made inactive. If a complete response parsed to nothing while the
    scope had more than a few active listings, nothing is closed and a warning says why (a format
    change looks exactly like that).
  - Every item that names a board, and every directory row an adapter gives, goes in the board
    directory.
  - A description an adapter gives (texts(): the describe step) is stored for the posting with that
    job key, and its facts (employment type, pay) fill the posting's data where it has none.
  - A job its own board says is closed (gone(): the describe step) gets data["gone"] = {at,
    status, url}, which the filters' "gone" stage cuts; the board showing it open again clears it.
"""
from collections import Counter, defaultdict
from datetime import datetime
from urllib.parse import urlsplit

from ..store import loads, raw
from .boards import job_from_url
from .items import (company_norm, content_hash, core_title, early_career, fuzzy_key, job_key, locations_overlap,
                    posting_id_for)

MASS_CLOSE_GUARD = 5


# Hosts whose pages are an aggregator's copy of a job or a short link, never the company's own
# listing. Includes the alert-email senders (M3).
AGGREGATOR_HOSTS = ("jobright.ai", "go.vansh.nyc", "simplify.jobs", "himalayas.app", "remoteok.com",
                    "weworkremotely.com", "jobicy.com", "themuse.com", "adzuna.com", "news.ycombinator.com",
                    "linkedin.com", "indeed.com", "glassdoor.com", "wellfound.com", "joinhandshake.com",
                    "dice.com", "builtin.com", "ziprecruiter.com")
WATCHLIST = "watchlist"
FINDER = "board-finder"
FOLLOWERS = (FINDER,)      # sources whose listings never keep a posting open by themselves


def _aggregator(url):
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in AGGREGATOR_HOSTS)


def link_rank(source_id, url, active, published_at=None):
    """Sort key for a posting's candidate links, best first:
      0  the company's board, read through its own API (the watchlist)
      1  a job-board page for the job (Greenhouse, Lever, Ashby, Workday, ...)
      2  another page on the company's own site (amazon.jobs, careers.microsoft.com, ...)
      3  an aggregator's page or a short link
    A listing that's still open beats one that's gone. Within a tier the newest listing wins: a
    company that posts the same role under several ids, as TikTok does, gets its latest one."""
    if not url:
        tier = 4
    elif source_id == WATCHLIST:
        tier = 0
    elif job_from_url(url):
        tier = 1
    elif not _aggregator(url):
        tier = 2
    else:
        tier = 3
    newest_first = -datetime.fromisoformat(published_at.replace("Z", "+00:00")).timestamp() if published_at else 0.0
    return (not active, tier, newest_first)


def best_link(store, pid):
    rows = store.posting_links(pid)
    if not rows:
        return None
    best = min(rows, key=lambda r: (*link_rank(r["source_id"], r["url"], r["active"], r["published_at"]),
                                    r["first_seen_at"], r["source_id"], r["item_key"]))
    return best["url"] or None


def early_match(store, source, it, jk, early_sources):
    """The second chance for a listing that matched nothing by job key or exact title: the one
    open early-career posting at the same company with the same core title (class-year and
    new-grad words ignored, so vanshb03's "New Grad 2027: AI Engineer" finds Simplify's "AI Engineer
    New Grad") and an overlapping place. Both sides must be early-career: the title says so, or a
    new-grad list (early_sources) lists it. Another source must list the posting, its job id must not
    conflict, and exactly one posting may qualify (a conflicting one still counts); otherwise None."""
    core = core_title(it.title)
    if not core or not (source.id in early_sources or early_career(it.title)):
        return None
    found = []
    for row in store.open_postings_at(company_norm(it.company)):
        if core_title(row["title"]) != core or not locations_overlap(loads(row["locations"], []), it.locations):
            continue
        sources = store.posting_sources(row["posting_id"])
        if sources <= {source.id} or not (early_career(row["title"]) or sources & early_sources):
            continue
        found.append(row["posting_id"])
    if len(found) != 1:
        return None                                    # several similar roles: don't guess
    other = store.job_keys(found[0])
    return None if jk is not None and other and jk not in other else found[0]


def resolve(store, source, it, at, early_sources=frozenset()):
    """(posting_id, "new" | "merged") for a listing seen for the first time."""
    jk, fk = job_key(it), fuzzy_key(it.company, it.title)
    if jk:
        pid = store.find_key(jk)
        if pid:
            return pid, "merged"
    if it.target and store.posting(it.target) is not None:      # the board finder's match
        if jk:
            store.add_key(jk, it.target)
        return it.target, "merged"
    if fk:
        pid = store.find_key(fk)
        if pid:
            p = store.posting(pid)
            other = store.job_keys(pid)
            conflict = jk is not None and other and jk not in other     # same title, a different job
            if not conflict and locations_overlap(p["locations"], it.locations):
                if jk:
                    store.add_key(jk, pid)
                return pid, "merged"
    pid = early_match(store, source, it, jk, early_sources)
    if pid:
        if jk:
            store.add_key(jk, pid)
        return pid, "merged"
    first = jk or fk or f"src:{source.id}:{it.item_key}"
    pid, n = posting_id_for(first), 1
    while store.posting(pid) is not None:
        pid, n = posting_id_for(f"{first}#{n}"), n + 1
    store.save_posting({"posting_id": pid, "company": it.company, "title": it.title, "locations": list(it.locations),
                        "url": it.url or None, "board": it.board, "published_at": it.published_at,
                        "first_seen_at": at, "last_seen_at": at, "closed_at": None, "data": {}})
    for k in (jk, fk):
        if k:
            store.add_key(k, pid)
    return pid, "new"


def merge_into(store, pid, it, at, source_id):
    """Fold one listing's facts into its posting."""
    p = store.posting(pid)
    locs = list(p["locations"])
    for loc in it.locations:
        if loc not in locs:
            locs.append(loc)
    p["locations"] = locs
    if it.published_at and (not p["published_at"] or it.published_at < p["published_at"]):
        p["published_at"] = it.published_at
    p["board"] = p["board"] or it.board
    if it.active:
        p["last_seen_at"] = max(p["last_seen_at"], at)
    d = p["data"]
    if it.pay and not d.get("pay"):
        d["pay"] = it.pay
    if it.remote is not None and d.get("remote") is None:
        d["remote"] = it.remote
    for k, v in (it.extra or {}).items():
        if k not in ("links", "via", "hn", "thread") and v not in (None, "", []):
            d.setdefault(k, v)
    store.save_posting(p)
    if it.text:
        store.save_text(pid, source_id, it.text, at)


def refresh_posting(store, pid, at):
    """Its link and open/closed state, from its listings as they stand now."""
    p = store.posting(pid)
    link = best_link(store, pid)
    active = store.any_active(pid, ignore=FOLLOWERS)
    change = None
    if active and p["closed_at"]:
        p["closed_at"], change = None, "reopened"
    elif not active and not p["closed_at"]:
        p["closed_at"], change = at, "closed"
    if change or link != p["url"]:
        p["url"] = link
        store.save_posting(p)
    return change


def apply(store, source, adapter, records, refs, warn=None, early_sources=frozenset()):
    """Store one source's records. Returns counts. early_sources: the ids of the new-grad lists,
    whose every listing is early-career (for early_match)."""
    counts = Counter()
    seen = defaultdict(set)
    for rec, ref in zip(records, refs):
        at = rec["fetched_at"]
        for key, text, facts in adapter.texts(rec, source):
            pid = store.find_key(f"job:{key}")
            if not pid:
                continue
            if text:
                store.save_text(pid, source.id, text, at)
                counts["texts"] += 1
            p = store.posting(pid)
            new = {k: v for k, v in (facts or {}).items() if v not in (None, "", []) and k not in p["data"]}
            if new:
                p["data"].update(new)
                store.save_posting(p)
        gone = getattr(adapter, "gone", None)
        for key, closed in (gone(rec, source) if gone else {}).items():
            pid = store.find_key(f"job:{key}")
            if not pid:
                continue
            p = store.posting(pid)
            if closed and not p["data"].get("gone"):
                p["data"]["gone"] = {"at": at, "status": rec.get("status"), "url": rec.get("url")}
                store.save_posting(p)
                counts["gone_on_board"] += 1
            elif not closed and p["data"].get("gone"):
                del p["data"]["gone"]
                store.save_posting(p)
        for board, name in adapter.directory(rec, source):
            store.save_directory(board, name, company_norm(name), source.id, at)
            counts["directory"] += 1
        for it in adapter.parse(rec, source):
            counts["items"] += 1
            seen[it.scope].add(it.item_key)
            data, h = it.as_dict(), content_hash(it)
            row = store.item(source.id, it.item_key)
            if row is None:
                if not it.active:
                    counts["skipped_inactive"] += 1
                    continue
                pid, how = resolve(store, source, it, at, early_sources)
                store.insert_item(source.id, it.item_key, it.scope, pid, at, h, ref, data, active=True)
                counts["new"] += 1
                counts["postings_new" if how == "new" else "merged"] += 1
            else:
                pid = row["posting_id"]
                if h != row["content_hash"]:
                    counts["changed"] += 1
                store.update_item(source.id, it.item_key, at, it.active, h, ref if h != row["content_hash"] else row["raw_ref"],
                                  data)
            merge_into(store, pid, it, at, source.id)
            changed = refresh_posting(store, pid, at)
            if changed:
                counts[changed] += 1
            if it.board and it.company:
                store.save_directory(it.board, it.company, company_norm(it.company), source.id, at)
    if adapter.snapshot == "full":
        by_scope = defaultdict(list)
        for rec in records:
            by_scope[rec.get("each") or ""].append(rec)
        for scope, recs in by_scope.items():
            complete = all(raw.ok(r) and r.get("status") != 304 for r in recs)
            if not complete:
                continue
            gone = [k for k in store.active_in_scope(source.id, scope) if k not in seen[scope]]
            if not seen[scope] and len(gone) > MASS_CLOSE_GUARD:
                if warn:
                    warn(f"{scope or source.id}: a complete response parsed to no jobs while {len(gone)} were open; "
                         "nothing closed (a format change?)")
                counts["close_guard"] += 1
                continue
            at = max(r["fetched_at"] for r in recs)
            for k in gone:
                store.deactivate(source.id, k)
                pid = store.item(source.id, k)["posting_id"]
                counts["gone"] += 1
                if refresh_posting(store, pid, at) == "closed":
                    counts["closed"] += 1
    return counts
