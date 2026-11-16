"""The site's pages as finished HTML: a day's page from its brief.json (and its picture, picture.py),
the archive list, the "How Debrief works" page (templates/about.md), the 404 page, and the Atom
feed. templates/page.html is the shell every page shares (its head, the Content-Security-Policy),
templates/site.css the one stylesheet, and each page loads one script. On a day's page,
templates/progress.js marks the rail's link to the part being read and draws the reading-progress
bar. On the front page, templates/calendar.js pages the calendar, keeps it on the month being read,
and shows what a day held when it's pointed at. Without them every page still reads in full.

Every string from the data is escaped. Prose gets only **bold** and *italic*. Links come only from
the data's url fields, and only http(s) ones. Each section has id="s-N" and data-stories (its story
ids), and every story row data-story, so later features can point at a part of the page.
"""
import hashlib
import html
import re
from bisect import bisect_left
from calendar import monthrange
from dataclasses import dataclass
from datetime import date as _date
from itertools import groupby
from string import Template
from urllib.parse import urlsplit

from ..brief.day import host
from ..brief.render import SECTION_NAMES, SPLIT_LABELS, chart_value
from ..config import REPO

TEMPLATES = REPO / "templates"
SITE = "https://debrief.alexwoodka.com"
TAGLINE = "Your morning read on the AI/ML landscape."
PROGRESS = "continuous"   # the bar across the top of a day's page while reading: "continuous" or "none"
NO_BRIEF = "The brief didn't run; every story kept is listed."
LEGEND = ('<p class="legend"><span class="on-light">Darker</span><span class="on-dark">Brighter</span> days drew more '
          "discussion. Striped days weren't measured.</p>")
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
SOURCE_NAMES = {"hn": "Hacker News", "lesswrong": "LessWrong", "alignment-forum": "Alignment Forum",
                "hf-paper-comments": "Hugging Face"}
SHORT_NAMES = {"hn": "HN", "lesswrong": "LessWrong", "alignment-forum": "AF", "hf-paper-comments": "HF"}
KARMA = {"lesswrong", "alignment-forum"}
LANDMARKS = {"what-happened": ("What happened", "what_happened"), "updates": ("Updates", "updates"),
             "scan": ("Everything kept today", "scan")}
_BOLD = re.compile(r"\*\*(\S(?:.*?\S)?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*(\S(?:.*?\S)?)\*(?![\w*])")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
JUNK_GISTS = {   # what a scraped page can say in place of its story; a scan row shows no gist for these
    "asks for JavaScript": r"\benable JavaScript\b|\bJavaScript (?:is )?(?:required|disabled)\b",
    "a cookie notice": r"\b(?:we|this (?:site|website)) uses? cookies\b|\baccept (?:all )?cookies\b",
    "a sign-up or paywall prompt": r"\b(?:subscribe|sign in|log in|register) to (?:read|continue)\b|\benter your email\b",
    "a bot check": r"\bverify (?:that )?you are (?:a )?human\b|\bchecking your browser\b",
    "a newsletter's note about itself": r"^This (?:story|article) (?:originally )?appeared in\b"
                                        r"|^This is [^.]{1,40}, an? (?:daily|weekly|monthly) newsletter\b",
}
_JUNK = re.compile("|".join(f"(?:{p})" for p in JUNK_GISTS.values()), re.I)


@dataclass(frozen=True)
class Issue:
    """One issue in the archive. `version` is "v2" (a validated runs/<date>/brief.json) or "v1" (a
    frozen page from <data>/site/v1/). `headline` is v2's headline or the line v1's build chose, and
    may be "". `comments` is the count on the threads of the day's stories, or None when the day
    wasn't measured: every v1 day, and a night whose triage ran on rules alone. An Issue is also a
    feed entry: date, title, url and summary."""
    date: str
    version: str
    headline: str
    comments: int | None = None

    @property
    def summary(self):
        return self.headline or (NO_BRIEF if self.version == "v2" else "")

    @property
    def title(self):
        return plain(self.headline) or fmt_date(self.date)

    @property
    def url(self):
        return f"{SITE}/{self.date}/"


