"""`debrief jobs score`: the morning list; `debrief jobs catchup`: the one-time catch-up list; and
`debrief jobs today`, which prints either (docs/jobs/PLAN.md, "M2 design" and "Revision 5").

1. The pool: every open posting not yet highlighted on an earlier date (the highlighted table).
   A rerun on the same date replaces that date's rows, so it gives the same list. "New tonight"
   are the pool's postings first seen since the last scoring run (meta score_cursor).
2. Filters (filters.py), with the job's description when there is one, on the whole pool every
   night: a job leaves when it closes, ages past max_age_days or fails a filter. Full cut detail is
   kept for new postings; carried-over ones are only counted.
3. Points (prescore.py) for jobs with no answer for this profile version: ranking.threshold, then
   at most ranking.max_candidates answers a night (tonight's fresh jobs first, the watchlist first
   among them, then the rest). A job over the cap stays in the pool and is asked another night.
   no_cap lifts the cap.
4. The scoring call (score.py) for those jobs, in chunks of score.chunk_size. If it fails, the jobs
   it didn't answer wait below the list as "not scored" and the cursor stays put, so the next run
   asks again and they are still new.
5. The list: kept jobs by score plus ranking.fresh_bonus for recently posted ones (ties: score, no
   required years before some, a read description before none, points, newest, id). A job kept only
   by filters.locations.other_us (in the US, outside every area) ranks, lists and shows as if it scored
   ranking.other_us_penalty less, and one whose description requires 1+ years of experience
   (filters.min_years) as if it scored at most YEARS_CAP (adjusted); its entry keeps the model's own
   score and says why. Each lands in one place
   (PLACES): the list ("new") holds only tonight's fresh jobs (first seen since the last run,
   posted within ranking.fresh_days) scoring at least
   ranking.min_score, at most ranking.per_company from one company (items.company_key, so "Bask"
   and "Bask Health" are one) and ranking.top_n in all. With ranking.repeat_days, a company on the
   list in that many earlier nights waits unless the job scores REPEAT_PASS or more. With
   ranking.nearby (area names from filters.locations.places), a fresh job tonight in one of those
   areas that scores at least ranking.nearby_min but under min_score goes to "nearby", shown after
   the list as worth an early application, under the same rules (LANES); per_company counts the
   list and nearby together, and top_n bounds each. Every other kept job goes to the collapsed list
   below them, as a cut whose stage names its place, so it stays in the pool. "watchlist" shows
   tonight's fresh scored and kept watchlist jobs, wherever they landed; one it shows is not also
   below. Every job on the list, nearby or the watchlist, and every copy shown under it, is
   highlighted. Saved in daily_list; the report, runs/<date>/score-<run>.json and .md, has the cuts
   with their stage and reason.

The catch-up (mode "catchup") is the same pass over the same pool with no cap and no threshold,
watchlist companies exempt from the age limit, and every kept job listed in one section named after
it ("catchup", or a name such as "older"), with no top_n and no company limit. since replaces the
age limit with "posted on or after this date" (the older-roles catch-up). It doesn't move the cursor.
"""
import re
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple

from ..store import dumps
from ..triage.score import ScoringError, run_claude
from . import filters, prescore, profile as profiles, score
from .collect import early_sources, startup_sources, trusted_sources
from .items import company_key, core_title
from .night import DAILY, age_days, fresh, posted, tonight
from .store import JobsStore, state_path

BIG_STAGES = ("title", "place", "age")       # counted in the .md report; listed in full only in the .json
NAME = re.compile(r"^[a-z][a-z0-9-]{0,30}$")


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_run_id(store, started, kind="score"):
    base = f"{kind}-" + started.strftime("%Y%m%dT%H%M%SZ")
    run_id, n = base, 1
    while store.one("SELECT 1 FROM runs WHERE run_id=?", (run_id,)):
        n += 1
        run_id = f"{base}-{n}"
    return run_id


def fresh_bonus(p, profile, now):
    table = (profile.get("ranking") or {}).get("fresh_bonus") or {}
    when = posted(p)
    if not table or not when:
        return 0
    days = age_days(when, now)
    return max((int(b) for d, b in table.items() if days <= int(d)), default=0)


