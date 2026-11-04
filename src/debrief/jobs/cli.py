"""`debrief jobs ...`: the jobs commands, inside Debrief's command line.

  debrief jobs probe-sources [--source ID ...] [--skip ID ...] [--no-universe] [--sample N] [--md PATH]
                                   check every job source once and time the board universe (M0)
"""
import sys

from ..config import ConfigError


def _probe(args):
    from . import config, probe
    cfg = config.load()
    report = probe.run(cfg, only=set(args.source or []) or None, skip=set(args.skip or []),
                       universe=not args.no_universe, sample=args.sample)
    if args.source and not args.md:
        # A partial probe never overwrites the full review table.
        print("(--source: SOURCES.md left as it is; pass --md PATH to write this probe's table)")
        print("\n".join(probe._source_row(r) for r in report["sources"]))
        return 0
    jpath, md = probe.write(report, cfg, md_path=args.md)
    print(f"wrote {md} and {jpath}")
    return 0


def register(sub):
    p = sub.add_parser("jobs", help="the job-listing collector (docs/jobs)")
    jsub = p.add_subparsers(dest="jobs_cmd", required=True)
    jp = jsub.add_parser("probe-sources", help="check every job source once and time the board universe (M0)")
    jp.add_argument("--source", action="append", help="probe only this source id (repeatable; skips the universe)")
    jp.add_argument("--skip", action="append",
                    help="don't request this source; carry its result over from the last probe (repeatable)")
    jp.add_argument("--no-universe", action="store_true", help="skip the board-universe timing sample")
    jp.add_argument("--sample", type=int, help="boards per system in the universe sample (default: settings)")
    jp.add_argument("--md", help="where to write the markdown (default docs/jobs/SOURCES.md)")
    jp.set_defaults(func=lambda args, _cfg: _run(_probe, args))


def _run(fn, args):
    try:
        return fn(args)
    except ConfigError as e:
        print(f"jobs config error: {e}", file=sys.stderr)
        return 1
