"""The profile's filters (docs/jobs/PLAN.md, "M2 design"): code decides which jobs may reach the
scoring call. Every check comes from the profile, so changing the profile changes the filters with
no code change.

check(posting, profile, now, text=None) -> Verdict. The checks run in this order, and the first
that fails names the stage:
  title       a position type's title appears in the job title (the longest match wins), and no
              excluded word is in what's left of the title once that match is taken out
  place       one of the profile's areas, remote in the US, or just "United States" (no location
              at all passes too: the model sees it)
  age         published (or, failing that, first seen) within max_age_days
  level       new_grad_only: some sign the job is for new grads (new_grad_sign)
  degree      not PhD- or Master's-only by its title, and not above the profile's degree when a source
              lists degrees
  blocklist   the company isn't on it
  employment  full_time_only: not marked part-time, contract, temporary or internship by the source
              or the title
  pay         stated pay not clearly below min_hourly_pay (unclear or unstated pay passes)
  clearance   no active clearance above what the profile has (title, and text when given)
  years       no "N+ years of experience" above max_years (text only)
The text-based checks run only when a description is given, so `describe` can pre-filter without
one and `score` filters again with it.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .items import company_norm, early_career


@dataclass
class Verdict:
    ok: bool
    stage: str = ""
    reason: str = ""
    facts: dict = field(default_factory=dict)     # position_type, matched title, area, hourly pay


def words(text):
    """Lowercase words, punctuation as spaces. "C++" and "C#" stay whole; a lone trailing "+" doesn't
    ("Staff+ Engineer" has the word "staff")."""
    return re.findall(r"[a-z0-9]+(?:\+\+|#)?", str(text or "").lower())


def _has(hay, phrase):
    return f" {' '.join(words(phrase))} " in f" {' '.join(hay)} "


# ----------------------------------------------------------------------------------------- title

def title_fit(title, profile):
    """(position type name, matched title) or (None, why)."""
    ws = words(title)
    best = None
    for pt in profile["position_types"]:
        for t in pt["titles"]:
            if _has(ws, t) and (best is None or len(words(t)) > len(words(best[1]))):
                best = (pt["name"], t)
    if not best:
        return None, "not one of the position types"
    rest = f" {' '.join(ws)} ".replace(f" {' '.join(words(best[1]))} ", " ", 1).split()
    for ex in profile["filters"]["exclude_titles"]:
        if _has(rest, ex):
            return None, f"excluded title word: {ex}"
    return best


# ----------------------------------------------------------------------------------------- place

STATES = {"alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar", "california": "ca", "colorado": "co",
          "connecticut": "ct", "delaware": "de", "district of columbia": "dc", "florida": "fl", "georgia": "ga",
          "hawaii": "hi", "idaho": "id", "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks",
          "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md", "massachusetts": "ma",
          "michigan": "mi", "minnesota": "mn", "mississippi": "ms", "missouri": "mo", "montana": "mt",
          "nebraska": "ne", "nevada": "nv", "new hampshire": "nh", "new jersey": "nj", "new mexico": "nm",
          "new york": "ny", "north carolina": "nc", "north dakota": "nd", "ohio": "oh", "oklahoma": "ok",
          "oregon": "or", "pennsylvania": "pa", "rhode island": "ri", "south carolina": "sc", "south dakota": "sd",
          "tennessee": "tn", "texas": "tx", "utah": "ut", "vermont": "vt", "virginia": "va", "washington": "wa",
          "west virginia": "wv", "wisconsin": "wi", "wyoming": "wy"}
# Metro names some sources use instead of a city, and the profile area name words they belong to.
METROS = {"sf": "san francisco", "sf bay area": "san francisco", "bay area": "san francisco",
          "san francisco bay area": "san francisco", "silicon valley": "san jose", "nyc": "new york",
          "new york city": "new york", "new york metropolitan area": "new york", "greater new york": "new york",
          "washington dc": "washington", "washington d c": "washington", "dc": "washington",
          "washington dc baltimore area": "washington", "dmv": "washington", "northern virginia": "arlington"}
US_WORDS = {"us", "usa", "u s", "united states", "united states of america", "america", "north america",
            "anywhere", "worldwide", "global", "earth"}
SEPARATORS = re.compile(r";|/|\||\bor\b|\s[-–—•·]\s|\(|\)")


def _segments(loc):
    return [" ".join(words(s)) for s in SEPARATORS.split(str(loc or "").lower()) if words(s)]


def _city_state(seg_raw):
    """("san francisco", "ca") from "San Francisco, CA, United States"; state None when absent."""
    parts = [" ".join(words(p)) for p in seg_raw.split(",") if words(p)]
    if not parts:
        return None, None
    city, state = parts[0], None
    for p in parts[1:]:
        if len(p) == 2 and p in STATES.values():
            state = p
        elif p in STATES:
            state = STATES[p]
    return METROS.get(city, city), state


def _places(profile):
    out = []
    for area, names in profile["filters"]["locations"]["places"].items():
        for n in names:
            city, state = _city_state(n.lower())
            out.append((area, city, state))
    return out


def _us(raw):
    """Does this piece of a location name the US: a US word, a state, or "City, ST"? "Perth, WA,
    Australia" doesn't: a third part that isn't the US vetoes the state code."""
    ws = " ".join(words(raw))
    if not ws:
        return False
    toks = ws.split()
    if ws in US_WORDS or ws in STATES or ws in STATES.values():
        return True
    if toks[-1] in ("us", "usa") or "united states" in ws or "anywhere" in toks or "worldwide" in toks:
        return True
    parts = [" ".join(words(p)) for p in raw.split(",") if words(p)]
    if len(parts) >= 3 and parts[-1] not in US_WORDS:
        return False
    return _city_state(raw)[1] is not None