def run(cfg, run_date=None, no_cap=False, dry_run=False, runner=run_claude, now=None, log=print):
    """The night's morning list."""
    return _pass(cfg, "daily", run_date, no_cap, dry_run, runner, now, log)


def catchup(cfg, run_date=None, dry_run=False, runner=run_claude, now=None, log=print, since=None, name="catchup"):
    """A one-time catch-up list: every kept job in today's pool, in its own section `name`. since (a
    date): jobs posted on or after it, instead of the profile's age limit."""
    if not NAME.match(name or "") or name in DAILY:
        raise SystemExit(f"a catch-up name is a short lowercase word, not {DAILY}: {name!r}")
    return _pass(cfg, "catchup", run_date, True, dry_run, runner, now, log, since=since, name=name)


def _pass(cfg, mode, run_date, no_cap, dry_run, runner, now, log, since=None, name="catchup"):
    started = now or datetime.now(timezone.utc)
    run_date = run_date or started.astimezone().date().isoformat()
    version, prof = profiles.latest(cfg)
    if prof is None:
        raise SystemExit("no profile saved (debrief jobs profile save FILE)")
    pid = profiles.profile_id(prof)
    ranking = profiles.ranking(prof)
    s = cfg.settings["score"]
    daily = mode == "daily"
    kind = "score" if daily else "catchup"
    sections = DAILY if daily else (name,)
    posted_since = since                                   # `since` is the nightly cursor below
    since_at = f"{posted_since}T00:00:00Z" if posted_since else None
    store = JobsStore(state_path(cfg))
    run_id = _new_run_id(store, started, kind)
    E, trusted = early_sources(cfg), trusted_sources(cfg)
    no_years = cfg.settings["level"]["no_years_sign"]
    S = startup_sources(cfg) if cfg.settings["level"]["startup_sign"] else None
    startups = {company_key(c["name"]) for c in prof["companies"] if profiles.company_kind(c) == "startup"}

    # 1. the pool, and what's new tonight
    since = store.score_since(run_date)
    rows = store.pool(run_date, sections)
    new_ids = {r["posting_id"] for r in rows if not since or r["first_seen_at"] > since}
    newest_seen = max([since or ""] + [r["first_seen_at"] for r in rows if r["posting_id"] in new_ids]) or None
    report = {"kind": kind, "name": None if daily else name, "posted_since": posted_since, "run_id": run_id,
              "run_date": run_date, "started_at": _iso(started),
              "profile": {"version": version, "id": pid}, "since": since, "candidates": len(rows),
              "new": len(new_ids), "carried": len(rows) - len(new_ids),
              "no_cap": no_cap, "dry_run": dry_run, "cuts": [], "calls": []}
    stages = Counter()

    def cut(p, stage, reason, points=None, a=None):
        stages[stage] += 1
        if not daily or p["posting_id"] in new_ids or stage in LIST_STAGES:
            report["cuts"].append(_cut(p, stage, reason, points, a))

    log(f"{len(rows)} open postings not yet highlighted: {len(new_ids)} new since {since or 'the start'}, "
        f"{len(rows) - len(new_ids)} from earlier nights")

    # 2-3. filters, answers, points
    passed = []
    for r in rows:
        p = store.posting(r["posting_id"])
        text = store.texts_for(p["posting_id"])
        srcs = store.posting_sources(p["posting_id"])
        watched = not daily and prescore.watched(p, prof)
        skip = ("age",) if watched or since_at else ()
        startup = S is not None and (bool(srcs & S) or company_key(p["company"]) in startups
                                     or filters.small_company((p.get("data") or {}).get("company_size")))
        v = filters.check(p, prof, started, text=text, listed_early=bool(srcs & E), listed_trusted=bool(srcs & trusted),
                          no_years=no_years, skip=skip, startup=startup)
        if not v.ok:
            cut(p, v.stage, v.reason)
            continue
        if since_at and not watched and (posted(p) or "") < since_at:
            cut(p, "age", f"posted {(posted(p) or '?')[:10]}, before {posted_since}")
            continue
        pts, parts = prescore.points(p, prof, started, text=text, facts=v.facts, sources=srcs, early_sources=E)
        passed.append((p, pts, parts, text, srcs, v.facts))
    answers = {p["posting_id"]: store.score_for(p["posting_id"], pid) for p, *_ in passed}
    unanswered = [x for x in passed if answers[x[0]["posting_id"]] is None]
    if no_cap:
        cap = 0
    else:
        left = ranking["max_candidates"] - store.answered_on(run_date, pid)
        cap = left if left > 0 else -1
    first = {p["posting_id"] for p, *_ in passed if tonight(p, since, ranking, started)}
    sent, over = prescore.select(unanswered, prof, cap=cap, first=first, threshold=daily,
                                       read={x[0]["posting_id"] for x in unanswered if x[3]})
    for (p, pts, *_), why in over:
        cut(p, "threshold" if why.startswith("below") else "cap", why, pts)
    report["passed_filters"], report["sent"] = len(passed), len(sent)
    report["already_scored"] = len(passed) - len(unanswered)
    log(f"{len(passed)} passed the filters ({report['already_scored']} already answered); {len(sent)} go to the "
        f"scoring call" + (f" ({len(over)} wait: the threshold or the cap)" if over else ""))

    # 4. the call
    status = "ok"
    if sent and not dry_run:
        status = _score(store, cfg, s, prof, sent, answers, pid, run_id, started, E, report, runner, log)
    elif dry_run:
        status = "dry_run"

    # 5. the list
    sent_ids = {x[0]["posting_id"] for x in sent}
    unread = {p["posting_id"] for p, _, _, text, *_ in passed if not text}
    kept, not_kept = [], []
    for p, pts, _, _, _, facts in passed:
        a = answers.get(p["posting_id"])
        if a is None:
            if status in ("fallback", "dry_run") and p["posting_id"] in sent_ids:
                kept.append((p, pts, None))
            continue
        within = None if a["keep"] else years_within(a, prof["filters"]["max_years"])
        if a["keep"] or within is not None:
            kept.append((p, pts, adjusted({**a, "keep": True}, facts.get("min_years") or within, facts.get("outside"),
                                          ranking["other_us_penalty"])))
        else:
            not_kept.append((p, pts, a))
            cut(p, "not kept", "; ".join(a["mismatches"]) or a["why"], pts, a)

    def order(x):
        p, pts, a = x
        sc = a["score"] if a else 0
        bonus = fresh_bonus(p, prof, started) if a else 0
        return (-(sc + bonus), -sc, bool(a and a.get("years")), p["posting_id"] in unread, -pts, -prescore._ts(posted(p)),
                p["posting_id"])
    kept.sort(key=order)
    groups = collapse(kept)
    if daily:
        jobs = [_job(g, new_ids, prof, ranking, started) for g in groups]
        recent = _recent(store, run_date, ranking["repeat_days"])
        watched = [j.tonight and j.fresh and j.score is not None and prescore.watched(g[0][0], prof)
                   for g, j in zip(groups, jobs)]
        placed = [(g, "watchlist" if w and where not in LANE_NAMES else where)
                  for g, where, w in zip(groups, place(jobs, ranking, recent), watched)]
        watch = [g for g, w in zip(groups, watched) if w]
        listed = ("new", "nearby") if ranking["nearby"] else ("new",)
    else:
        placed = [(g, name) for g in groups]
        listed = (name,)
    shown = {k: [g for g, where in placed if where == k] for k in listed}
    for ((p, pts, a), _), where in placed:
        if where in REASONS:
            cut(p, where, REASONS[where].format(seen=_day(p["first_seen_at"]), posted=_day(posted(p)),
                                                score=a["score"] if a else None, company=p["company"], **ranking), pts, a)
    held = Counter(company_key(g[0][0]["company"]) for g, where in placed if where == "company limit")
    last = {company_key(g[0][0]["company"]): (k, i) for k in listed for i, g in enumerate(shown[k])}
    for (p0, *_), same in groups:
        for p, pts, a in same:
            cut(p, "same job", f"shown with {p0['posting_id']} ({p0['title']})", pts, a)
    report["kept"], report["kept_distinct"], report["not_kept"] = len(kept), len(groups), len(not_kept)

    def entries(gs, section=None):
        out = []
        for i, g in enumerate(gs):
            c = company_key(g[0][0]["company"])
            more = held[c] if last.get(c) == (section, i) else 0
            out.append(_entry(i + 1, *g[0], also=g[1], more=more, new=g[0][0]["posting_id"] in new_ids,
                              bonus=fresh_bonus(g[0][0], prof, started) if g[0][2] else 0))
        return out
    report["list"] = {k: entries(shown[k], k) for k in listed}
    if daily:
        report["list"]["watchlist"] = entries(watch)
    report["status"] = status
    report["stages"] = dict(stages)
    report["finished_at"] = _iso(datetime.now(timezone.utc))

    if not dry_run:
        rows_out, marks = [], []
        for section, es in report["list"].items():
            for e in es:
                rows_out.append((section, e["rank"], e["posting_id"], e["score"], e["points"]))
                marks += [(e["posting_id"], section)] + [(o["posting_id"], section) for o in e["also"]]
        with store.transaction():
            store.save_list(run_date, run_id, rows_out, sections=sections)
            store.save_highlighted(run_date, run_id, marks, sections)
            if daily and status == "ok":
                if store.get_meta("score_cursor_date") != run_date:
                    store.set_meta("score_cursor_prev", since or "")
                store.set_meta("score_cursor", newest_seen or "")
                store.set_meta("score_cursor_date", run_date)
            store.start_run(run_id, kind, run_date, report["started_at"])
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
            jobs.append(score.job_view(jid, p, text, srcs, E, s["text_max_chars"], facts.get("min_years")))
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
        key = (company_key(x[0]["company"]), core) if core else x[0]["posting_id"]
        if key in groups:
            groups[key][1].append(x)
        else:
            groups[key] = (x, [])
            order.append(key)
    return [groups[k] for k in order]


