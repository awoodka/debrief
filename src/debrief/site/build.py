"""The site: every day's page as static files, which deploy/run.sh publishes to debrief.alexwoodka.com.

`debrief site build --out DIR` writes:
  index.html          the archive list, newest first
  <date>/index.html   a v2 day's page (site/page.py), or a frozen v1 page
  <date>/brief.json   a v2 day's brief.json minus what's only for us (the session, the check's
                      warnings, refused fetches): the day as data, for anything that wants it

v2 days are the runs/<date>/brief.json files that validate. The v1 pipeline's pages (2026-09-14
to 2026-10-25) were copied byte for byte into <data>/site/v1/ when v2 took over the site, and are
listed unchanged; a v2 brief wins a date that both have. Nothing depends on the clock, so the same
inputs give the same bytes.
"""
import json
import re
from pathlib import Path

from ..triage.output import Validator
from . import page

DATE = re.compile(r"\d{4}-\d{2}-\d{2}$")
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
    entries = []
    for d in sorted(set(v2) | set(v1), reverse=True):
        (out / d).mkdir()
        if d in v2:
            (out / d / "index.html").write_text(page.day_page(v2[d]))
            (out / d / "brief.json").write_text(json.dumps(public(v2[d]), sort_keys=True, ensure_ascii=False, indent=1) + "\n")
            entries.append({"date": d, "summary": v2[d]["headline"] or "The brief didn't run; every story kept is listed."})
        else:
            body = v1[d].read_bytes()
            (out / d / "index.html").write_bytes(body)
            entries.append({"date": d, "summary": v1_summary(body.decode("utf-8", "replace"))})
    (out / "index.html").write_text(page.index_page(entries))
    return {"v2": sorted(v2), "v1": sorted(set(v1) - set(v2))}

