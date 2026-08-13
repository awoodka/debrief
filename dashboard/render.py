#!/usr/bin/env python3
"""Fill the debrief HTML template with a run's debrief.json and open it in Chrome.

Usage:  python dashboard/render.py [YYYY-MM-DD]
No date → the most recent debrief. Stdlib only; the page is self-contained (opens via file://).
"""
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "dashboard" / "debrief.template.html"
DEBRIEFS = ROOT / "data" / "debriefs"
PLACEHOLDER = "/*__DEBRIEF_DATA__*/"


def _resolve_dir(date):
    if date:
        d = DEBRIEFS / date
        if not d.is_dir():
            sys.exit(f"render: no debrief dir for {date} ({d})")
        return d
    dirs = sorted(p for p in DEBRIEFS.iterdir() if p.is_dir()) if DEBRIEFS.is_dir() else []
    if not dirs:
        sys.exit("render: no debriefs found under data/debriefs/")
    return dirs[-1]


def _open_in_browser(path):
    """Prefer Chrome; fall back to the default browser. Never fatal — headless is fine."""
    p = str(path)
    try:
        sysname = platform.system()
        if sysname == "Darwin":
            r = subprocess.run(["open", "-a", "Google Chrome", p])
            if r.returncode != 0:
                subprocess.run(["open", p])          # default browser
        elif sysname == "Linux":
            subprocess.run(["xdg-open", p])
        elif sysname == "Windows":
            subprocess.run(["cmd", "/c", "start", "", p], shell=False)
        else:
            print(f"render: open manually → {p}")
    except Exception as e:  # noqa: BLE001 — opening a browser must never break the pipeline
        print(f"render: could not auto-open ({type(e).__name__}) → open manually: {p}")


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else None
    d = _resolve_dir(date)
    jf = d / "debrief.json"
    if not jf.exists():
        sys.exit(f"render: missing {jf} — run /debrief first")

    data = json.loads(jf.read_text())
    data.pop("_appendix", None)          # keep the page lean — index + raw memos are not rendered

    if not TEMPLATE.exists():
        sys.exit(f"render: missing template {TEMPLATE}")
    tpl = TEMPLATE.read_text()

    # Embed the JSON inside <script type="application/json">. Escape "</" so no string value
    # (e.g. one containing "</script>") can close the tag early; JSON.parse restores it.
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = tpl.replace(PLACEHOLDER, blob)

    out = d / "debrief.html"
    out.write_text(html)
    kb = out.stat().st_size / 1024
    print(f"render: wrote {out} ({kb:.0f} KB)")
    _open_in_browser(out)


if __name__ == "__main__":
    main()
