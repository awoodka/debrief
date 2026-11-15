"""The site (src/debrief/site/): a day's page from its brief.json, the archive, v1's frozen pages,
the feed, and the published copy of the data. On the brief fixture day (tests/fixtures/brief/)."""
import html as htmllib
import json
import re
import xml.etree.ElementTree as ET

import pytest
from test_brief import DATE, good_draft, setup_day, write_draft

from debrief.brief import finalize
from debrief.site import build, page

V1_PAGE = '<!doctype html><title>v1</title><script id="debrief-data" type="application/json">{}</script>'


def written(tmp_path):
    cfg, day = setup_day(tmp_path)
    write_draft(day, good_draft(day))
    doc, r = finalize.finalize(day)
    assert doc["mode"] == "written", r["errors"]
    return cfg, doc


def v1_page(data):
    return V1_PAGE.replace("{}", json.dumps(data).replace("</", "<\\/"))


def add_v1(cfg, pages):
    for d, body in pages.items():
        (build.v1_dir(cfg) / d).mkdir(parents=True)
        (build.v1_dir(cfg) / d / "index.html").write_text(body)


def text(page_html):
    """What a reader sees: the body's text, tags dropped, entities decoded, spaces collapsed."""
    body = re.sub(r"</?(?:a|em|strong)\b[^>]*>", "", page_html.split("<body>", 1)[-1])
    return " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", body)).split())


CSP = "default-src 'none'; style-src 'self'; font-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'"


def between(html, start, end):
    return html.split(start, 1)[1].split(end, 1)[0]


def hrefs(fragment):
    return re.findall(r'href="([^"]+)"', fragment)


def two_v2_days(cfg, doc, later="2026-10-25"):
    """A second v2 brief, the day after the fixture day."""
    (cfg.data_dir / "runs" / later).mkdir()
    (cfg.data_dir / "runs" / later / "brief.json").write_text(json.dumps({**doc, "date": later, "headline": "The day after"}))


def test_a_day_page_has_every_part_of_the_brief(tmp_path):
    _, doc = written(tmp_path)
    doc["meta"]["session"] = {"cost_usd_list_price": 1.2345, "stderr": "a session error"}
    doc["meta"]["warnings"] = ["the article is 120 words, under the aim"]
    html = page.day_page(doc)
    seen = text(html)
    assert re.search(r"<h1>Saturday, October 24, 2026</h1>\s*<p class=\"standfirst\">" + re.escape(page.inline(doc["headline"])), html)
    assert "55 words · 8 stories Saturday, October 24, 2026" in seen
    assert all(page.inline(e["text"]) in html for e in doc["what_happened"])
    for n, s in enumerate(doc["sections"], 1):
        assert f'<section class="theme" id="s-{n}" data-stories="{page.esc(" ".join(s["stories"]))}">' in html
        assert f'<h2><span class="num">{n}</span> {page.inline(s["heading"])}</h2>' in html
    split = doc["sections"][0]["split"]
    assert (f"The case for {split['for']} The case against {split['against']} "
            f"The analysis suggests {split['read']}") in seen
    css = (page.TEMPLATES / "site.css").read_text()
    for key, label in (("for", "The case for"), ("against", "The case against"), ("read", "The analysis suggests")):
        assert f'<div class="arg {key}"><p class="label caps">{label}</p><p>{page.inline(split[key])}</p></div>' in html
        cue = re.search(rf"\.arg\.{key} {{ --cue: var\(--(\w+)\); }}", css)[1]
        assert cue == {"read": "accent"}.get(key, key)
    assert ".arg { padding-left: 16px; border-left: 2px solid var(--cue); }" in css and "background" not in between(css, ".split", ".sources")
    q = doc["sections"][0]["quotes"][0]
    assert page.esc(q["text"]) in html and f'href="{page.esc(q["url"])}"' in html
    assert page.inline(doc["updates"][0]["text"]) in html
    assert all(page.esc(x["title"]) in html for x in doc["scan"])
    assert re.search(r"HN · [\d,]+ points · [\d,]+ comments", seen)
    assert "My read" not in seen and "chip" not in html
    assert f'content="{CSP}"' in html and "<script" not in html and "<style" not in html and "style=" not in html
    assert "1.2345" not in html and "a session error" not in html and "under the aim" not in html


