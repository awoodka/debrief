"""The jobs scoring call (docs/jobs/PLAN.md, "M2 design"): Debrief's triage call pattern. One
`claude -p` call (settings score.model) with no tools, an explicit system prompt
(prompts/jobs_score_v1.md), no settings, MCP servers or slash commands, in an empty directory.

The user message is the profile and the jobs, as JSON inside tags with "</" escaped. For each job
the model answers keep (Alex could apply: Revision 3's rule), score (1-10 relevance), why (one
line) and mismatches. Answers are cached in <data>/jobs/cache/score/<key>.json, keyed by
everything the call sees, so re-running on the same data replays the answer for free. A failed
call is retried once; then ScoringError, and the caller ranks by points instead.
"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone

from .. import sanitize
from ..config import REPO
from ..triage.score import ScoringError, argv_for, parse_response, run_claude

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "keep", "score", "why", "mismatches"],
        "properties": {
            "id": {"type": "string"},
            "keep": {"type": "boolean"},
            "score": {"type": "integer", "minimum": 1, "maximum": 10},
            "why": {"type": "string"},
            "mismatches": {"type": "array", "items": {"type": "string"}},
        }}}},
}

MONEY = re.compile(r"[$€£]\s?\d[\d,.]*\s*[kKmM]?(?:\s*(?:-|–|to)\s*[$€£]?\s?\d[\d,.]*\s*[kKmM]?)?"
                   r"(?:\s*(?:/|per|an?)\s*(?:yr|year|annum|hr|hour))?(?:\s*\+\s*(?:equity|bonus)(?:\s*\+\s*(?:equity|bonus))?)?",
                   re.I)


def no_pay(text):
    """Text with money amounts taken out, so pay can't reach the model (Alex: not a scoring factor)."""
    return MONEY.sub("[pay]", text or "")


QUAL_HEADS = re.compile(r"(minimum qualifications|basic qualifications|required qualifications|requirements|"
                        r"qualifications|what you(?:'|’)ll need|what we(?:'|’)re looking for|you might thrive|"
                        r"about you|who you are|you have|you bring|you will need|you should have)", re.I)


def qualifications(text, max_chars):
    """The part of a description that says what the job asks for: from the first qualifications
    heading when there is one, else from the start."""
    if not text:
        return ""
    m = QUAL_HEADS.search(text)
    start = m.start() if m and m.start() > 0 else 0
    return sanitize.text(no_pay(text[start:]), max_chars)


def pay_line(pay):
    if not pay:
        return None
    if pay.get("summary"):
        return sanitize.text(pay["summary"], 80)
    lo, hi = pay.get("min"), pay.get("max")
    cur = pay.get("currency") or ""
    per = f" a {pay['period']}" if pay.get("period") else ""
    nums = " to ".join(f"{v:,.0f}" for v in (lo, hi) if isinstance(v, (int, float)) and v)
    return f"{nums} {cur}{per}".strip() or None


def profile_view(profile):
    """What the model reads about Alex: everything that bears on fit, constraints included."""
    f = profile["filters"]
    return {
        "degree": profile["degree"], "coursework": profile["coursework"], "skills": profile["skills"],
        "position_types": [p["name"] for p in profile["position_types"]],
        "projects": [{"name": p["name"], "summary": p["summary"]} for p in profile["projects"]],
        "interests": profile["interests"], "watched_companies": [c["name"] for c in profile["companies"]],
        "wants": profile["wants"],
        "constraints": {"places": f["locations"]["places"], "remote_us_ok": f["locations"]["remote_us"],
                        "max_years_experience": f["max_years"], "work_authorization": f["work_authorization"],
                        "clearance": f["clearance"], "full_time_only": f["full_time_only"],
                        "new_grad_only": f["new_grad_only"]},
    }


