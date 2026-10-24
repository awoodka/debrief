"""The brief's deterministic parts (src/debrief/brief/): context, the draft's check, finalize, the
skill's hooks, and the ledger rule for days before the first brief. On 2026-10-24's triage output,
frozen in tests/fixtures/brief/."""
import io
import json
import re
import shutil

import pytest
from conftest import FIXTURES

from debrief import config
from debrief.brief import context, finalize, hooks
from debrief.brief.check import check, norm
from debrief.brief.day import Day
from debrief.triage import ledger

DATE = "2026-10-24"
UPDATE, MEMBER = "url:01440c58175255e5", "url:128c778d038c2534"   # PHP2Shell, and a story merged into it
PAPER, LW, THREAD = "arxiv:2610.28559", "lesswrong:ie7bSp5ZDwYkCQxuv", "hn:50216791"


def setup_day(tmp_path, date=DATE):
    shutil.copytree(FIXTURES / "brief" / DATE, tmp_path / "data" / "runs" / date)
    cfg = config.load(env={"DEBRIEF_DATA_DIR": str(tmp_path / "data")})
    return cfg, Day(cfg, date)


def comment(day, story, author):
    d = day.detail(story)
    return next(c["text"] for t in d["discussion"]["threads"] for c in t["top_comments"] if c["author"] == author)


def good_draft(day):
    text = comment(day, UPDATE, "fwlr")
    words = text.split()
    quote = " ".join(words[:8]) + " … " + " ".join(words[12:18])     # an elision, pieces in order
    return {
        "date": DATE,
        "headline": "PHP2Shell attackers had four days; flow language models catch up with autoregressive ones",
        "what_happened": [
            {"text": "Attackers exploited PHP2Shell for four days before the fix shipped.", "stories": [UPDATE]},
            {"text": "An 8B flow language model matched autoregressive quality.", "stories": [PAPER]},
            {"text": "A LessWrong post tested gradient routing on new hack types.", "stories": [LW]},
        ],
        "lead": "The day's story is the gap between finding a bug and fixing it.\n\nIt shows up twice.",
        "sections": [
            {"heading": "PHP2Shell was exploited for four days before the patch",
             "body": "The disclosure window looked long.\n\nCommenters asked why.",
             "stories": [UPDATE, THREAD],
             "split": {"for": "Coordinated disclosure worked.", "against": "Four days is a long time.",
                       "read": "Both, and the second matters more."},
             "quotes": [{"story": UPDATE, "text": quote}]},
            {"heading": "Flow language models are getting close", "body": "Large Flow Language Models trains cheaply.",
             "stories": [PAPER, MEMBER]},
        ],
        "updates": [{"story": UPDATE, "text": "New: the exploitation started four days before the patch."}],
        "notes": ["A quiet Saturday."],
        "fetch_notes": {"https://arxiv.org/abs/2610.28559": "to check the benchmark table"},
    }


def write_draft(day, draft):
    day.draft_path.write_text(json.dumps(draft))


# ---------------------------------------------------------------------------------------- check

def test_a_good_draft_passes(tmp_path):
    cfg, day = setup_day(tmp_path)
    write_draft(day, good_draft(day))
    r = check(day)
    assert r["ok"], r["errors"]
    assert r["words"] > 0 and any("under the aim" in w for w in r["warnings"])          # a quiet day is short


@pytest.mark.parametrize("change, expect", [
    (lambda d: d["sections"][1]["stories"].append("arxiv:9999.99999"), "isn't a story in today's index"),
    (lambda d: d["sections"][0]["quotes"].append({"story": UPDATE, "text": "a sentence nobody wrote"}),
     "not found word for word"),
    (lambda d: d.update(lead="See https://example.com for more."), "lead: has a URL"),
    (lambda d: d["sections"][1].update(body=f"As {PAPER} shows."), "has a story id"),
    (lambda d: d.update(what_happened=d["what_happened"][:2]), "what_happened has 2 items; it needs 3 to 6"),
    (lambda d: d["sections"][0].update(heading="x" * 130), "heading is 130 characters"),
    (lambda d: d["sections"][1].update(body="word " * 4000), "cut it to under 3750"),
    (lambda d: d.update(date="2026-10-23"), "this is the brief for 2026-10-24"),
    (lambda d: d.pop("lead"), "schema: (top): 'lead' is a required property"),
])
def test_each_rule_fails_the_draft(tmp_path, change, expect):
    cfg, day = setup_day(tmp_path)
    draft = good_draft(day)
    change(draft)
    write_draft(day, draft)
    r = check(day)
    assert not r["ok"] and any(expect in e for e in r["errors"]), r["errors"]