@dataclass(frozen=True)
class Neighbors:
    """Where a day's page sits among all the issues, v1 and v2: the dates just before and after it
    (None at either end), and every date that has an issue."""
    prev: str | None = None
    next: str | None = None
    known: frozenset = frozenset()


def esc(s):
    return html.escape("" if s is None else str(s))


def plain(s):
    """Prose without its **bold** and *italic* markers, for titles, feeds and the archive."""
    return _ITALIC.sub(r"\1", _BOLD.sub(r"\1", s or ""))


def inline(s):
    """Escaped prose with **bold** and *italic*, and nothing else."""
    return _ITALIC.sub(r"<em>\1</em>", _BOLD.sub(r"<strong>\1</strong>", esc(s)))


def paras(s):
    return "".join(f"<p>{inline(p.strip())}</p>" for p in re.split(r"\n{2,}", s or "") if p.strip())


def link(inner, url):
    """`inner` (already HTML) as a link to url, or unlinked when url isn't http(s)."""
    return f'<a href="{esc(url)}">{inner}</a>' if re.match(r"https?://", url or "", re.I) else inner


def fmt_date(iso):
    """'2026-10-23' -> 'Friday, October 23, 2026' (v1's format)."""
    y, m, d = (int(x) for x in iso[:10].split("-"))
    return f"{DAYS[_date(y, m, d).weekday()]}, {MONTHS[m - 1]} {d}, {y}"


def short_date(iso):
    y, m, d = (int(x) for x in iso[:10].split("-"))
    return f"{MONTHS[m - 1][:3]} {d}"


def _count(n, one, many):
    return f"{n:,} {one if n == 1 else many}"


def _span(first, last):
    """'September 14 to October 25, 2026', with the year once when both dates share it."""
    a, b = fmt_date(first).split(", ", 1)[1], fmt_date(last).split(", ", 1)[1]
    if first == last:
        return b
    return f"{a.rsplit(', ', 1)[0]} to {b}" if first[:4] == last[:4] else f"{a} to {b}"


def _ids(xs):
    return esc(" ".join(xs))


def _cut(s, n=60):
    """`s` cut at a word boundary to about n characters, with an ellipsis when it was cut."""
    return s if len(s) <= n else s[:n + 1].rsplit(" ", 1)[0].rstrip(",;:·-") + "…"


def _fingerprinted(name):
    """templates/<name>'s URL with ?v= the start of its sha256. build.py publishes the file byte for byte, so a
    changed file gets a new URL and no cache can serve the old one under a new page."""
    return f"{name}?v={hashlib.sha256((TEMPLATES / name).read_bytes()).hexdigest()[:10]}"


def _shell(title, root, body, head="", script="progress.js"):
    """A finished page in templates/page.html. `root` is the path from the page to the site's root."""
    return Template((TEMPLATES / "page.html").read_text()).substitute(title=esc(title), root=root, head=head, body=body,
                                                                      progress=PROGRESS, css=_fingerprinted("site.css"),
                                                                      script=_fingerprinted(script))


def _head(description, url, og_title, og_type):
    return "\n".join([
        f'<meta name="description" content="{esc(description)}">',
        f'<meta property="og:title" content="{esc(og_title)}">',
        f'<meta property="og:description" content="{esc(description)}">',
        f'<meta property="og:type" content="{og_type}">',
        f'<meta property="og:url" content="{esc(url)}">',
        '<meta property="og:site_name" content="Debrief">',
        f'<link rel="canonical" href="{esc(url)}">',
    ])


def _links(root, about=True):
    how = f' · <a href="{root}about/">How Debrief works</a>' if about else ""
    return f'<p><a href="{root}">All issues</a> · <a href="{root}feed.xml">RSS</a>{how}</p>'


def _thread_counts(story):
    """Each thread with comments (at most 2), most discussed first: 'HN · 273 points · 84 comments'."""
    out = []
    for t in sorted((t for t in story["threads"] if t["comments"]), key=lambda t: -t["comments"])[:2]:
        bits = [SHORT_NAMES.get(t["source"], t["source"])]
        if t["points"] is not None:
            bits.append(f"{t['points']:,} karma" if t["source"] in KARMA else _count(t["points"], "point", "points"))
        bits.append(_count(t["comments"], "comment", "comments"))
        out.append(link(esc(" · ".join(bits)), t["url"]))
    return f' <span class="counts">{", ".join(out)}</span>' if out else ""


