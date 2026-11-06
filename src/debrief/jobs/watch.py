"""The watchlist: the profile's companies, whose boards are read in full every night.

`watch add NAME` finds the company's board with no AI:
  1. a careers URL (--url) is read by boards.board_from_url;
  2. otherwise the name is looked up in the board directory (the two company-board lists, plus
     every board the community lists and watchlist link to). Every board filed under exactly that
     name is listed once, live, and the one with the most open jobs wins; a board that's dead or
     refused by robots.txt never does.
A company with no board found is still added (board null) with a note to set a LinkedIn alert
for it. Every change saves a new profile version.
"""
import json

from ..http import Client
from . import boards, profile as profiles
from .items import company_norm
from .store import JobsStore, state_path


class WatchError(Exception):
    pass


def check_board(client, board):
    """(open jobs, None) if the board answers, else (None, why)."""
    method, url, body = boards.list_request(board)
    allowed = client.robots_allowed(url)
    if allowed is not True:
        return None, "robots.txt disallows it" if allowed is False else "robots.txt unreachable"
    r = client.request(method, url, json_body=body)
    if not r.ok:
        return None, r.error or f"HTTP {r.status}"
    try:
        jobs, total = boards.parse_list(boards.split(board)[0], r.json())
    except Exception as e:  # noqa: BLE001
        return None, f"unreadable answer ({type(e).__name__})"
    return (total if total is not None else len(jobs)), None


def find_board(cfg, name, url=None, client=None, store=None):
    """(board or None, open jobs, [notes])."""
    client = client or Client(cfg.user_agent, cfg.settings["http"])
    notes = []
    if url:
        board = boards.board_from_url(url)
        if not board:
            return None, None, [f"{url} isn't on a job-board system we can read"]
        n, why = check_board(client, board)
        return (board, n, notes) if why is None else (None, None, [f"{board}: {why}"])
    store = store or JobsStore(state_path(cfg))
    rows = store.directory_by_name(company_norm(name))
    candidates = sorted({r["board"] for r in rows})
    if not candidates:
        norm = company_norm(name)
        rows = store.directory_like(norm[:20]) or (store.directory_like(norm.split()[0]) if norm.split() and len(norm.split()[0]) >= 3 else [])
        similar = [f"{r['name']} ({r['board']})" for r in rows]
        notes.append("not in the board directory" + (f"; similar names: {', '.join(similar)}" if similar else ""))
        return None, None, notes
    best = (None, -1)
    for b in candidates:
        n, why = check_board(client, b)
        notes.append(f"{b}: {why or f'{n} open jobs'}")
        if why is None and n > best[1]:
            best = (b, n)
    return (best[0], best[1], notes) if best[0] else (None, None, notes)


def _latest_without(cfg, name):
    _, prof = profiles.latest(cfg)
    if prof is None:
        raise WatchError("no profile saved yet")
    if any(c["name"].strip().lower() == name.strip().lower() for c in prof["companies"]):
        raise WatchError(f"{name} is already on the watchlist")
    return prof


def add(cfg, name, url=None, note=None, client=None, store=None):
    _latest_without(cfg, name)
    board, n, notes = find_board(cfg, name, url=url, client=client, store=store)
    prof = _latest_without(cfg, name)    # the lookup makes network requests, and a save during it must survive
    entry = {"name": name, "board": board}
    if board:
        entry["note"] = note or f"board found {('from ' + url) if url else 'in the directory'}; {n} open jobs when added"
    else:
        entry["note"] = note or "no public board found: set a LinkedIn alert for this company"
    prof = json.loads(json.dumps(prof))
    prof["companies"].append(entry)
    new_version, _ = profiles.save(cfg, prof, note=f"watch add {name}")
    return entry, notes, new_version


def remove(cfg, name):
    version, prof = profiles.latest(cfg)
    if prof is None:
        raise WatchError("no profile saved yet")
    keep = [c for c in prof["companies"] if c["name"].strip().lower() != name.strip().lower()]
    if len(keep) == len(prof["companies"]):
        raise WatchError(f"{name} isn't on the watchlist")
    prof = {**prof, "companies": keep}
    new_version, _ = profiles.save(cfg, prof, note=f"watch remove {name}")
    return new_version


def listing(cfg, store=None):
    """[(company, board, open postings collected from that board)]."""
    _, prof = profiles.latest(cfg)
    store = store or JobsStore(state_path(cfg))
    out = []
    for c in (prof or {}).get("companies") or []:
        n = None
        if c.get("board"):
            n = store.one("""SELECT COUNT(DISTINCT i.posting_id) FROM items i JOIN postings p USING (posting_id)
                             WHERE i.source_id='watchlist' AND i.scope=? AND i.active=1 AND p.closed_at IS NULL""",
                          (c["board"],))[0]
        out.append((c["name"], c.get("board"), n))
    return out