def test_a_day_page_head_names_the_issue_for_tabs_feeds_and_link_previews(tmp_path):
    _, doc = written(tmp_path)
    html = page.day_page(doc)
    assert "<title>Debrief · Oct 24 · PHP2Shell attackers had four days; flow language models…</title>" in html
    url = f"https://debrief.alexwoodka.com/{DATE}/"
    for tag in (f'<meta name="description" content="{page.esc(doc["headline"])}">',
                '<meta property="og:title" content="Debrief · Saturday, October 24, 2026">',
                f'<meta property="og:description" content="{page.esc(doc["headline"])}">',
                '<meta property="og:type" content="article">', f'<meta property="og:url" content="{url}">',
                '<meta property="og:site_name" content="Debrief">', f'<link rel="canonical" href="{url}">',
                '<link rel="alternate" type="application/atom+xml" title="Debrief" href="../feed.xml">',
                '<link rel="icon" href="../favicon.svg" type="image/svg+xml">', '<link rel="stylesheet" href="../site.css">'):
        assert tag in html, tag
    doc["headline"] = ""
    html = page.day_page(doc)
    assert "<title>Debrief · Oct 24</title>" in html and 'class="standfirst"' not in html


def test_both_contents_list_the_sections_then_the_other_parts(tmp_path):
    _, doc = written(tmp_path)
    html = page.day_page(doc)
    rail, folded = between(html, '<aside class="rail">', "</aside>"), between(html, '<details class="toc-inline">', "</details>")
    expect = ["#s-1", "#s-2", "#what-happened", "#updates", "#scan"]
    assert [h for h in hrefs(rail) if h.startswith("#")] == expect == hrefs(folded)
    assert rail.index("Debrief") < rail.index("Saturday, October 24, 2026") < rail.index("#s-1")
    assert "<summary" in folded and "In this issue" in folded and "open" not in between(html, "<details", ">")
    assert text(folded).startswith("In this issue PHP2Shell was exploited")
    assert html.index("</header>") < html.index('<details class="toc-inline">') < html.index('id="what-happened"')
    doc["updates"] = []
    html = page.day_page(doc)
    assert "#updates" not in html and 'id="updates"' not in html


def test_previous_and_next_issues_link_across_versions_and_stop_at_the_ends(tmp_path):
    cfg, doc = written(tmp_path)
    add_v1(cfg, {"2026-10-23": v1_page({"dek": "x"})})
    two_v2_days(cfg, doc)
    build.build(cfg, tmp_path / "out")
    middle = (tmp_path / "out" / DATE / "index.html").read_text()
    newest = (tmp_path / "out" / "2026-10-25" / "index.html").read_text()
    foot = between(middle, '<footer class="foot">', "</footer>")
    assert '<a rel="prev" href="../2026-10-23/">← Friday, October 23</a>' in foot
    assert '<a rel="next" href="../2026-10-25/">Sunday, October 25 →</a>' in foot
    assert 'rel="prev" href="../2026-10-23/"' in between(middle, '<aside class="rail">', "</aside>")
    assert 'rel="prev" href="../2026-10-24/"' in newest and 'rel="next"' not in newest

    cfg, doc = written(tmp_path / "alone")
    build.build(cfg, tmp_path / "alone" / "out")
    only = (tmp_path / "alone" / "out" / DATE / "index.html").read_text()
    assert 'rel="prev"' not in only and 'rel="next"' not in only and 'class="issue-nav"' not in only


