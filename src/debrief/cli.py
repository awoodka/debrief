"""The `debrief` command line.

  debrief probe-sources [--source ID ...] [--include-disabled] [--md PATH]
                                         check every source once (Milestone 0)
  debrief collect [--source ID ...] [--date YYYY-MM-DD]
                                         fetch, keep raw responses, build stories
  debrief collect --replay YYYY-MM-DD [--into PATH]
                                         rebuild a day from raw files, no network
  debrief sections                       which sources feed each section (from the config)
  debrief triage | snapshot-fixtures     arrive in M3 and M6
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
    if args.replay:
        into = args.into or cfg.data_dir / "replay" / f"{args.replay}-{datetime.now():%Y%m%d-%H%M%S}.sqlite"
        result = collect.replay(cfg, args.replay, into)
        print(f"replayed {len(result['runs'])} run(s) into {result['into']}: {result['totals']}")
        return 0
    report = collect.run(cfg, only=set(args.source or []) or None, run_date=args.date)
    print(f"collect {report['run_id']}: {report['status']} · " +
          ", ".join(f"{k} {v}" for k, v in sorted(report["statuses"].items())) +
          f" · report in runs/{report['run_date']}/collect-{report['run_id']}.md")
    return 0 if report["status"] == "ok" else 3


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
    p.set_defaults(func=_collect)

    sub.add_parser("sections", help="list the sources that feed each section").set_defaults(func=_sections)

    for name, milestone in (("triage", "M3"), ("snapshot-fixtures", "M6")):
        sub.add_parser(name).set_defaults(func=_not_yet(milestone))

    args = ap.parse_args(argv)
    try:
        cfg = config.load()
    except config.ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 1
    return args.func(args, cfg)
