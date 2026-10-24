"""brief.md: a brief.json document as plain markdown, to read without a page (and to review drafts).
Styling belongs to the page; this is only a faithful text rendering."""

SECTION_NAMES = {"papers": "Papers", "releases": "Releases", "announcements": "Announcements",
                 "press_policy": "Press and policy", "discussion": "Discussion"}


def _link(title, url):
    return f"[{title}]({url})" if url else title


def _sources(doc, ids):
    return " · ".join(_link(doc["stories"][sid]["title"], doc["stories"][sid]["url"]) for sid in ids if sid in doc["stories"])


def markdown(doc):
    L = [f"# Debrief, {doc['date']}", ""]
    if doc["headline"]:
        L += [f"## {doc['headline']}", ""]
    if doc["what_happened"]:
        L += ["**What happened**", ""] + [f"- {e['text']}" for e in doc["what_happened"]] + [""]
    if doc["lead"]:
        L += [doc["lead"].strip(), ""]
    for s in doc["sections"]:
        L += [f"### {s['heading']}", "", s["body"].strip(), ""]
        if s.get("split"):
            sp = s["split"]
            L += [f"- **For:** {sp['for']}", f"- **Against:** {sp['against']}", f"- **My read:** {sp['read']}", ""]
        for q in s["quotes"]:
            who = ", ".join(x for x in (q["author"], q["source"]) if x) or ("source" if q["url"] else "")
            L += [f"> {q['text']}" + (f" ({_link(who, q['url'])})" if who else ""), ""]
        L += [f"_Sources: {_sources(doc, s['stories'])}_", ""]
    if doc["updates"]:
        L += ["### Updates", ""]
        for u in doc["updates"]:
            since = f" (reported {u['prev_reported']})" if u["prev_reported"] else ""
            L.append(f"- **{_sources(doc, [u['story']])}**{since}: {u['text']}")
        L.append("")
    if doc["notes"]:
        L += ["### Notes", ""] + [f"- {n}" for n in doc["notes"]] + [""]
    L += ["---", "", "## The scan", ""]
    current = None
    for x in doc["scan"]:
        if x["section"] != current:
            current = x["section"]
            L += ["", f"**{SECTION_NAMES.get(current, current)}**", ""]
        mark = " ✓" if x["covered"] else ""
        L.append(f"- {_link(x['title'], x['url'])}{mark}: {x['gist']}")
    L.append("")
    allowed = [f for f in doc["fetches"] if f["allowed"]]
    if allowed:
        L += ["", "_Fetched: " + "; ".join(f"{f['url']}" + (f" ({f['why']})" if f["why"] else "") for f in allowed) + "_"]
    m = doc["meta"]
    L += ["", f"_{m['covered']} of {m['kept']} stories in the article · {m['words']} words · triage {m['triage_status']}"
          + (f" · sources down: {', '.join(m['failed_sources'])}" if m["failed_sources"] else "") + "_", ""]
    return "\n".join(L)
