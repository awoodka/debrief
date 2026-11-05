"""One source's listing of a job (JobItem), and how listings become one posting.

Two listings are the same job when:
  1. they share a job key: the job board's own id for the job, read from the adapter or from the
     URL (boards.job_from_url). Greenhouse, Lever and Ashby ids are global; the others are scoped
     to the company's board; or
  2. no job key decides it, and their fuzzy key matches (normalized company + normalized title)
     and their locations overlap (or one side has none).
A listing whose job key differs from the matched posting's job key is a different job (the same
title posted twice, say, for two cities) and gets a posting of its own.
"""
import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field

from . import boards


@dataclass
class JobItem:
    item_key: str                     # stable within the source
    title: str
    company: str
    locations: tuple = ()
    url: str = ""                     # where to read or apply
    board: str | None = None          # system:id, when known
    job_key: str | None = None        # "job:<system>:<id>", when known (else read from url)
    published_at: str | None = None   # ISO 8601 UTC
    active: bool = True
    scope: str = ""                   # the snapshot this item belongs to (a board, a list)
    remote: bool | None = None
    pay: dict | None = None           # {min, max, currency, period}
    text: str | None = None           # description, when the source gives one
    extra: dict = field(default_factory=dict)

    def as_dict(self):
        d = asdict(self)
        d["locations"] = list(self.locations)
        return d


# ------------------------------------------------------------------------------- normalizing

_SUFFIX = re.compile(r"\b(inc|incorporated|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|plc|gmbh|ag|sa|pbc|"
                     r"pte|pty|bv|nv|lp|llp)\b\.?$")


def _fold(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return text.lower().replace("&", " and ")


def company_norm(name):
    s = re.sub(r"[^a-z0-9+#]+", " ", _fold(name)).strip()
    s = re.sub(r"^the\s+", "", s)
    for _ in range(2):
        s = _SUFFIX.sub("", s).strip()
    return s


_NUMERALS = {"1": "i", "2": "ii", "3": "iii", "4": "iv"}
_COUNTRY_TAIL = (["united", "states"], ["usa"], ["us"])


def title_norm(title):
    """Lowercase words only, so punctuation and dashes don't matter; "Engineer 1" reads as
    "Engineer I"; a trailing country ("... - US", "... - United States") is dropped."""
    toks = [_NUMERALS.get(t, t) for t in re.sub(r"[^a-z0-9+#]+", " ", _fold(title)).split()]
    stripped = True
    while stripped and len(toks) > 1:
        stripped = False
        for tail in _COUNTRY_TAIL:
            if len(toks) > len(tail) and toks[-len(tail):] == tail:
                toks, stripped = toks[:-len(tail)], True
    return " ".join(toks)


def place_tokens(locations):
    """City-level tokens of a location list, for the overlap test: "San Francisco, CA" -> "san francisco"."""
    out = set()
    for loc in locations or ():
        for part in re.split(r"[;/|]| or ", _fold(loc)):
            city = part.split(",")[0].strip()
            city = re.sub(r"[^a-z ]+", " ", city).strip()
            if city:
                out.add(" ".join(city.split()))
    return out


def locations_overlap(a, b):
    ta, tb = place_tokens(a), place_tokens(b)
    if not ta or not tb:
        return True
    if ta & tb:
        return True
    return _remote(ta) and _remote(tb)


_REMOTE_PLACES = {"united states", "us", "usa", "anywhere", "worldwide"}


def _remote(tokens):
    return any("remote" in t.split() or t in _REMOTE_PLACES for t in tokens)


# Words that mark a role as early-career; the core title ignores them, plus class years and a
# trailing level "I", so "New Grad 2027: AI Engineer" and "AI Engineer - New Grad" share a core.
_EARLY = {"grad", "grads", "graduate", "graduates", "college", "university", "entry", "junior", "jr"}
_CORE_IGNORE = _EARLY | {"new", "early", "career", "level", "i", "start"}           # "(2027 Start)"
_YEAR = re.compile(r"^20[2-3]\d$")


def early_career(title):
    toks = title_norm(title).split()
    return (bool(_EARLY & set(toks)) or any(_YEAR.match(t) for t in toks) or toks[-1:] == ["i"]
            or "early career" in " ".join(toks))


def core_title(title):
    """The title's words without the early-career ones, as a set (word order ignored)."""
    return frozenset(t for t in title_norm(title).split() if t not in _CORE_IGNORE and not _YEAR.match(t))


def fuzzy_key(company, title):
    c, t = company_norm(company), title_norm(title)
    return f"fz:{c}|{t}" if c and t else None


def job_key(item):
    if item.job_key:
        return item.job_key
    found = boards.job_from_url(item.url)
    return f"job:{found}" if found else None


def posting_id_for(key):
    return "p:" + hashlib.sha256(key.encode()).hexdigest()[:16]


def content_hash(item):
    return hashlib.sha256(json.dumps(item.as_dict(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
