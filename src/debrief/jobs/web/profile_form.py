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
from dataclasses import dataclass, field as dc_field, replace
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


NOT_SET = "Not set"
STORED_DIFFERENTLY = "Same value, stored differently"


@dataclass(frozen=True)
class Change:
    """One line of "what changed", in a person's words. group and field are the section and the
    top-level setting it belongs to, filled in by changes()."""
    label: str
    was: str | None = None
    now: str | None = None
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    note: str = ""
    group: str = ""
    field: str = ""

    def in_words(self):
        parts = [f"added {_and(self.added)}" if self.added else "", f"removed {_and(self.removed)}" if self.removed else "",
                 f"now {self.now}" if self.now else "", self.note.lower()]
        return f"{self.label} {', '.join(p for p in parts if p)}"


@dataclass(frozen=True)
class RowView:
    index: int
    title: str
    summary: str
    fields: tuple
    open: bool
    spare: bool
    removing: bool
    problems: int
    remove_name: str
    remove_label: str


@dataclass(frozen=True)
class FieldView:
    """All a template macro needs. Templates never see the document."""
    widget: str
    name: str
    id: str
    label: str
    help: str
    value: str = ""
    errors: tuple[str, ...] = ()
    pre: str = ""
    unit: str = ""
    inputmode: str = ""
    placeholder: str = ""
    height: int = 0
    choices: tuple[tuple[str, str], ...] = ()
    rows: tuple[RowView, ...] = ()


@dataclass(frozen=True)
class GroupView:
    id: str
    title: str
    part: str
    summary: str
    open: bool = False
    link: str = ""
    fields: tuple[FieldView, ...] = ()
    fine: tuple[FieldView, ...] = ()
    fine_open: bool = False
    problems: int = 0


@dataclass(frozen=True)
class FormView:
    base: int
    groups: tuple[GroupView, ...]
    page_errors: tuple[str, ...]
    banner: tuple[tuple[str, str, str], ...]   # (input id, label, message) in page order
    budget: int | None


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
    widget = "text"
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

    def text(self, value):
        """Read-only form, for the version page and the change list."""
        return NOT_SET if value is MISSING or value is None else self.to_text(value) or "Blank"

    def said_label(self, row):
        return f"{row}: {self.label}" if row else self.label

    def say(self, old, new, row=""):
        """This setting's lines in a change list. Empty when the words wouldn't differ."""
        was, now = self.text(old), self.text(new)
        if was == now:
            return []
        return [Change(self.said_label(row), was=None if was == NOT_SET else was, now=now)]

    def extras(self, typed):
        return {}

    def blank_in_new_row(self):
        return MISSING

    def view(self, form, at, ref):
        typed = form.raw.get(at, "")
        return FieldView(self.widget, at, _input_id(at), self.label, self.help, typed, tuple(form.errors.get(at, ())),
                         **self.extras(typed))


@dataclass(frozen=True)
class Text(Field):
    """Single-line <input type=text>."""
    blank: Any = ""
    placeholder: str = ""
    accept: Callable[[str], str] | None = None

    def parse(self, typed, ref):
        typed = typed.strip()
        return self.accept(typed) if self.accept else typed

    def extras(self, typed):
        return {"placeholder": self.placeholder}


class _MultiLine:
    def clean(self, typed):
        """<textarea> posts CRLF, and the HTML parser turns a stored lone CR into LF."""
        return typed.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True)
