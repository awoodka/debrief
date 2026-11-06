"""The profile as settings: the tables below say what each setting is called, which section it sits
in and which kind of control edits it. Everything else is derived from them.

A page's state is a Raw, a flat {input name: text} exactly as a browser posts it. A failed save
re-renders it and a stale page is merged on it. The profile's id must not change unless a person
changed something, because any new id re-asks the model about every job in the pool, so Field.read
returns the stored value verbatim whenever the input still shows that value.
"""
import copy
import re
from collections import defaultdict
from dataclasses import dataclass, field as dc_field
from typing import Any, Callable, Mapping

from .. import boards, profile as profiles


class _Sentinel:
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return self.name


MISSING = _Sentinel("MISSING")    # a key that isn't in the document, unlike None (board: null) or "" (note: "")
REQUIRED = _Sentinel("REQUIRED")  # Field.blank: an emptied input is an error rather than a value

Raw = dict[str, str]


class Bad(Exception):
    """A plain sentence for the person. Never a schema message."""


@dataclass
class Reading:
    """One pass of a table over a Raw. places maps document paths back to input names, so a schema
    error on ("companies", 1, "board") lands on "companies.3.board" even after blank rows shift it."""
    raw: Raw
    places: dict[tuple, str] = dc_field(default_factory=dict)
    fields: dict[str, "Field"] = dc_field(default_factory=dict)
    failed: dict[str, list[str]] = dc_field(default_factory=dict)


def same(a, b):
    """Strict in the way the profile id is: 25 is not 25.0, and MISSING only equals MISSING."""
    if a is MISSING or b is MISSING:
        return a is b
    return profiles.canonical(a) == profiles.canonical(b)


def get(doc, path):
    for key in path:
        if not isinstance(doc, dict) or key not in doc:
            return MISSING
        doc = doc[key]
    return doc


def put(doc, path, value):
    *parents, last = path
    for key in parents:
        doc = doc.setdefault(key, {})
    if value is MISSING:
        doc.pop(last, None)
    else:
        doc[last] = value


# ---------------------------------------------------------------------------- the field kinds

@dataclass(frozen=True)
class Field:
    key: str                 # dotted from the document's top, or from the row inside a Rows
    label: str
    help: str = ""
    blank: Any = REQUIRED    # what an emptied input means; the schema then decides whether it's allowed
    messages: Mapping[str, str] = dc_field(default_factory=dict)   # per-rule wording

    @property
    def path(self):
        return tuple(self.key.split("."))

    def to_text(self, value):
        return "" if value is MISSING or value is None else str(value)

    def clean(self, typed):
        """Undo what the browser does: <input> strips newlines from a value."""
        return typed.replace("\r", "").replace("\n", "")

    def parse(self, typed, ref):
        return typed.strip()

    def read(self, rd, at, path, ref):
        """The one rule that keeps the id stable: an input still showing the stored value returns it
        verbatim, including MISSING, None, "", 25.0 and odd whitespace."""
        typed = self.clean(rd.raw.get(at, ""))
        rd.places[path] = at
        rd.fields[at] = self
        if typed == self.clean(self.to_text(ref)):
            return ref
        if not typed.strip():
            if self.blank is REQUIRED:
                rd.failed[at] = [self.messages.get("required", "Can't be blank.")]
                return ref
            return self.blank
        try:
            value = self.parse(typed, ref)
        except Bad as e:
            rd.failed[at] = [str(e)]
            return ref
        return ref if same(value, ref) else value

    def fill(self, value, at=None):
        return {at or self.key: self.to_text(value)}

    def unchanged(self, raw, ref, at=None):
        rd = Reading(raw)
        return same(self.read(rd, at or self.key, self.path, ref), ref) and not rd.failed

    def slots(self, prefix=""):
        yield prefix + self.key, self


