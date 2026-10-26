"""One day's page, and the archive list, as finished HTML from a brief.json. The page runs no
script: templates/day.html and templates/index.html hold v1's look (its CSS and skeleton), and this
fills them in.

Every string from the data is escaped. Prose gets only **bold** and *italic*. Links come only from
the data's url fields, and only http(s) ones. Each section has id="s-N" and data-stories (its story
ids), and every story row data-story, so later features can point at a part of the page.
"""
import html
import re
from datetime import date as _date
from string import Template
from urllib.parse import urlsplit

from ..brief.render import SECTION_NAMES
from ..config import REPO

TEMPLATES = REPO / "templates"
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
SOURCE_NAMES = {"hn": "Hacker News", "lesswrong": "LessWrong", "alignment-forum": "Alignment Forum",
                "hf-paper-comments": "Hugging Face"}
SHORT_NAMES = {"hn": "HN", "lesswrong": "LessWrong", "alignment-forum": "AF", "hf-paper-comments": "HF"}
KARMA = {"lesswrong", "alignment-forum"}
_BOLD = re.compile(r"\*\*(\S(?:.*?\S)?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*(\S(?:.*?\S)?)\*(?![\w*])")


def esc(s):
    return html.escape("" if s is None else str(s))


def inline(s):
    """Escaped prose with **bold** and *italic*, and nothing else."""
    return _ITALIC.sub(r"<em>\1</em>", _BOLD.sub(r"<strong>\1</strong>", esc(s)))


def paras(s):
    return "".join(f"<p>{inline(p.strip())}</p>" for p in re.split(r"\n{2,}", s or "") if p.strip())


def link(inner, url, cls=None):
    """`inner` (already HTML) as a link to url, or unlinked when url isn't http(s)."""
    attr = f' class="{cls}"' if cls else ""
    if not re.match(r"https?://", url or "", re.I):
        return f"<span{attr}>{inner}</span>" if cls else inner
    return f'<a{attr} href="{esc(url)}">{inner}</a>'


def fmt_date(iso):
    """'2026-10-23' -> 'Friday, October 23, 2026' (v1's format)."""
    y, m, d = (int(x) for x in iso[:10].split("-"))
    return f"{DAYS[_date(y, m, d).weekday()]}, {MONTHS[m - 1]} {d}, {y}"


def short_date(iso):
    y, m, d = (int(x) for x in iso[:10].split("-"))
    return f"{MONTHS[m - 1][:3]} {d}"


def _ids(xs):
    return esc(" ".join(xs))


def _chips(story):
    """A chip per thread with comments (at most 2), most discussed first."""
    out = []
    for t in sorted((t for t in story["threads"] if t["comments"]), key=lambda t: -t["comments"])[:2]:
        bits = [SHORT_NAMES.get(t["source"], t["source"])]
        if t["points"] is not None:
            bits.append(f"{t['points']:,} {'karma' if t['source'] in KARMA else 'pts'}")
        if t["comments"] is not None:
            bits.append(f"{t['comments']:,} comments")
        out.append(link(esc(" · ".join(bits)), t["url"], "chip"))
    return "".join(out)


def _story_link(doc, sid):
    st = doc["stories"].get(sid)
    return link(esc(st["title"]), st["url"]) if st else ""


def _masthead(doc):
    head = (f'<a class="back" href="../">← all debriefs</a>\n<header class="top"><div class="brandrow">'
            f'<div class="wordmark">Debrief</div><div class="rundate">{esc(fmt_date(doc["date"]))}</div></div>')
    if doc["headline"]:
        head += f'<h1 class="headline">{inline(doc["headline"])}</h1>'
    return head + "</header>"


def _what_happened(doc):
    if not doc["what_happened"]:
        return ""
    items = "".join(f'<li data-stories="{_ids(e["stories"])}">{inline(e["text"])}</li>' for e in doc["what_happened"])
    return f'<section class="scan" id="what-happened"><div class="kicker">What happened</div><ul class="wh">{items}</ul></section>'


def _lead(doc):
    return f'<section class="lead">{paras(doc["lead"])}</section><hr class="div">' if doc["lead"] else ""


def _quote(q):
    who = ", ".join(x for x in (q["author"], SOURCE_NAMES.get(q["source"], q["source"])) if x) or "source"
    return (f'<blockquote class="q" data-story="{esc(q["story"])}"><p>{esc(q["text"])}</p>'
            f'<span class="who">— {link(esc(who), q["url"])}</span></blockquote>')


