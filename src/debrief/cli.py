"""The `debrief` command line.

  debrief probe-sources [--source ID ...] [--include-disabled] [--md PATH]
                                                         Milestone 0: check every source once
  debrief collect | triage | snapshot-fixtures           arrive in M1, M3 and M6
"""
import argparse
import sys

from . import config


def _probe(args, cfg):
    from . import probe
    results = probe.run(cfg, only=set(args.source or []) or None, include_disabled=args.include_disabled)
    if args.source:
        results = probe.merge_with_latest(results, cfg)
    jpath, md = probe.write(results, cfg, md_path=args.md)
    print(f"wrote {md} and {jpath}")
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

    for name, milestone in (("collect", "M1"), ("triage", "M3"), ("snapshot-fixtures", "M6")):
        sub.add_parser(name).set_defaults(func=_not_yet(milestone))

    args = ap.parse_args(argv)
    try:
        cfg = config.load()
    except config.ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 1
    return args.func(args, cfg)
