"""`debrief jobs score`: from the night's new postings to the morning list (docs/jobs/PLAN.md,
"M2 design"), and `debrief jobs today`, which prints it.

1. Candidates: open postings first seen since the last scoring run (meta score_cursor), or every
   open posting on the first run. Re-running the same day starts from the same point, and answers
   already stored aren't asked again.
2. Filters (filters.py), with the job's description when there is one.
3. Points (prescore.py): ranking.threshold, then ranking.max_candidates, watchlist companies first
   and past the threshold. no_cap lifts the cap (night one's backlog).
4. The scoring call (score.py), only for jobs with no answer for this profile version, in chunks of
   score.chunk_size. If it fails, the list is ranked by points and the cursor stays put, so the next
   run asks again.
5. The list: kept jobs by score (ties: points, newest, id), top ranking.top_n as "new", and every
   kept watchlist job as "watchlist". Saved in daily_list. The report, runs/<date>/score-<run>.json
   and .md, has every cut with its stage and reason.
"""
import json
from collections import Counter
from datetime import datetime, timezone

from ..store import dumps
from ..triage.score import ScoringError, run_claude
from . import filters, prescore, profile as profiles, score
from .collect import early_sources
from .items import company_norm, core_title
from .store import JobsStore, state_path

BIG_STAGES = ("title", "place", "age")       # counted in the .md report; listed in full only in the .json


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_run_id(store, started):
    base = "score-" + started.strftime("%Y%m%dT%H%M%SZ")
    run_id, n = base, 1
    while store.one("SELECT 1 FROM runs WHERE run_id=?", (run_id,)):
        n += 1
        run_id = f"{base}-{n}"
    return run_id


def run(cfg, run_date=None, no_cap=False, dry_run=False, runner=run_claude, now=None, log=print):
    started = now or datetime.now(timezone.utc)
    run_date = run_date or started.astimezone().date().isoformat()
    version, prof = profiles.latest(cfg)
    if prof is None:
        raise SystemExit("no profile saved (debrief jobs profile save FILE)")
    pid = profiles.profile_id(prof)
    s = cfg.settings["score"]
    store = JobsStore(state_path(cfg))
    run_id = _new_run_id(store, started)
    E = early_sources(cfg)

    if store.get_meta("score_cursor_date") == run_date:
        since = store.get_meta("score_cursor_prev")
    else:
        since = store.get_meta("score_cursor")
    rows = store.q("SELECT posting_id, first_seen_at FROM postings WHERE closed_at IS NULL"
                   + (" AND first_seen_at > ?" if since else "") + " ORDER BY posting_id", (since,) if since else ())
    newest_seen = max((r["first_seen_at"] for r in rows), default=since)
    report = {"kind": "score", "run_id": run_id, "run_date": run_date, "started_at": _iso(started),
              "profile": {"version": version, "id": pid}, "since": since, "candidates": len(rows),
              "no_cap": no_cap, "dry_run": dry_run, "cuts": [], "calls": []}
    log(f"{len(rows)} new open postings since {since or 'the start'}")

    # 2-3. filters and points
    passed = []
    for r in rows:
        p = store.posting(r["posting_id"])
        text = store.texts_for(p["posting_id"])
        srcs = store.posting_sources(p["posting_id"])
        v = filters.check(p, prof, started, text=text, listed_early=bool(srcs & E))
        if not v.ok:
            report["cuts"].append(_cut(p, v.stage, v.reason))
            continue
        pts, parts = prescore.points(p, prof, started, text=text, facts=v.facts, sources=srcs, early_sources=E)
        passed.append((p, pts, parts, text, srcs, v.facts))
    sent, over = prescore.select(passed, prof, cap=0 if no_cap else None)
    for (p, pts, *_), why in over:
        report["cuts"].append(_cut(p, "threshold" if why.startswith("below") else "cap", why, pts))
    report["passed_filters"], report["sent"] = len(passed), len(sent)
    log(f"{len(passed)} passed the filters; {len(sent)} go to the scoring call"
        + (f" ({len(over)} cut by the threshold or the cap)" if over else ""))

    # 4. the call, for jobs with no answer for this profile version
    answers = {p["posting_id"]: store.score_for(p["posting_id"], pid) for p, *_ in sent}
    todo = [x for x in sent if answers[x[0]["posting_id"]] is None]
    report["already_scored"] = len(sent) - len(todo)
    status = "ok"
    if todo and not dry_run:
        status = _score(store, cfg, s, prof, todo, answers, pid, run_id, started, E, report, runner, log)
    elif dry_run:
        status = "dry_run"

    # 5. the list
    kept, not_kept = [], []
    for p, pts, parts, *_ in sent:
        a = answers.get(p["posting_id"])
        if a is None:
            if status in ("fallback", "dry_run"):
                kept.append((p, pts, None))
            continue
        if a["keep"]:
            kept.append((p, pts, a))
        else:
            not_kept.append((p, pts, a))
            report["cuts"].append(_cut(p, "not kept", "; ".join(a["mismatches"]) or a["why"], pts, a["score"]))
    kept.sort(key=lambda x: (-(x[2]["score"] if x[2] else 0), -x[1], -prescore._ts(x[0].get("published_at")
                                                                                    or x[0].get("first_seen_at")),
                             x[0]["posting_id"]))
    groups = collapse(kept)
    top_n = prof["ranking"]["top_n"]
    new = groups[:top_n]
    for (p, pts, a), _ in groups[top_n:]:
        report["cuts"].append(_cut(p, "beyond N", f"kept, ranked below the top {top_n}", pts, a["score"] if a else None))
    for (p0, *_), same in groups:
        for p, pts, a in same:
            report["cuts"].append(_cut(p, "same job", f"shown with {p0['posting_id']} ({p0['title']})", pts,
                                       a["score"] if a else None))
    watch = [g for g in groups if prescore.watched(g[0][0], prof)]
    report["kept"], report["kept_distinct"], report["not_kept"] = len(kept), len(groups), len(not_kept)
    report["list"] = {"new": [_entry(i + 1, *g[0], also=g[1]) for i, g in enumerate(new)],
                      "watchlist": [_entry(i + 1, *g[0], also=g[1]) for i, g in enumerate(watch)]}
    report["status"] = status
    report["stages"] = dict(Counter(c["stage"] for c in report["cuts"]))
    report["finished_at"] = _iso(datetime.now(timezone.utc))

    if not dry_run:
        with store.transaction():
            store.save_list(run_date, run_id, [("new", e["rank"], e["posting_id"], e["score"], e["points"])
                                               for e in report["list"]["new"]]
                            + [("watchlist", e["rank"], e["posting_id"], e["score"], e["points"])
                               for e in report["list"]["watchlist"]])
            if status == "ok":
                if store.get_meta("score_cursor_date") != run_date:
                    store.set_meta("score_cursor_prev", since or "")
                store.set_meta("score_cursor", newest_seen or "")
                store.set_meta("score_cursor_date", run_date)
            store.start_run(run_id, "score", run_date, report["started_at"])
            store.finish_run(run_id, report["finished_at"], status, report)
        out = cfg.data_dir / "runs" / run_date
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{run_id}.json").write_text(dumps(report) + "\n")
        (out / f"{run_id}.md").write_text(render_report(report))
    store.close()
    return report