def job_view(jid, posting, text, sources, early_sources, max_chars):
    d = posting.get("data") or {}
    origin = sorted({"a new-grad list" if s in early_sources else "the company's board" if s in ("watchlist", "board-finder")
                     else "Hacker News" if s.startswith("hn-") else "a job site" for s in sources})
    out = {"id": jid, "title": sanitize.text(no_pay(posting["title"]), 200), "company": sanitize.text(posting["company"], 120),
           "locations": [sanitize.text(x, 80) for x in posting.get("locations") or []][:6],
           "posted": (posting.get("published_at") or posting.get("first_seen_at") or "")[:10], "from": origin,
           "qualifications": qualifications(text, max_chars)}
    # Pay is never shown to the model (Alex, 2026-11-04: not a scoring factor); code alone applies
    # the minimum-pay filter.
    if d.get("employment"):
        out["employment"] = sanitize.text(d["employment"], 40)
    if d.get("remote") is True or d.get("work_model") == "remote":
        out["remote"] = True                      # a source says so, whatever city it names
    return out


def _escape(s):
    return s.replace("</", "<\\/")


def render(profile_doc, jobs):
    lines = "\n".join(_escape(json.dumps(j, ensure_ascii=False)) for j in jobs)
    return (f"<profile>\n{_escape(json.dumps(profile_doc, ensure_ascii=False))}\n</profile>\n\n"
            f"<jobs>\n{lines}\n</jobs>\n")


def system_prompt(s):
    return (REPO / s["prompt"]).read_text()


def prompt_version(s, system):
    return f"{s['prompt'].rsplit('/', 1)[-1].removesuffix('.md')}@{hashlib.sha256(system.encode()).hexdigest()[:8]}"


def validate(answer, ids, s):
    """{id: {keep, score, why, mismatches}} for every job, or ScoringError."""
    seen = {}
    for it in (answer or {}).get("items") or []:
        if isinstance(it, dict) and it.get("id") in ids and it["id"] not in seen:
            seen[it["id"]] = it
    missing = [i for i in ids if i not in seen]
    if missing:
        raise ScoringError(f"{len(missing)} of {len(ids)} jobs unanswered, e.g. {missing[0]}")
    out = {}
    for i in ids:
        it = seen[i]
        score = it.get("score")
        if not isinstance(score, int) or not 1 <= score <= 10:
            raise ScoringError(f"{i}: score {score!r} isn't 1-10")
        out[i] = {"keep": bool(it.get("keep")), "score": score, "why": sanitize.text(it.get("why"), s["why_max_chars"]),
                  "mismatches": [sanitize.text(m, 120) for m in (it.get("mismatches") or []) if str(m).strip()][:s["mismatch_max"]]}
    return out


def cache_key(model, effort, system, user):
    blob = json.dumps({"model": model, "effort": effort, "system": system, "user": user, "schema": SCHEMA},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def call(cache_dir, s, profile_doc, jobs, runner=run_claude, fresh=False, log=print):
    """Score `jobs` (from job_view). Returns {"answers", "usage", "cached", "attempts", "model",
    "prompt_version"}; raises ScoringError."""
    model, effort = s["model"], s.get("effort")
    system = system_prompt(s)
    user = render(profile_doc, jobs)
    key = cache_key(model, effort, system, user)
    ids = [j["id"] for j in jobs]
    meta = {"model": model, "prompt_version": prompt_version(s, system), "key": key[:16]}
    path = cache_dir / f"{key}.json"
    if path.exists() and not fresh:
        hit = json.loads(path.read_text())
        return {**meta, "answers": validate(hit["answer"], ids, s), "usage": hit["usage"], "cached": True,
                "attempts": hit["attempts"]}
    errors = []
    for attempt in (1, 2):
        try:
            answer, usage = parse_response(*runner(argv_for(model, system, effort, schema=SCHEMA), user, s["timeout_s"]))
            answers = validate(answer, ids, s)
        except ScoringError as e:
            errors.append(str(e))
            log(f"scoring attempt {attempt} failed: {e}")
            continue
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"key": key, **meta, "answer": answer, "usage": usage, "attempts": attempt,
                                   "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
                                  ensure_ascii=False, indent=1))
        os.replace(tmp, path)
        return {**meta, "answers": answers, "usage": usage, "cached": False, "attempts": attempt}
    raise ScoringError("; ".join(errors))
