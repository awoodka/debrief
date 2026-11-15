"""`debrief brief check`: is the skill's draft (runs/<date>/brief.draft.json) fit to publish?

The skill's Stop hook runs this, and every error goes back to the model as written, so each one says
where it is and what to do. Errors:
  - the draft doesn't match schemas/brief_draft.schema.json, or is for another date
  - a story id that isn't in today's index (an index line's id, or one merged into it)
  - a quote that isn't verbatim in its story's detail file: a picked comment, the full text or the
    summary. Whitespace, curly quotes, dashes and markdown emphasis are normalized first, and "…" may
    stand for omitted words, as long as the pieces appear in order in one text.
  - a figure whose value isn't in those texts of its story, normalized the same way and not as part
    of a longer number ("7,403" isn't in "17,403")
  - a chart in a section the draft doesn't have, with fewer than 2 or more than 5 bars, or with a
    bar whose value (printed "7,403" or "7403") isn't in the texts of any story that section cites
  - a URL or a story id inside a prose field (links and ids come from the data at finalize)
  - more what_happened items than brief.what_happened.max, or fewer than its min (or than the number
    of stories, on a day with fewer)
  - a heading longer than brief.heading_max_chars
  - an article over brief.words.hard_max words
Warnings (reported, never blocking): an article under brief.words.min words (fine on a quiet day),
or over brief.words.max.
"""
import re

from ..triage.output import Validator
from .render import number

_URL = re.compile(r"https?://|\bwww\.\w")
_ID = re.compile(r"\b(?:arxiv|hf-model|hf-dataset|hf-space|gh-repo|gh-release|pkg|url|hn|lesswrong|alignment-forum)"
                 r":[A-Za-z0-9]")
_WORD = re.compile(r"\w[\w'\u2019-]*")
_NORM = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-",
                       "\u2014": "-", "\u00a0": " ", "*": None, "_": None, "`": None})
_ELLIPSIS = re.compile(r"…|\.\.\.")
CHART_BARS = (2, 5)


def norm(text):
    """Text as quotes are compared: plain quotes and dashes, no emphasis marks, single spaces."""
    lines = [ln.lstrip("> ") for ln in (text or "").translate(_NORM).splitlines()]
    return " ".join(" ".join(lines).split())


def quotable(detail):
    """[(kind, text, author, source, url)] a quote from this story may come from."""
    out = []
    for t in detail["discussion"]["threads"]:
        for c in t["top_comments"]:
            out.append(("comment", c["text"], c["author"], t["src"], t["url"]))
    for f in detail["full_text"]:
        out.append(("text", f["text"], None, None, f["url"]))
    if detail.get("summary"):
        out.append(("text", detail["summary"], None, None, (detail["artifact_urls"] or [None])[0]))
    return out


def find_quote(detail, quote):
    """The first source the quote is verbatim in, or None."""
    pieces = [norm(p) for p in _ELLIPSIS.split(quote)]
    pieces = [p for p in pieces if p]
    if not pieces:
        return None
    for src in quotable(detail):
        text, at = norm(src[1]), 0
        for p in pieces:
            i = text.find(p, at)
            if i < 0:
                break
            at = i + len(p)
        else:
            return src
    return None


def story_texts(detail):
    return [src[1] for src in quotable(detail)]


def has_number(value, texts):
    """Whether `value` ("7,403", "85%") is in one of `texts`, normalized as quotes are, and not as
    part of a longer number."""
    pattern = re.compile(rf"(?<!\d)(?<!\d[.,]){re.escape(norm(value))}(?![.,]?\d)")
    return any(pattern.search(norm(t)) for t in texts)


def printed(value):
    """The ways a source prints a chart's number: 7403 -> ["7,403", "7403"], 2.54 -> ["2.54"]."""
    return list(dict.fromkeys([number(value), number(value).replace(",", "")]))


def words(text):
    return len(_WORD.findall(text or ""))


def prose_fields(draft):
    """[(where, text)] for every field a reader reads as prose."""
    out = [("headline", draft.get("headline", "")), ("lead", draft.get("lead", ""))]
    out += [(f"what_happened[{i}]", e.get("text", "")) for i, e in enumerate(draft.get("what_happened", []))]
    for i, s in enumerate(draft.get("sections", [])):
        out += [(f"sections[{i}].heading", s.get("heading", "")), (f"sections[{i}].body", s.get("body", ""))]
        out += [(f"sections[{i}].split.{k}", v) for k, v in (s.get("split") or {}).items()]
    out += [(f"updates[{i}]", u.get("text", "")) for i, u in enumerate(draft.get("updates", []))]
    out += [(f"notes[{i}]", n) for i, n in enumerate(draft.get("notes", []))]
    if draft.get("figure"):
        out.append(("figure.caption", draft["figure"].get("caption", "")))
    if draft.get("chart"):
        out.append(("chart.caption", draft["chart"].get("caption", "")))
        out += [(f"chart.bars[{i}].label", b.get("label", "")) for i, b in enumerate(draft["chart"].get("bars", []))]
    return out


def article_words(draft):
    """The article's length: the lead, the sections (with their splits) and the updates."""
    texts = [draft.get("lead", "")]
    for s in draft.get("sections", []):
        texts += [s.get("body", ""), *(s.get("split") or {}).values()]
    texts += [u.get("text", "") for u in draft.get("updates", [])]
    return sum(words(t) for t in texts)