@dataclass(frozen=True)
class Text(Field):
    """Single-line <input type=text>."""
    blank: Any = ""
    placeholder: str = ""
    accept: Callable[[str], str] | None = None

    def parse(self, typed, ref):
        typed = typed.strip()
        return self.accept(typed) if self.accept else typed


class _MultiLine:
    def clean(self, typed):
        """<textarea> posts CRLF, and the HTML parser turns a stored lone CR into LF."""
        return typed.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True)
class Area(_MultiLine, Field):
    """Prose. The template writes a newline straight after <textarea>, so a value that itself starts
    with a newline survives the HTML parser."""
    blank: Any = ""


@dataclass(frozen=True)
class Lines(_MultiLine, Field):
    """A list of strings, one per line, in order. Lines, not commas: "New York, NY" is one place."""
    blank: Any = dc_field(default_factory=list)
    noun: str = "item"
    first_means: str = ""    # "Place searched for in {row}": what the top line does, for the change list

    def to_text(self, value):
        return "" if value is MISSING else "\n".join(value)

    def parse(self, typed, ref):
        return list(dict.fromkeys(s.strip() for s in typed.split("\n") if s.strip()))


@dataclass(frozen=True)
class Number(Field):
    """<input type=text inputmode=...>, not type=number, which blanks what it can't parse and so would
    lose typed input. Typing 25 over a stored 25.0 keeps 25.0, because they are the same number and a
    different spelling would still change the id."""
    whole: bool = True
    pre: str = ""
    unit: str = ""

    def parse(self, typed, ref):
        typed = typed.strip().removeprefix(self.pre).strip()
        if re.fullmatch(r"-?[0-9]{1,15}", typed):
            value = int(typed)
        elif not self.whole and re.fullmatch(r"-?[0-9]{0,15}\.[0-9]{1,15}", typed):
            value = float(typed)
        elif self.whole:
            raise Bad("Enter a whole number, like 14.")
        else:
            raise Bad("Enter a number, like 25.")
        return ref if ref is not MISSING and value == ref else value


@dataclass(frozen=True)
class Toggle(Field):
    """A checkbox. Unchecked posts nothing, so absent means False."""
    blank: Any = False

    def to_text(self, value):
        return "on" if value is True else ""

    def parse(self, typed, ref):
        return True


@dataclass(frozen=True)
class Choice(Field):
    """<select>. labels is {stored value: words}, and a test holds it equal to the schema's enum."""
    labels: Mapping[str, str] = dc_field(default_factory=dict)


@dataclass(frozen=True)
class Rows(Field):
    """A list of objects as folded rows, with inputs f"{key}.{n}.{sub.key}" and f"{key}.{n}.remove".
    A row's stored value is found by its id input, so rows need no hidden ids and a renamed row is
    honestly a new row. spare=False is an edit-only list: no blank row and no remove box."""
    blank: Any = dc_field(default_factory=list)
    row_noun: str = "row"
    add_label: str = ""
    id_key: str = "name"
    spare: bool = True
    fields: tuple[Field, ...] = ()
    summary: Callable[[Raw], str] = lambda r: ""
    title: Callable[[Raw], str] | None = None

    def sub(self, key):
        return next(s for s in self.fields if s.key == key)

    def as_rows(self, value):
        return [] if value is MISSING else list(value)

    def numbers(self, raw, at):
        """The row numbers present in raw. A row number comes from the page, so it's only ever a label."""
        pattern = re.compile(re.escape(at) + r"\.([0-9]{1,6})\.")
        return sorted({int(m.group(1)) for name in raw if (m := pattern.match(name))})

    def skipped(self, raw, at, n):
        if not self.spare:
            return False
        return bool(raw.get(f"{at}.{n}.remove")) or not any(raw.get(f"{at}.{n}.{s.key}", "").strip() for s in self.fields)

    def stored_rows(self, ref):
        """{id input's text: rows}. A list because two projects may share a name: each takes its own row, in order."""
        pool = defaultdict(list)
        id_field = self.sub(self.id_key)
        for row in self.as_rows(ref):
            pool[id_field.clean(id_field.to_text(row.get(self.id_key, MISSING)))].append(row)
        return pool

    def stored_row(self, pool, raw, at, n):
        id_field = self.sub(self.id_key)
        found = pool.get(id_field.clean(raw.get(f"{at}.{n}.{self.id_key}", "")))
        return found.pop(0) if found else {}

    def read(self, rd, at, path, ref):
        rd.places[path] = at
        rd.fields[at] = self
        pool, out = self.stored_rows(ref), []
        for n in self.numbers(rd.raw, at):
            if self.skipped(rd.raw, at, n):
                continue
            stored, row = self.stored_row(pool, rd.raw, at, n), {}
            for s in self.fields:
                value = s.read(rd, f"{at}.{n}.{s.key}", path + (len(out), s.key), stored.get(s.key, MISSING))
                if value is not MISSING:
                    row[s.key] = value
            out.append(row)
        if same(out, ref):
            return ref
        return out if out else self.blank

    def fill(self, value, at=None):
        at = at or self.key
        out = {}
        for n, row in enumerate(self.as_rows(value)):
            for s in self.fields:
                out |= s.fill(row.get(s.key, MISSING), f"{at}.{n}.{s.key}")
        return out

    def slots(self, prefix=""):
        yield prefix + self.key, self
        for s in self.fields:
            yield from s.slots(f"{prefix}{self.key}[].")