def test_an_update_links_to_the_issue_that_first_reported_it_only_when_that_issue_exists(tmp_path):
    _, doc = written(tmp_path)
    assert doc["updates"][0]["prev_reported"] == "2026-10-23"
    linked = page.day_page(doc, page.Neighbors(known=frozenset({"2026-10-23", DATE})))
    assert '<p class="since">first reported <a href="../2026-10-23/">Oct 23</a></p>' in linked
    unlinked = page.day_page(doc, page.Neighbors(known=frozenset({DATE})))
    assert '<p class="since">first reported Oct 23</p>' in unlinked and "../2026-10-23/" not in unlinked


def test_the_scan_marks_where_the_brief_covers_each_story(tmp_path):
    _, doc = written(tmp_path)
    spare = next(x for x in doc["scan"] if not x["covered"])
    spare["covered"] = True
    doc["updates"].append({"story": spare["id"], "text": "Still going.", "prev_reported": None})
    scan = between(page.day_page(doc), '<section class="scan" id="scan">', "</section>")
    rows = dict(re.findall(r'<li data-story="([^"]+)">(.*?)</li>', scan))
    marks = {sid: re.findall(r'<a class="where" href="(#[^"]+)">([^<]+)</a>', row) for sid, row in rows.items()}
    first_section = {sid: n for n, s in reversed(list(enumerate(doc["sections"], 1)))
                     for sid in [*s["stories"], *(q["story"] for q in s["quotes"])]}
    for x in doc["scan"]:
        if not x["covered"]:
            assert marks[page.esc(x["id"])] == [], x["id"]
        elif x["id"] in first_section:
            n = first_section[x["id"]]
            assert marks[page.esc(x["id"])] == [(f"#s-{n}", f"§{n}")], x["id"]
    assert marks[page.esc(spare["id"])] == [("#updates", "Updates")]
    assert marks[page.esc("lesswrong:ie7bSp5ZDwYkCQxuv")] == [("#what-happened", "What happened")]
    assert "Everything kept today" in text(scan) and "Papers" in text(scan)
    assert "↑ in the brief" not in scan


def test_the_footer_speaks_to_readers(tmp_path):
    _, doc = written(tmp_path)
    doc["meta"].update(triage_status="degraded", failed_sources=["lmarena", "no-such-source"])
    doc["fetches"] = [{"url": "https://arxiv.org/abs/2610.28559", "allowed": True, "why": "to check the table"},
                      {"url": "https://refused.example/x", "allowed": False, "why": None}]
    html = page.day_page(doc, source_names={"lmarena": "LMArena leaderboard"})
    foot = text(between(html, '<footer class="foot">', "</footer>"))
    assert foot.startswith("8 stories kept today, 4 covered above. "
                           "Some sources didn't load today: LMArena leaderboard, no-such-source. "
                           "Pages checked for this issue arxiv.org/abs/2610.28559 (to check the table) "
                           "All issues · RSS")
    assert "refused.example" not in html and "triage" not in text(html).lower() and "degraded" not in html
    foot_html = between(html, '<footer class="foot">', "</footer>")
    assert 'href="../"' in foot_html and 'href="../feed.xml"' in foot_html
    assert "automated daily briefing" not in html and 'href="https://alexwoodka.com"' not in html


def test_a_scan_only_brief_gets_a_page_with_its_note(tmp_path):
    cfg, day = setup_day(tmp_path)
    write_draft(day, {"date": DATE})
    doc, _ = finalize.finalize(day)
    assert doc["mode"] == "scan_only"
    html = page.day_page(doc)
    assert 'class="notice"' in html and page.inline(doc["notes"][0]) in html
    assert 'class="theme"' not in html and 'class="standfirst"' not in html
    assert all(page.esc(x["title"]) in html for x in doc["scan"])
    seen = text(html)
    assert f"{doc['meta']['kept']} stories Saturday, October 24, 2026" in seen
    assert f"{doc['meta']['kept']} stories kept today." in seen and "covered above" not in seen
    assert "triage" not in seen.lower()
    assert [h for h in hrefs(between(html, '<aside class="rail">', "</aside>")) if h.startswith("#")] == ["#scan"]


