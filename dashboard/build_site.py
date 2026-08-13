#!/usr/bin/env python3
"""Build a static site of every debrief, ready to host (e.g. Cloudflare Pages).

Scans data/debriefs/*/debrief.json, renders each through the matching dashboard template, and writes a
self-contained `site/` folder:

    site/index.html          – simple newest-first list of every debrief
    site/<date>/index.html   – one debrief, viewable at <host>/<date>/

Each debrief page is fully self-contained (inline CSS/JS), so the whole `site/` folder is static — no
server needed. Stdlib only.

Schema note: newer debriefs use the flowing-article schema (top-level `lead`/`body`) rendered by
`debrief.template.html`; older ones use the original `bottom_line`/`gems`/`landscape` schema rendered by
`debrief.template.prev.html`. The builder picks the right template per file so both keep rendering.

Usage:
    .venv/bin/python dashboard/build_site.py      # writes ./site
    wrangler pages deploy site                    # deploy (after `wrangler login`)
"""
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASH = ROOT / "dashboard"
DEBRIEFS = ROOT / "data" / "debriefs"
SITE = ROOT / "site"
# Serve under a sub-path (e.g. alexwoodka.com/debrief) by setting DEBRIEF_SITE_BASE=/debrief; set it
# empty to serve at the domain root (e.g. a subdomain, debrief.alexwoodka.com).
BASE = os.environ.get("DEBRIEF_SITE_BASE", "/debrief").strip("/")
OUT = SITE / BASE if BASE else SITE
PLACEHOLDER = "/*__DEBRIEF_DATA__*/"
TPL_NEW = DASH / "debrief.template.html"
TPL_LEGACY = DASH / "debrief.template.prev.html"          # renders the original schema
WRAP_ANCHOR = '<div class="wrap"><main id="app">'
NAV = ('<a href="../" style="display:inline-block;font-family:-apple-system,BlinkMacSystemFont,'
       '\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;font-size:13px;letter-spacing:.02em;'
       'color:#b0472a;text-decoration:none;margin-bottom:22px">← all debriefs</a>')
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def esc(s):
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def fmt_date(iso):
    """'2026-08-07' -> 'Friday, August 7, 2026'; falls back to the raw string."""
    parts = str(iso or "").strip()[:10].split("-")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return str(iso or "")
    y, m, d = (int(p) for p in parts)
    try:
        from datetime import date
        wd = DAYS[date(y, m, d).weekday()] + ", "
    except Exception:  # noqa: BLE001
        wd = ""
    return f"{wd}{MONTHS[m - 1]} {d}, {y}"


def render_page(data, tpl_text):
    data = dict(data)
    data.pop("_appendix", None)                          # keep the page lean
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = tpl_text.replace(PLACEHOLDER, blob)
    return page.replace(WRAP_ANCHOR, '<div class="wrap">' + NAV + '<main id="app">', 1)


def summary_of(data):
    """One-line blurb for the index: the dek, else the old through-line, else first event."""
    bl = data.get("bottom_line") or {}
    return (data.get("dek")
            or bl.get("read")
            or (data.get("what_happened") or [""])[0]
            or "")


INDEX_CSS = """
  :root{--paper:#faf7f1;--surface:#fffdf8;--ink:#221d16;--muted:#6f6656;--faint:#9a917f;
        --accent:#b0472a;--rule:#e7ddcd;--hair:#efe7d8;
        --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
        --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
  @media(prefers-color-scheme:dark){:root{--paper:#1a1611;--surface:#221d16;--ink:#efe6d6;
        --muted:#a99f8c;--faint:#7c7362;--accent:#e5825a;--rule:#342c20;--hair:#2e271d}}
  *{box-sizing:border-box}html,body{margin:0}
  body{background:var(--paper);color:var(--ink);font-family:var(--serif);line-height:1.6;
       -webkit-font-smoothing:antialiased}
  .wrap{max-width:720px;margin:0 auto;padding:64px 30px 110px}
  header{border-bottom:2px solid var(--ink);padding-bottom:16px;margin-bottom:8px}
  .wordmark{font-family:var(--sans);font-weight:800;letter-spacing:.28em;font-size:14px;
            color:var(--accent);text-transform:uppercase}
  .tag{font-style:italic;color:var(--muted);font-size:17px;margin:12px 0 0}
  ul.list{list-style:none;margin:26px 0 0;padding:0}
  ul.list li{border-top:1px solid var(--hair)}
  ul.list li:first-child{border-top:0}
  ul.list a{display:block;padding:18px 0;text-decoration:none;color:inherit}
  ul.list a:hover .d{color:var(--accent)}
  .d{display:block;font-family:var(--sans);font-weight:700;font-size:15px;color:var(--ink);
     letter-spacing:.01em;margin-bottom:4px}
  .k{display:block;color:var(--muted);font-size:17px;line-height:1.45}
  footer{margin-top:50px;padding-top:18px;border-top:1px solid var(--rule);
         font-family:var(--sans);font-size:12.5px;color:var(--faint)}
"""


def render_index(entries):
    rows = []
    for name, data in entries:
        label = fmt_date((data.get("meta") or {}).get("date") or name)
        rows.append(
            f'<li><a href="{esc(name)}/"><span class="d">{esc(label)}</span>'
            f'<span class="k">{esc(summary_of(data))}</span></a></li>')
    if not rows:
        rows.append('<li><a><span class="k">No debriefs yet.</span></a></li>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Debrief</title>
<style>{INDEX_CSS}</style></head>
<body><div class="wrap">
  <header>
    <div class="wordmark">Debrief</div>
    <p class="tag">Your morning read on the AI/ML landscape.</p>
  </header>
  <ul class="list">
    {"".join(rows)}
  </ul>
  <footer>Each entry is a full daily briefing. Newest first.</footer>
</div></body></html>
"""


def build():
    if not DEBRIEFS.is_dir():
        raise SystemExit(f"build_site: no debriefs dir at {DEBRIEFS}")
    tpl_new = TPL_NEW.read_text()
    tpl_legacy = TPL_LEGACY.read_text() if TPL_LEGACY.exists() else tpl_new

    entries = []
    for d in sorted((p for p in DEBRIEFS.iterdir() if p.is_dir()), reverse=True):
        jf = d / "debrief.json"
        if not jf.exists():
            continue
        try:
            data = json.loads(jf.read_text())
        except Exception as e:  # noqa: BLE001
            print(f"  skip {d.name}: bad JSON ({e})")
            continue
        entries.append((d.name, data))

    if SITE.exists():
        shutil.rmtree(SITE)
    OUT.mkdir(parents=True, exist_ok=True)

    for name, data in entries:
        is_new = ("body" in data) or ("lead" in data)
        page = render_page(data, tpl_new if is_new else tpl_legacy)
        out = OUT / name
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text(page)
        print(f"  {name}  ({'new' if is_new else 'legacy'} schema)")

    (OUT / "index.html").write_text(render_index(entries))
    if BASE:
        # apex root -> the sub-path, so the bare domain lands on the archive
        (SITE / "_redirects").write_text(f"/    /{BASE}/    302\n")
    where = f"/{BASE}/" if BASE else "/"
    print(f"built {len(entries)} debrief(s) + index -> {OUT}  (serves at {where})")


if __name__ == "__main__":
    build()