class Job(NamedTuple):
    tonight: bool
    fresh: bool
    score: int | None
    company: str
    in_nearby_area: bool = False


@dataclass
class Tally:
    ranking: dict
    recent: frozenset = frozenset()
    by_lane: Counter = field(default_factory=Counter)
    by_company: Counter = field(default_factory=Counter)


REPEAT_PASS = 7
YEARS_CAP = 5
NOTES_ON_SCORE = ("place_note", "years_note")
ADJUSTED = ("model_score", "years", *NOTES_ON_SCORE)


def years_within(a, max_years):
    """The years a dropped job's reason (its first mismatch) asks for, when they are within max_years, else
    None. The scoring call sometimes drops "1-3 years" although the profile allows 1; Alex keeps such a job,
    ranked as YEARS_CAP at most."""
    n = filters.min_years((a.get("mismatches") or [""])[0])
    return n if n is not None and n <= max_years else None


def adjusted(a, years, outside, penalty):
    """The answer as the list ranks it. A job outside Alex's places (outside: its US places, from
    filters.check) ranks penalty lower, never under 1; then one that requires years ranks as YEARS_CAP
    at most. The model's own score stays as model_score, with a note for each change."""
    sc, notes = a["score"], {}
    if outside and penalty:
        sc = max(1, sc - penalty)
        notes["place_note"] = f"Outside your places ({'; '.join(outside[:2])}): ranked {penalty} lower."
    if years:
        sc = min(sc, YEARS_CAP)
        notes |= {"years": years, "years_note": f"Requires {years} year{'' if years == 1 else 's'} of experience: "
                                                f"ranked as {YEARS_CAP} at most."}
    return {**a, "score": sc, "model_score": a["score"], **notes} if notes else a


