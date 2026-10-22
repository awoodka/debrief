"""Each adapter's parse() against real responses captured on 2026-10-22 (tests/fixtures)."""
from datetime import date

from conftest import fixture, record

from debrief import config, extract
from debrief.adapters import REGISTRY
from debrief.adapters.arxiv import missed_weekdays

CFG = config.load(env={"DEBRIEF_DATA_DIR": "/tmp/debrief-test-data"})
OPTS = CFG.settings["collect"]


def parse(source_id, rec):
    src = CFG.source(source_id)
    return REGISTRY[src.adapter].parse(rec, src, OPTS)


def test_lab_feed_entries_are_anchors():
    items = parse("openai-news", record(fixture("rss/openai_news.xml")))
    assert len(items) == 3 and {i.role for i in items} == {"anchor"}
    first = items[0]
    assert first.title and first.url.startswith("https://openai.com/")
    assert first.published_at.endswith("Z") and first.org == "OpenAI" and first.kind == "announcement"


def test_reference_feed_entries_are_mentions():
    items = parse("simon-willison", record(fixture("rss/simonwillison.atom")))
    assert len(items) == 3 and {i.role for i in items} == {"mention"}
    assert all(i.url.startswith("https://simonwillison.net/") for i in items)


def test_failed_response_parses_to_nothing():
    assert parse("openai-news", record(b"rate limited", status=429)) == []


def test_listing_links_skip_category_pages():
    src = CFG.source("anthropic-news")
    found = [u for u, _ in extract.links(fixture("listing/anthropic_news.html"), "https://www.anthropic.com/news",
                                          src.probe["link_pattern"], exclude=src.options["exclude"])]
    slugs = {u.rsplit("/", 1)[1] for u in found}
    assert "public-benefit-fund" in slugs
    assert not slugs & {"announcements", "features", "product", "policy", "safeguards", "security", "partnerships"}


def test_listing_page_record_marks_listed_links_seen():
    meta = {"kind": "page", "seen": ["https://anthropic.com/news/a", "https://anthropic.com/news/b"],
            "baseline": ["https://anthropic.com/news/b"], "blocked": []}
    items = parse("anthropic-news", record(b"<html></html>", meta=meta))
    assert [(i.item_key, i.role, i.extra["reason"]) for i in items] == [
        ("https://anthropic.com/news/a", "seen", "listed"), ("https://anthropic.com/news/b", "seen", "baseline")]


def test_listing_post_becomes_an_anchor_with_metadata():
    link = "https://anthropic.com/news/public-benefit-fund"
    items = parse("anthropic-news", record(fixture("listing/anthropic_post.html"), url=link,
                                           meta={"kind": "post", "link": link}))
    (it,) = items
    assert (it.role, it.item_key, it.title) == ("anchor", link, "Investing in the public benefit")
    assert it.published_at == "2026-10-18T00:00:00Z" and it.summary.startswith("We’re committing")


def test_listing_post_without_article_markup_is_still_a_post():
    link = "https://ai.meta.com/blog/some-post"
    html = b"<html><head><title>Introducing X</title></head><body><p>Posted July 9, 2026</p><p>Today we ship X.</p></body></html>"
    (it,) = parse("meta-ai-blog", record(html, url=link, meta={"kind": "post", "link": link}))
    assert (it.role, it.title, it.published_at) == ("anchor", "Introducing X", "2026-07-09T00:00:00Z")
    assert it.extra == {"article_markup": False, "date_from": "text"}


def test_listing_post_from_a_reference_source_is_a_mention():
    link = "https://deeplearning.ai/the-batch/issue-375"
    (it,) = parse("the-batch", record(fixture("listing/anthropic_post.html"), url=link,
                                       meta={"kind": "post", "link": link}))
    assert it.role == "mention"


def test_hf_daily_papers():
    items = parse("hf-daily-papers", record(fixture("hf/daily_papers_2026-10-21.json"), meta={"date": "2026-10-21"}))
    assert len(items) == 3 and {i.role for i in items} == {"anchor"}
    it = items[0]
    assert it.story_id == f"arxiv:{it.item_key}" and it.url == f"https://arxiv.org/abs/{it.item_key}"
    assert it.artifact_urls == (f"https://huggingface.co/papers/{it.item_key}",)
    assert isinstance(it.extra["hf_upvotes"], int) and it.extra["listed_on"] == "2026-10-21"
    assert len(it.authors) <= OPTS["max_authors"]


def test_arxiv_rss_roles_follow_announce_type():
    items = parse("arxiv-rss", record(fixture("arxiv/rss_stat.ML.xml"), meta={"kind": "rss"}, each="stat.ML"))
    assert [(i.extra["announce_type"], i.role) for i in items] == [
        ("new", "anchor"), ("new", "anchor"), ("cross", "anchor"), ("replace", "seen"), ("replace-cross", "seen")]
    it = items[0]
    assert it.story_id == "arxiv:2610.28708" and not it.summary.startswith("arXiv:")
    assert it.published_at == "2026-10-22T04:00:00Z" and "stat.ML" in it.extra["categories"]


def test_arxiv_oai_keeps_only_new_papers():
    items = parse("arxiv-rss", record(fixture("arxiv/oai_arxivraw_page1.xml"), meta={"kind": "oai"}))
    assert [(i.item_key, i.role) for i in items] == [
        ("2610.24336", "anchor"),      # only a v1, 3 days before its datestamp: newly announced
        ("2610.23917", "seen"),        # only a v1, but 17 days before: held or edited, not new
        ("2305.17817", "seen"),        # only a v1, from 2023: a metadata edit
        ("1908.04284", "seen")]        # seven versions: a replacement
    assert items[0].published_at == "2026-10-20T00:00:00Z"


def test_missed_weekdays_skip_weekends():
    assert missed_weekdays(date(2026, 10, 15), date(2026, 10, 20)) == [date(2026, 10, 16), date(2026, 10, 19)]
    assert missed_weekdays(date(2026, 10, 21), date(2026, 10, 22)) == []


def test_github_releases():
    items = parse("github-releases", record(fixture("github/releases_vllm.json"), each="vllm-project/vllm"))
    assert [i.story_id for i in items][0] == "gh-release:vllm-project/vllm@v0.32.0"
    assert items[0].title == "vllm-project/vllm v0.32.0" and items[0].extra["prerelease"] is False
    assert items[0].code_urls == ("https://github.com/vllm-project/vllm",)