def test_untrusted_text_is_escaped_and_only_http_links_are_kept(tmp_path):
    _, doc = written(tmp_path)
    doc["scan"][0].update(title='<script>alert(1)</script>', url="javascript:alert(1)")
    doc["sections"][0]["body"] += "\n\nA **bold** claim, an *aside*, 2 * 3 * 4, and <b>raw</b> tags."
    html = page.day_page(doc)
    assert "<script>alert" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "javascript:" not in html.replace("&lt;script&gt;", "")
    assert "<strong>bold</strong>" in html and "<em>aside</em>" in html and "2 * 3 * 4" in html
    assert "&lt;b&gt;raw&lt;/b&gt;" in html


def test_the_site_build_is_byte_stable_and_publishes_only_the_public_data(tmp_path):
    cfg, doc = written(tmp_path)
    a, b = build.build(cfg, tmp_path / "a"), build.build(cfg, tmp_path / "b")
    assert a == b == {"v2": [DATE], "v1": []}
    files = sorted(p.relative_to(tmp_path / "a") for p in (tmp_path / "a").rglob("*") if p.is_file())
    assert [str(f) for f in files] == [f"{DATE}/brief.json", f"{DATE}/index.html", f"{DATE}/picture.svg", "404.html", "favicon.svg",
                                       "feed.xml", "fonts/OFL.txt", "fonts/newsreader-italic.woff2",
                                       "fonts/newsreader.woff2", "index.html", "site.css"]
    pub = json.loads((tmp_path / "a" / DATE / "brief.json").read_text())
    assert "session" not in pub["meta"] and "warnings" not in pub["meta"]
    assert all(f["allowed"] for f in pub["fetches"]) and pub["sections"] == doc["sections"]
    assert page.esc(doc["headline"]) in (tmp_path / "a" / "index.html").read_text()


def test_two_builds_of_the_same_inputs_are_byte_identical(tmp_path):
    cfg, doc = written(tmp_path)
    add_v1(cfg, {d: v1_page({"dek": f"v1 on {d}"}) for d in ("2026-09-30", "2026-10-01", "2026-10-23")})
    two_v2_days(cfg, doc)
    build.build(cfg, tmp_path / "a")
    build.build(cfg, tmp_path / "b")
    def tree(root):
        return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    a = tree(tmp_path / "a")
    assert len(a) == 17 and a == tree(tmp_path / "b")


PICTURES = {"endpaper": "an endpaper", "map": "a map of the day", "spines": "a shelf of book spines",
            "strata": "a cut through layers of rock", "contours": "a contour map", "weave": "a weave",
            "constellation": "a constellation"}


def v2_days_on(cfg, doc, dates):
    for d in dates:
        (cfg.data_dir / "runs" / d).mkdir(exist_ok=True)
        (cfg.data_dir / "runs" / d / "brief.json").write_text(json.dumps({**doc, "date": d}))


def picture_on(page_html):
    """The day's picture as a reader meets it: (alt text, caption, the style the caption names)."""
    fig = between(page_html, '<figure class="picture">', "</figure>")
    alt = re.fullmatch(r'<img src="picture.svg" width="1200" height="500" alt="([^"]+)"><figcaption>.*', fig)[1]
    caption = text(between(fig, "<figcaption>", "</figcaption>"))
    style = next(s for s, name in PICTURES.items() if caption.startswith(f"Today's picture is {name}. "))
    return htmllib.unescape(alt), caption, style


def test_every_v2_day_gets_a_picture_under_the_standfirst_and_v1_days_get_none(tmp_path):
    cfg, doc = written(tmp_path)
    add_v1(cfg, {"2026-10-23": v1_page({"dek": "x"})})
    build.build(cfg, tmp_path / "out")
    out = tmp_path / "out"
    html = (out / DATE / "index.html").read_text()
    alt, caption, _ = picture_on(html)
    assert len(alt) > 20 and len(caption.split(". ")) == 2
    assert html.index('class="standfirst"') < html.index('<figure class="picture">') < html.index('id="what-happened"')
    svg = ET.parse(out / DATE / "picture.svg").getroot()
    assert svg.tag == "{http://www.w3.org/2000/svg}svg" and svg.get("viewBox") == "0 0 1200 500"
    assert not (out / "2026-10-23" / "picture.svg").exists() and "picture" not in (out / "2026-10-23" / "index.html").read_text()