REMOTE_WORDS = re.compile(r"\b(remote|in|hybrid|on-?site|onsite|in-office|work from home|wfh)\b")


def place_fit(locations, profile, remote_flag=None):
    """(area, "remote (US)", "anywhere in the US" or "unknown", None), or (None, why)."""
    if not locations:
        return "unknown", None
    places = _places(profile)
    remote_ok = profile["filters"]["locations"]["remote_us"]
    for loc in locations:
        raws = [s for s in SEPARATORS.split(str(loc).lower()) if words(s)] or [str(loc).lower()]
        for raw in raws:
            city, state = _city_state(raw)
            for area, pc, ps in places:
                if city == pc and (ps is None or state is None or state == ps):
                    return area, None
        if not remote_ok:
            continue
        says_remote = any("remote" in words(r) for r in raws)
        rest = [r for r in (REMOTE_WORDS.sub(" ", r) for r in raws) if words(r)]
        if says_remote and (not rest or any(_us(r) for r in rest)):
            return "remote (US)", None
        if remote_flag and rest and any(_us(r) for r in rest):
            return "remote (US)", None
        if not says_remote and all(" ".join(words(r)) in US_WORDS for r in raws):
            return "anywhere in the US", None
    return None, "not in the profile's places or remote US: " + "; ".join(str(x) for x in locations)[:120]


# ------------------------------------------------------------------------------------------ level

LEVEL_TITLE = re.compile(r"\bassociate\b|\brotation(al)?\b|\bresidency\b|\blevel\s*(i|1)\b", re.I)
LEVEL_DATA = re.compile(r"entry|junior|new grad|graduate|early career|student", re.I)
LEVEL_TEXT = re.compile(
    r"\bnew[\s-]grad(uate)?s?\b|\brecent(ly)?\s+grad(uate|uated|s)?\b|\bentry[\s-]level\b|\bearly[\s-]career\b|"
    r"\b(university|college)\s+grad(uate)?s?\b|\bnew\s+college\s+grad|\bclass\s+of\s+20[2-3]\d\b|"
    r"\bgraduating\s+(in|by)\s+(\w+\s+)?20[2-3]\d\b|\b0\s*(-|–|to)\s*[12]\s*years?\b|"
    r"\bno\s+(prior\s+|professional\s+)?experience\s+(is\s+)?(required|necessary|needed)\b|"
    r"\bnew\s+graduate\s+level\b", re.I)