def test_a_missing_or_broken_draft_says_what_to_do(tmp_path):
    cfg, day = setup_day(tmp_path)
    assert "no brief.draft.json yet" in check(day)["errors"][0]
    day.draft_path.write_text("{not json")
    assert "isn't valid JSON" in check(day)["errors"][0]


def test_quotes_survive_curly_quotes_dashes_and_emphasis():
    assert norm("It’s “fine” — **really**") == norm("It's \"fine\" - really")


# ------------------------------------------------------------------------------------- finalize

def test_finalize_resolves_ids_builds_the_scan_and_writes_the_ledger(tmp_path):
    cfg, day = setup_day(tmp_path)
    write_draft(day, good_draft(day))
    doc, r = finalize.finalize(day)
    assert doc["mode"] == "written" and r["ok"]
    assert [x["id"] for x in doc["scan"]] == [x["id"] for x in day.index]                # every kept story, in order
    covered = {x["id"] for x in doc["scan"] if x["covered"]}
    assert covered == {UPDATE, PAPER, LW, THREAD}
    assert doc["sections"][1]["stories"] == [PAPER, UPDATE]                               # the member became its story
    q = doc["sections"][0]["quotes"][0]
    assert (q["kind"], q["author"], q["source"]) == ("comment", "fwlr", "hn") and q["url"].startswith("https://news.ycombinator.com/")
    assert doc["updates"][0]["prev_reported"] == "2026-10-23"
    assert doc["stories"][PAPER]["url"] == "https://arxiv.org/abs/2610.28559"
    assert doc["fetches"] == [] and doc["meta"]["covered"] == 4 and doc["meta"]["kept"] == 8
    reported = json.loads((day.dir / "reported.json").read_text())
    assert reported["date"] == DATE and MEMBER in reported["story_ids"] and len(reported["story_ids"]) == 10
    md = (day.dir / "brief.md").read_text()
    assert md.startswith("# Debrief, 2026-10-24") and "## The scan" in md and "> So from what I gather" in md


def test_finalize_is_byte_stable(tmp_path):
    cfg, day = setup_day(tmp_path)
    write_draft(day, good_draft(day))
    finalize.finalize(day)
    first = {p.name: p.read_bytes() for p in day.dir.iterdir() if p.is_file()}
    finalize.finalize(Day(cfg, DATE))
    assert first == {p.name: p.read_bytes() for p in day.dir.iterdir() if p.is_file()}


def test_a_failing_draft_gives_a_scan_only_brief_and_no_ledger(tmp_path):
    cfg, day = setup_day(tmp_path)
    write_draft(day, good_draft(day))
    finalize.finalize(day)                                    # a good brief first...
    assert (day.dir / "reported.json").exists()
    bad = good_draft(day)
    bad["lead"] = "Read https://example.com"
    write_draft(day, bad)
    doc, r = finalize.finalize(Day(cfg, DATE))                # ...then a rerun whose draft fails
    assert doc["mode"] == "scan_only" and not r["ok"] and len(doc["scan"]) == 8
    assert doc["notes"] == ["The brief couldn't be written today. Below is every story triage kept."]
    assert doc["meta"]["warnings"][0].startswith("scan only: lead: has a URL")
    assert not (day.dir / "reported.json").exists()


# ---------------------------------------------------------------------------------------- hooks

def fetch_event(url, session="s1"):
    return {"session_id": session, "tool_name": "WebFetch", "tool_input": {"url": url, "prompt": "?"},
            "cwd": f"/data/runs/{DATE}"}