def score_notes(e):
    """Why an entry ranks other than as the model scored it, one sentence each."""
    return [e[k] for k in NOTES_ON_SCORE if e.get(k)]


LANES = (
    ("new", lambda j, r: j.score >= r["min_score"]),
    ("nearby", lambda j, r: j.in_nearby_area and j.score >= r["nearby_min"]),
)
LANE_NAMES = tuple(name for name, _ in LANES)


def lane(j, ranking):
    """Where a scored job could be shown, by its score: the first lane it fits, or None."""
    return next((name for name, fits in LANES if fits(j, ranking)), None)


PLACES = (
    ("from an earlier night", lambda j, t: not j.tonight, "From an earlier night: first found {seen}."),
    ("posted earlier", lambda j, t: not j.fresh, "Posted earlier: found tonight, posted {posted}."),
    ("not scored", lambda j, t: j.score is None, "Not scored yet: the scoring call didn't answer."),
    ("below the floor", lambda j, t: lane(j, t.ranking) is None, "Below the floor: scored {score}, under {min_score}."),
    ("listed this week", lambda j, t: j.company in t.recent and j.score < REPEAT_PASS,
     f"Listed this week: {{company}} was on the list in the last {{repeat_days}} nights, and this scored under {REPEAT_PASS}."),
    ("company limit", lambda j, t: bool(t.ranking.get("per_company")) and t.by_company[j.company] >= t.ranking["per_company"],
     "Company limit: {company} already has {per_company} on the list."),
    ("beyond N", lambda j, t: t.by_lane[lane(j, t.ranking)] >= t.ranking["top_n"], "Beyond {top_n}: the list holds at most {top_n}."),
)
REASONS = {name: reason for name, _, reason in PLACES}
LIST_STAGES = ("not kept", *REASONS, "same job")