def new_grad_sign(title, text=None, data=None, listed_early=False):
    """Where the job says it's for new grads, or None: the title ("New Grad", "Junior", "Associate",
    "Engineer I", a class year), a new-grad list carrying it, the source's own level field ("Entry
    Level"), or the description ("new grads", "recent graduates", "0-2 years", "entry-level")."""
    if early_career(title) or LEVEL_TITLE.search(title or ""):
        return "title"
    if listed_early:
        return "a new-grad list"
    d = data or {}
    for k in ("levels", "seniority", "level", "experience"):
        v = d.get(k)
        vals = v if isinstance(v, list) else [v]
        if any(isinstance(x, str) and LEVEL_DATA.search(x) for x in vals):
            return f"the source's level ({k})"
    if text:
        m = LEVEL_TEXT.search(text)
        if m:
            return f"the description: {' '.join(m.group(0).split())!r}"
    return None


# ------------------------------------------------------------------------------- degree, employment

DEGREE_RANK = {"high school": 0, "associate's": 1, "associate": 1, "bachelor's": 2, "bachelor": 2, "bs": 2, "ba": 2,
               "master's": 3, "master": 3, "ms": 3, "ma": 3, "mba": 3, "phd": 4, "ph.d.": 4, "doctorate": 4}
PHD_TITLE = re.compile(r"\bph\.?\s?d\b|\bdoctoral\b", re.I)
LOWER_DEGREE_TITLE = re.compile(r"\b(bs|ba|b\.s|bachelor\w*|undergrad\w*|ms|m\.s|master\w*)\b", re.I)
MASTERS_TITLE = re.compile(r"\bmaster(?:'|’)?s\b|\(ms\)|\bm\.s\.(?=\W|$)", re.I)
BACHELOR_TITLE = re.compile(r"\b(bs|ba|b\.s|bachelor\w*|undergrad\w*)\b", re.I)


def degree_fit(title, degrees, profile):
    mine = DEGREE_RANK.get(profile["degree"]["level"].lower().rstrip("."), 2)
    if PHD_TITLE.search(title or "") and not LOWER_DEGREE_TITLE.search(title or "") and mine < 4:
        return "the title asks for a PhD"
    if MASTERS_TITLE.search(title or "") and not BACHELOR_TITLE.search(title or "") and mine < 3:
        return "the title asks for a Master's"
    ranks = [DEGREE_RANK[d.lower()] for d in degrees or [] if d.lower() in DEGREE_RANK]
    if ranks and min(ranks) > mine:
        return f"needs at least a {min(degrees or [], key=lambda d: DEGREE_RANK.get(d.lower(), 9))}"
    return None


NOT_FULL_TIME = re.compile(r"part[\s-]?time|\bcontract(or|ors)?\b|\btemp(orary)?\b|\bseasonal\b|\bper[\s-]diem\b|"
                           r"\bfreelance\b|\b1099\b|\bintern(ship)?\b|\bco[\s-]?op\b|\bapprentice(ship)?\b", re.I)


def employment_fit(title, employment):
    """None if full-time (or unknown), else why."""
    e = str(employment or "")
    if e and NOT_FULL_TIME.search(re.sub(r"([a-z])([A-Z])", r"\1 \2", e)) and not re.search(r"full", e, re.I):
        return f"not full-time: {e}"
    m = re.search(r"part[\s-]?time|(?<!smart )\bcontract(or)?\b|\btemporary\b|\bseasonal\b|\bper[\s-]diem\b|\bfreelance\b",
                  title or "", re.I)
    if m:
        return f"not full-time: the title says {m.group(0).lower()!r}"
    return None


# ------------------------------------------------------------------------------------------- pay

HOURS = {"hour": 1, "day": 8, "week": 40, "month": 2080 / 12, "year": 2080}
AMOUNT = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*([kK])?")


def _period(p):
    p = str(p or "").lower()
    for word, key in (("hour", "hour"), ("hr", "hour"), ("day", "day"), ("daily", "day"), ("week", "week"),
                      ("month", "month"), ("year", "year"), ("yr", "year"), ("annual", "year"), ("salary", "year")):
        if word in p:
            return key
    return None