def test_the_fetch_hook_caps_a_session_and_keeps_to_todays_hosts(tmp_path):
    cfg, day = setup_day(tmp_path)
    got = [hooks.fetch(cfg, fetch_event(f"https://arxiv.org/abs/2610.2855{i}"))[0] for i in range(6)]
    assert got == [True] * 5 + [False]                                  # the 6th is over the cap
    assert hooks.fetch(cfg, fetch_event("https://arxiv.org/abs/1", session="s2"))[0]   # a new session, a new budget
    ok, reason = hooks.fetch(cfg, fetch_event("https://evil.example/payload", session="s3"))
    assert not ok and "isn't a host of today's stories" in reason
    host = re.match(r"https?://([^/]+)", day.detail(UPDATE)["artifact_urls"][0]).group(1)
    assert hooks.fetch(cfg, fetch_event(f"https://{host}/other", session="s3"))[0]  # a story's own site
    log = [json.loads(x) for x in (day.dir / "brief_fetches.jsonl").read_text().splitlines()]
    assert len(log) == 9 and [x["allowed"] for x in log if x["session_id"] == "s1"] == got
    assert [f["url"] for f in Day(cfg, DATE).fetches("s1") if f["allowed"]][-1].endswith("2610.28554")


def test_the_fetch_hook_answers_claude_code_with_a_deny(tmp_path):
    cfg, _ = setup_day(tmp_path)
    out = io.StringIO()
    assert hooks.main("fetch", cfg, stdin=io.StringIO(json.dumps(fetch_event("https://evil.example/x"))), stdout=out) == 0
    d = json.loads(out.getvalue())["hookSpecificOutput"]
    assert (d["hookEventName"], d["permissionDecision"]) == ("PreToolUse", "deny")
    out = io.StringIO()
    hooks.main("fetch", cfg, stdin=io.StringIO(json.dumps(fetch_event("https://arxiv.org/abs/2"))), stdout=out)
    assert out.getvalue() == ""                                                      # allowed: say nothing


def test_the_stop_hook_blocks_until_the_draft_passes(tmp_path):
    cfg, day = setup_day(tmp_path)
    event = {"session_id": "s1", "stop_hook_active": False, "cwd": str(day.dir)}
    err = io.StringIO()
    assert hooks.main("stop", cfg, stdin=io.StringIO(json.dumps(event)), stderr=err) == 2
    assert "no brief.draft.json yet" in err.getvalue()
    bad = good_draft(day)
    bad["sections"][1]["stories"] = ["arxiv:9999.99999"]
    write_draft(day, bad)
    code, message = hooks.stop(cfg, event)
    assert code == 2 and "isn't a story in today's index" in message and message.endswith("finish again.")
    write_draft(day, good_draft(day))
    assert hooks.stop(cfg, event) == (0, "")


# -------------------------------------------------------------------------------------- context

def test_context_carries_failures_updates_and_earlier_briefs(tmp_path):
    cfg, day = setup_day(tmp_path)
    report = day.dir / "run_report.md"
    report.write_text(report.read_text().replace('"failed_sources": []', '"failed_sources": ["techcrunch-ai"]', 1))
    earlier = tmp_path / "data" / "runs" / "2026-10-23"
    earlier.mkdir()
    (earlier / "brief.json").write_text(json.dumps({
        "date": "2026-10-23", "mode": "written", "headline": "Yesterday's news",
        "what_happened": [{"text": "Something happened.", "stories": [UPDATE]}], "sections": [],
        "stories": {UPDATE: {"title": "PHP2Shell"}}}))
    write_draft(day, {"old": True})
    ctx = context.write(Day(cfg, DATE))
    assert [x["id"] for x in ctx["failed_sources"]] == ["techcrunch-ai"]       # with its status from the collect runs
    assert ctx["updates"] == [{"id": UPDATE, "title": day.by_id[UPDATE]["title"], "prev_reported": "2026-10-23"}]
    assert ctx["recent_briefs"][0]["covered"] == [{"id": UPDATE, "title": "PHP2Shell"}]
    assert ctx["kept_by_section"] == {"papers": 3, "releases": 1, "press_policy": 2, "discussion": 2}
    assert not day.draft_path.exists() and json.loads((day.dir / "brief.draft.json.1").read_text()) == {"old": True}
    assert json.loads((day.dir / "brief_context.json").read_text()) == ctx


# --------------------------------------------------------------------------------------- ledger

