"""`debrief jobs ...`: the jobs commands, inside Debrief's command line.

  debrief jobs probe-sources [--source ID ...] [--skip ID ...] [--no-universe] [--sample N] [--md PATH]
                                   check every job source once and time the board universe (M0)
  debrief jobs profile show | check FILE | save FILE [--note TEXT]
                                   the profile: latest version, a check, a new version (FILE may be -)
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


def _profile(args):
    import json
    from . import config, profile
    cfg = config.load()
    if args.step == "show":
        n, doc = profile.latest(cfg)
        if doc is None:
            print("no profile saved yet (debrief jobs profile save FILE)", file=sys.stderr)
            return 1
        print(f"# version {n} · id {profile.profile_id(doc)}")
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0
    text = sys.stdin.read() if args.file == "-" else open(args.file).read()
    try:
        doc = json.loads(text)
    except ValueError as e:
        print(f"not JSON: {e}", file=sys.stderr)
        return 1
    if args.step == "check":
        bad = profile.problems(doc)
        print("ok" if not bad else "problems:\n  " + "\n  ".join(bad))
        return 0 if not bad else 3
    try:
        version, saved = profile.save(cfg, doc, note=args.note or "")
    except profile.ProfileError as e:
        print(e, file=sys.stderr)
        return 3
    print(f"saved as version {version}" if saved else f"unchanged: same as version {version}, nothing written")
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

    jp = jsub.add_parser("profile", help="the profile: show the latest version, check a file, or save a new version")
    psub = jp.add_subparsers(dest="step", required=True)
    psub.add_parser("show", help="print the latest saved version")
    for step, text in (("check", "check a profile file against the schema and rules (exit 3 if not ok)"),
                       ("save", "check, then save as a new version (nothing written if unchanged)")):
        pp = psub.add_parser(step, help=text)
        pp.add_argument("file", help="a JSON file, or - for stdin")
        if step == "save":
            pp.add_argument("--note", help="why this version (kept in history.jsonl)")
    jp.set_defaults(func=lambda args, _cfg: _run(_profile, args))


def _run(fn, args):
    try:
        return fn(args)
    except ConfigError as e:
        print(f"jobs config error: {e}", file=sys.stderr)
        return 1
