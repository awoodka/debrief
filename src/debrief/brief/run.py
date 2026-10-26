"""`debrief brief run`: the whole brief for a day. Context, then the skill's Claude Code session,
then finalize.

The session is `claude -p "/debrief:brief <date>"` (config/brief.yml model, effort and limits), run
as a subprocess in the run folder, with ANTHROPIC_API_KEY removed so it uses the Max plan's token:
  - The skill loads from skill/ as a plugin (--plugin-dir). --setting-sources "" keeps everything
    else out: no user or project settings, commands or agents load. Never
    --bare (it forces API-key auth) and never --disable-slash-commands (it disables skills too).
  - Tools are Read, Write, Edit and WebFetch, with no Bash: read-only shell commands get past
    permission rules. Settings (--settings, generated here) allow Read of the skill folder, Write
    of the draft (as an Edit rule: Write rules never match) and WebFetch, and deny Read of
    triage_log.jsonl (triage's drops are never shown to the skill), the collect reports, the
    session's own logs and old drafts. In dontAsk mode anything else outside the folder is denied.
  - Two hooks (brief/hooks.py): the WebFetch cap and allowlist (PreToolUse), and the check on Stop.
  - The session's stream (stream-json) is kept in brief_session.jsonl. A summary of it is kept in
    brief_session.json (finalize puts it in the brief's meta): the model, turns, tokens, time,
    whether the plugin and WebFetch were there, and how many tool calls were denied.
Then finalize writes brief.json, brief.md and reported.json, or a scan-only brief if the session
failed or left no draft that passes the check.
"""
import json
import os
import subprocess
import sys

from ..config import REPO
from . import context, finalize
from .day import DRAFT, FETCH_LOG, SESSION, Day

SKILL_DIR = REPO / "skill"
TOOLS = "Read,Write,Edit,WebFetch"


def settings_for(day):
    hook = f"{sys.executable} -P -m debrief brief hook"
    return {
        "permissions": {
            "allow": [f"Read(/{SKILL_DIR}/**)", f"Edit(/{day.dir / DRAFT})", "WebFetch"],
            "deny": ["Read(./triage_log.jsonl)", "Read(./collect-*)", f"Read(./{FETCH_LOG})",
                     "Read(./brief_session.jsonl)", f"Read(./{SESSION})", f"Read(./{DRAFT}.*)"],
        },
        "hooks": {
            "PreToolUse": [{"matcher": "WebFetch", "hooks": [{"type": "command", "command": f"{hook} fetch"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": f"{hook} stop"}]}],
        },
    }


def argv_for(day, model=None, prompt=None):
    o = day.cfg.settings["brief"]
    argv = ["claude", "-p", prompt or f"/debrief:brief {day.date}", "--model", model or o["model"],
            "--plugin-dir", str(SKILL_DIR), "--setting-sources", "", "--settings", json.dumps(settings_for(day)),
            "--strict-mcp-config", "--tools", TOOLS, "--permission-mode", "dontAsk",
            "--output-format", "stream-json", "--verbose", "--no-session-persistence",
            "--max-turns", str(o["max_turns"]), "--max-budget-usd", str(o["max_budget_usd"])]
    return argv + (["--effort", o["effort"]] if o.get("effort") else [])


def run_claude(argv, cwd, env, timeout):
    """(exit code, stdout, stderr), like triage's: a timeout is 124, a missing CLI 127."""
    try:
        p = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        return 124, out, f"timed out after {timeout} s"
    except FileNotFoundError:
        return 127, "", "the claude CLI isn't installed here"
    return p.returncode, p.stdout, p.stderr


def summarize(code, stdout, stderr):
    """brief_session.json: what the session was, from its stream."""
    init, result = {}, {}
    for line in (stdout or "").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            init = ev
        elif ev.get("type") == "result":
            result = ev
    u = result.get("usage") or {}
    plugins = [p.get("name") if isinstance(p, dict) else p for p in init.get("plugins") or []]
    return {
        "exit": code, "result": result.get("subtype") or ("timeout" if code == 124 else "none"),
        "error": bool(result.get("is_error")) or code != 0,
        "session_id": result.get("session_id") or init.get("session_id"),
        "model": init.get("model"), "served_by": sorted((result.get("modelUsage") or {}).keys()),
        "plugin_loaded": "debrief" in plugins, "tools": sorted(init.get("tools") or []),
        "webfetch": "WebFetch" in (init.get("tools") or []),
        "turns": result.get("num_turns"), "duration_s": round((result.get("duration_ms") or 0) / 1000, 1),
        "input_tokens": int((u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
                            + (u.get("cache_read_input_tokens") or 0)),
        "output_tokens": int(u.get("output_tokens") or 0),
        "cost_usd_list_price": round(float(result.get("total_cost_usd") or 0), 4),
        "denied": len(result.get("permission_denials") or []),
        "stderr": (stderr or "").strip()[-500:] or None,
    }


def run(cfg, date, model=None, runner=run_claude, log=print):
    """Returns (brief.json document, session summary)."""
    day = Day(cfg, date)
    context.write(day)
    o = cfg.settings["brief"]
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env["DEBRIEF_BRIEF_DATE"] = date
    code, stdout, stderr = runner(argv_for(day, model), day.dir, env, o["timeout_s"])
    (day.dir / "brief_session.jsonl").write_text(stdout or "")
    summary = summarize(code, stdout, stderr)
    (day.dir / SESSION).write_text(json.dumps(summary, sort_keys=True, ensure_ascii=False, indent=1) + "\n")
    if not summary["plugin_loaded"]:
        log("brief: WARNING the debrief plugin didn't load in the session")
    if not summary["webfetch"]:
        log("brief: WARNING WebFetch wasn't available in the session")
    doc, _ = finalize.finalize(Day(cfg, date))
    return doc, summary