def _job(group, new_ids, prof, ranking, now):
    p, _, a = group[0]
    in_nearby_area = not set(ranking["nearby"]).isdisjoint(filters.areas(p["locations"], prof))
    return Job(p["posting_id"] in new_ids, fresh(p, ranking, now), a["score"] if a else None, company_key(p["company"]),
               in_nearby_area)


def _recent(store, run_date, days):
    if not days:
        return frozenset()
    first = (date.fromisoformat(run_date) - timedelta(days=days)).isoformat()
    return frozenset(company_key(c) for c in store.listed_companies(first, run_date))


def place(jobs, ranking, recent=frozenset()):
    """Each job's place, in order: the first of PLACES that holds, else its lane."""
    t, out = Tally(ranking, recent), []
    for j in jobs:
        where = next((name for name, holds, _ in PLACES if holds(j, t)), None)
        if where is None:
            where = lane(j, ranking)
            t.by_lane[where] += 1
            t.by_company[j.company] += 1
        out.append(where)
    return out


def below(report):
    """The collapsed list: kept jobs not on the list, best score first (ties: the list's order)."""
    return sorted((c for c in report.get("cuts") or [] if c["stage"] in REASONS), key=lambda c: -(c.get("score") or 0))


def _cut(p, stage, reason, points=None, a=None):
    out = {"posting_id": p["posting_id"], "company": p["company"], "title": p["title"], "stage": stage,
           "reason": reason}
    if points is not None:
        out["points"] = points
    if a:
        out |= {k: a[k] for k in ("score", *ADJUSTED) if k in a}
    return out


def _entry(rank, p, pts, a, also=(), more=0, new=True, bonus=0):
    """more: the company's jobs held back by ranking.per_company (on its last entry only); new:
    first seen since the last run (else carried over); bonus: the freshness bonus in its rank."""
    return {"rank": rank, "posting_id": p["posting_id"], "company": p["company"], "title": p["title"],
            "locations": p["locations"], "url": p["url"], "points": pts, "score": a["score"] if a else None,
            "why": a["why"] if a else None, "mismatches": a["mismatches"] if a else [],
            "also": [{"posting_id": q["posting_id"], "title": q["title"], "url": q["url"]} for q, *_ in also],
            "more": more, "posted": (posted(p) or "")[:10], "new": new, "bonus": bonus,
            **{k: a[k] for k in ADJUSTED if a and k in a}}


# ------------------------------------------------------------------------------------- reports