@dataclass(frozen=True)
class MapRows(Rows):
    """An object as rows: {"NYC": ["New York, NY"]} <-> [{area: "NYC", places: [...]}]. Two rows with
    one name would silently collapse in a dict, so the second is an error."""
    blank: Any = dc_field(default_factory=dict)
    value_key: str = ""

    def as_rows(self, value):
        return [] if value is MISSING else [{self.id_key: k, self.value_key: v} for k, v in value.items()]

    def read(self, rd, at, path, ref):
        rd.places[path] = at
        rd.fields[at] = self
        id_field, value_field = self.sub(self.id_key), self.sub(self.value_key)
        pool, out = self.stored_rows(ref), {}
        for n in self.numbers(rd.raw, at):
            if self.skipped(rd.raw, at, n):
                continue
            stored = self.stored_row(pool, rd.raw, at, n)
            id_at, value_at = f"{at}.{n}.{self.id_key}", f"{at}.{n}.{self.value_key}"
            name = id_field.read(rd, id_at, path + ("key", n), stored.get(self.id_key, MISSING))
            value = value_field.read(rd, value_at, path + (name,) if name is not MISSING else path + ("value", n),
                                     stored.get(self.value_key, MISSING))
            if name is MISSING:
                rd.failed[id_at] = [id_field.messages.get("required", "Can't be blank.")]
            elif name in out:
                rd.failed[id_at] = [f"{name} is listed twice."]
            elif value is MISSING:
                rd.failed.setdefault(value_at, [value_field.messages.get("required", "Can't be blank.")])
            else:
                out[name] = value
        if same(out, ref):
            return ref
        return out if out else self.blank

    def slots(self, prefix=""):
        yield prefix + self.key + "{}", self


# ---------------------------------------------------------------------------- summary lines

def _noun(n, noun):
    if n == 1:
        return f"1 {noun}"
    return f"{n} {noun[:-1]}ies" if noun.endswith("y") else f"{n} {noun}s"


def _and(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1] if names else ""


def _some(names, keep=3, more="more"):
    names = list(names)
    return _and(names) if len(names) <= keep + 1 else f"{', '.join(names[:keep])} and {len(names) - keep} {more}"


def _dots(*parts):
    return " · ".join(p for p in parts if p)


def _money(value):
    return f"${value}"


def _month(text):
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{months[int(text[5:7]) - 1]} {text[:4]}" if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", text) else text


def _lines_in(text):
    return [s.strip() for s in text.replace("\r", "").split("\n") if s.strip()]