def _section(doc, n, s):
    out = [f'<section class="theme" id="s-{n}" data-stories="{_ids(s["stories"])}">',
           f"<h2>{inline(s['heading'])}</h2>", paras(s["body"])]
    sp = s.get("split")
    if sp:
        out += ['<div class="split">',
                f'<div class="pane bull"><span class="tag">For</span>{inline(sp["for"])}</div>',
                f'<div class="pane bear"><span class="tag">Against</span>{inline(sp["against"])}</div></div>',
                f'<div class="myread"><span class="tag">My read</span><div class="rd">{inline(sp["read"])}</div></div>']
    out += [_quote(q) for q in s["quotes"]]
    srcs = [f'<span class="src">{_story_link(doc, sid)}{_chips(doc["stories"][sid])}</span>'
            for sid in s["stories"] if sid in doc["stories"]]
    if srcs:
        out.append(f'<div class="refs"><span class="rtag">Sources</span>{"".join(srcs)}</div>')
    return "".join(out) + "</section>"


def _updates(doc):
    if not doc["updates"]:
        return ""
    rows = []
    for u in doc["updates"]:
        since = f'<span class="since">first reported {esc(short_date(u["prev_reported"]))}</span>' if u["prev_reported"] else ""
        rows.append(f'<div class="upd" data-story="{esc(u["story"])}"><div class="ut">{_story_link(doc, u["story"])}{since}</div>'
                    f'<p>{inline(u["text"])}</p></div>')
    return f'<section class="updates" id="updates"><div class="kicker">Updates</div>{"".join(rows)}</section>'


def _notes(doc):
    if not doc["notes"]:
        return ""
    if doc["mode"] == "scan_only":
        return '<div class="notice">' + "".join(f"<p>{inline(n)}</p>" for n in doc["notes"]) + "</div>"
    items = "".join(f"<li>{inline(n)}</li>" for n in doc["notes"])
    return f'<section class="notes"><div class="kicker">Notes</div><ul>{items}</ul></section>'


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


def _scan(doc):
    if not doc["scan"]:
        return ""
    anchors, out, current = _anchors(doc), [], None
    out.append('<section class="index" id="scan"><div class="kicker">The scan · every story kept today</div>')
    for x in doc["scan"]:
        if x["section"] != current:
            current = x["section"]
            out.append(f'<div class="ixsec">{esc(SECTION_NAMES.get(current, current))}</div>')
        mark = f'<a class="in" href="#{anchors[x["id"]]}">↑ in the brief</a>' if x["covered"] and x["id"] in anchors else ""
        out.append(f'<div class="ix" data-story="{esc(x["id"])}">{link(esc(x["title"]), x["url"], "t")}{mark}'
                   f'<span class="n">{inline(x["gist"])}</span></div>')
    return "".join(out) + "</section>"


def _footer(doc):
    m, bits = doc["meta"], []
    line = f"{m['covered']} of {m['kept']} stories in the article" if doc["mode"] == "written" else f"{m['kept']} stories kept"
    if m["triage_status"] != "ok":
        line += f" · triage {m['triage_status']}"
    if m["failed_sources"]:
        line += " · sources down: " + ", ".join(m["failed_sources"])
    bits.append(esc(line))
    fetched = [f for f in doc["fetches"] if f["allowed"]]
    if fetched:
        pages = "; ".join(link(esc((urlsplit(f["url"]).netloc + urlsplit(f["url"]).path)[:80]), f["url"])
                          + (f" ({esc(f['why'])})" if f["why"] else "") for f in fetched)
        bits.append(f"<b>Pages checked</b>{pages}")
    bits.append("Your morning read on the AI/ML landscape.")
    return '<footer class="foot">' + "".join(f"<div>{b}</div>" for b in bits) + "</footer>"


def _template(name):
    return Template((TEMPLATES / name).read_text())


def day_page(doc):
    """The page for one brief.json document (written or scan_only)."""
    parts = [_masthead(doc)]
    if doc["mode"] == "written":
        parts += [_what_happened(doc), _lead(doc), *(_section(doc, n, s) for n, s in enumerate(doc["sections"], 1)),
                  _updates(doc), _notes(doc)]
    else:
        parts.append(_notes(doc))
    parts += [_scan(doc), _footer(doc)]
    return _template("day.html").substitute(title=esc(f"Debrief · {fmt_date(doc['date'])}"),
                                            content="\n".join(p for p in parts if p))


def index_page(entries):
    """The archive list. `entries` are {date, summary}, newest first."""
    rows = [f'    <li><a href="{esc(e["date"])}/"><span class="d">{esc(fmt_date(e["date"]))}</span>'
            f'<span class="k">{esc(e["summary"])}</span></a></li>' for e in entries]
    return _template("index.html").substitute(rows="\n".join(rows) or '    <li><a><span class="k">No debriefs yet.</span></a></li>')
