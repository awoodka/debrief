"""The profile's filters (docs/jobs/PLAN.md, "M2 design"): code decides which jobs may reach the
scoring call. Every check comes from the profile, so changing the profile changes the filters with
no code change.

check(posting, profile, now, text=None) -> Verdict. The checks run in this order, and the first
that fails names the stage:
  title       a position type's title appears in the job title (the longest match wins), and no
              excluded word is in what's left of the title once that match is taken out
  gone        not closed on its own job board (the describe step's check)
  place       one of the profile's areas, remote in the US, or just "United States" (no location
              at all, or only a count such as Workday's "3 Locations", passes too: the model sees
              it); with filters.locations.other_us, any other place in the US too (area OTHER_US,
              ranked lower by rank.py); and not placed abroad by the job's own board (board_country
              from the describe step), whatever a list says
  age         published (or, failing that, first seen) within max_age_days
  level       new_grad_only: the title names no level above entry ("Engineer II"), and some sign
              says the job is for new grads (new_grad_sign)
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

from .items import METROS, company_norm, early_career, says_new_grad


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
        if _has(rest, ex) or _has(rest, ex + "s"):
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
US_WORDS = {"us", "usa", "u s", "united states", "united states of america", "america", "north america",
            "anywhere", "worldwide", "global", "earth"}
SEPARATORS = re.compile(r";|/|\||\bor\b|\s[-–—•·]\s|\(|\)")


def _segments(loc):
    return [" ".join(words(s)) for s in SEPARATORS.split(str(loc or "").lower()) if words(s)]


# Countries that end a place name in the sources: a town there is never the US town of that name
# ("Vienna, Austria" is not Vienna, VA).
FOREIGN = {"uk", "united kingdom", "england", "scotland", "wales", "canada", "japan", "germany", "australia", "france",
           "ireland", "south korea", "korea", "india", "netherlands", "the netherlands", "spain", "switzerland", "sweden",
           "italy", "belgium", "poland", "israel", "singapore", "mexico", "brazil", "argentina", "new zealand", "uae",
           "united arab emirates", "qatar", "saudi arabia", "china", "taiwan", "hong kong", "austria", "denmark", "norway",
           "finland", "portugal", "czech republic", "romania", "ukraine", "belarus", "philippines", "ie", "nl", "mx", "ch"}


def _city(name):
    """A city's canonical name: metro names first ("bay area" -> "san francisco"), then without
    "greater" and a trailing "(metropolitan|metro) area" ("New York City Metropolitan Area" -> new york)."""
    if name in METROS:
        return METROS[name]
    bare = re.sub(r"\s+(metropolitan area|metro area|metro|area)$", "", re.sub(r"^greater\s+", "", name))
    return METROS.get(bare, bare)


def _cities_state(seg_raw):
    """(cities, state, foreign) for one place: every part that isn't a state, a US word or a
    country may be the city ("Arlington County, Arlington, VA" -> arlington county, arlington; va).
    foreign: the place ends in a country outside the US."""
    parts = [" ".join(words(p)) for p in seg_raw.split(",") if words(p)]
    cities, state = [], None
    for i, p in enumerate(parts):
        if i and len(p) == 2 and p in STATES.values():
            state = p
        elif i and p in STATES:
            state = STATES[p]
        elif i and (p in US_WORDS or p in FOREIGN):
            continue
        else:
            cities.append(_city(p))
    return [c for c in cities if c], state, bool(parts) and len(parts) > 1 and parts[-1] in FOREIGN


def _city_state(seg_raw):
    """("san francisco", "ca") from "San Francisco, CA, United States"; state None when absent."""
    cities, state, _ = _cities_state(seg_raw)
    return (cities[0] if cities else None), state


def _places(profile):
    out = []
    for area, names in profile["filters"]["locations"]["places"].items():
        for n in names:
            city, state = _city_state(n.lower())
            out.append((area, city, state))
    return out


def _pieces(loc):
    return [s for s in SEPARATORS.split(str(loc).lower()) if words(s)] or [str(loc).lower()]


def _area(raw, places):
    """The profile area one piece of a location is in, or None."""
    cities, state, foreign = _cities_state(raw)
    if foreign:
        return None
    return next((area for area, pc, ps in places if pc in cities and (ps is None or state is None or state == ps)), None)


def areas(locations, profile):
    """Every profile area (filters.locations.places) one of these locations is in."""
    places = _places(profile)
    return {a for loc in locations or () for raw in _pieces(loc) if (a := _area(raw, places))}


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


US_COUNTRY = {"united states", "united states of america", "us", "usa", "u s", "u s a"}


def board_abroad(data):
    """The country the job's own board puts it in, when that's outside the US and the board lists no
    other place; else None. The board outranks a list: speedyapply listed Blackstone's London
    analyst role as "Berkeley, CA", its Workday board says United Kingdom."""
    country = (data or {}).get("board_country")
    if not country or (data or {}).get("board_more_places"):
        return None
    return None if " ".join(words(country)) in US_COUNTRY else country


PLACE_COUNT = re.compile(r"\s*\d+\s+locations?\s*", re.I)        # Workday's "3 Locations": the places are elsewhere


def _bare_remote_us(locations):
    """A bare "Remote" is US remote when it's the job's only place or another of its places is in the US:
    next to "Tokyo" or "London, UK" it's remote there."""
    others = [raw for loc in locations for raw in _pieces(loc)
              if words(REMOTE_WORDS.sub(" ", raw)) and not PLACE_COUNT.fullmatch(raw)]
    return not others or any(_us(raw) for raw in others)