def _where(doc):
    loc = doc["filters"]["locations"]
    return _dots("Remote in the US" if loc["remote_us"] else "", *loc["places"]) or "Nowhere yet"


def _looking(doc):
    return _dots(_some(p["name"] for p in doc["position_types"]),
                 _noun(len(doc["filters"]["exclude_titles"]), "title word") + " ruled out")


def _keep(doc):
    f = doc["filters"]
    return _dots(f"{_money(f['min_hourly_pay'])} an hour or more", f"last {f['max_age_days']} days", f"up to {f['max_years']} years",
                 "full-time" if f["full_time_only"] else "", "new grad" if f["new_grad_only"] else "",
                 _noun(len(f["blocklist"]), "company") + " hidden")


def _daily(doc):
    r = doc["ranking"]
    per = r.get("per_company")
    return _dots(f"{r['top_n']} jobs a day" + (f", at most {per} from one company" if per else ""),
                 f"the model reads up to {r['max_candidates']} a night")


def _watching(doc):
    names = [c["name"] for c in doc["companies"]]
    return f"{_and(names)}. Boards and notes are set on the Watchlist page." if names else "No companies yet."


def _skills(doc):
    skills = doc["skills"]
    head = _noun(len(skills), "skill") + (f", including {_and(skills[:3])}" if len(skills) > 3 else "")
    return _dots(head, _noun(len(doc["interests"]), "interest"))


def _projects(doc):
    return _some(p["name"] for p in doc["projects"]) or "No projects yet"


def _education(doc):
    d = doc["degree"]
    return _dots(", ".join(x for x in (f"{d['level']} {d['field']}", d["school"], _month(d["graduation"])) if x),
                 _noun(len(doc["coursework"]), "course"))


def _eligibility(doc):
    c = doc["filters"]["clearance"]
    has = "no clearance" if c["has"] == "none" else CLEARANCE.get(c["has"], c["has"]) + " clearance"
    could = ", could obtain one" if c["eligible"] and c["has"] == "none" else ""
    return _dots(WORK_AUTHORIZATION.get(doc["filters"]["work_authorization"], doc["filters"]["work_authorization"]), has + could)


# ------------------------------------------------------------------------------------ the tables

@dataclass(frozen=True)
class Group:
    id: str
    title: str
    part: str = ""
    summary: Callable[[dict], str] = lambda doc: ""
    fields: tuple[Field, ...] = ()
    fine: tuple[Field, ...] = ()     # inside a nested closed "Fine tuning"
    link: str = ""                   # a link card with no fields of its own


def _must(sentence):
    return {"required": sentence, "minLength": sentence, "minItems": sentence}


def _board(typed):
    """A pasted careers URL becomes its board key, with no network. Anything else meets the schema's pattern."""
    if "://" not in typed:
        return typed
    board = boards.board_from_url(typed)
    if not board:
        raise Bad("That isn't a job board we can read. Paste its careers page URL, or type it like greenhouse:acme.")
    return board


def _first_lines(what):
    def line(r, key):
        lines = _lines_in(r.get(key, ""))
        return _dots(_noun(len(lines), what), f"searches {lines[0]}" if lines else "")
    return line


_titles_line, _places_line = _first_lines("title"), _first_lines("place")

WORK_AUTHORIZATION = {"us_citizen": "US citizen", "permanent_resident": "Permanent resident",
                      "authorized_no_sponsorship": "Authorized, no sponsorship needed", "needs_sponsorship": "Needs sponsorship"}
CLEARANCE = {"none": "None", "public_trust": "Public trust", "secret": "Secret", "top_secret": "Top secret", "ts_sci": "TS/SCI"}
DEGREES = {k: k for k in ("High school", "Associate", "BS", "BA", "MS", "MEng", "MBA", "PhD", "Other")}
SEARCH, ABOUT = "Your search", "About you"
ANY_CHOICE = {"required": "Pick one of the options."}