def referenced(draft):
    """[(where, story id)] for every story the draft points at."""
    out = []
    for i, e in enumerate(draft.get("what_happened", [])):
        out += [(f"what_happened[{i}].stories", sid) for sid in e.get("stories", [])]
    for i, s in enumerate(draft.get("sections", [])):
        out += [(f"sections[{i}].stories", sid) for sid in s.get("stories", [])]
        out += [(f"sections[{i}].quotes[{j}].story", q.get("story")) for j, q in enumerate(s.get("quotes", []))]
    out += [(f"updates[{i}].story", u.get("story")) for i, u in enumerate(draft.get("updates", []))]
    if draft.get("figure"):
        out.append(("figure.story", draft["figure"].get("story")))
    return out


def check_figure(day, figure):
    if figure["story"] in day.rep and not has_number(figure["value"], story_texts(day.detail(figure["story"]))):
        return [f"figure.value: {figure['value']!r} isn't in {figure['story']}'s detail file (a picked comment, the "
                f"full text or the summary); use the number exactly as the source prints it, or leave the figure out"]
    return []


def check_chart(day, chart, sections):
    n, (lo, hi) = len(chart["bars"]), CHART_BARS
    errors = [] if lo <= n <= hi else [f"chart.bars has {n} items; it needs {lo} to {hi}"]
    if chart["section"] > len(sections):
        return errors + [f"chart.section is {chart['section']}, but the draft's sections run 1 to {len(sections)}; "
                         f"use the number of the one the chart belongs in"]
    i = chart["section"] - 1
    cited = [t for sid in sections[i]["stories"] if sid in day.rep for t in story_texts(day.detail(sid))]
    for j, bar in enumerate(chart["bars"]):
        forms = printed(bar["value"])
        if not any(has_number(f, cited) for f in forms):
            errors.append(f"chart.bars[{j}].value: {' or '.join(forms)} isn't in any story section {chart['section']} "
                          f"cites (sections[{i}].stories); use numbers exactly as the source states them, or leave the "
                          f"chart out")
    return errors


def check(day, validator=None):
    """{"ok", "errors", "warnings", "words"} for the day's draft."""
    o = day.cfg.settings["brief"]
    errors, warnings = [], []
    try:
        draft = day.draft()
    except ValueError as e:
        return {"ok": False, "errors": [f"brief.draft.json isn't valid JSON: {e}"], "warnings": [], "words": 0}
    if draft is None:
        return {"ok": False, "errors": ["there's no brief.draft.json yet; write it (references/format.md)"],
                "warnings": [], "words": 0}
    v = (validator or Validator()).v["brief_draft"]
    for err in sorted(v.iter_errors(draft), key=lambda e: list(e.path))[:20]:
        where = "/".join(str(p) for p in err.path) or "(top)"
        errors.append(f"schema: {where}: {err.message[:200]}")
    if errors:                       # the rest assumes the shape is right
        return {"ok": False, "errors": errors, "warnings": [], "words": 0}

    if draft["date"] != day.date:
        errors.append(f"date is {draft['date']}, but this is the brief for {day.date}")
    for where, sid in referenced(draft):
        if sid not in day.rep:
            errors.append(f"{where}: {sid!r} isn't a story in today's index (use an index line's id)")
    for i, s in enumerate(draft["sections"]):
        for j, q in enumerate(s.get("quotes", [])):
            if q["story"] in day.rep and not find_quote(day.detail(q["story"]), q["text"]):
                errors.append(f"sections[{i}].quotes[{j}]: not found word for word in {q['story']}'s detail file "
                              f"(a picked comment, the full text or the summary); quote it exactly, or drop it")
    if draft.get("figure"):
        errors += check_figure(day, draft["figure"])
    if draft.get("chart"):
        errors += check_chart(day, draft["chart"], draft["sections"])
    ids = set(day.rep)
    for where, text in prose_fields(draft):
        if _URL.search(text):
            errors.append(f"{where}: has a URL; links come from the data, so name the thing instead")
        if _ID.search(text) or any(sid in text for sid in ids):
            errors.append(f"{where}: has a story id; ids go in the stories lists, not the prose")
    n, lo, hi = len(draft["what_happened"]), o["what_happened"]["min"], o["what_happened"]["max"]
    lo = max(1, min(lo, len(day.index)))
    if not lo <= n <= hi:
        errors.append(f"what_happened has {n} items; it needs {lo} to {hi}")
    for i, s in enumerate(draft["sections"]):
        if len(s["heading"]) > o["heading_max_chars"]:
            errors.append(f"sections[{i}].heading is {len(s['heading'])} characters; at most {o['heading_max_chars']}")
    w, wo = article_words(draft), o["words"]
    if w > wo["hard_max"]:
        errors.append(f"the article is {w} words; cut it to under {wo['hard_max']} (the aim is {wo['min']}-{wo['max']})")
    elif w > wo["max"]:
        warnings.append(f"the article is {w} words, over the aim of {wo['min']}-{wo['max']}")
    elif w < wo["min"]:
        warnings.append(f"the article is {w} words, under the aim of {wo['min']}-{wo['max']} (fine on a quiet day)")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "words": w}