def test_each_day_draws_the_style_its_date_picks_and_never_the_style_of_the_day_before(tmp_path):
    """The expected styles are what the approved design's script picks for these dates. 11-29 and
    11-30 hash to the same style, so 11-30 moves on, wrapping to the start of the list; 12-01 then
    hashes to the style 11-30 moved to; 12-03 and 12-04 hash alike too."""
    cfg, doc = written(tmp_path)
    expect = {DATE: "strata", "2026-11-27": "spines", "2026-11-28": "contours", "2026-11-29": "constellation",
              "2026-11-30": "endpaper", "2026-12-01": "map", "2026-12-02": "weave", "2026-12-03": "strata",
              "2026-12-04": "contours"}
    v2_days_on(cfg, doc, list(expect)[1:])
    build.build(cfg, tmp_path / "out")
    got = {d: picture_on((tmp_path / "out" / d / "index.html").read_text())[2] for d in expect}
    assert got == expect
    styles = list(got.values())
    assert all(a != b for a, b in zip(styles, styles[1:]))


def test_every_style_draws_a_small_self_contained_svg_in_the_light_and_dark_colors(tmp_path):
    cfg, doc = written(tmp_path)
    busy = {**doc, "sections": doc["sections"] * 4, "scan": doc["scan"] * 6, "updates": doc["updates"] * 8}
    dates = [DATE, "2026-11-27", "2026-11-28", "2026-11-29", "2026-11-30", "2026-12-01", "2026-12-02"]
    v2_days_on(cfg, busy, dates)
    build.build(cfg, tmp_path / "out")
    light, dark = themes()
    seen = set()
    for d in dates:
        seen.add(picture_on((tmp_path / "out" / d / "index.html").read_text())[2])
        svg = (tmp_path / "out" / d / "picture.svg").read_text()
        assert len(svg.encode()) < 40_000, d
        light_part, dark_part = svg.split("@media (prefers-color-scheme:dark)")
        assert f"fill:{light['accent']}" in light_part and f"fill:{dark['accent']}" in dark_part and light["accent"] not in dark_part
        assert "href" not in svg and "url(" not in svg
        assert all(t.isdigit() for t in re.findall(r"<text\b[^>]*>([^<]*)</text>", svg)), d
    assert seen == set(PICTURES)


def test_a_scan_only_day_gets_a_picture_of_its_stories(tmp_path):
    cfg, day = setup_day(tmp_path)
    write_draft(day, {"date": DATE})
    assert finalize.finalize(day)[0]["mode"] == "scan_only"
    build.build(cfg, tmp_path / "out")
    alt, caption, style = picture_on((tmp_path / "out" / DATE / "index.html").read_text())
    assert style not in ("spines", "strata", "contours") and alt and caption
    assert (tmp_path / "out" / DATE / "picture.svg").stat().st_size > 1000


def test_v1_pages_are_copied_unchanged_and_a_v2_brief_wins_its_date(tmp_path):
    cfg, doc = written(tmp_path)
    src = build.v1_dir(cfg)
    pages = {"2026-09-14": v1_page({"dek": "The dek line"}),
             "2026-09-15": v1_page({"bottom_line": {"read": "The old through-line"}}),
             "2026-09-16": v1_page({"what_happened": ["First event </script> and more"]}),
             DATE: v1_page({"dek": "v1's take on the fixture day"})}
    for d, body in pages.items():
        (src / d).mkdir(parents=True)
        (src / d / "index.html").write_text(body)
    r = build.build(cfg, tmp_path / "out")
    assert r == {"v2": [DATE], "v1": ["2026-09-14", "2026-09-15", "2026-09-16"]}
    out = tmp_path / "out"
    assert (out / "2026-09-15" / "index.html").read_bytes() == (src / "2026-09-15" / "index.html").read_bytes()
    assert "v1's take" not in (out / DATE / "index.html").read_text()
    index = (out / "index.html").read_text()
    order = [index.index(f'href="{d}/"') for d in (DATE, "2026-09-16", "2026-09-15", "2026-09-14")]
    assert order == sorted(order)
    for line in ("The dek line", "The old through-line", "First event &lt;/script&gt; and more"):
        assert line in index


