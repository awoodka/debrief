"""The `debrief` command line.

  debrief probe-sources [--source ID ...] [--include-disabled] [--md PATH]
                                         check every source once (Milestone 0)
  debrief collect [--source ID ...] [--date YYYY-MM-DD] [--cadence daily|6h]
                                         fetch, keep raw responses, build stories, attach discussion
  debrief bootstrap-bands [--days N]     read back HN history so discussion levels have a baseline
  debrief story ID_OR_TITLE              one story: sources, signals, discussion, coverage, level
  debrief collect --replay YYYY-MM-DD [--into PATH]
                                         rebuild a day from raw files, no network
  debrief collect --rebuild              rebuild the whole state from raw (after a parser change)
  debrief sections                       which sources feed each section (from the config)
  debrief triage [--date YYYY-MM-DD] [--at ISO] [--no-model] [--model M] [--fresh]
                                         the day's candidates, the scoring call, the index (runs/<date>/)
  debrief calibrate [--model M] [--fresh]
                                         score the labeled calibration set; report agreement
  debrief brief context|check|finalize [--date YYYY-MM-DD]
                                         around the brief skill: its context file, the draft's check,
                                         and brief.json / brief.md / reported.json
  debrief brief hook fetch|stop          the skill session's hooks (the fetch cap, the check on stop)
  debrief snapshot-fixtures              arrives in M6
"""
import argparse
import sys
from datetime import datetime

from . import config


def _probe(args, cfg):
    from . import probe
    results = probe.run(cfg, only=set(args.source or []) or None, include_disabled=args.include_disabled)
    if args.source:
        results = probe.merge_with_latest(results, cfg)
    jpath, md = probe.write(results, cfg, md_path=args.md)
    print(f"wrote {md} and {jpath}")
    return 0


def _collect(args, cfg):
    from . import collect
    if args.rebuild:
        out = collect.rebuild(cfg)
        print(f"rebuilt state from {len(out['dates'])} day(s) of raw data: {out['totals']}; "
              f"previous state kept as state/{out['backup']}")
        return 0
    if args.replay:
        into = args.into or cfg.data_dir / "replay" / f"{args.replay}-{datetime.now():%Y%m%d-%H%M%S}.sqlite"
        result = collect.replay(cfg, args.replay, into)
        print(f"replayed {len(result['runs'])} run(s) into {result['into']}: {result['totals']}")
        return 0
    report = collect.run(cfg, only=set(args.source or []) or None, run_date=args.date, cadence=args.cadence)
    print(f"collect {report['run_id']}: {report['status']} · " +
          ", ".join(f"{k} {v}" for k, v in sorted(report["statuses"].items())) +
          f" · report in runs/{report['run_date']}/collect-{report['run_id']}.md")
    return 0 if report["status"] == "ok" else 3


def _bootstrap_bands(args, cfg):
    from . import collect
    days = args.days or cfg.settings["bands"]["window_days"]
    report = collect.run(cfg, only={"hn"}, backfill_hours=days * 24)
    hn = next((x for x in report["sources"] if x["id"] == "hn"), {})
    print(f"hn: {hn.get('status')}, {hn.get('requests')} requests, {hn.get('counts', {}).get('new', 0)} new stories stored")
    return 0 if hn.get("status") == "ok" else 3


def _story(args, cfg):
    from .collect import state_path
    from .store import Store, loads
    from .stories import signals
    store = Store(state_path(cfg))
    q = args.query
    row = store.one("SELECT story_id FROM stories WHERE story_id=?", (q,)) or store.one(
        "SELECT story_id FROM stories WHERE title LIKE ? ORDER BY first_seen_at DESC, story_id LIMIT 1", (f"%{q}%",))
    if not row:
        print(f"no story matches {q!r}")
        return 1
    st = store.story(row[0])
    at = store.one("SELECT MAX(fetched_at) FROM fetches")[0]
    d = signals.discussion(store, cfg.settings, st["story_id"], at)
    print(f"{st['story_id']}  [{st['section']}, tier {st['tier']}]  {st['title']}")
    print(f"  published {st['published_at']}  first seen {st['first_seen']}  sources {', '.join(st['sources'])}")
    print(f"  urls: {', '.join(st['artifact_urls'][:4])}" + (f"  code: {', '.join(st['code_urls'])}" if st["code_urls"] else ""))
    sig = store.latest_signals(st["story_id"])
    if sig:
        print("  signals: " + ", ".join(f"{k} {v:g}" for k, v in sig.items()))
    if st["section"] == "papers":
        u = signals.paper_usage(store, st["story_id"])
        print(f"  use bar: {'passes' if signals.passes_use_bar(u, cfg.settings['triage']['papers']['use_bar']) else 'fails'} {u}")
    print(f"  discussion: band {d['band']}, spread {d['spread']}, trend {d['trend']}, where {d['where']}")
    for src, b in d["by_source"].items():
        print(f"    {src}: {b}")
    for m in store.q("""SELECT mention_id, kind, title, url, match_method, match_score FROM mentions WHERE story_id=?
                        AND kind IN ('thread','post') ORDER BY mention_id""", (st["story_id"],)):
        e = signals._latest(store, m["mention_id"], at)
        print(f"  {m['kind']} {m['mention_id']} ({m['match_method']} {m['match_score']}): {m['title'][:70]}  "
              f"{e.get('points')} points, {e.get('comments')} comments")
        for c in store.q("SELECT author, text, rank, replies FROM mentions WHERE parent_id=? ORDER BY rank LIMIT 3",
                         (m["mention_id"],)):
            print(f"      #{c['rank']} {c['author']} ({c['replies']} replies): {c['text'][:110]}")
    hf_comments = store.q("""SELECT author, text FROM mentions WHERE story_id=? AND kind='comment' AND parent_id IS NULL
                             ORDER BY rank LIMIT 3""", (st["story_id"],))
    for c in hf_comments:
        print(f"  comment {c['author']}: {c['text'][:110]}")
    for cv in store.q("SELECT source_id, covered_at, url FROM coverage WHERE story_id=? ORDER BY covered_at", (st["story_id"],)):
        print(f"  covered by {cv['source_id']} {cv['covered_at']} {cv['url']}")
    for pg in store.pages(st["story_id"]):
        print(f"  page {pg['status']} {pg['url']}: {len(pg['text'])} chars, {len(pg['links'])} links"
              + (f" ({', '.join(pg['links'][:3])}{', …' if len(pg['links']) > 3 else ''})" if pg["links"] else ""))
    return 0