def hourly_top(pay):
    """The top of a job's stated pay, in USD an hour, or None when unstated or unclear. A figure
    that looks mislabeled (a "yearly" amount under $15,000) counts as unclear."""
    if not pay:
        return None
    if pay.get("summary"):
        s = pay["summary"]
        amounts = [float(a.replace(",", "")) * (1000 if k else 1) for a, k in AMOUNT.findall(s)]
        if not amounts:
            return None
        top, period = max(amounts), _period(s)
    else:
        if pay.get("currency") not in (None, "", "USD"):
            return None
        vals = [v for v in (pay.get("max"), pay.get("min")) if isinstance(v, (int, float)) and v > 0]
        if not vals:
            return None
        top, period = max(vals), _period(pay.get("period"))
    if period is None:
        period = "year" if top >= 15000 else "hour" if top < 200 else None
    if period is None or (period == "year" and top < 15000) or (period == "hour" and top > 1000):
        return None
    return round(top / HOURS[period], 2)


# ------------------------------------------------------------------------------ clearance, years

CLEARANCE_RANK = {"none": 0, "public_trust": 1, "secret": 2, "top_secret": 3, "ts_sci": 4}
_LEVEL = r"(ts\s*/\s*sci|ts-sci|top[\s-]secret|secret|public[\s-]trust)"
CLEAR_TITLE = re.compile(r"\bcleared\b|\bts\s*/\s*sci\b|\btop[\s-]secret\b|\bpoly(graph)?\b|\bsecret\b", re.I)
CLEAR_TEXT = re.compile(
    r"(?:active|current|existing|must\s+(?:currently\s+)?(?:hold|have|possess)\s+an?|requires?\s+an?\s+active)\s+"
    + _LEVEL + r"(?:\s+(?:security\s+)?clearance)?|" + _LEVEL + r"(?:\s+[\w/-]+){0,5}?\s+(?:is\s+)?required", re.I)
POST_START = re.compile(r"post[\s-]start|after (?:the )?start|after hire|upon hire|once hired", re.I)
OBTAIN = re.compile(r"(ability|able|eligib\w*)\s+to\s+obtain|eligible\s+for", re.I)


def _level(s):
    s = s.lower()
    if "sci" in s or "poly" in s:
        return "ts_sci"
    if "top" in s:
        return "top_secret"
    if "trust" in s:
        return "public_trust"
    return "secret" if "secret" in s or "cleared" in s else "secret"


PREFERRED_AFTER = re.compile(r"^\W*(?:\w+\W+){0,6}?(preferred|a plus|nice to have|desired|ideally|is preferred|are preferred)\b",
                             re.I)
PREFERRED_HEAD = re.compile(r"prefer\w*|nice[\s-]to[\s-]haves?|bonus|ideally|desired", re.I)
REQUIRED_HEAD = re.compile(r"required|requirements|minimum qualifications|basic qualifications|must[\s-]haves?|"
                           r"what we(?:'|’)re looking for|you will need|you have:", re.I)


def preferred(text, start, end):
    """Is the phrase at text[start:end] a preference rather than a requirement? Yes when "preferred",
    "a plus" ... follows it closely, or when a preference heading ("Preferred qualifications", "Nice to
    have", "Preferably") comes after the last requirements heading before it."""
    if PREFERRED_AFTER.search(re.split(r"[.\n;•]", text[end:end + 60])[0]):       # same sentence only
        return True
    before = text[max(0, start - 800):start]
    p = [m.start() for m in PREFERRED_HEAD.finditer(before)]
    r = [m.start() for m in REQUIRED_HEAD.finditer(before)]
    return bool(p) and (not r or max(p) > max(r))