def _story_link(doc, sid):
    st = doc["stories"].get(sid)
    return link(esc(st["title"]), st["url"]) if st else ""


def _landmarks(doc):
    """The unnumbered parts this issue has, as (anchor, name), in contents order."""
    return [(anchor, name) for anchor, (name, key) in LANDMARKS.items() if doc[key]]


def _contents(doc):
    """The list both contents renderings share: the numbered sections, then the other parts."""
    sections = "".join(f'<li><a href="#s-{n}">{inline(s["heading"])}</a></li>' for n, s in enumerate(doc["sections"], 1))
    others = "".join(f'<li><a href="#{a}">{name}</a></li>' for a, name in _landmarks(doc))
    return (f'<ol class="toc">{sections}</ol>' if sections else "") + (f'<ul class="toc-more">{others}</ul>' if others else "")


def _issue_nav(nav, label):
    """Links to the issues before and after this one, each named by label(date)."""
    links = []
    if nav.prev:
        links.append(f'<a rel="prev" href="../{nav.prev}/">← {esc(label(nav.prev))}</a>')
    if nav.next:
        links.append(f'<a rel="next" href="../{nav.next}/">{esc(label(nav.next))} →</a>')
    return f'<nav class="issue-nav" aria-label="Other issues">{"".join(links)}</nav>' if links else ""


def _rail(doc, nav):
    return (f'<aside class="rail"><a class="wordmark" href="../">Debrief</a>'
            f'<p class="rail-date">{esc(fmt_date(doc["date"]))}</p>'
            f'<nav aria-label="Contents">{_contents(doc)}</nav>{_issue_nav(nav, short_date)}</aside>')


def _title(doc):
    m = doc["meta"]
    stats = _count(m["kept"], "story", "stories")
    if doc["mode"] == "written":
        stats = f"{_count(m['words'], 'word', 'words')} · {stats}"
    out = ['<header class="title"><div class="masthead"><a class="wordmark" href="../">Debrief</a></div>',
           f'<p class="meta caps">{stats}</p><h1>{esc(fmt_date(doc["date"]))}</h1>']
    if doc["headline"]:
        out.append(f'<p class="standfirst">{inline(doc["headline"])}</p>')
    return "".join(out) + "</header>"


def _figure(picture):
    if not picture:
        return ""
    return (f'<figure class="picture"><img src="picture.svg" width="1200" height="500" alt="{esc(picture.alt)}">'
            f'<figcaption>{esc(picture.caption)}</figcaption></figure>')


def _figure_of_the_day(doc):
    """The figure's value, large, then its caption with a § link to the section that covers its story."""
    fig = doc.get("figure")
    if not fig:
        return ""
    anchor = _anchors(doc).get(fig["story"], "")
    where = f' <a href="#{anchor}">§{anchor[2:]}</a>' if anchor.startswith("s-") else ""
    return (f'<section class="fig"><h2 class="kicker caps">Figure of the day</h2><p class="value">{esc(fig["value"])}</p>'
            f'<p class="caption">{inline(fig["caption"])}{where}</p></section>')


def _inline_contents(doc):
    return (f'<details class="toc-inline"><summary class="caps">In this issue</summary>'
            f'<nav aria-label="Contents">{_contents(doc)}</nav></details>')


def _what_happened(doc):
    if not doc["what_happened"]:
        return ""
    items = "".join(f'<li data-stories="{_ids(e["stories"])}">{inline(e["text"])}</li>' for e in doc["what_happened"])
    return f'<section class="what" id="what-happened"><h2 class="kicker caps">{LANDMARKS["what-happened"][0]}</h2><ul>{items}</ul></section>'


def _lead(doc):
    return f'<section class="lead">{paras(doc["lead"])}</section>' if doc["lead"] else ""


def _quote(q):
    """A quote signed by its author and source, else by the site it came from ("wired.com"), else
    not signed."""
    who = ", ".join(x for x in (q["author"], SOURCE_NAMES.get(q["source"], q["source"])) if x)
    if not who and re.match(r"https?://", q["url"] or "", re.I):
        who = host(q["url"])
    sign = f'<p class="who">— {link(esc(who), q["url"])}</p>' if who else ""
    return f'<blockquote data-story="{esc(q["story"])}"><p>{esc(q["text"])}</p>{sign}</blockquote>'