class Area(_MultiLine, Field):
    """Prose. The template writes a newline straight after <textarea>, so a value that itself starts
    with a newline survives the HTML parser."""
    widget = "area"
    blank: Any = ""

    def extras(self, typed):
        return {"height": min(max(len(typed) // 70 + typed.count("\n") + 2, 4), 20)}


@dataclass(frozen=True)
class Lines(_MultiLine, Field):
    """A list of strings, one per line, in order. Lines, not commas: "New York, NY" is one place."""
    widget = "lines"
    blank: Any = dc_field(default_factory=list)
    noun: str = "item"
    first_means: str = ""    # "Place searched for in {row}": what the top line does, for the change list

    def to_text(self, value):
        return "" if value is MISSING else "\n".join(value)

    def parse(self, typed, ref):
        return list(dict.fromkeys(s.strip() for s in typed.split("\n") if s.strip()))

    def text(self, value):
        return NOT_SET if value is MISSING else " · ".join(value) or "None"

    def blank_in_new_row(self):
        return []

    def say(self, old, new, row=""):
        old, new = [] if old is MISSING else old, [] if new is MISSING else new
        added, removed = tuple(x for x in new if x not in old), tuple(x for x in old if x not in new)
        out = []
        if added or removed:
            out.append(Change(self.said_label(row), added=added, removed=removed))
        elif old != new:
            out.append(Change(self.said_label(row), note="Order changed"))
        if self.first_means and old[:1] != new[:1] and old and new:
            out.append(Change(self.first_means.format(row=row), was=old[0], now=new[0]))
        return out

    def extras(self, typed):
        return {"height": min(max(typed.count("\n") + 2, 3), 30)}


@dataclass(frozen=True)
class Number(Field):
    """<input type=text inputmode=...>, not type=number, which blanks what it can't parse and so would
    lose typed input. Typing 25 over a stored 25.0 keeps 25.0, because they are the same number and a
    different spelling would still change the id."""
    widget = "number"
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

    def text(self, value):
        if value is MISSING:
            return NOT_SET
        unit = self.unit[:-1] if value == 1 and self.unit.endswith("s") else self.unit
        shown = _money(value) if self.pre == "$" else f"{self.pre}{value}"
        return f"{shown} {unit}" if unit else shown

    def say(self, old, new, row=""):
        return [] if old is not MISSING and new is not MISSING and old == new else super().say(old, new, row)

    def extras(self, typed):
        return {"pre": self.pre, "unit": self.unit, "inputmode": "numeric" if self.whole else "decimal"}


@dataclass(frozen=True)
class Toggle(Field):
    """A checkbox. Unchecked posts nothing, so absent means False."""
    widget = "toggle"
    blank: Any = False

    def to_text(self, value):
        return "on" if value is True else ""

    def parse(self, typed, ref):
        return True

    def text(self, value):
        return NOT_SET if value is MISSING else "On" if value else "Off"


@dataclass(frozen=True)
class Choice(Field):
    """<select>. labels is {stored value: words}, and a test holds it equal to the schema's enum."""
    widget = "choice"
    labels: Mapping[str, str] = dc_field(default_factory=dict)

    def text(self, value):
        return NOT_SET if value is MISSING else self.labels.get(value, str(value))

    def extras(self, typed):
        """A stored value outside labels gets its own option, so an untouched select can't change it."""
        extra = [("", "Choose")] if not typed else [(typed, typed)] if typed not in self.labels else []
        return {"choices": (*extra, *self.labels.items())}


@dataclass(frozen=True)
class Rows(Field):
    """A list of objects as folded rows, with inputs f"{key}.{n}.{sub.key}" and f"{key}.{n}.remove".
    A row's stored value is found by its id input, so rows need no hidden ids and a renamed row is
    honestly a new row. spare=False is an edit-only list: no blank row and no remove box."""
    widget = "rows"
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
        """The row numbers present in raw. They come from the page, so they only ever order the rows."""
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
                if value is MISSING and not stored:
                    value = s.blank_in_new_row()
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

    def row_name(self, row):
        cells = {s.key: s.to_text(row.get(s.key, MISSING)) for s in self.fields}
        return self.title(cells) if self.title else cells[self.id_key]

    def text(self, value):
        rows = self.as_rows(value)
        if not rows:
            return NOT_SET if value is MISSING else "None"
        details = lambda row: " · ".join(s.text(row.get(s.key, MISSING)) for s in self.fields if s.key != self.id_key)
        return "\n".join(f"{self.row_name(row)}: {details(row)}" for row in rows)

    def say(self, old, new, row=""):
        """Rows pair up by their id input, like read does."""
        pool, added, out, kept_order = self.stored_rows(old), [], [], []
        id_field = self.sub(self.id_key)
        for after in self.as_rows(new):
            found = pool.get(id_field.clean(id_field.to_text(after.get(self.id_key, MISSING))))
            if not found:
                added.append(self.row_name(after))
                continue
            before = found.pop(0)
            kept_order.append(id(before))
            for s in self.fields:
                if s.key != self.id_key:
                    out += s.say(before.get(s.key, MISSING), after.get(s.key, MISSING), self.row_name(after))
        removed = [self.row_name(r) for rows in pool.values() for r in rows]
        head = [Change(self.said_label(row), added=tuple(added), removed=tuple(removed))] if added or removed else []
        return head + out + self._order_note(old, kept_order, row)

    def _order_note(self, old, kept_order, row):
        return [Change(self.said_label(row), note="Order changed")] if [i for i in map(id, self.as_rows(old)) if i in kept_order] != kept_order else []

    def row_view(self, form, at, n, stored, spare=False):
        prefix = f"{at}.{n}."
        cells = {s.key: form.raw.get(prefix + s.key, "") for s in self.fields}
        problems = sum(len(v) for k, v in form.errors.items() if k.startswith(prefix))
        removing = bool(form.raw.get(prefix + "remove"))
        moved = not stored or any(not s.unchanged(form.raw, stored.get(s.key, MISSING), prefix + s.key) for s in self.fields)
        title = (self.title(cells) if self.title else cells.get(self.id_key, "").strip()) or f"New {self.row_noun}"
        return RowView(n, self.add_label if spare else title, "" if spare else self.summary(cells),
                       tuple(s.view(form, prefix + s.key, stored.get(s.key, MISSING)) for s in self.fields),
                       open=bool(problems) or (moved and not spare) or removing, spare=spare, removing=removing,
                       problems=problems, remove_name=prefix + "remove",
                       remove_label=f"Remove this {self.row_noun}" if self.spare and not spare else "")

    def view(self, form, at, ref):
        """Rows come from the inputs, so a re-render keeps what was typed. A blank row is dropped, and
        there is always one fresh spare."""
        pool, rows = self.stored_rows(ref), []
        numbers = self.numbers(form.raw, at)
        for n in numbers:
            cells_blank = not any(form.raw.get(f"{at}.{n}.{s.key}", "").strip() for s in self.fields)
            has_error = any(k.startswith(f"{at}.{n}.") for k in form.errors)
            if self.spare and cells_blank and not has_error:
                continue
            rows.append(self.row_view(form, at, n, self.stored_row(pool, form.raw, at, n)))
        if self.spare:
            rows.append(self.row_view(form, at, max(numbers, default=-1) + 1, {}, spare=True))
        return FieldView(self.widget, at, _input_id(at), self.label, self.help, errors=tuple(form.errors.get(at, ())), rows=tuple(rows))


@dataclass(frozen=True)
class MapRows(Rows):
    """An object as rows: {"NYC": ["New York, NY"]} <-> [{area: "NYC", places: [...]}]. Two rows with
    one name would silently collapse in a dict, so the second is an error."""
    blank: Any = dc_field(default_factory=dict)
    value_key: str = ""

    def as_rows(self, value):
        return [] if value is MISSING else [{self.id_key: k, self.value_key: v} for k, v in value.items()]

    def _order_note(self, old, kept_order, row):
        return []    # an object's key order isn't part of the id

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


def _some(names, keep=3):
    names = list(names)
    return _and(names) if len(names) <= keep + 1 else f"{', '.join(names[:keep])} and {len(names) - keep} more"


def _dots(*parts):
    return " · ".join(p for p in parts if p)


def _money(value):
    return f"${int(value)}" if value == int(value) else f"${value:.2f}"


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
        Rows("projects", "Your projects", "Written once and untailored. The model judges fit from these, never from a resume.",
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
    Group("companies", "Watchlist", fields=(
        Rows("companies", "Companies", "Open a company to change its name, job board or note. To add or remove one, use the controls above.",
             spare=False, row_noun="company",
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


# ------------------------------------------------------------------------------------ the form

def _input_id(at):
    return "i-" + at.replace(".", "-")


def _summary(group, doc):
    """A saved profile can predate a newly required setting, and this page is where it gets fixed."""
    try:
        return group.summary(doc)
    except (KeyError, TypeError):
        return "Something here needs filling in. Press Check to see what."


def _banner(views):
    for v in views:
        for message in v.errors:
            yield v.id, v.label, message
        for row in v.rows:
            for sub in row.fields:
                for message in sub.errors:
                    yield sub.id, f"{row.title}, {sub.label}", message


@dataclass(frozen=True)
class Form:
    """A page's state: the version it is based on, what's in the inputs, and what's wrong with them.
    Errors are keyed by input name, and "" is the page itself."""
    base: int
    raw: Raw
    errors: Mapping[str, list[str]] = dc_field(default_factory=dict)

    def view(self, saved, table=TABLE):
        """saved is version `base`'s document, or None for a first profile, when every section starts open."""
        doc = saved if saved is not None else blank_doc()
        groups = []
        for g in table:
            summary = _summary(g, doc) if saved is not None else "Not set yet"
            if g.link:
                groups.append(GroupView(g.id, g.title, g.part, summary, link=g.link))
                continue
            fields = tuple(f.view(self, f.key, get(doc, f.path)) for f in g.fields)
            fine = tuple(f.view(self, f.key, get(doc, f.path)) for f in g.fine)
            problems = sum(1 for _ in _banner(fields + fine))
            moved = any(not f.unchanged(self.raw, get(doc, f.path)) for f in g.fields)
            fine_moved = any(not f.unchanged(self.raw, get(doc, f.path)) for f in g.fine)
            fine_problems = sum(1 for _ in _banner(fine))
            groups.append(GroupView(g.id, g.title, g.part, summary, open=saved is None or bool(problems) or moved or fine_moved,
                                    fields=fields, fine=fine, fine_open=saved is None or bool(fine_problems) or fine_moved, problems=problems))
        banner = tuple(item for g in groups for item in _banner(g.fields + g.fine))
        budget = get(doc, ("ranking", "max_candidates"))
        return FormView(self.base, tuple(groups), tuple(self.errors.get("", ())), banner, None if budget is MISSING else budget)


@dataclass(frozen=True)
class Submission:
    form: Form                  # what to re-render, merged onto the latest version
    doc: dict | None            # what to save; None if form.errors
    kept: tuple[str, ...] = ()  # labels of settings that changed elsewhere and were left as they are now
    conflict: bool = False
    changes: tuple[Change, ...] = ()    # latest -> doc

    @property
    def _kept_words(self):
        return f" A change made elsewhere to {_and(self.kept)} was kept." if self.kept else ""

    @property
    def check_message(self):
        if not self.changes:
            return "No problems found, and nothing here differs from what's saved." + self._kept_words
        return "No problems found. Nothing is saved until you press Save. Saving would change the settings listed below." + self._kept_words

    def saved_message(self, version, saved):
        said = f"Saved as version {version}. It applies at the next nightly run." if saved else f"No change: it's the same as version {version}."
        return said + self._kept_words


def open_form(version, doc, table=TABLE):
    """A fresh page. No profile yet renders every input blank, with base 0."""
    return Form(version, fill_all(doc if doc is not None else blank_doc(), table))


def submit(posted, latest, load, table=TABLE):
    """Everything between a POST and the decision to write. Nothing here writes."""
    latest_n, latest_doc = latest
    try:
        base_n = int(posted.get("base") or "")
    except ValueError:
        base_n = None
    base_doc = blank_doc() if base_n == 0 else load(base_n) if base_n else None
    if base_doc is None:
        return Submission(Form(latest_n, open_form(latest_n, latest_doc, table).raw,
                               {"": ["This page was out of date, so here is the current profile. Nothing was saved."]}), None)
    raw = {k: str(v) for k, v in posted.items() if k not in ("base", "action", "note")}
    latest_or_blank = latest_doc if latest_doc is not None else blank_doc()
    conflicts, kept = {}, ()
    if base_n != latest_n:
        raw, conflicts, kept = _rebase(raw, base_doc, latest_or_blank, table)
    rd, doc = read_all(raw, latest_or_blank, table)
    errors = {**conflicts}
    for found in (rd.failed, _plain(profiles.issues(doc), rd)):
        for at, messages in found.items():
            errors.setdefault(at, []).extend(messages)
    return Submission(Form(latest_n, raw, errors), None if errors else doc, kept, bool(conflicts),
                      changes(latest_or_blank, doc) if not errors else ())


def _rebase(raw, base, latest, table):
    """Three-way merge on the inputs, so a re-render and a save see the same thing. Saved versions
    never change, so a page only has to carry its version number. A setting this page didn't touch
    takes the latest value. One that both changed stops the save with an explanation, and the page
    is then based on the latest, so a second Save knowingly wins. That is the only way a page can
    overwrite another write."""
    merged, conflicts, kept = {}, {}, []
    for f in top_fields(table):
        was, now = get(base, f.path), get(latest, f.path)
        if f.unchanged(raw, was):
            merged |= f.fill(now)
            if not same(was, now):
                kept.append(f.label)
        else:
            merged |= {k: v for k, v in raw.items() if k == f.key or k.startswith(f.key + ".")}
            if not same(was, now) and not f.unchanged(raw, now):
                told = "; ".join(c.in_words() for c in f.say(was, now)) or f"{f.label} changed"
                conflicts[f.key] = [CONFLICT.format(told=told)]
    for f in top_fields(TABLE + WATCH_TABLE):
        if f not in top_fields(table) and not same(get(base, f.path), get(latest, f.path)):
            kept.append(f.label)
    return merged, conflicts, tuple(kept)


CONFLICT = ("Changed elsewhere while this page was open: {told}. Your version is here and doesn't include that. "
            "Press Save again to use yours, or start over from the current profile.")


def changes(old, new):
    """The human change list, in table order. Non-empty exactly when the id would change: a difference
    with no visible wording (25 -> 25.0, board null -> absent) still gets a line."""
    out = []
    for g in TABLE + WATCH_TABLE:
        for f in (*g.fields, *g.fine):
            was, now = get(old, f.path), get(new, f.path)
            if not same(was, now):
                said = f.say(was, now) or [Change(f.label, note=STORED_DIFFERENTLY)]
                out += [replace(c, group=g.title, field=f.label) for c in said]
    if not out and not same(old, new):
        out.append(Change("Other settings", note=STORED_DIFFERENTLY, group="Other", field="Other settings"))
    return tuple(out)


def add_refused(name, doc):
    """Why a company can't be added, for a save that refused it after the board lookup."""
    hidden = {b.lower() for b in get(doc, ("filters", "blocklist"))}
    if name.lower() in hidden:
        return f"{name} is in Companies to hide, so it can't be watched too. Take it off under Which jobs to keep on the Profile page first."
    return f"{name} couldn't be added: the board it found doesn't fit the profile. Paste its careers page URL to try another."


def by_group(found):
    """[(section title, [changes])], for drawing, in the order given."""
    groups = {}
    for c in found:
        groups.setdefault(c.group, []).append(c)
    return list(groups.items())


def changed_fields(found):
    return list(dict.fromkeys(c.field for c in found))


def read_only(doc):
    """[(section title, [(label, value text)])] for "Everything in this version"."""
    return [(g.title, [(f.label, f.text(get(doc, f.path))) for f in (*g.fields, *g.fine)])
            for g in TABLE + WATCH_TABLE if not g.link]


def plain_problems(doc):
    """"Label: message" lines for a whole document with no form behind it (a restore)."""
    both = TABLE + WATCH_TABLE
    rd, _ = read_all(fill_all(doc, both), doc, both)
    return [f"{rd.fields[at].label}: {m}" if at else m for at, messages in _plain(profiles.issues(doc), rd).items() for m in messages]


KEYWORD_WORDING = {
    "minimum": "Must be at least {limit}.",
    "maximum": "Must be {limit} or less.",
    "minLength": "Can't be blank.",
    "minItems": "Add at least one.",
    "minProperties": "Add at least one.",
    "uniqueItems": "Something here is listed twice.",
    "enum": "Pick one of the options.",
    "pattern": "That doesn't look right.",
    "type": "That doesn't look right.",
    "required": "Can't be blank.",
    "additionalProperties": "This profile has a setting this page doesn't know about, so it can't be saved here.",
}

CROSS_WORDING = {
    "type_twice": "Two kinds of role are called {value}. Rename or remove one.",
    "title_excluded": "“{value}” is also in Words that rule out a title, so it could never match. Take it out of one.",
    "company_twice": "{value} is listed twice.",
    "company_blocked": "{value} is on both your Watchlist and Companies to hide. Take it off one of the two.",
    "top_n_over": "Can't be more than Most jobs the model reads ({limit}), or the list could never fill.",
    "nowhere": "Turn this on or add an area. With neither, every job is filtered out.",
}

ELSEWHERE = "A setting on another page needs fixing before this can be saved. Press Check on the Profile page to see it."

# The other side of a rule that spans the two pages, for the page that can't edit where it points.
OTHER_SIDE = {"company_blocked": ("filters", "blocklist")}


def _place(issue, rd):
    for path in (issue.path, OTHER_SIDE.get(issue.rule, ())):
        for n in range(len(path), 0, -1):
            if path[:n] in rd.places:
                return rd.places[path[:n]]
    return ""


def _plain(issues, rd):
    """profile.Issue -> {input name: [plain sentence]}. A field that already said what's wrong says nothing more."""
    out = defaultdict(list)
    for i in issues:
        at = _place(i, rd)
        if at in rd.failed:
            continue
        field = rd.fields.get(at)
        wording = (field.messages.get(i.rule) if field else None) or CROSS_WORDING.get(i.rule)
        wording = wording or (ELSEWHERE if not field and i.rule == "required" else KEYWORD_WORDING[i.rule])
        message = wording.format(limit=i.limit, value=i.value)
        if message not in out[at]:
            out[at].append(message)
    return out