def display_title(title, profile=None):
    """How a title is printed. A Hacker News "Who is hiring" post keeps its whole first line as its
    title ("Mechanize | San Francisco, CA (Onsite) | Junior Engineer | ..."), because the filters
    read all of it; the list shows just its role: the segment a position type matches, else the
    second one. A job site's title with a bar in it ("Forward Deployed Engineer | Graduates Considered") matches
    on its first segment and keeps it. Display only: storage, filters, matching and the model's input are
    unchanged."""
    if not title or " | " not in title:
        return title
    segs = [x.strip() for x in title.split("|") if x.strip()]
    if len(segs) < 2:
        return title
    if profile:
        for seg in segs:
            if filters.title_fit(seg, profile)[0] is not None:
                return seg
    return segs[1]


NEARBY = "Nearby, worth an early application"
TITLES = {"new": "The list", "nearby": NEARBY, "watchlist": "Watchlist", "catchup": "Catch-up", "older": "Older roles"}
SECTIONS = (("new", "New"), ("nearby", NEARBY), ("watchlist", "Watchlist"))
NOTES = {"nearby": "New tonight in a nearby area, scored under the list's floor."}
BELOW = "Also kept, below the list"


def header(report):
    """How many jobs are new tonight on the list, how many nearby (when the profile asks for that
    section), and how many are kept below them."""
    lists = report.get("list") or {}
    n = sum(1 for e in lists.get("new") or [] if e.get("new", True))
    near = f" · {len(lists['nearby'])} nearby" if "nearby" in lists else ""
    return f"{n} new tonight{near} · {len(below(report))} below"


def _day(iso):
    """"Nov 3" from "2026-11-03"."""
    try:
        d = datetime.strptime((iso or "")[:10], "%Y-%m-%d")
    except ValueError:
        return ""
    return f"{d:%b} {d.day}"