def _width(value, top):
    """A bar's length in whole percent of the track, never 0 for a value above 0."""
    return max(1, round(100 * value / top)) if value else 0


def _chart(chart):
    """The chart as HTML bars: each label above its bar, the value at the bar's end, the caption
    under them. Bars are scaled to the largest value, or to 100 when the unit is "%". The page can't
    set a width inline (its Content-Security-Policy allows no inline styles), so a bar takes the
    class wN, which site.css makes N% of the track."""
    values = [b["value"] for b in chart["bars"]]
    top = max(100, *values) if chart["unit"] == "%" else max(values) or 1
    bars = "".join(f'<span class="lab">{inline(b["label"])}</span><span class="track"><span class="fill w{_width(b["value"], top)}">'
                   f'</span></span><span class="val">{esc(chart_value(b["value"], chart["unit"]))}</span>' for b in chart["bars"])
    return f'<figure class="chart"><div class="bars">{bars}</div><figcaption>{inline(chart["caption"])}</figcaption></figure>'


def _section(doc, n, s):
    out = [f'<section class="theme" id="s-{n}" data-stories="{_ids(s["stories"])}">',
           f'<h2><span class="num">{n}</span> {inline(s["heading"])}</h2>', paras(s["body"])]
    chart = doc.get("chart")
    if chart and chart["section"] == n:
        out.append(_chart(chart))
    if s.get("split"):
        out.append('<div class="split">' + "".join(f'<div class="arg {key}"><p class="label caps">{label}</p><p>{inline(s["split"][key])}</p></div>'
                                                   for key, label in SPLIT_LABELS.items()) + "</div>")
    out += [_quote(q) for q in s["quotes"]]
    srcs = [f"<li>{_story_link(doc, sid)}{_thread_counts(doc['stories'][sid])}</li>" for sid in s["stories"] if sid in doc["stories"]]
    if srcs:
        out.append(f'<div class="sources"><p class="label caps">Sources</p><ul>{"".join(srcs)}</ul></div>')
    return "".join(out) + "</section>"


def _updates(doc, known):
    if not doc["updates"]:
        return ""
    rows = []
    for u in doc["updates"]:
        since, d = "", (u["prev_reported"] or "")[:10]
        if d:
            when = esc(short_date(d))
            if d in known:
                when = f'<a href="../{d}/">{when}</a>'
            since = f'<p class="since">first reported {when}</p>'
        rows.append(f'<div class="update" data-story="{esc(u["story"])}"><h3>{_story_link(doc, u["story"])}</h3>{since}'
                    f'<p>{inline(u["text"])}</p></div>')
    return f'<section class="updates" id="updates"><h2 class="kicker caps">{LANDMARKS["updates"][0]}</h2>{"".join(rows)}</section>'


def _notes(doc):
    if not doc["notes"]:
        return ""
    if doc["mode"] == "scan_only":
        return '<div class="notice">' + "".join(f"<p>{inline(n)}</p>" for n in doc["notes"]) + "</div>"
    items = "".join(f"<li>{inline(n)}</li>" for n in doc["notes"])
    return f'<section class="notes"><h2 class="kicker caps">Notes</h2><ul>{items}</ul></section>'


def _anchors(doc):
    """story id -> where the brief covers it: its first section, else the updates or the top list."""
    out = {}
    for n, s in enumerate(doc["sections"], 1):
        for sid in [*s["stories"], *(q["story"] for q in s["quotes"])]:
            out.setdefault(sid, f"s-{n}")
    for u in doc["updates"]:
        out.setdefault(u["story"], "updates")
    for e in doc["what_happened"]:
        for sid in e["stories"]:
            out.setdefault(sid, "what-happened")
    return out


def _where(anchor):
    """The mark a covered scan row carries: §N for a section, else the part's name."""
    return f"§{anchor[2:]}" if anchor.startswith("s-") else LANDMARKS[anchor][0]