OTHER_US = "other US"


def us_places(locations):
    """The locations that name a place in the US and don't say remote ("Atlanta, GA", "Hybrid - Austin, TX")."""
    return [loc for loc in locations or () if not any("remote" in words(r) for r in _pieces(loc))
            and any(_us(r) for r in _pieces(loc))]


def place_fit(locations, profile, remote_flag=None):
    """(area, "remote (US)", "anywhere in the US", "unknown" or OTHER_US, None), or (None, why). OTHER_US:
    with filters.locations.other_us, a job in the US that is in none of the areas and not remote US."""
    if not locations or all(PLACE_COUNT.fullmatch(str(x)) for x in locations):
        return "unknown", None
    places = _places(profile)
    remote_ok = profile["filters"]["locations"]["remote_us"]
    for loc in locations:
        raws = _pieces(loc)
        area = next((a for raw in raws if (a := _area(raw, places))), None)
        if area:
            return area, None
        if not remote_ok:
            continue
        says_remote = any("remote" in words(r) for r in raws)
        rest = [r for r in (REMOTE_WORDS.sub(" ", r) for r in raws) if words(r)]
        if says_remote and (any(_us(r) for r in rest) or not rest and _bare_remote_us(locations)):
            return "remote (US)", None
        if remote_flag and rest and any(_us(r) for r in rest):
            return "remote (US)", None
        if not says_remote and all(" ".join(words(r)) in US_WORDS for r in raws):
            return "anywhere in the US", None
    if profile["filters"]["locations"].get("other_us") and us_places(locations):
        return OTHER_US, None
    return None, "not in the profile's places or remote US: " + "; ".join(str(x) for x in locations)[:120]


# ------------------------------------------------------------------------------------------ level

ENTRY_WORDS = r"\bassociate\b|\brotation(al)?\b|\bresidency\b|\blevel\s*(0|i|1)\b(?!\.\d)"
ROLE = r"\b(engineer|developer|scientist|analyst|programmer|sde|swe|mts)"
UP_TO_THREE_WORDS = r"(\W+[\w+#]+){0,3}?"
LEVEL_ONE = r"\W+(?<!-)(i|1)(\s*/\s*(ii|2))?"
NOT_II_IM_POINT_IO_OR_RANGE = r"(?![\w'’]|\.\d|\s*/\s*o\b|\s*(-|–|to)\s*\d)"
LEVEL_TITLE = re.compile(f"{ENTRY_WORDS}|{ROLE}{UP_TO_THREE_WORDS}{LEVEL_ONE}{NOT_II_IM_POINT_IO_OR_RANGE}", re.I)
RIGHT_AFTER = rf"(?:{ROLE}|\blevels?)[\s,(]+"
ROMAN_AFTER = rf"{ROLE}{UP_TO_THREE_WORDS}[\s,(]+(?=[iv])"      # a digit after other words is a version or a team ("Python 3")
ABOVE_ENTRY_TITLE = re.compile(rf"(?:{RIGHT_AFTER}|{ROMAN_AFTER})(?P<level>ii|iii|iv|2|3|4|5){NOT_II_IM_POINT_IO_OR_RANGE}", re.I)
ASSISTANT_VICE_PRESIDENT = re.compile(r"\bavp\b", re.I)
LEVEL_RANGE = re.compile(r"\b(?:i|1)\s*(?:/|-|–|,|&|\bor\b|\band\b|\bto\b)\s*(?:ii|2)\b", re.I)
LEVEL_DATA = re.compile(r"entry|junior|new grad|graduate|early career|student", re.I)
LEVEL_KEYS = ("levels", "seniority", "level", "experience")
LEVEL_TEXT = re.compile(
    r"\bnew[\s-]grad(uate)?s?\b|\brecent(ly)?\s+grad(uate|uated|s)?\b|\bentry[\s-]level\b|\bearly[\s-]career\b|"
    r"\b(university|college)\s+grad(uate)?s?\b|\bnew\s+college\s+grad|\bclass\s+of\s+20[2-3]\d\b|"
    r"\bgraduating\s+(in|by)\s+(\w+\s+)?20[2-3]\d\b|\b0\s*(-|–|to)\s*[12]\s*years?\b|"
    r"\bno\s+(prior\s+|professional\s+)?experience\s+(is\s+)?(required|necessary|needed)\b|"
    r"\bnew\s+graduate\s+level\b", re.I)