def test_the_archive_groups_issues_by_month_and_marks_where_the_first_version_begins(tmp_path):
    cfg, doc = written(tmp_path)
    add_v1(cfg, {d: v1_page({"dek": f"v1 on {d}"}) for d in ("2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02")})
    build.build(cfg, tmp_path / "out")
    index = (tmp_path / "out" / "index.html").read_text()
    months = {m.split("</h2>")[0]: m for m in index.split("<h2>")[1:]}
    assert [text(h) for h in months] == ["October 2026 3 issues", "September 2026 2 issues"]
    october, september = months.values()
    assert re.findall(r'href="(\d{4}-\d{2}-\d{2})/"', october) == [DATE, "2026-10-02", "2026-10-01"]
    assert re.findall(r'href="(\d{4}-\d{2}-\d{2})/"', september) == ["2026-09-30", "2026-09-29"]
    divider = "The first version · September 29 to October 2, 2026 · These issues keep their original layout."
    seen = text(index)
    assert seen.count(divider) == 1
    assert seen.index(f"Sat 24 {page.plain(doc['headline'])}") < seen.index(divider) < seen.index("Fri 2 v1 on 2026-10-02")
    assert "Your morning read on the AI/ML landscape." in seen
    assert "automated daily briefing" not in seen and 'href="https://alexwoodka.com"' not in index
    assert 'href="feed.xml"' in index and 'href="">All issues</a>' in index


def test_an_archive_with_no_first_version_issues_has_no_divider(tmp_path):
    cfg, _ = written(tmp_path)
    build.build(cfg, tmp_path / "out")
    assert "The first version" not in (tmp_path / "out" / "index.html").read_text()


def test_every_page_shares_one_stylesheet_under_a_strict_policy(tmp_path):
    cfg, _ = written(tmp_path)
    add_v1(cfg, {"2026-09-14": v1_page({"dek": "x"})})
    build.build(cfg, tmp_path / "out")
    out = tmp_path / "out"
    for f, root in (("index.html", ""), ("404.html", "/"), (f"{DATE}/index.html", "../")):
        body = (out / f).read_text()
        assert f'content="{CSP}"' in body
        assert f'<link rel="stylesheet" href="{root}site.css">' in body and "<style" not in body and "style=" not in body
        assert f'href="{root}favicon.svg"' in body and "<script" not in body
    assert (out / "site.css").read_text() == (page.TEMPLATES / "site.css").read_text()
    assert "No issue at this address." in text((out / "404.html").read_text())
    assert 'href="/">All issues</a>' in (out / "404.html").read_text()
    icon, light = (out / "favicon.svg").read_text(), themes()[0]
    assert icon.startswith("<svg") and ">D</text>" in icon
    assert f'fill="{light["paper"]}"' in icon and f'fill="{light["accent"]}"' in icon


def test_the_typeface_ships_with_the_site_and_every_page_preloads_the_upright_face(tmp_path):
    cfg, _ = written(tmp_path)
    add_v1(cfg, {"2026-09-14": v1_page({"dek": "x"})})
    build.build(cfg, tmp_path / "out")
    out = tmp_path / "out"
    css = (out / "site.css").read_text()
    faces = {re.search(r"font-style:\s*(\w+)", f)[1]: re.search(r"url\(([^)]+)\)", f)[1]
             for f in re.findall(r"@font-face\s*\{([^}]*)\}", css)}
    assert faces == {"normal": "fonts/newsreader.woff2", "italic": "fonts/newsreader-italic.woff2"}
    assert all((out / src).read_bytes()[:4] == b"wOF2" for src in faces.values())
    assert "SIL Open Font License" in (out / "fonts" / "OFL.txt").read_text()
    assert "--serif: Newsreader, Georgia, serif;" in css and "font-family: var(--serif);" in css
    for f, root in (("index.html", ""), ("404.html", "/"), (f"{DATE}/index.html", "../")):
        body = (out / f).read_text()
        assert f'<link rel="preload" href="{root}fonts/newsreader.woff2" as="font" type="font/woff2" crossorigin>' in body