def _score(store, cfg, s, prof, todo, answers, pid, run_id, started, E, report, runner, log):
    view = score.profile_view(prof)
    size = int(s["chunk_size"])
    for start in range(0, len(todo), size):
        chunk = todo[start:start + size]
        jobs, by_jid = [], {}
        for i, (p, pts, parts, text, srcs, facts) in enumerate(chunk, 1):
            jid = f"j{i}"
            by_jid[jid] = p["posting_id"]
            jobs.append(score.job_view(jid, p, text, srcs, E, s["text_max_chars"]))
        log(f"scoring call: {len(jobs)} jobs" + (f" (chunk {start // size + 1})" if len(todo) > size else ""))
        try:
            res = score.call(cfg.data_dir / "cache" / "score", s, view, jobs, runner=runner, log=log)
        except ScoringError as e:
            report["calls"].append({"jobs": len(jobs), "error": str(e)[:500]})
            log(f"the scoring call failed: {e}; the list is ranked by points")
            return "fallback"
        report["calls"].append({"jobs": len(jobs), "cached": res["cached"], "attempts": res["attempts"],
                                "model": res["model"], "prompt_version": res["prompt_version"], "usage": res["usage"]})
        u = res["usage"]
        log(f"  {'cached' if res['cached'] else 'answered'}: {u.get('input_tokens', 0):,} tokens in, "
            f"{u.get('output_tokens', 0):,} out, {u.get('duration_s', 0)} s, list price ${u.get('cost_usd_list_price', 0)}")
        with store.transaction():
            for jid, a in res["answers"].items():
                store.save_score(by_jid[jid], pid, run_id, _iso(started), a, res["model"], res["prompt_version"])
                answers[by_jid[jid]] = {**a, "keep": bool(a["keep"])}
    return "ok"


def collapse(kept):
    """[(best, [the others])]: kept jobs at the same company with the same core title (new-grad
    words and class years ignored) are one entry, shown once with the others' links. Order kept."""
    groups, order = {}, []
    for x in kept:
        core = core_title(x[0]["title"])
        key = (company_norm(x[0]["company"]), core) if core else x[0]["posting_id"]
        if key in groups:
            groups[key][1].append(x)
        else:
            groups[key] = (x, [])
            order.append(key)
    return [groups[k] for k in order]


def _cut(p, stage, reason, points=None, score_=None):
    out = {"posting_id": p["posting_id"], "company": p["company"], "title": p["title"], "stage": stage,
           "reason": reason}
    if points is not None:
        out["points"] = points
    if score_ is not None:
        out["score"] = score_
    return out


def _entry(rank, p, pts, a, also=()):
    return {"rank": rank, "posting_id": p["posting_id"], "company": p["company"], "title": p["title"],
            "locations": p["locations"], "url": p["url"], "points": pts, "score": a["score"] if a else None,
            "why": a["why"] if a else None, "mismatches": a["mismatches"] if a else [],
            "also": [{"posting_id": q["posting_id"], "title": q["title"], "url": q["url"]} for q, *_ in also]}