def _sections(args, cfg):
    """Print which sources feed each section, straight from the config."""
    from .adapters import REGISTRY
    sections = cfg.settings["sections"]
    order = list(dict.fromkeys(sections.values()))
    enabled = [s for s in cfg.sources if s.enabled]
    for sec in order:
        rows = [s for s in enabled if "anchor" in s.roles and sections.get(s.kind) == sec]
        if not rows:
            print(f"{sec}: no anchor sources; these stories are formed from discussion clusters (M2)")
            continue
        print(f"{sec} ({len(rows)} sources)")
        for s in rows:
            built = "" if s.adapter in REGISTRY else "   [adapter not built yet]"
            print(f"  tier {s.tier}  {s.id:22} {s.name}{built}")
    for role, label in (("discussion", "discussion: attach to stories (M2), never stories themselves"),
                        ("reference", "reference: coverage and the recall check, never scored or shown")):
        rows = [s for s in enabled if role in s.roles and "anchor" not in s.roles]
        print(f"{label} ({len(rows)})")
        print("  " + ", ".join(s.id for s in rows))
    off = [s.id for s in cfg.sources if not s.enabled]
    print(f"disabled ({len(off)}): " + ", ".join(off))
    return 0


def _triage(args, cfg):
    from .triage import run
    try:
        h, out, cached = run.run(cfg, run_date=args.date, at=args.at, model=not args.no_model,
                                 model_name=args.model, fresh=args.fresh)
    except run.TriageError as e:
        print(f"triage: {e}", file=sys.stderr)
        return 1
    st, sc = h["stages"], h["scoring"] or {}
    how = ("" if not sc else f" · scoring failed: {sc['error'][:120]}" if sc.get("error") else
           f" · {sc['model']} {'replayed from the cache' if cached else 'called'}, "
           f"{sc['usage']['input_tokens']:,} in / {sc['usage']['output_tokens']:,} out tokens")
    print(f"triage {h['date']}: {h['status']} ({h['mode']}) · {h['candidates']} candidates, {h['kept']} kept · " +
          ", ".join(f"{sec} {x['kept']}/{x['candidates']}" for sec, x in st.items() if x["pool"]) +
          how + f" · {out}/run_report.md")
    return 0 if h["status"] == "ok" else 3


def _calibrate(args, cfg):
    from .triage import calibrate, score
    try:
        result, cmp, out = calibrate.run(cfg, fixture=args.fixture or calibrate.DEFAULT, model=args.model,
                                         fresh=args.fresh)
    except score.ScoringError as e:
        print(f"calibrate: {e}", file=sys.stderr)
        return 1
    took = "cached" if result["cached"] else f"{result['usage']['duration_s']} s"
    print(f"{result['model']}: agrees on {cmp['agree']} of {cmp['n']}; kept {cmp['kept_in']} of "
          f"{cmp['kept_in'] + cmp['missed_in']} relevant, let {cmp['let_through']} not-relevant through · {took} · {out}")
    return 0


