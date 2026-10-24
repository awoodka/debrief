"""The skill's two Claude Code hooks, run as `python -P -m debrief brief hook fetch|stop` with the
hook's JSON on stdin (the wrapper puts them in the session's --settings; -P keeps the run folder,
the session's cwd, off sys.path). The day comes from DEBRIEF_BRIEF_DATE, else from the cwd's name.

fetch (PreToolUse, WebFetch): the web budget. Every call is appended to runs/<date>/brief_fetches.jsonl
under a lock: {session_id, url, ts, allowed, reason}. It's denied once the session has had
brief.fetch_cap allowed fetches, or when the URL's host isn't one of brief.allow_hosts or a host of
today's stories (their links, code, threads, full text, coverage, and the links inside their stored
pages). So a link planted in a comment can't be fetched. A denial tells the model why, and to write
from the stored data instead. The log is the record finalize reports.

stop (Stop): the check. While the draft is missing or fails it, stopping is blocked (exit 2) and the
errors go back to the model, which keeps fixing. Claude Code caps how often a Stop hook can block.
"""
import fcntl
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..collect import state_path
from ..store import Store
from . import check as checks
from .day import FETCH_LOG, Day, DayError, host

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def day_of(event):
    d = os.environ.get("DEBRIEF_BRIEF_DATE") or Path(event.get("cwd") or os.getcwd()).name
    return d if _DATE.match(d) else None


def allowed_hosts(day):
    o = day.cfg.settings["brief"]
    urls = []
    store = Store(state_path(day.cfg)) if state_path(day.cfg).exists() else None
    for line in day.index:
        d = day.detail(line["id"])
        urls += d["artifact_urls"] + d["code_urls"] + [m["url"] for m in d["members"] if m.get("url")]
        urls += [t["url"] for t in d["discussion"]["threads"] if t.get("url")]
        urls += [f["url"] for f in d["full_text"] if f.get("url")] + [c["url"] for c in d["covered_by"] if c.get("url")]
        if store:
            for sid in [line["id"], *line.get("merged", [])]:
                for p in store.pages(sid):
                    urls += p["links"]
    if store:
        store.close()
    return {host(u) for u in urls if host(u)} | {h.lower() for h in o["allow_hosts"]}


def fetch(cfg, event, now=None):
    """(allowed, reason) for one WebFetch call, logged either way."""
    url = (event.get("tool_input") or {}).get("url") or ""
    session = event.get("session_id") or "?"
    date = day_of(event)
    if not date:
        return False, "the brief's date is unknown, so its fetch budget can't be checked"
    try:
        day = Day(cfg, date)
    except DayError as e:
        return False, str(e)
    cap = cfg.settings["brief"]["fetch_cap"]
    with open(day.dir / FETCH_LOG, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        used = sum(1 for x in (json.loads(ln) for ln in f.read().splitlines() if ln.strip())
                   if x["session_id"] == session and x["allowed"])
        if used >= cap:
            ok, reason = False, f"the fetch budget ({cap}) is used up; write from the stored data"
        elif host(url) not in allowed_hosts(day):
            ok, reason = False, (f"{host(url) or url!r} isn't a host of today's stories or brief.allow_hosts; "
                                 "fetch only pages the stored data links")
        else:
            ok, reason = True, f"fetch {used + 1} of {cap}"
        ts = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
        f.write(json.dumps({"session_id": session, "url": url, "ts": ts, "allowed": ok, "reason": reason},
                           sort_keys=True) + "\n")
    return ok, reason


def stop(cfg, event):
    """(exit code, message): 0 lets the session end; 2 sends the message back to the model."""
    date = day_of(event)
    if not date:
        return 0, ""
    try:
        day = Day(cfg, date)
    except DayError:
        return 0, ""
    r = checks.check(day)
    if r["ok"]:
        return 0, ""
    return 2, ("brief.draft.json doesn't pass the check yet:\n" + "\n".join(f"- {e}" for e in r["errors"])
               + "\nFix the draft and finish again.")


def main(kind, cfg, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr):
    event = json.loads(stdin.read() or "{}")
    if kind == "fetch":
        ok, reason = fetch(cfg, event)
        if not ok:
            stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                            "permissionDecision": "deny",
                                                            "permissionDecisionReason": reason}}) + "\n")
        return 0
    code, message = stop(cfg, event)
    if message:
        stderr.write(message + "\n")
    return code
