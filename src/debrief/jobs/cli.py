"""`debrief jobs ...`: the jobs commands, inside Debrief's command line.

  debrief jobs probe-sources [--source ID ...] [--skip ID ...] [--no-universe] [--sample N] [--md PATH]
                                   check every job source once and time the board universe (M0)
  debrief jobs profile show | check FILE | save FILE [--note TEXT]
                                   the profile: latest version, a check, a new version (FILE may be -)
  debrief jobs collect [--source ID ...] [--date D]   fetch every source, build postings (M1)
  debrief jobs collect --replay DATE [--into PATH]    rebuild a day from raw files, no network
  debrief jobs collect --rebuild                      rebuild the database from all raw files (parser change)
  debrief jobs watch add NAME [--url URL] | remove NAME | list
                                   the watchlist (each change is a new profile version)
  debrief jobs posting QUERY [--all]                  one posting, its listings and keys
  debrief jobs score [--date D] [--no-cap] [--dry-run] filters, points, the scoring call, the morning list (M2)
  debrief jobs today [--date D] [--all] [--catchup]   print the morning list (or the catch-up list)
  debrief jobs catchup [--date D] [--dry-run]         once: every kept job in the pool, to catchup.txt
  debrief jobs calibrate                              compare the pipeline's decisions with Alex's labels
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


def _collect(args):
    from datetime import datetime
    from . import collect, config
    cfg = config.load()
    if args.rebuild:
        out = collect.rebuild(cfg)
        print(f"rebuilt from {len(out['dates'])} day(s) of raw data: {out['totals']}"
              + (f"; previous database kept as state/{out['backup']}" if out["backup"] else ""))
        return 0
    if args.replay:
        into = args.into or cfg.data_dir / "replay" / f"{args.replay}-{datetime.now():%Y%m%d-%H%M%S}.sqlite"
        out = collect.replay(cfg, args.replay, into)
        print(f"replayed {len(out['runs'])} run(s) into {out['into']}: {out['totals']}")
        return 0
    report = collect.run(cfg, only=set(args.source or []) or None, run_date=args.date)
    print(f"jobs collect {report['run_id']}: {report['status']} · "
          + ", ".join(f"{k} {v}" for k, v in sorted(report["statuses"].items()))
          + f" · {report['postings']['open']} open postings · report in jobs/runs/{report['run_date']}/collect-{report['run_id']}.md")
    return 0 if report["status"] == "ok" else 3


def _watch(args):
    from . import config, watch
    cfg = config.load()
    try:
        if args.step == "add":
            entry, notes, version = watch.add(cfg, args.name, url=args.url, note=args.note)
            for n in notes:
                print(f"  {n}")
            print(f"added {entry['name']} ({entry['board'] or 'no board'}): profile version {version}")
            if not entry["board"]:
                print("  no public board found; set a LinkedIn alert for it, or rerun with --url <careers page>")
        elif args.step == "remove":
            print(f"removed {args.name}: profile version {watch.remove(cfg, args.name)}")
        else:
            for name, board, n in watch.listing(cfg):
                print(f"{name:30} {board or '(no board)':45} {'' if n is None else f'{n} open'}")
    except watch.WatchError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


def _posting(args):
    import json
    from . import config
    from .items import company_norm
    from .store import JobsStore, state_path
    from ..store.db import loads
    cfg = config.load()
    store = JobsStore(state_path(cfg))
    q = args.query
    rows = store.q("SELECT posting_id FROM postings WHERE posting_id=?", (q,)) or store.q(
        "SELECT posting_id FROM postings WHERE title LIKE ? OR company LIKE ? ORDER BY first_seen_at DESC LIMIT 20",
        (f"%{q}%", f"%{q}%"))
    if not rows:
        print("no posting matches", file=sys.stderr)
        return 1
    if len(rows) > 1 and not args.all:
        for r in rows:
            p = store.posting(r[0])
            print(f"{p['posting_id']}  {p['company'][:28]:28} {p['title'][:60]:60} {'closed' if p['closed_at'] else 'open'}")
        print("(more than one: pass a posting id, or --all)")
        return 0
    for r in rows:
        p = store.posting(r[0])
        print(json.dumps({**p, "keys": [k[0] for k in store.q("SELECT key FROM posting_keys WHERE posting_id=?", (p["posting_id"],))],
                          "listings": [{"source": i["source_id"], "key": i["item_key"], "active": bool(i["active"]),
                                        "first_seen": i["first_seen_at"], "url": loads(i["data"])["url"]}
                                       for i in store.q("SELECT * FROM items WHERE posting_id=? ORDER BY first_seen_at", (p["posting_id"],))],
                          "text_from": [x[0] for x in store.q("SELECT source_id FROM posting_text WHERE posting_id=?", (p["posting_id"],))]},
                         indent=1, ensure_ascii=False))
    return 0


def _score(args):
    from . import config, rank
    cfg = config.load()
    r = rank.run(cfg, run_date=args.date, no_cap=args.no_cap, dry_run=args.dry_run)
    print(f"jobs score {r['run_id']}: {r['status']} · pool {r['candidates']} ({r['new']} new) · {r['passed_filters']} "
          f"passed the filters · {r['sent']} asked · {r['kept']} kept · list of {len(r['list']['new'])}"
          + ("" if args.dry_run else f" · report in jobs/runs/{r['run_date']}/{r['run_id']}.md"))
    if args.dry_run:
        for e in r["list"]["new"]:
            print(f"  {e['points']:3} pts  {e['title'][:70]} — {e['company']}")
    return 0 if r["status"] in ("ok", "dry_run") else 2


def _today(args):
    from . import config, rank
    print(rank.today(config.load(), list_date=args.date, show_all=args.all, catchup=args.catchup))
    return 0


def _catchup(args):
    """The one-time catch-up (docs/jobs/PLAN.md, "Revision 5"): every kept job in today's pool,
    written to <data>/jobs/catchup.txt."""
    from . import config, rank
    cfg = config.load()
    r = rank.catchup(cfg, run_date=args.date, dry_run=args.dry_run)
    print(f"jobs catchup {r['run_id']}: {r['status']} · pool {r['candidates']} · {r['passed_filters']} passed the "
          f"filters · {r['sent']} asked · {r['kept']} kept · list of {len(r['list']['catchup'])}"
          + ("" if args.dry_run else f" · report in jobs/runs/{r['run_date']}/{r['run_id']}.md"))
    if args.dry_run:
        for e in r["list"]["catchup"][:60]:
            print(f"  {e['points']:3} pts  posted {e['posted']}  {e['title'][:60]} — {e['company']}")
        return 0
    path = cfg.data_dir / "catchup.txt"
    tmp = path.with_suffix(".txt.tmp")
    tmp.write_text(rank.today(cfg, list_date=r["run_date"], catchup=True) + "\n")
    tmp.replace(path)
    print(f"written to {path}")
    return 0 if r["status"] == "ok" else 2


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

    jp = jsub.add_parser("collect", help="fetch every job source, keep raw responses, build postings (M1)")
    jp.add_argument("--source", action="append", help="collect only this source id (repeatable)")
    jp.add_argument("--date", help="logical run date for raw files (default: today)")
    jp.add_argument("--replay", metavar="DATE", help="rebuild DATE from raw files into a new database, no network")
    jp.add_argument("--into", help="replay target (default: <data>/jobs/replay/<date>-<time>.sqlite)")
    jp.add_argument("--rebuild", action="store_true",
                    help="rebuild the state database from all raw data (after a parser change); keeps ETags and run history")
    jp.set_defaults(func=lambda args, _cfg: _run(_collect, args))

    jp = jsub.add_parser("watch", help="the watchlist (the profile's companies): add, remove, list")
    wsub = jp.add_subparsers(dest="step", required=True)
    wp = wsub.add_parser("add", help="find the company's board (directory, or --url) and add it")
    wp.add_argument("name")
    wp.add_argument("--url", help="the company's careers or job-board URL, when the directory doesn't have it")
    wp.add_argument("--note")
    wp = wsub.add_parser("remove", help="take a company off the watchlist")
    wp.add_argument("name")
    wsub.add_parser("list", help="the watchlist, with each board's open postings")
    jp.set_defaults(func=lambda args, _cfg: _run(_watch, args))

    jp = jsub.add_parser("posting", help="one posting: its listings, keys and sources")
    jp.add_argument("query", help="a posting id, or part of a title or company")
    jp.add_argument("--all", action="store_true", help="print every match in full")
    jp.set_defaults(func=lambda args, _cfg: _run(_posting, args))
    _register_m2(jsub)


def _calibrate(args):
    from . import calibrate, config
    out = calibrate.run(config.load())
    print(out["text"] + f"(written to {out['path']})")
    return 0


def _register_m2(jsub):
    jp = jsub.add_parser("score", help="filters, points, the scoring call and the morning list (M2)")
    jp.add_argument("--date", help="the list's date (default: today)")
    jp.add_argument("--no-cap", action="store_true", help="send every job past the threshold (night one's backlog)")
    jp.add_argument("--dry-run", action="store_true", help="filters and points only: no call, nothing saved")
    jp.set_defaults(func=lambda args, _cfg: _run(_score, args))
    jp = jsub.add_parser("today", help="print the morning list")
    jp.add_argument("--date", help="a past list (default: the latest)")
    jp.add_argument("--all", action="store_true", help="also every kept job below the top N")
    jp.add_argument("--catchup", action="store_true", help="the catch-up list instead of the morning list")
    jp.set_defaults(func=lambda args, _cfg: _run(_today, args))
    jp = jsub.add_parser("catchup", help="once: score today's whole pool with no cap and list every kept job (catchup.txt)")
    jp.add_argument("--date", help="the list's date (default: today)")
    jp.add_argument("--dry-run", action="store_true", help="filters and points only: no call, nothing saved")
    jp.set_defaults(func=lambda args, _cfg: _run(_catchup, args))
    jp = jsub.add_parser("calibrate", help="compare the pipeline's decisions with Alex's labels (calibration/labels.jsonl)")
    jp.set_defaults(func=lambda args, _cfg: _run(_calibrate, args))


def _run(fn, args):
    try:
        return fn(args)
    except ConfigError as e:
        print(f"jobs config error: {e}", file=sys.stderr)
        return 1