def _brief(args, cfg):
    import json
    from .brief import context, finalize, hooks
    from .brief.check import check
    from .brief.day import Day, DayError
    if args.step == "hook":
        return hooks.main(args.kind, cfg)
    try:
        day = Day(cfg, args.date or datetime.now().astimezone().date().isoformat())
    except DayError as e:
        print(f"brief: {e}", file=sys.stderr)
        return 1
    if args.step == "context":
        ctx = context.write(day)
        print(f"brief context {day.date}: {sum(ctx['kept_by_section'].values())} stories, "
              f"{len(ctx['updates'])} updates, {len(ctx['failed_sources'])} failed sources, "
              f"{len(ctx['recent_briefs'])} earlier briefs · {day.dir / 'brief_context.json'}")
        return 0
    if args.step == "check":
        r = check(day)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if r["ok"] else 3
    doc, r = finalize.finalize(day)
    m = doc["meta"]
    print(f"brief {day.date}: {doc['mode']} · {m['covered']} of {m['kept']} stories in the article, {m['words']} words, "
          f"{sum(f['allowed'] for f in doc['fetches'])} fetches · {day.dir / 'brief.json'}"
          + ("" if r["ok"] else f" · check failed: {r['errors'][0][:160]}"))
    return 0 if doc["mode"] == "written" else 3


def _not_yet(milestone):
    def run(args, cfg):
        print(f"{args.cmd}: not built yet (arrives in {milestone})", file=sys.stderr)
        return 2
    return run


def main(argv=None):
    ap = argparse.ArgumentParser(prog="debrief", description="Debrief v2 collector and triage")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe-sources", help="hit every source once and report what works")
    p.add_argument("--source", action="append", help="probe only this source id (repeatable)")
    p.add_argument("--md", help="where to write the markdown table (default docs/v2/SOURCES.md)")
    p.add_argument("--include-disabled", action="store_true", help="also request disabled sources")
    p.set_defaults(func=_probe)

    p = sub.add_parser("collect", help="fetch sources, keep raw responses, build stories")
    p.add_argument("--source", action="append", help="collect only this source id (repeatable)")
    p.add_argument("--date", help="logical run date for raw files and first_seen (default: today)")
    p.add_argument("--replay", metavar="DATE", help="rebuild DATE from raw files into a new database")
    p.add_argument("--into", help="replay target database (default: <data>/replay/<date>-<time>.sqlite)")
    p.add_argument("--rebuild", action="store_true",
                   help="rebuild the state database from all raw data (after a parser change); keeps cursors and ETags")
    p.add_argument("--cadence", default="daily", choices=("daily", "6h"),
                   help="6h runs only the discussion poll sources (default: daily, everything)")
    p.set_defaults(func=_collect)

    p = sub.add_parser("bootstrap-bands", help="read back HN history so discussion levels have a baseline")
    p.add_argument("--days", type=int, help="default: bands.window_days")
    p.set_defaults(func=_bootstrap_bands)

    p = sub.add_parser("story", help="show one story with its signals and attached discussion")
    p.add_argument("query", help="a story id, or part of a title")
    p.set_defaults(func=_story)

    sub.add_parser("sections", help="list the sources that feed each section").set_defaults(func=_sections)

    p = sub.add_parser("triage", help="pick the day's candidates and write the index (runs/<date>/)")
    p.add_argument("--date", help="run date (default: today); its collect runs give the data's time")
    p.add_argument("--at", help="the data's time instead (ISO 8601 UTC, e.g. 2026-10-23T12:00:00Z)")
    p.add_argument("--no-model", action="store_true", help="skip the scoring call: rules only, marked degraded")
    p.add_argument("--model", help="score with this model instead of triage.yml's (e.g. claude-sonnet-5-5)")
    p.add_argument("--fresh", action="store_true", help="call the model even if the answer is cached")
    p.set_defaults(func=_triage)

    p = sub.add_parser("calibrate", help="score the labeled calibration set and compare with the labels")
    p.add_argument("--fixture", help="a labeled set (default tests/fixtures/relevance/2026-10-23.jsonl)")
    p.add_argument("--model", help="score with this model instead of triage.yml's")
    p.add_argument("--fresh", action="store_true", help="call the model even if the answer is cached")
    p.set_defaults(func=_calibrate)

    p = sub.add_parser("brief", help="the brief around the skill: context, check, finalize, and the skill's hooks")
    bsub = p.add_subparsers(dest="step", required=True)
    for step, text in (("context", "write brief_context.json, the skill's first read"),
                       ("check", "check the skill's draft (exit 3 if it fails)"),
                       ("finalize", "write brief.json, brief.md and reported.json (scan only if the draft fails)")):
        bp = bsub.add_parser(step, help=text)
        bp.add_argument("--date", help="run date (default: today)")
    bp = bsub.add_parser("hook", help="a Claude Code hook for the skill's session (JSON on stdin)")
    bp.add_argument("kind", choices=("fetch", "stop"))
    p.set_defaults(func=_brief)

    sub.add_parser("snapshot-fixtures").set_defaults(func=_not_yet("M6"))

    args = ap.parse_args(argv)
    try:
        cfg = config.load()
    except config.ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 1
    return args.func(args, cfg)
