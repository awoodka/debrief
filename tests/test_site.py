"""The site (src/debrief/site/): a day's page from its brief.json, the archive, v1's frozen pages,
and the published copy of the data. On the brief fixture day (tests/fixtures/brief/)."""
import json

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


def test_a_day_page_has_every_part_of_the_brief(tmp_path):
    _, doc = written(tmp_path)
    doc["meta"]["session"] = {"cost_usd_list_price": 1.2345, "stderr": "a session error"}
    doc["meta"]["warnings"] = ["the article is 120 words, under the aim"]
    html = page.day_page(doc)
    for text in [doc["headline"], *(e["text"] for e in doc["what_happened"])]:
        assert page.inline(text) in html
    for n, s in enumerate(doc["sections"], 1):
        assert f'<section class="theme" id="s-{n}" data-stories="{page.esc(" ".join(s["stories"]))}">' in html
        assert page.inline(s["heading"]) in html
    assert ">For<" in html and ">Against<" in html and ">My read<" in html
    q = doc["sections"][0]["quotes"][0]
    assert page.esc(q["text"]) in html and f'href="{page.esc(q["url"])}"' in html
    assert page.inline(doc["updates"][0]["text"]) in html and "first reported" in html
    assert all(page.esc(x["title"]) in html for x in doc["scan"])
    assert html.count('class="in"') == sum(x["covered"] for x in doc["scan"])
    assert "HN · " in html                                                       # thread chips
    assert "Content-Security-Policy" in html and "<script" not in html
    assert "1.2345" not in html and "a session error" not in html and "under the aim" not in html


def test_untrusted_text_is_escaped_and_only_http_links_are_kept(tmp_path):
    _, doc = written(tmp_path)
    doc["scan"][0].update(title='<script>alert(1)</script>', url="javascript:alert(1)")
    doc["sections"][0]["body"] += "\n\nA **bold** claim, an *aside*, 2 * 3 * 4, and <b>raw</b> tags."
    html = page.day_page(doc)
    assert "<script>alert" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "javascript:" not in html.replace("&lt;script&gt;", "")
    assert "<strong>bold</strong>" in html and "<em>aside</em>" in html and "2 * 3 * 4" in html
    assert "&lt;b&gt;raw&lt;/b&gt;" in html


def test_a_scan_only_brief_gets_a_page_with_its_note(tmp_path):
    cfg, day = setup_day(tmp_path)
    write_draft(day, {"date": DATE})
    doc, _ = finalize.finalize(day)
    assert doc["mode"] == "scan_only"
    html = page.day_page(doc)
    assert 'class="notice"' in html and page.inline(doc["notes"][0]) in html
    assert 'class="theme"' not in html and "headline" not in html.split("<main", 1)[1]
    assert all(page.esc(x["title"]) in html for x in doc["scan"])


def test_the_site_build_is_byte_stable_and_publishes_only_the_public_data(tmp_path):
    cfg, doc = written(tmp_path)
    a, b = build.build(cfg, tmp_path / "a"), build.build(cfg, tmp_path / "b")
    assert a == b == {"v2": [DATE], "v1": []}
    files = sorted(p.relative_to(tmp_path / "a") for p in (tmp_path / "a").rglob("*") if p.is_file())
    assert [str(f) for f in files] == [f"{DATE}/brief.json", f"{DATE}/index.html", "index.html"]
    assert all((tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes() for f in files)
    pub = json.loads((tmp_path / "a" / DATE / "brief.json").read_text())
    assert "session" not in pub["meta"] and "warnings" not in pub["meta"]
    assert all(f["allowed"] for f in pub["fetches"]) and pub["sections"] == doc["sections"]
    assert page.esc(doc["headline"]) in (tmp_path / "a" / "index.html").read_text()


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


def test_the_build_refuses_a_folder_with_files_in_it(tmp_path):
    cfg, _ = written(tmp_path)
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "old.html").write_text("x")
    with pytest.raises(build.SiteError):
        build.build(cfg, tmp_path / "out")
