"""Community-kept job lists on GitHub: Simplify-style listings.json, and markdown tables
(speedyapply, jobright)."""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ...store import raw
from .. import boards
from ..items import JobItem, company_norm, title_norm
from . import Adapter

DROP_PARAMS = re.compile(r"^(utm_.*|ref|gh_src|source|src|lever-source.*|lever-origin)$", re.I)


def clean_url(url):
    """The URL without tracking parameters (utm_*, ref, gh_src, ...), so a daily-changing tracker
    doesn't make the same job look new. gh_jid and other ids stay. A job board's application-form
    link becomes the job's page (boards.job_page)."""
    url = (url or "").strip()
    try:
        p = urlsplit(url)
    except ValueError:
        return url
    if not p.scheme.startswith("http"):
        return url
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not DROP_PARAMS.match(k)]
    return boards.job_page(urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), "")))


def _key(*parts):
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]


class SimplifyJSON(Adapter):
    """listings.json from SimplifyJobs-style repos: every listing ever, with an `active` flag."""
    name = "simplify_json"
    snapshot = "flag"

    def fetch(self, ctx, source):
        ctx.get(source, source.url)

    def parse(self, record, source):
        if not raw.ok(record):
            return []
        out = []
        for it in json.loads(raw.body(record)):
            if not isinstance(it, dict) or not it.get("id") or not it.get("title"):
                continue
            url = clean_url(it.get("url"))
            out.append(JobItem(
                item_key=str(it["id"]), title=str(it["title"]).strip(), company=str(it.get("company_name") or "").strip(),
                locations=tuple(str(x) for x in it.get("locations") or ()), url=url, board=boards.board_from_url(url),
                published_at=boards.when(it.get("date_posted")),
                active=bool(it.get("active", True)) and it.get("is_visible", True) is not False,
                extra={k: it[k] for k in ("sponsorship", "degrees", "category", "terms", "season", "company_url")
                       if it.get(k) not in (None, "", [])}))
        return out


# ------------------------------------------------------------------------------ markdown tables

SEPARATOR = re.compile(r"^\|[\s:|-]*-[\s:|-]*$")
LINK_MD = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
LINK_HTML = re.compile(r"""<a\s[^>]*href=["'](https?://[^"']+)["']""", re.I)
COLUMNS = {
    "company": ("company",),
    "title": ("position", "job title", "role", "title"),
    "location": ("location", "locations"),
    "pay": ("salary", "pay", "compensation"),
    "link": ("posting", "apply", "link", "application"),
    "age": ("age", "date posted", "posted", "date"),
    "model": ("work model", "workplace"),
}
AGE = re.compile(r"^(\d+)\s*(h|d|w|mo|m|y)$", re.I)
MONTH_DAY = re.compile(r"^([A-Z][a-z]{2})\s+(\d{1,2})$")


def _cells(row):
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _text(cell):
    t = re.sub(r"<[^>]+>", " ", cell)
    t = LINK_MD.sub(r"\1", t)
    return " ".join(unescape(t).replace("**", "").replace("*", "").split())


def _links(cell):
    return [u for _, u in LINK_MD.findall(cell)] + LINK_HTML.findall(cell)


def md_date(text, fetched_at):
    """A table's age or date as ISO UTC (date precision, so it stays put night after night):
    "5d", "3h", "2w", "1mo" count back from the fetch; "Nov 04" is this year (last year if that
    would be in the future)."""
    text = (text or "").strip()
    day = datetime.fromisoformat(fetched_at.replace("Z", "+00:00")).replace(hour=0, minute=0, second=0, microsecond=0)
    m = AGE.match(text)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        days = {"h": 0, "d": n, "w": 7 * n, "mo": 30 * n, "m": 30 * n, "y": 365 * n}[unit]
        return (day - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    m = MONTH_DAY.match(text)
    if m:
        try:
            d = datetime.strptime(f"{m.group(1)} {m.group(2)} {day.year}", "%b %d %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        if d > day + timedelta(days=1):
            d = d.replace(year=d.year - 1)
        return d.strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def md_tables(text):
    """[(column map {field: index}, [row cells])] for every table in the markdown."""
    lines = text.splitlines()
    out, cur = [], None
    for i, line in enumerate(lines):
        s = line.strip()
        if not s.startswith("|"):
            cur = None
            continue
        if SEPARATOR.match(s):
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if SEPARATOR.match(nxt):
            names = [_text(c).lower() for c in _cells(s)]
            cols = {}
            for field, aliases in COLUMNS.items():
                for j, n in enumerate(names):
                    if n in aliases and field not in cols:
                        cols[field] = j
            cur = (cols, [])
            out.append(cur)
        elif cur is not None:
            cur[1].append(_cells(s))
    return out


class MarkdownTable(Adapter):
    """A README or .md file of job tables. A complete list: a job missing from it has closed."""
    name = "md_table"
    snapshot = "full"

    def fetch(self, ctx, source):
        from ... import urls
        for each, url in urls.expand(source.url, source.each):
            ctx.get(source, url, each=each)

    def parse(self, record, source):
        if not raw.ok(record):
            return []
        out, seen = [], set()
        for cols, rows in md_tables(raw.body(record).decode("utf-8", "replace")):
            if "company" not in cols or "title" not in cols:
                continue
            company = ""
            for cells in rows:
                def cell(field):
                    j = cols.get(field)
                    return cells[j] if j is not None and j < len(cells) else ""
                name = _text(cell("company"))
                if name in ("↳", "") and company:
                    name = company
                company = name
                title = _text(cell("title"))
                if not company or not title:
                    continue
                links = _links(cell("link")) or _links(cell("title"))
                url = clean_url(links[0]) if links else ""
                parts = [_text(x) for x in re.split(r"</?br\s*/?>|;", cell("location"), flags=re.I)]
                parts = [x for x in parts if x]
                more = re.search(r"\s*\+(\d+)$", parts[-1]) if parts else None
                if more:
                    parts[-1] = parts[-1][: more.start()].strip()
                locations = tuple(x for x in parts if x)
                model = _text(cell("model")).lower()
                found = boards.job_from_url(url)
                key = f"job:{found}" if found else _key(company_norm(company), title_norm(title), url)
                if key in seen:
                    continue
                seen.add(key)
                extra = {}
                if more:
                    extra["more_locations"] = int(more.group(1))
                if model:
                    extra["work_model"] = model
                pay = _text(cell("pay"))
                out.append(JobItem(
                    item_key=key, title=title, company=company, locations=locations, url=url,
                    board=boards.board_from_url(url), published_at=md_date(_text(cell("age")), record["fetched_at"]),
                    scope=record.get("each") or "", remote=(True if model == "remote" else None),
                    pay={"summary": pay} if pay else None, extra=extra))
        return out


ADAPTERS = [SimplifyJSON(), MarkdownTable()]