def clearance_fit(title, text, profile):
    have = CLEARANCE_RANK[profile["filters"]["clearance"]["has"]]
    m = CLEAR_TITLE.search(title or "")
    if m and not OBTAIN.search(title or "") and CLEARANCE_RANK[_level(m.group(0))] > have:
        return f"needs an active clearance (title: {m.group(0)!r})"
    if text:
        for m in CLEAR_TEXT.finditer(text):
            before = text[max(0, m.start() - 40):m.start()]
            if re.search(r"obtain|eligib", before, re.I):          # "ability to obtain a Secret clearance is required"
                continue
            if preferred(text, m.start(), m.end()) or POST_START.search(text[m.start():m.end() + 40]):
                continue
            if CLEARANCE_RANK[_level(m.group(0))] > have:
                return f"needs an active clearance: {' '.join(m.group(0).split())!r}"
    return None


YEARS = re.compile(r"\b(\d{1,2})\s*(?:\+|plus)?\s*((?:-|–|to|and)\s*\d{1,2}\s*\+?\s*)?years?[’']?\s+(?:of\s+)?"
                   r"(?:[a-z/+#-]+\s+){0,4}?experience", re.I)
UPPER_BOUND = re.compile(r"(up to|between|less than|fewer than|under|no more than|maximum of|max\.?|at most)\s+(?:\S+\s+){0,4}$", re.I)


def min_years(text):
    """The smallest number of years of experience a description *requires*, or None. An upper bound
    ("up to 5 years", "between new grad and 5 years") and a preference ("Preferred qualifications:
    5+ years", "5+ years preferred") don't count."""
    text = text or ""
    found = []
    for m in YEARS.finditer(text):
        n = int(m.group(1))
        bounded = not m.group(2) and UPPER_BOUND.search(text[max(0, m.start() - 50):m.start()])   # a range owns its "between"
        if n > 30 or bounded or preferred(text, m.start(), m.end()):
            continue
        found.append(n)
    return min(found) if found else None


# ------------------------------------------------------------------------------------- the check

def check(posting, profile, now, text=None, listed_early=False, skip=()):
    """listed_early: a new-grad list (a "list" family source) carries the job. skip: stages not to
    check (describe skips "level", since the description it fetches may be what shows it)."""
    f = profile["filters"]
    d = posting.get("data") or {}
    fit = title_fit(posting["title"], profile)
    if fit[0] is None:
        return Verdict(False, "title", fit[1])
    facts = {"position_type": fit[0], "matched": fit[1]}
    area, why = place_fit(posting.get("locations") or [], profile, d.get("remote"))
    if area is None:
        return Verdict(False, "place", why, facts)
    facts["area"] = area
    when = posting.get("published_at") or posting.get("first_seen_at")
    if when and datetime.fromisoformat(when.replace("Z", "+00:00")) < now - timedelta(days=f["max_age_days"]):
        return Verdict(False, "age", f"posted {when[:10]}, more than {f['max_age_days']} days ago", facts)
    if f["new_grad_only"] and "level" not in skip:
        sign = new_grad_sign(posting["title"], text, d, listed_early)
        if sign is None:
            return Verdict(False, "level", "nothing says it's for new grads" + ("" if text else " (title only)"), facts)
        facts["new_grad_sign"] = sign
    why = degree_fit(posting["title"], d.get("degrees"), profile)
    if why:
        return Verdict(False, "degree", why, facts)
    if company_norm(posting["company"]) in {company_norm(b) for b in f["blocklist"]}:
        return Verdict(False, "blocklist", f"{posting['company']} is on the blocklist", facts)
    if f["full_time_only"]:
        why = employment_fit(posting["title"], d.get("employment"))
        if why:
            return Verdict(False, "employment", why, facts)
    top = hourly_top(d.get("pay"))
    facts["hourly_top"] = top
    if top is not None and top < f["min_hourly_pay"]:
        return Verdict(False, "pay", f"pays at most ${top:.2f} an hour", facts)
    why = clearance_fit(posting["title"], text, profile)
    if why:
        return Verdict(False, "clearance", why, facts)
    if text:
        n = min_years(text)
        facts["min_years"] = n
        if n is not None and n > f["max_years"]:
            return Verdict(False, "years", f"asks for {n}+ years of experience (max {f['max_years']})", facts)
    return Verdict(True, facts=facts)