def above_entry(title):
    if says_new_grad(title) or LEVEL_RANGE.search(title or ""):
        return None
    m = ABOVE_ENTRY_TITLE.search(title or "")
    return m.group("level").upper() if m else "AVP" if ASSISTANT_VICE_PRESIDENT.search(title or "") else None


def title_level(title):
    return not above_entry(title) and (early_career(title) or bool(LEVEL_TITLE.search(title or "")))


def new_grad_sign(title, text=None, data=None, listed_early=False, listed_trusted=False, no_years=False,
                  startup=False, max_years=0):
    """What says the job is for new grads, or None. A trusted new-grad list counts on its own; any other
    list, and the description's words, only once the description has been read. no_years: a read
    description that asks for no years of experience counts too. startup: at a startup, a read
    description that asks for at most max_years counts, since startups rarely label a role entry level."""
    if title_level(title):
        return "title"
    d = data or {}
    for k in LEVEL_KEYS:
        v = d.get(k)
        vals = v if isinstance(v, list) else [v]
        if any(isinstance(x, str) and LEVEL_DATA.search(x) for x in vals):
            return f"the source's level ({k})"
    if not text:
        return "a trusted new-grad list" if listed_trusted else None
    if listed_early:
        return "a new-grad list"
    m = LEVEL_TEXT.search(text)
    if m:
        return f"the description: {' '.join(m.group(0).split())!r}"
    years = min_years(text)
    if no_years and not years:
        return "the description asks for no years of experience"
    if startup and (years or 0) <= max_years:
        return f"a startup role asking for {years or 'no'} year{'' if years == 1 else 's'} of experience"
    return None


SIZE = re.compile(r"(\d[\d,]*)(?:\s*-\s*(\d[\d,]*))?\+?\s*people")
STARTUP_MAX_PEOPLE = 1000


def small_company(size):
    """True when a company_size label ("11-50 people, series a", "25 people, YC W24") puts the company at
    STARTUP_MAX_PEOPLE or fewer."""
    m = SIZE.search(size or "")
    return bool(m) and int((m.group(2) or m.group(1)).replace(",", "")) <= STARTUP_MAX_PEOPLE


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


_N = r"(?:\d{1,2}|one)(?:\s*\(1\))?"
YEARS = re.compile(rf"\b({_N})\s*(?:\+|plus|or\s+more)?\s*((?:-|–|to|and)\s*\d{{1,2}}\s*\+?\s*)?years?[’']?\s+(?:of\s+)?"
                   r"(?:[a-z/+#-]+\s+){0,4}?experience", re.I)
UPPER_BOUND = re.compile(r"(up to|between|less than|fewer than|under|no more than|maximum of|max\.?|at most)\s+(?:\S+\s+){0,4}[~≈]?$", re.I)
INSTEAD_OF_A_DEGREE = re.compile(r"\bin\s+(?:lieu|place)\s+of\b|\bsubstitut", re.I)
SCHOOL = (r"(?:intern(?:ship)?s?|co-?ops?|course\s?work|hackathons?|project\s+(?:work|experience)|"
          r"(?:school|class|academic|personal|collegiate|student(?:\s+team)?)\s+(?:projects?|work|experience))")