# ------------------------------------------------------------------------------------- reports

def render_report(r):
    L = [f"# Jobs score {r['run_id']} ({r['run_date']})", "",
         f"Status: **{r['status']}**. Profile version {r['profile']['version']} ({r['profile']['id']}). "
         f"New open postings since {r['since'] or 'the start'}: {r['candidates']}.", "",
         f"Passed the filters: {r['passed_filters']}. Sent to the scoring call: {r['sent']} "
         f"({r['already_scored']} already answered). Kept: {r['kept']} ({r.get('kept_distinct', r['kept'])} once "
         f"repeats of the same job are shown together). Not kept: {r['not_kept']}.", ""]
    for c in r["calls"]:
        u = c.get("usage") or {}
        L.append(f"- Call: {c['jobs']} jobs, " + (f"failed: {c['error']}" if c.get("error") else
                 f"{'cached' if c['cached'] else 'answered'}, {u.get('input_tokens', 0):,} tokens in, "
                 f"{u.get('output_tokens', 0):,} out, {u.get('duration_s', 0)} s, list price ${u.get('cost_usd_list_price', 0)}"))
    L += ["", "## Cuts by stage", "", "| Stage | Jobs |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in sorted(r["stages"].items(), key=lambda kv: -kv[1])]
    for name, title in (("new", "The list"), ("watchlist", "Watchlist")):
        L += ["", f"## {title}", ""]
        for e in r["list"][name]:
            L.append(f"{e['rank']}. **{e['score'] if e['score'] is not None else '–'}** {e['title']} — {e['company']} "
                     f"({'; '.join(e['locations'][:2])}) · {e['points']} pts · {e['url'] or ''}")
            if e["why"]:
                L.append(f"   {e['why']}")
        if not r["list"][name]:
            L.append("(none)")
    detail = [c for c in r["cuts"] if c["stage"] not in BIG_STAGES]
    if detail:
        L += ["", "## Every other cut", "", "| Stage | Job | Reason |", "|---|---|---|"]
        for c in sorted(detail, key=lambda c: (c["stage"], -c.get("score", 0) if c.get("score") else 0, c["company"])):
            job = f"{c['title']} — {c['company']}".replace("|", "\\|")
            sc = f" (score {c['score']})" if c.get("score") is not None else ""
            L.append(f"| {c['stage']} | {job[:110]} | {c['reason'].replace('|', '/')[:160]}{sc} |")
    return "\n".join(L) + "\n"


def today(cfg, list_date=None, show_all=False):
    """The morning list as terminal text."""
    store = JobsStore(state_path(cfg))
    if list_date is None:
        row = store.one("SELECT MAX(list_date) FROM daily_list")
        list_date = row[0] if row else None
    if not list_date:
        store.close()
        return "no list yet (debrief jobs score)"
    rows = store.q("SELECT * FROM daily_list WHERE list_date=? ORDER BY section, rank", (list_date,))
    run_id = rows[0]["run_id"] if rows else None
    rep = store.one("SELECT report FROM runs WHERE run_id=?", (run_id,)) if run_id else None
    report = json.loads(rep[0]) if rep else {}
    L = [f"Jobs for {list_date} · {report.get('kept', '?')} kept of {report.get('sent', '?')} scored · "
         f"profile v{report.get('profile', {}).get('version', '?')} · {report.get('status', '?')}"]
    entries = report.get("list", {})
    sections = [("new", "New"), ("watchlist", "Watchlist")]
    if show_all:
        allkept = [c for c in report.get("cuts", []) if c["stage"] == "beyond N"]
        sections.append(("beyond", f"Also kept ({len(allkept)})"))
        entries = {**entries, "beyond": [{"rank": i + 1, **c, "locations": [], "url": store.posting(c["posting_id"])["url"],
                                          "why": None, "mismatches": []} for i, c in enumerate(allkept)]}
    for key, title in sections:
        es = entries.get(key) or []
        if not es:
            continue
        L += ["", f"{title}:"]
        for e in es:
            p = store.posting(e["posting_id"]) or {}
            pay = score.pay_line((p.get("data") or {}).get("pay"))
            where = "; ".join((e.get("locations") or p.get("locations") or [])[:2])
            sc = e.get("score")
            L.append(f"{e['rank']:3}. [{sc if sc is not None else '–':>2}] {e['title']} — {e['company']}"
                     + (f" · {where}" if where else "") + (f" · {pay}" if pay else ""))
            if e.get("why"):
                L.append(f"       {e['why']}")
            if p and not store.texts_for(e["posting_id"]):
                L.append("       (no description: scored on the title alone)")
            if e.get("mismatches"):
                L.append(f"       ! {'; '.join(e['mismatches'])}")
            if e.get("url"):
                L.append(f"       {e['url']}")
            for o in e.get("also") or []:
                L.append(f"       also listed as \"{o['title']}\": {o['url']}")
    store.close()
    return "\n".join(L)