def _scan(doc):
    if not doc["scan"]:
        return ""
    anchors = _anchors(doc)
    out = [f'<section class="scan" id="scan"><h2 class="kicker caps">{LANDMARKS["scan"][0]}</h2>']
    for section, rows in groupby(doc["scan"], key=lambda x: x["section"]):
        out.append(f'<h3 class="group caps">{esc(SECTION_NAMES.get(section, section))}</h3><ul class="scan-list">')
        for x in rows:
            anchor = anchors.get(x["id"]) if x["covered"] else None
            mark = f'<a class="where" href="#{anchor}">{_where(anchor)}</a>' if anchor else ""
            gist = f'<p class="gist">{inline(x["gist"])}</p>' if x["gist"] and not _JUNK.search(x["gist"]) else ""
            out.append(f'<li data-story="{esc(x["id"])}"><div class="row">{link(esc(x["title"]), x["url"])}{mark}</div>{gist}</li>')
        out.append("</ul>")
    return "".join(out) + "</section>"


def _footer(doc, nav, source_names):
    m = doc["meta"]
    kept = f"{_count(m['kept'], 'story', 'stories')} kept today"
    out = [f"<p>{kept}, {m['covered']:,} covered above.</p>" if doc["mode"] == "written" else f"<p>{kept}.</p>"]
    if m["failed_sources"]:
        names = ", ".join(source_names.get(s, s) for s in m["failed_sources"])
        out.append(f"<p>Some sources didn't load today: {esc(names)}.</p>")
    fetched = [f for f in doc["fetches"] if f["allowed"]]
    if fetched:
        pages = "".join(f"<li>{link(esc((urlsplit(f['url']).netloc + urlsplit(f['url']).path)[:80]), f['url'])}"
                        + (f" ({esc(f['why'])})" if f["why"] else "") + "</li>" for f in fetched)
        out.append(f'<p class="label caps">Pages checked for this issue</p><ul class="checked">{pages}</ul>')
    out += [_links("../"), _issue_nav(nav, lambda d: fmt_date(d).rsplit(", ", 1)[0])]
    return '<footer class="foot">' + "".join(out) + "</footer>"


def day_page(doc, nav=Neighbors(), source_names=None, picture=None):
    """The page for one brief.json document (written or scan_only). `nav` places it among the other
    issues; `source_names` maps a source id to the name sources.yml gives it; `picture` is the
    day's picture.Picture, shown under the standfirst."""
    date = doc["date"]
    parts = [_title(doc), _figure(picture), _inline_contents(doc), _figure_of_the_day(doc), _what_happened(doc), _lead(doc),
             *(_section(doc, n, s) for n, s in enumerate(doc["sections"], 1)),
             _updates(doc, nav.known), _notes(doc), _scan(doc)]
    main = "\n".join(p for p in parts if p)
    title = f"Debrief · {short_date(date)}" + (f" · {_cut(plain(doc['headline']))}" if doc["headline"] else "")
    head = _head(plain(doc["headline"]) or NO_BRIEF, f"{SITE}/{date}/", f"Debrief · {fmt_date(date)}", "article")
    return _shell(title, "../", head=head, body=(
        f'<a class="skip" href="#issue">Skip to the issue</a>\n<div class="page day">\n{_rail(doc, nav)}\n'
        f'<main id="issue">\n{main}\n</main>\n{_footer(doc, nav, source_names or {})}\n</div>'))


def _today(e, picture):
    """The newest issue at the top of the front page: its picture, its date and its headline."""
    fig = (f'<figure class="picture"><img src="{e.date}/picture.svg" width="1200" height="500" alt="{esc(picture.alt)}">'
           f'</figure>' if picture else "")
    standfirst = f'<p class="standfirst">{inline(e.summary)}</p>' if e.summary else ""
    return (f'<section class="today" data-month="{e.date[:7]}">{fig}<p class="meta caps">Today</p>'
            f'<h2 class="today-date"><a href="{e.date}/">{esc(fmt_date(e.date).rsplit(", ", 1)[0])}</a></h2>{standfirst}'
            f'<p class="read-today"><a href="{e.date}/">Read today\'s issue →</a></p></section>')


def _shades(issues):
    """date -> the day's shade in the calendar: 1, 2 or 3 as its comments rank in the lower, middle
    or upper third of every measured day's, or None when the day wasn't measured."""
    counts = sorted(e.comments for e in issues if e.comments is not None)
    return {e.date: None if e.comments is None else bisect_left(counts, e.comments) * 3 // len(counts) + 1
            for e in issues}