TABLE = (
    Group("where", "Where you want to work", SEARCH, _where, fields=(
        Toggle("filters.locations.remote_us", "Remote jobs in the US", "Keeps remote jobs and adds remote searches."),
        MapRows("filters.locations.places", "Areas",
                "A job matches if its location contains one of an area's places. The first place in each area is the one "
                "searched for by name.",
                row_noun="area", add_label="Add an area", id_key="area", value_key="places",
                summary=lambda r: _places_line(r, "places"),
                fields=(Text("area", "Area name", messages=_must("Give this area a name.")),
                        Lines("places", "Places", "One per line. Keep the one to search for on top.", noun="place",
                              first_means="Place searched for in {row}", messages=_must("Add at least one place.")))),
    )),
    Group("looking", "What you're looking for", SEARCH, _looking, fields=(
        Area("wants", "In your own words", "The model reads this to decide what counts as relevant, and what doesn't.",
             messages=_must("Say what you're looking for. The model judges every job against it.")),
        Rows("position_types", "Kinds of role", "A job passes if its title contains one of a kind's titles.",
             row_noun="kind of role", add_label="Add a kind of role", messages=_must("Keep at least one kind of role. Searches are built from them."),
             summary=lambda r: _titles_line(r, "titles"),
             fields=(Text("name", "Name", "The model reads this name. It doesn't affect matching.", messages=_must("Give this a name.")),
                     Lines("titles", "Titles", "One per line, whole words, any case. The top one is what gets searched for.",
                           noun="title", first_means="Title searched for in {row}", messages=_must("Add at least one title.")))),
        Lines("filters.exclude_titles", "Words that rule out a title",
              "One per line. Any match drops the job. “Staff” won't drop “member of technical staff” if that is one of your titles.",
              noun="word"),
    )),
    Group("keep", "Which jobs to keep", SEARCH, _keep, fields=(
        Number("filters.min_hourly_pay", "Minimum pay", "Stated pay clearly below this is dropped. Unstated pay stays.",
               whole=False, pre="$", unit="an hour"),
        Number("filters.max_age_days", "Oldest job", "Older postings are dropped.", unit="days"),
        Number("filters.max_years", "Most experience asked for",
               "Jobs asking for more are dropped. The scoring prompt separately assumes 2.", unit="years"),
        Toggle("filters.full_time_only", "Full-time only", "Drops jobs a source marks part-time, contract, temporary or internship."),
        Toggle("filters.new_grad_only", "New grad only", "Keeps only jobs with a sign they're for new grads."),
        Lines("filters.blocklist", "Companies to hide", "One per line. These never appear on your list.", noun="company"),
    )),
    Group("daily", "Your daily list", SEARCH, _daily, fields=(
        Number("ranking.top_n", "Jobs on the list", "How many the day's list shows.", unit="jobs"),
        Number("ranking.per_company", "Most from one company", "Leave blank for no limit.", blank=MISSING, unit="jobs"),
    ), fine=(
        Number("ranking.max_candidates", "Most jobs the model reads", "Sets the nightly question budget.", unit="jobs"),
        Number("ranking.threshold", "Points to reach the model", "Watchlist companies skip this.", unit="points"),
        MapRows("ranking.fresh_bonus", "Freshness bonus", "Added to the model's score when ordering the list. Leave blank for no bonus.",
                blank=MISSING, row_noun="bonus", add_label="Add a bonus", id_key="days", value_key="bonus",
                messages={"additionalProperties": "Days must be a whole number from 1 to 999."},
                title=lambda r: f"Within {r.get('days', '')} days", summary=lambda r: f"+{r.get('bonus', '')} points",
                fields=(Text("days", "Within this many days", messages=_must("Enter a number of days.")),
                        Number("bonus", "Add", unit="points", messages=_must("Enter the points to add.")))),
    )),
    Group("watchlist", "Watchlist", SEARCH, _watching, link="/watchlist"),
    Group("skills", "Skills and interests", ABOUT, _skills, fields=(
        Lines("skills", "Skills", "One per line. Each match in a job's text earns points, and the model reads the whole list.", noun="skill"),
        Lines("interests", "Interests", "One per line.", noun="interest"),
    )),
    Group("projects", "Projects", ABOUT, _projects, fields=(
        Rows("projects", "Projects", "Written once and untailored. The model judges fit from these, never from a resume.",
             row_noun="project", add_label="Add a project",
             summary=lambda r: r.get("summary", "")[:70],
             fields=(Text("name", "Name", messages=_must("Give this project a name.")),
                     Area("summary", "What it is", messages=_must("Say what this project is. It's what the model reads.")),
                     Lines("skills", "Skills used", "One per line. For your own records. Only the name and summary reach the model.",
                           noun="skill"))),
    )),
    Group("education", "Education", ABOUT, _education, fields=(
        Choice("degree.level", "Degree", labels=DEGREES, messages=ANY_CHOICE),
        Text("degree.field", "Field of study", messages=_must("Enter your field of study.")),
        Text("degree.school", "School"),
        Text("degree.graduation", "Graduation", "Year and month.", placeholder="2027-05",
             messages={"pattern": "Use year and month, like 2027-05.", **_must("Use year and month, like 2027-05.")}),
        Lines("coursework", "Coursework", "One per line.", noun="course"),
    )),
    Group("eligibility", "Work eligibility", ABOUT, _eligibility, fields=(
        Choice("filters.work_authorization", "Work authorization", "The model reads this. No job is dropped by it directly.",
               labels=WORK_AUTHORIZATION, messages=ANY_CHOICE),
        Choice("filters.clearance.has", "Security clearance", "Jobs needing more than this are dropped.", labels=CLEARANCE, messages=ANY_CHOICE),
        Toggle("filters.clearance.eligible", "I could obtain a clearance", "The model reads this. No job is dropped by it directly."),
    )),
)