SCHOOL_COUNTS = re.compile(
    rf"\b(?:includ\w*|counts?|such\s+as|through|via|or(?:\s+equivalent)?|and/or)\W+(?:[\w-]+\W+){{0,5}}?{SCHOOL}\b|"
    rf"\b{SCHOOL}\W+(?:[\w/&-]+\W+){{0,4}}?(?:acceptable|accepted|applicable|apply|qualif(?:y|ies)|counts?|considered|included)\b|"
    rf"\b{SCHOOL},?\s+(?:or|and/or)\b", re.I)
SCHOOL_EXCLUDED = re.compile(r"\bexclu\w*|(?:\bnot|n[’']t)\s+(?:be\s+)?(?:includ|count|appl|qualif|accept|consider)\w*|"
                             r"\bother\s+than\b|\bbeyond\b|\bpost[\s-]?grad\w*|\bafter\s+graduat\w*", re.I)


def min_years(text):
    """The smallest number of years of experience a description *requires*, or None. An upper bound
    ("up to 5 years", "between new grad and 5 years"), a preference ("Preferred qualifications:
    5+ years", "5+ years preferred"), years offered instead of a degree ("or 2+ years of experience
    in lieu of a degree") and years that school can fill ("1+ years of experience (internship
    experience qualifies)", unlike "excluding internships") don't count."""
    text = text or ""
    found = []
    ms = list(YEARS.finditer(text))
    for m, nxt in zip(ms, ms[1:] + [None]):
        n = 1 if m.group(1).lower().startswith("one") else int(m.group(1).split("(")[0])
        bounded = not m.group(2) and UPPER_BOUND.search(text[max(0, m.start() - 50):m.start()])   # a range owns its "between"
        clause = m.group(0) + re.split(r"[.\n;•]", text[m.end():min(nxt.start() if nxt else len(text), m.end() + 160)])[0]
        school = SCHOOL_COUNTS.search(clause) and not SCHOOL_EXCLUDED.search(clause)
        if n > 30 or bounded or school or INSTEAD_OF_A_DEGREE.search(clause) or preferred(text, m.start(), m.end()):
            continue
        found.append(n)
    return min(found) if found else None


# ------------------------------------------------------------------------------------- the check

def check(posting, profile, now, text=None, listed_early=False, listed_trusted=False, no_years=False, skip=(),
          startup=False):
    """listed_early: a new-grad list (a "list" family source) carries the job; listed_trusted: one of
    them is trusted (collect.trusted_sources). no_years: settings level.no_years_sign. startup: the job is
    at a startup and settings level.startup_sign is on (see new_grad_sign). skip: stages not
    to check: "level" (describe skips the new-grad sign, since the description it fetches may be what
    shows it; a title above entry still fails) and "age" (the catch-up skips it for watchlist companies)."""
    f = profile["filters"]
    d = posting.get("data") or {}
    fit = title_fit(posting["title"], profile)
    if fit[0] is None:
        return Verdict(False, "title", fit[1])
    facts = {"position_type": fit[0], "matched": fit[1]}
    gone = d.get("gone")
    if gone:
        return Verdict(False, "gone", f"closed on its own job board (HTTP {gone.get('status')} or missing from "
                                      f"its list, {str(gone.get('at') or '')[:10]})", facts)
    abroad = board_abroad(d)
    if abroad:
        return Verdict(False, "place", f"its own job board places it in {abroad}", facts)
    area, why = place_fit(posting.get("locations") or [], profile, d.get("remote"))
    if area is None:
        return Verdict(False, "place", why, facts)
    facts["area"] = area
    if area == OTHER_US:
        facts["outside"] = us_places(posting.get("locations"))
    when = posting.get("published_at") or posting.get("first_seen_at")
    if "age" not in skip and when and datetime.fromisoformat(when.replace("Z", "+00:00")) < now - timedelta(days=f["max_age_days"]):
        return Verdict(False, "age", f"posted {when[:10]}, more than {f['max_age_days']} days ago", facts)
    level = f["new_grad_only"] and above_entry(posting["title"])
    if level:
        return Verdict(False, "level", f"the title names level {level}, above entry", facts)
    if f["new_grad_only"] and "level" not in skip:
        sign = new_grad_sign(posting["title"], text, d, listed_early, listed_trusted, no_years, startup, f["max_years"])
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