def _discussion(e):
    if e.version == "v1":
        return "first version, not measured"
    return "not measured" if e.comments is None else _count(e.comments, "comment", "comments")


def _calendar(issues):
    """The rail's calendar, a month at a time, Monday first. Only the newest month shows; the others
    wait, hidden, for calendar.js. A day with an issue links to it, shaded by its discussion or
    striped when that wasn't measured, and its label is the line the popover heads with."""
    by_date, shades, months = {e.date: e for e in issues}, _shades(issues), sorted({e.date[:7] for e in issues})
    out = []
    for ym in months:
        y, m = int(ym[:4]), int(ym[5:])
        lead, days = monthrange(y, m)
        cells = [f'<span class="wd" aria-hidden="true">{w}</span>' for w in "MTWTFSS"] + ["<span></span>"] * lead
        for d in range(1, days + 1):
            e = by_date.get(f"{ym}-{d:02d}")
            if not e:
                cells.append(f'<span class="cell" aria-hidden="true">{d}</span>')
                continue
            shade = f"shade-{shades[e.date]}" if shades[e.date] else "unmeasured"
            label = f"{DAYS[_date.fromisoformat(e.date).weekday()][:3]}, {short_date(e.date)} · {_discussion(e)}"
            current = ' aria-current="date"' if e.date == issues[0].date else ""
            cells.append(f'<a class="cell {shade}" href="{e.date}/" aria-label="{esc(label)}" '
                         f'data-title="{esc(plain(e.summary))}"{current}>{d}</a>')
        hidden = "" if ym == months[-1] else " hidden"
        out.append(f'<div class="cal-month" data-month="{ym}"{hidden}><h2>{MONTHS[m - 1]} {y}</h2>'
                   f'<div class="cal-grid">{"".join(cells)}</div></div>')
    buttons = "".join(f'<button type="button" class="cal-nav" data-step="{step}" aria-label="{name} month" hidden>{arrow}</button>'
                      for step, name, arrow in ((-1, "Previous", "‹"), (1, "Next", "›")))
    return f'<nav class="cal" aria-label="Calendar">{buttons}{"".join(out)}</nav>'


def _since(issues):
    """The line under the calendar. It claims an issue every day only when no date between the
    first issue and the newest is missing one."""
    first, newest = (_date.fromisoformat(issues[i].date) for i in (-1, 0))
    when = fmt_date(issues[-1].date).split(", ", 1)[1]
    every = (newest - first).days + 1 == len(issues)
    return f'<p class="since-line">{"An issue every day since" if every else "Since"} {when}.</p>'


def index_page(issues, pictures=None):
    """The front page: the newest of `issues` (newest first), with its picture from `pictures`
    ({date: picture.Picture}), then the archive of the rest grouped by month. Where the v2 issues
    give way to v1's, a divider says those keep their original layout. On a wide screen the header
    is a rail with a calendar of every issue."""
    v1 = [e.date for e in issues if e.version == "v1"]
    body = [_today(issues[0], (pictures or {}).get(issues[0].date))] if issues else []
    divided = False
    for month, group in groupby(issues[1:], key=lambda e: e.date[:7]):
        body.append(f'<section class="month" data-month="{month}"><h2>{MONTHS[int(month[5:]) - 1]} {month[:4]}</h2>')
        rows = []
        for e in group:
            if e.version == "v1" and not divided:
                if rows:
                    body.append(f'<ul class="issues">{"".join(rows)}</ul>')
                    rows = []
                body.append(f'<div class="divider"><p>The first version · {esc(_span(min(v1), max(v1)))} · '
                            f'These issues keep their original layout.</p></div>')
                divided = True
            y, m, d = (int(x) for x in e.date.split("-"))
            rows.append(f'<li><a href="{esc(e.date)}/"><span class="d">{DAYS[_date(y, m, d).weekday()][:3]} {d}</span>'
                        f'<span class="k">{esc(plain(e.summary))}</span></a></li>')
        body.append(f'<ul class="issues">{"".join(rows)}</ul></section>')
    main = "\n".join(body) or "<p>No issues yet.</p>"
    rail = f"{_calendar(issues)}{LEGEND}{_since(issues)}" if issues else ""
    return _shell("Debrief", "", head=_head(TAGLINE, f"{SITE}/", "Debrief", "website"), script="calendar.js", body=(
        f'<div class="page front">\n<header class="archive-head"><h1 class="wordmark">Debrief</h1>'
        f'<p class="tagline">{esc(TAGLINE)}</p>{rail}</header>\n<main>\n{main}\n</main>\n'
        f'<footer class="foot">{_links("")}</footer>\n</div>'))


