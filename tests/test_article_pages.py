"""Article pages (M3): what gets fetched for which story, and the text and links kept."""
from conftest import record

from debrief import config
from debrief.adapters import REGISTRY, Item
from debrief.adapters.article_pages import due, page_targets
from debrief.collect import state_path
from debrief.store import Store
from test_triage import EARLIER, apply, model, post

CFG = config.load(env={"DEBRIEF_DATA_DIR": "/tmp/debrief-test-data"})
OPTS = CFG.settings["collect"]

ARTICLE = """<html><head><title>Big news</title></head><body>
<nav><a href="https://site.test/">Home</a> <a href="https://site.test/subscribe">Subscribe</a></nav>
<article><h1>Big news</h1>
<p>The lab released <a href="https://huggingface.co/lab/Big-1">its new model</a> today, along with a
<a href="/research/paper">paper</a> describing how it was trained on a very large amount of data.</p>
<p>Critics pointed to <a href="https://arxiv.org/abs/2610.12345v2">earlier work</a> and said the gains
were smaller than claimed, but most agreed the release changes what small teams can build.</p>
<p>It is available under an open license, and the weights can be downloaded now from the hub.</p>
</article><footer><a href="https://site.test/privacy">Privacy</a></footer></body></html>"""


def test_article_keeps_main_text_and_its_links_only():
    rec = record(ARTICLE, url="https://site.test/news/big", meta={"story": "url:abc", "kind": "html"})
    [it] = REGISTRY["article_pages"].parse(rec, CFG.source("article-pages"), OPTS)
    assert it.role == "page" and it.story_id == "url:abc" and it.page["status"] == 200
    assert "changes what small teams can build" in it.page["text"] and "Privacy" not in it.page["text"]
    assert it.page["links"] == ["https://huggingface.co/lab/Big-1", "https://site.test/research/paper",
                                "https://arxiv.org/abs/2610.12345v2"]


def test_model_card_drops_front_matter_and_relative_links():
    card = "---\nlicense: mit\nbase_model: x/y\n---\n# Big-1\nSee [the paper](https://arxiv.org/abs/2610.1) and [license](LICENSE).\n"
    rec = record(card, url="https://huggingface.co/lab/Big-1/raw/main/README.md", meta={"story": "s", "kind": "readme"})
    [it] = REGISTRY["article_pages"].parse(rec, CFG.source("article-pages"), OPTS)
    assert "license: mit" not in it.page["text"] and "See the paper and license." in it.page["text"]
    assert it.page["links"] == ["https://arxiv.org/abs/2610.1"]


def test_a_failed_fetch_is_a_page_with_its_status():
    rec = record("nope", url="https://site.test/x", status=404, meta={"story": "s", "kind": "html"})
    [it] = REGISTRY["article_pages"].parse(rec, CFG.source("article-pages"), OPTS)
    assert it.page == {"status": 404, "text": "", "links": []}


def test_what_is_due(tmp_path):
    cfg = config.load(env={"DEBRIEF_DATA_DIR": str(tmp_path / "data")})
    s = Store(state_path(cfg))
    apply(s, cfg, "openai-news", [post("a", "A post about models"), post("b", "Another post about models"),
                                  post("old", "An old post", "2026-10-10T00:00:00Z"),
                                  post("wsj", "Paywalled", url="https://www.wsj.com/tech/x")])
    apply(s, cfg, "hf-org-watch", [model("lab/Big-1", "2026-10-23T02:00:00Z", 5),
                                   model("lab/Gated-2", "2026-10-23T02:00:00Z", 5)])
    apply(s, cfg, "prnewswire", [post("pr", "A press release", kind="press")])           # disabled source
    a, b = s.story_for_url("https://example.test/a"), s.story_for_url("https://example.test/b")
    gated = "hf-model:lab/gated-2"
    apply(s, cfg, "article-pages", [Item(item_key=a, role="page", story_id=a, url="https://example.test/a",
                                         page={"status": 403, "text": "", "links": []}),
                                    Item(item_key=gated, role="page", story_id=gated,
                                         url="https://huggingface.co/lab/Gated-2/raw/main/README.md",
                                         page={"status": 401, "text": "", "links": []})])
    enabled = {x.id for x in cfg.sources if x.enabled}
    todo, skipped = due(s, cfg.settings, enabled, "2026-10-23T12:00:00Z")
    # oldest first, then by story_id; a 403 isn't retried, a week-old post is out of the window
    assert [(st["story_id"], url, kind) for st, url, kind in todo] == [
        ("hf-model:lab/big-1", "https://huggingface.co/lab/Big-1/raw/main/README.md", "readme"),
        (gated, "https://huggingface.co/lab/Gated-2", "html"),
        (b, "https://example.test/b", "html")]
    assert skipped == {"paywalled domain": 1}


def test_release_page_targets():
    gh = {"story_id": "gh-repo:deepseek-ai/deepinfer", "section": "releases", "extra": {},
          "artifact_urls": ["https://github.com/deepseek-ai/DeepInfer"], "code_urls": ["https://github.com/deepseek-ai/DeepInfer"]}
    assert page_targets(gh)[0] == [("https://raw.githubusercontent.com/deepseek-ai/DeepInfer/HEAD/README.md", "readme")]
    fmt = {"story_id": "hf-model:lab/big-1", "section": "releases", "artifact_urls": [], "code_urls": [],
           "extra": {"a": {"model_id": "lab/Big-1-GGUF", "format": ["gguf"]}, "b": {"model_id": "lab/Big-1", "format": None}}}
    assert page_targets(fmt)[0] == [("https://huggingface.co/lab/Big-1/raw/main/README.md", "readme"),
                                    ("https://huggingface.co/lab/Big-1", "html")]     # a gated repo's card
    arena = {"story_id": "url:x", "section": "releases", "extra": {}, "code_urls": [],
             "artifact_urls": ["https://lmarena.ai/leaderboard?model=x"]}
    assert page_targets(arena) == ([], "no page for this kind of release")


def test_a_listing_post_carries_its_page(tmp_path):
    cfg = config.load(env={"DEBRIEF_DATA_DIR": str(tmp_path / "data")})
    src = cfg.source("anthropic-news")
    rec = record(ARTICLE, url="https://www.anthropic.com/news/big", meta={"kind": "post", "link": "https://anthropic.com/news/big"})
    [it] = REGISTRY["listing_page"].parse(rec, src, cfg.settings["collect"])
    assert it.role == "anchor" and "huggingface.co/lab/Big-1" in it.page["links"][0]
    s = Store(state_path(cfg))
    apply(s, cfg, "anthropic-news", [it], at=EARLIER)
    [p] = s.pages(s.story_for_url("https://anthropic.com/news/big"))
    assert p["status"] == 200 and p["links"][0] == "https://huggingface.co/lab/Big-1"
