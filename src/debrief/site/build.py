"""The site: every day's page as static files, which deploy/run.sh publishes to debrief.alexwoodka.com.

`debrief site build --out DIR` writes:
  index.html          the newest issue, then the archive of the rest, newest first, grouped by month;
                      on a wide screen, a calendar shaded by each day's comments
  <date>/index.html   a v2 day's page (site/page.py), or a frozen v1 page
  <date>/picture.svg  a v2 day's picture, drawn from the shape of its brief (site/picture.py)
  <date>/brief.json   a v2 day's brief.json minus what's only for us (the session, the check's
                      warnings, refused fetches): the day as data, for anything that wants it
  site.css            the one stylesheet (templates/site.css), with every color and face as a token
  progress.js         a day's page's script (templates/progress.js): the rail marks the part being
                      read, and a bar across the top shows how far through the issue the reader is
  calendar.js         the front page's script (templates/calendar.js): the calendar pages through
                      the months, follows the archive, and shows a day's issue when pointed at
  about/index.html    "How Debrief works", from templates/about.md
  favicon.svg         a D in the tokens' accent and paper colors
  fonts/              the typeface, Newsreader, upright and italic (templates/fonts/, with its license)
  404.html            the page for an address with no issue
  feed.xml            an Atom feed of the newest 30 issues, v1 and v2

v2 days are the runs/<date>/brief.json files that validate. The v1 pipeline's pages (2026-09-14
to 2026-10-25) were copied byte for byte into <data>/site/v1/ when v2 took over the site, and are
listed unchanged; a v2 brief wins a date that both have. Nothing depends on the clock, so the same
inputs give the same bytes.
"""
import json
import re
import shutil
from pathlib import Path

from ..triage.output import Validator
from . import page, picture

DATE = re.compile(r"\d{4}-\d{2}-\d{2}$")
FEED_SIZE = 30
V1_DATA = re.compile(r'<script id="debrief-data" type="application/json">(.*?)</script>', re.S)


class SiteError(Exception):
    pass


def v1_dir(cfg):
    return cfg.data_dir / "site" / "v1"


def public(doc):
    """The published copy of a brief.json."""
    doc = json.loads(json.dumps(doc))
    for k in ("session", "warnings"):
        doc["meta"].pop(k, None)
    doc["fetches"] = [f for f in doc["fetches"] if f["allowed"]]
    return doc


def comments(doc):
    """How many comments the threads of a brief's stories had."""
    return sum(t["comments"] or 0 for s in doc["stories"].values() for t in s["threads"])


def scored(cfg, date):
    """False when the night's triage ran on rules alone, as runs/<date>/brief_context.json records
    (the published brief.json doesn't say). A day without that file counts as scored."""
    p = cfg.data_dir / "runs" / date / "brief_context.json"
    return not p.is_file() or json.loads(p.read_text())["triage"]["mode"] != "rules_only"


def v1_summary(page_html):
    """A v1 page's line in the archive, as v1's build_site.py chose it: the dek, else the old
    through-line, else the first event. "" when the page has no embedded data."""
    m = V1_DATA.search(page_html)
    try:
        data = json.loads(m.group(1)) if m else {}
    except ValueError:
        return ""
    first = (data.get("what_happened") or [""])[0]
    return str(data.get("dek") or (data.get("bottom_line") or {}).get("read") or (first if isinstance(first, str) else "") or "")


def v2_days(cfg, validator, log=print):
    """{date: brief.json document} for every run with a brief that validates."""
    out, runs = {}, cfg.data_dir / "runs"
    for f in sorted(runs.glob("*/brief.json")) if runs.is_dir() else []:
        if not DATE.match(f.parent.name):
            continue
        try:
            doc = json.loads(f.read_text())
            validator.validate("brief", doc)
        except ValueError as e:
            log(f"site: skipping {f.parent.name}: {str(e)[:200]}")
            continue
        out[f.parent.name] = doc
    return out


def build(cfg, out, validator=None, log=print):
    """Write the site into `out` (empty or new). Returns {"v2": [dates], "v1": [dates]}."""
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise SiteError(f"{out} isn't empty")
    out.mkdir(parents=True, exist_ok=True)
    v2 = v2_days(cfg, validator or Validator(), log)
    v1 = {p.parent.name: p for p in sorted(v1_dir(cfg).glob("*/index.html")) if DATE.match(p.parent.name)}
    dates, names = sorted(set(v2) | set(v1)), {src.id: src.name for src in cfg.sources}
    css = (page.TEMPLATES / "site.css").read_text()
    pictures, issues = picture.pictures(v2, css), []
    for i, d in enumerate(dates):
        (out / d).mkdir()
        if d in v2:
            nav = page.Neighbors(dates[i - 1] if i else None, dates[i + 1] if i + 1 < len(dates) else None, frozenset(dates))
            (out / d / "picture.svg").write_text(pictures[d].svg)
            (out / d / "index.html").write_text(page.day_page(v2[d], nav, names, pictures[d]))
            (out / d / "brief.json").write_text(json.dumps(public(v2[d]), sort_keys=True, ensure_ascii=False, indent=1) + "\n")
            issues.append(page.Issue(d, "v2", v2[d]["headline"], comments(v2[d]) if scored(cfg, d) else None))
        else:
            body = v1[d].read_bytes()
            (out / d / "index.html").write_bytes(body)
            issues.append(page.Issue(d, "v1", v1_summary(body.decode("utf-8", "replace"))))
    issues.reverse()
    (out / "site.css").write_text(css)
    for script in ("progress.js", "calendar.js"):
        shutil.copyfile(page.TEMPLATES / script, out / script)
    shutil.copytree(page.TEMPLATES / "fonts", out / "fonts")
    (out / "favicon.svg").write_text(page.favicon(css))
    (out / "about").mkdir()
    (out / "about" / "index.html").write_text(page.about_page())
    (out / "404.html").write_text(page.not_found_page())
    (out / "index.html").write_text(page.index_page(issues, pictures))
    (out / "feed.xml").write_text(page.feed(issues[:FEED_SIZE]))
    return {"v2": sorted(v2), "v1": sorted(set(v1) - set(v2))}