def feed(issues):
    """feed.xml: an Atom feed of `issues`, newest first. Updated is each issue's date at midnight UTC,
    and the feed's is the newest issue's, so the same issues give the same bytes."""
    entries = "".join(
        f'<entry>\n<title>{esc(e.title)}</title>\n<id>{esc(e.url)}</id>\n<link rel="alternate" type="text/html" href="{esc(e.url)}"/>\n'
        f'<updated>{e.date}T00:00:00Z</updated>\n<summary>{esc(plain(e.summary))}</summary>\n</entry>\n' for e in issues)
    return ('<?xml version="1.0" encoding="utf-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom">\n'
            f'<title>Debrief</title>\n<subtitle>{esc(TAGLINE)}</subtitle>\n<id>{SITE}/</id>\n'
            f'<link rel="self" type="application/atom+xml" href="{SITE}/feed.xml"/>\n'
            f'<link rel="alternate" type="text/html" href="{SITE}/"/>\n'
            f'<updated>{issues[0].date if issues else "1970-01-01"}T00:00:00Z</updated>\n'
            f'<author><name>Debrief</name><uri>{SITE}/</uri></author>\n{entries}</feed>\n')


def not_found_page():
    """404.html: served at any path, so its links start at the site's root."""
    return _shell("Debrief · Not found", "/", head='<meta name="robots" content="noindex">', body=(
        '<div class="page"><main class="notfound">\n<div class="masthead"><a class="wordmark" href="/">Debrief</a></div>\n'
        '<h1>No issue at this address.</h1>\n<p><a href="/">All issues</a> · <a href="/about/">How Debrief works</a></p>\n</main></div>'))


def _markdown(md):
    """templates/about.md as HTML. It needs only '#' and '##' headings, '- ' lists, paragraphs and
    **bold**, and an email address in it becomes a mailto link."""
    out = []
    for block in re.split(r"\n{2,}", md.strip()):
        if block.startswith("## "):
            out.append(f"<h2>{inline(block[3:])}</h2>")
        elif block.startswith("# "):
            out.append(f"<h1>{inline(block[2:])}</h1>")
        elif block.startswith("- "):
            out.append("<ul>" + "".join(f"<li>{inline(ln[2:])}</li>" for ln in block.splitlines()) + "</ul>")
        else:
            out.append(f"<p>{inline(block)}</p>")
    return _EMAIL.sub(lambda m: f'<a href="mailto:{m[0]}">{m[0]}</a>', "\n".join(out))


def about_page():
    """about/index.html: how Debrief is made, from templates/about.md. Its first line is the title
    and its first paragraph the description link previews show."""
    md = (TEMPLATES / "about.md").read_text()
    title, first = re.split(r"\n{2,}", md.strip())[:2]
    title = title.removeprefix("# ")
    return _shell(title, "../", head=_head(plain(first), f"{SITE}/about/", title, "website"), body=(
        '<div class="page">\n<div class="masthead"><a class="wordmark" href="../">Debrief</a></div>\n'
        f'<main class="about">\n{_markdown(md)}\n</main>\n<footer class="foot">{_links("../", about=False)}</footer>\n</div>'))


def favicon(css):
    """favicon.svg: a serif D in the accent color on the paper color, both read from site.css's
    first (light) token block, so the icon follows the tokens."""
    paper, accent = (re.search(rf"--{name}:\s*([^;]+);", css).group(1).strip() for name in ("paper", "accent"))
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
            f'<rect width="32" height="32" rx="6" fill="{esc(paper)}"/>'
            f'<text x="16" y="24.5" text-anchor="middle" font-family="Charter, Georgia, serif" font-size="24" '
            f'font-weight="700" fill="{esc(accent)}">D</text></svg>\n')