WATCH_TABLE = (
    Group("companies", "Boards and notes", fields=(
        Rows("companies", "Companies", spare=False, row_noun="company",
             summary=lambda r: r.get("board", "").strip() or "no board yet",
             fields=(Text("name", "Company", "Must match the employer name on postings. Renaming can lose its watchlist matches.",
                          messages=_must("Give this company a name. To take it off, use Remove.")),
                     Text("board", "Job board", "Paste the careers page URL, or type it like greenhouse:acme.",
                          blank=None, placeholder="greenhouse:acme", accept=_board,
                          messages={"pattern": "That isn't a job board we can read. Paste its careers page URL, or type it like "
                                               "greenhouse:acme. Systems: greenhouse, lever, ashby, workable, smartrecruiters, "
                                               "recruitee, workday."}),
                     Text("note", "Note", "Your own reminder, shown on this page only.", blank=MISSING))),
    )),
)


def top_fields(table):
    return [f for g in table for f in (*g.fields, *g.fine)]


def leaves():
    """(schema slot, field) for every setting in both tables, sub-fields included."""
    for f in top_fields(TABLE + WATCH_TABLE):
        yield from f.slots()


def blank_doc():
    """What a first profile starts from: every setting whose empty value is valid. The rest stay absent."""
    doc = {}
    for f in top_fields(TABLE + WATCH_TABLE):
        put(doc, f.path, MISSING if f.blank is REQUIRED or f.blank is MISSING else copy.copy(f.blank))
    return doc


def fill_all(doc, table=TABLE):
    raw = {}
    for f in top_fields(table):
        raw |= f.fill(get(doc, f.path))
    return raw


def read_all(raw, ref, table=TABLE):
    """(Reading, document). Starts from ref, so what this table doesn't place survives: the settings
    page can't touch companies, and the Watchlist page can't touch anything else."""
    rd, doc = Reading(raw), copy.deepcopy(ref)
    for f in top_fields(table):
        put(doc, f.path, f.read(rd, f.key, f.path, get(ref, f.path)))
    return rd, doc
