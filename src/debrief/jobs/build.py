"""From one source's raw records to items and postings, inside the caller's transaction.

Everything here reads times from the records (fetched_at), never the clock, so a replay of the
same raw files rebuilds the same rows.

  - A listing (item) seen for the first time joins a posting by job key, then by fuzzy key
    (items.py), or starts a new posting. An inactive listing never seen before is skipped: it's a
    job that closed before we started watching.
  - A posting closes when every listing of it is inactive, and reopens when one is active again.
  - "full" snapshot sources: a listing missing from a complete response for its scope (every
    request ok, none a 304) is made inactive. If a complete response parsed to nothing while the
    scope had more than a few active listings, nothing is closed and a warning says why (a format
    change looks exactly like that).
  - Every item that names a board, and every directory row an adapter gives, goes in the board
    directory.
"""
from collections import Counter, defaultdict

from ..store import raw
from .items import company_norm, content_hash, fuzzy_key, job_key, locations_overlap, posting_id_for

MASS_CLOSE_GUARD = 5


def _better_url(old, new):
    """Prefer a link to the job board's own page over an aggregator's link."""
    from .boards import job_from_url
    if not old:
        return new
    if new and job_from_url(new) and not job_from_url(old):
        return new
    return old


def resolve(store, source, it, at):
    """(posting_id, "new" | "merged") for a listing seen for the first time."""
    jk, fk = job_key(it), fuzzy_key(it.company, it.title)
    if jk:
        pid = store.find_key(jk)
        if pid:
            return pid, "merged"
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
    p["url"] = _better_url(p["url"], it.url or None)
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


def refresh_closed(store, pid, at):
    p = store.posting(pid)
    active = store.any_active(pid)
    if active and p["closed_at"]:
        p["closed_at"] = None
        store.save_posting(p)
        return "reopened"
    if not active and not p["closed_at"]:
        p["closed_at"] = at
        store.save_posting(p)
        return "closed"
    return None


def apply(store, source, adapter, records, refs, warn=None):
    """Store one source's records. Returns counts."""
    counts = Counter()
    seen = defaultdict(set)
    for rec, ref in zip(records, refs):
        at = rec["fetched_at"]
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
                pid, how = resolve(store, source, it, at)
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
            changed = refresh_closed(store, pid, at)
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
                if refresh_closed(store, pid, at) == "closed":
                    counts["closed"] += 1
    return counts
