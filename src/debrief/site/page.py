"""The site's pages as finished HTML: a day's page from its brief.json, the archive list, the 404
page, and the Atom feed. The pages run no script. templates/page.html is the shell every page
shares (its head, the Content-Security-Policy), and templates/site.css the one stylesheet.

Every string from the data is escaped. Prose gets only **bold** and *italic*. Links come only from
the data's url fields, and only http(s) ones. Each section has id="s-N" and data-stories (its story
ids), and every story row data-story, so later features can point at a part of the page.
"""
import html
import re
from dataclasses import dataclass
from datetime import date as _date
from itertools import groupby
from string import Template
from urllib.parse import urlsplit

from ..brief.render import SECTION_NAMES, SPLIT_LABELS
from ..config import REPO

TEMPLATES = REPO / "templates"
SITE = "https://debrief.alexwoodka.com"
TAGLINE = "Your morning read on the AI/ML landscape."
NO_BRIEF = "The brief didn't run; every story kept is listed."
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


@dataclass(frozen=True)
class Issue:
    """One issue in the archive. `version` is "v2" (a validated runs/<date>/brief.json) or "v1" (a
    frozen page from <data>/site/v1/). `headline` is v2's headline or the line v1's build chose, and
    may be "". An Issue is also a feed entry: date, title, url and summary."""
    date: str
    version: str
    headline: str

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


def _shell(title, root, body, head=""):
    """A finished page in templates/page.html. `root` is the path from the page to the site's root."""
    return Template((TEMPLATES / "page.html").read_text()).substitute(title=esc(title), root=root, head=head, body=body)


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


def _links(root):
    return f'<p><a href="{root}">All issues</a> · <a href="{root}feed.xml">RSS</a></p>'


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
    who = ", ".join(x for x in (q["author"], SOURCE_NAMES.get(q["source"], q["source"])) if x) or "source"
    return (f'<blockquote data-story="{esc(q["story"])}"><p>{esc(q["text"])}</p>'
            f'<p class="who">— {link(esc(who), q["url"])}</p></blockquote>')


def _section(doc, n, s):
    out = [f'<section class="theme" id="s-{n}" data-stories="{_ids(s["stories"])}">',
           f'<h2><span class="num">{n}</span> {inline(s["heading"])}</h2>', paras(s["body"])]
    if s.get("split"):
        out.append('<div class="split">' + "".join(f'<p class="label caps {key}">{label}</p><p>{inline(s["split"][key])}</p>'
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
            out.append(f'<li data-story="{esc(x["id"])}"><div class="row">{link(esc(x["title"]), x["url"])}{mark}</div>'
                       f'<p class="gist">{inline(x["gist"])}</p></li>')
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


def day_page(doc, nav=Neighbors(), source_names=None):
    """The page for one brief.json document (written or scan_only). `nav` places it among the other
    issues; `source_names` maps a source id to the name sources.yml gives it."""
    date = doc["date"]
    parts = [_title(doc), _inline_contents(doc), _what_happened(doc), _lead(doc),
             *(_section(doc, n, s) for n, s in enumerate(doc["sections"], 1)),
             _updates(doc, nav.known), _notes(doc), _scan(doc)]
    main = "\n".join(p for p in parts if p)
    title = f"Debrief · {short_date(date)}" + (f" · {_cut(plain(doc['headline']))}" if doc["headline"] else "")
    head = _head(plain(doc["headline"]) or NO_BRIEF, f"{SITE}/{date}/", f"Debrief · {fmt_date(date)}", "article")
    return _shell(title, "../", head=head, body=(
        f'<a class="skip" href="#issue">Skip to the issue</a>\n<div class="page day">\n{_rail(doc, nav)}\n'
        f'<main id="issue">\n{main}\n</main>\n{_footer(doc, nav, source_names or {})}\n</div>'))


def index_page(issues):
    """The archive: `issues` newest first, grouped by month. Where the v2 issues give way to v1's,
    a divider says those keep their original layout."""
    v1 = [e.date for e in issues if e.version == "v1"]
    body, divided = [], False
    for month, group in groupby(issues, key=lambda e: e.date[:7]):
        group = list(group)
        body.append(f'<section class="month"><h2>{MONTHS[int(month[5:]) - 1]} {month[:4]} '
                    f'<span class="count">{_count(len(group), "issue", "issues")}</span></h2>')
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
    return _shell("Debrief", "", head=_head(TAGLINE, f"{SITE}/", "Debrief", "website"), body=(
        f'<div class="page">\n<header class="archive-head"><h1 class="wordmark">Debrief</h1>'
        f'<p class="tagline">{esc(TAGLINE)}</p></header>\n<main>\n{main}\n</main>\n<footer class="foot">{_links("")}</footer>\n</div>'))


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
        '<h1>No issue at this address.</h1>\n<p><a href="/">All issues</a></p>\n</main></div>'))


def favicon(css):
    """favicon.svg: a serif D in the accent color on the paper color, both read from site.css's
    first (light) token block, so the icon follows the tokens."""
    paper, accent = (re.search(rf"--{name}:\s*([^;]+);", css).group(1).strip() for name in ("paper", "accent"))
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
            f'<rect width="32" height="32" rx="6" fill="{esc(paper)}"/>'
            f'<text x="16" y="24.5" text-anchor="middle" font-family="Charter, Georgia, serif" font-size="24" '
            f'font-weight="700" fill="{esc(accent)}">D</text></svg>\n')