def test_days_before_the_first_brief_count_by_their_index(tmp_path):
    runs = tmp_path / "runs"
    for d, ids in (("2026-10-22", ["a"]), ("2026-10-23", ["b"]), ("2026-10-24", ["c"])):
        (runs / d).mkdir(parents=True)
        (runs / d / "stories_index.jsonl").write_text("".join(json.dumps({"id": i, "merged": []}) + "\n" for i in ids))
    (runs / "2026-10-23" / "reported.json").write_text(json.dumps({"date": "2026-10-23", "story_ids": ["b", "b2"]}))
    got = ledger.load(tmp_path, "2026-10-25")
    # 10-22 is from before there were briefs; 10-24's brief failed, so it reported nothing
    assert got["kind"] == "reported" and got["dates"] == {"a": ["2026-10-22"], "b": ["2026-10-23"], "b2": ["2026-10-23"]}
    assert ledger.load(tmp_path, "2026-10-23")["kind"] == "index"


# ------------------------------------------------------------------------------------ the session

def test_the_session_gets_the_skill_and_nothing_else(tmp_path):
    from debrief.brief import run
    cfg, day = setup_day(tmp_path)
    argv = run.argv_for(day)
    assert argv[:3] == ["claude", "-p", f"/debrief:brief {DATE}"]
    opt = {a: argv[i + 1] for i, a in enumerate(argv) if a.startswith("--") and i + 1 < len(argv)}
    assert opt["--setting-sources"] == "" and opt["--plugin-dir"].endswith("/skill")
    assert opt["--tools"] == "Read,Write,Edit,WebFetch" and opt["--permission-mode"] == "dontAsk"
    assert opt["--model"] == cfg.settings["brief"]["model"]
    assert "--bare" not in argv and "--disable-slash-commands" not in argv
    s = json.loads(opt["--settings"])
    assert f"Edit(/{day.dir}/brief.draft.json)" in s["permissions"]["allow"]
    assert "Read(./triage_log.jsonl)" in s["permissions"]["deny"]
    assert s["hooks"]["PreToolUse"][0]["matcher"] == "WebFetch"
    assert s["hooks"]["Stop"][0]["hooks"][0]["command"].endswith("-P -m debrief brief hook stop")


def stream(*events):
    return "".join(json.dumps(e) + "\n" for e in events)


INIT = {"type": "system", "subtype": "init", "session_id": "sess-1", "model": "claude-opus-5-5",
        "tools": ["Read", "Write", "Edit", "WebFetch"], "plugins": [{"name": "debrief", "path": "/app/skill"}]}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "session_id": "sess-1", "num_turns": 12,
          "duration_ms": 95000, "total_cost_usd": 1.5, "modelUsage": {"claude-opus-5-5": {}},
          "usage": {"input_tokens": 10, "cache_read_input_tokens": 50000, "output_tokens": 4000},
          "permission_denials": [{"tool_name": "Read"}]}


def test_a_run_writes_the_brief_with_the_session_in_its_meta(tmp_path):
    from debrief.brief import run
    cfg, day = setup_day(tmp_path)
    seen = {}

    def stub(argv, cwd, env, timeout):
        seen.update(cwd=cwd, env=env)
        (cwd / "brief.draft.json").write_text(json.dumps(good_draft(day)))
        return 0, stream(INIT, RESULT), ""
    write_draft(day, {"left": "over"})                        # moved aside by context, not reused
    doc, ses = run.run(cfg, DATE, runner=stub, log=lambda *a: None)
    assert seen["cwd"] == day.dir and seen["env"]["DEBRIEF_BRIEF_DATE"] == DATE and "ANTHROPIC_API_KEY" not in seen["env"]
    assert doc["mode"] == "written" and (day.dir / "reported.json").exists()
    assert (ses["plugin_loaded"], ses["webfetch"], ses["turns"], ses["input_tokens"], ses["denied"]) == (True, True, 12, 50010, 1)
    assert doc["meta"]["session"] == ses and (day.dir / "brief_session.jsonl").read_text().count("\n") == 2


def test_a_failed_session_still_gives_the_scan(tmp_path):
    from debrief.brief import run
    cfg, day = setup_day(tmp_path)
    warnings = []
    doc, ses = run.run(cfg, DATE, runner=lambda *a: (124, stream({**INIT, "tools": ["Read"], "plugins": []}), "timed out"),
                       log=warnings.append)
    assert doc["mode"] == "scan_only" and ses["result"] == "timeout" and ses["error"]
    assert not (day.dir / "reported.json").exists() and len(warnings) == 2           # no plugin, no WebFetch