def themes():
    """site.css's tokens: (light, dark), each {name: value}, dark falling back to light."""
    css = (page.TEMPLATES / "site.css").read_text()
    light_part, dark_part = css.split("@media (prefers-color-scheme: dark)", 1)
    token = re.compile(r"--([\w-]+):\s*([^;]+);")
    light = dict(token.findall(light_part))
    return light, {**light, **dict(token.findall(dark_part.split("}", 1)[0]))}


def rgb(value, theme):
    mix = re.fullmatch(r"color-mix\(in srgb, var\(--([\w-]+)\) (\d+)%, var\(--([\w-]+)\)\)", value)
    if mix:
        w = int(mix[2]) / 100
        return tuple(w * a + (1 - w) * b for a, b in zip(rgb(theme[mix[1]], theme), rgb(theme[mix[3]], theme)))
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def contrast(a, b):
    def lum(c):
        lin = [(v / 255) / 12.92 if v / 255 <= 0.04045 else ((v / 255 + 0.055) / 1.055) ** 2.4 for v in c]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("theme", [0, 1], ids=["light", "dark"])
def test_every_text_color_reads_at_4_5_to_1_on_the_paper(theme):
    t = themes()[theme]
    for name in ("ink", "muted", "accent", "for", "against"):
        assert contrast(rgb(t[name], t), rgb(t["paper"], t)) >= 4.5, name


def test_the_feed_lists_the_newest_30_issues_newest_first(tmp_path):
    cfg, doc = written(tmp_path)
    add_v1(cfg, {f"2026-08-{d:02}": v1_page({"dek": f'Day {d}: "quotes" & <tags>'}) for d in range(1, 32)})
    build.build(cfg, tmp_path / "out")
    atom = "{http://www.w3.org/2005/Atom}"
    feed = ET.parse(tmp_path / "out" / "feed.xml").getroot()
    entries = feed.findall(f"{atom}entry")
    assert len(entries) == 30
    ids = [e.findtext(f"{atom}id") for e in entries]
    assert ids[:2] == [f"https://debrief.alexwoodka.com/{DATE}/", "https://debrief.alexwoodka.com/2026-08-31/"]
    assert ids[-1] == "https://debrief.alexwoodka.com/2026-08-03/"
    first, second = entries[0], entries[1]
    assert first.findtext(f"{atom}title") == page.plain(doc["headline"])
    assert first.find(f"{atom}link").get("href") == ids[0]
    assert first.findtext(f"{atom}updated") == f"{DATE}T00:00:00Z" == feed.findtext(f"{atom}updated")
    assert second.findtext(f"{atom}summary") == 'Day 31: "quotes" & <tags>'
    assert 'type="application/atom+xml"' in (tmp_path / "out" / "index.html").read_text()


def test_an_issue_with_no_headline_is_titled_by_its_date_in_the_feed(tmp_path):
    cfg, doc = written(tmp_path)
    doc["headline"] = ""
    (cfg.data_dir / "runs" / DATE / "brief.json").write_text(json.dumps(doc))
    build.build(cfg, tmp_path / "out")
    entry = ET.parse(tmp_path / "out" / "feed.xml").getroot().find("{http://www.w3.org/2005/Atom}entry")
    assert entry.findtext("{http://www.w3.org/2005/Atom}title") == "Saturday, October 24, 2026"


def test_the_build_refuses_a_folder_with_files_in_it(tmp_path):
    cfg, _ = written(tmp_path)
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "old.html").write_text("x")
    with pytest.raises(build.SiteError):
        build.build(cfg, tmp_path / "out")