def render_report(r):
    what = "catch-up" if r["kind"] == "catchup" else "score"
    L = [f"# Jobs {what} {r['run_id']} ({r['run_date']})", "",
         f"Status: **{r['status']}**. Profile version {r['profile']['version']} ({r['profile']['id']}). "
         f"Pool: {r['candidates']} open postings not yet highlighted ({r.get('new', r['candidates'])} new since "
         f"{r['since'] or 'the start'}, {r.get('carried', 0)} from earlier nights).", "",
         f"Passed the filters: {r['passed_filters']} ({r['already_scored']} already answered). Asked in the scoring "
         f"call: {r['sent']}. Kept: {r['kept']} ({r.get('kept_distinct', r['kept'])} once repeats of the same job are "
         f"shown together). Not kept: {r['not_kept']}.", ""]
    for c in r["calls"]:
        u = c.get("usage") or {}
        L.append(f"- Call: {c['jobs']} jobs, " + (f"failed: {c['error']}" if c.get("error") else
                 f"{'cached' if c['cached'] else 'answered'}, {u.get('input_tokens', 0):,} tokens in, "
                 f"{u.get('output_tokens', 0):,} out, {u.get('duration_s', 0)} s, list price ${u.get('cost_usd_list_price', 0)}"))
    L += ["", "## Cuts by stage", "", "| Stage | Jobs |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in sorted(r["stages"].items(), key=lambda kv: -kv[1])]
    for name, es in r["list"].items():
        L += ["", f"## {TITLES.get(name, name)}", ""]
        for e in es:
            bonus = f" +{e['bonus']}" if e.get("bonus") else ""
            L.append(f"{e['rank']}. **{e['score'] if e['score'] is not None else '–'}{bonus}** {display_title(e['title'])} — {e['company']} "
                     f"({'; '.join(e['locations'][:2])}) · posted {e.get('posted') or '?'} · {e['points']} pts · {e['url'] or ''}")
            if e["why"]:
                L.append(f"   {e['why']}")
            if score_notes(e):
                L.append(f"   {' '.join(score_notes(e))} The model scored {e['model_score']}.")
            if e.get("more"):
                L.append(f"   also kept at {e['company']}: {e['more']} more (company limit)")
        if not es:
            L.append("(none)")
    detail = [c for c in r["cuts"] if c["stage"] not in BIG_STAGES]
    if detail:
        L += ["", "## Every other cut" + (" (new postings, and the list's own cuts)" if r["kind"] == "score" else ""), "",
              "| Stage | Job | Reason |", "|---|---|---|"]
        for c in sorted(detail, key=lambda c: (c["stage"], -c.get("score", 0) if c.get("score") else 0, c["company"])):
            job = f"{c['title']} — {c['company']}".replace("|", "\\|")
            sc = f" (score {c['score']})" if c.get("score") is not None else ""
            L.append(f"| {c['stage']} | {job[:110]} | {c['reason'].replace('|', '/')[:160]}{sc} |")
    return "\n".join(L) + "\n"


def today(cfg, list_date=None, catchup=None):
    """The morning list with the jobs kept below it, or with catchup (True for "catchup", or a
    catch-up's name such as "older") that catch-up list, as terminal text."""
    store = JobsStore(state_path(cfg))
    if catchup is True:
        catchup = "catchup"
    if catchup:
        if list_date is None:
            list_date = store.one("SELECT MAX(list_date) FROM daily_list WHERE section=?", (catchup,))[0]
        row = store.one("SELECT run_id FROM daily_list WHERE list_date=? AND section=? LIMIT 1", (list_date, catchup))
        rep = store.one("SELECT report FROM runs WHERE run_id=?", (row[0],)) if row else None
        report = json.loads(rep[0]) if rep else {}
    else:
        list_date = list_date or next(iter(store.list_dates()), None)
        report = store.list_report(list_date) if list_date else {}
        list_date = list_date if report else None
    if not list_date:
        store.close()
        return f"no {catchup} list yet (debrief jobs catchup)" if catchup else "no list yet (debrief jobs score)"
    prof = profiles.latest(cfg)[1]
    entries = report.get("list", {})
    tail = f"profile v{report.get('profile', {}).get('version', '?')} · {report.get('status', '?')}"
    if catchup:
        title = TITLES.get(catchup, f"Catch-up: {catchup}")
        since = f" · posted since {_day(report['posted_since'])}" if report.get("posted_since") else ""
        L = [f"{title} for {list_date} · {len(entries.get(catchup) or [])} kept jobs, best first{since} · {tail}"]
        sections = [(catchup, title)]
    else:
        kept = below(report)
        L = [f"Jobs for {list_date} · {header(report)} · {tail}"]
        from .coverage import summary_line
        line = summary_line(store, list_date)
        if line:
            L.append(line)
        sections = [*SECTIONS, ("below", f"{BELOW} ({len(kept)})")]
        entries = {**entries, "below": [{**c, "rank": i + 1} for i, c in enumerate(kept)]}
    for key, title in sections:
        es = entries.get(key) or []
        if not es:
            continue
        L += ["", f"{title}:"] + ([f"  {NOTES[key]}"] if key in NOTES else [])
        for e in es:
            p = store.posting(e["posting_id"]) or {}
            pay = score.pay_line((p.get("data") or {}).get("pay"))
            where = "; ".join((e.get("locations") or p.get("locations") or [])[:2])
            when = _day(e.get("posted") or (posted(p) if p else None))
            sc = e.get("score")
            L.append(f"{e['rank']:3}. [{sc if sc is not None else '–':>2}] {display_title(e['title'], prof)} — {e['company']}"
                     + (f" · {where}" if where else "") + (f" · {pay}" if pay else "") + (f" · posted {when}" if when else ""))
            if e.get("why"):
                L.append(f"       {e['why']}")
            for note in score_notes(e):
                L.append(f"       {note}")
            if e.get("stage"):
                L.append(f"       {e['reason']}")
            if p and not store.texts_for(e["posting_id"]):
                L.append("       (no description: scored on the title alone)")
            if e.get("mismatches"):
                L.append(f"       ! {'; '.join(e['mismatches'])}")
            if e.get("url") or p.get("url"):
                L.append(f"       {e.get('url') or p['url']}")
            for o in e.get("also") or []:
                L.append(f"       also listed as \"{o['title']}\": {o['url']}")
            if e.get("more"):
                L.append(f"       also kept at {e['company']}: {e['more']} more (below)")
    store.close()
    return "\n".join(L)
