"""Attach rules, coverage, the retro pass, discussion bands and trend, on a hand-built store."""
from dataclasses import replace

from conftest import record

from debrief import config
from debrief.adapters import Adapter, Item
from debrief.store import Store
from debrief.stories import attach, build, signals

CFG = config.load(env={"DEBRIEF_DATA_DIR": "/tmp/debrief-test-data"})
S = CFG.settings


class Stub(Adapter):
    windowed = False

    def __init__(self, items):
        self.items = items

    def parse(self, rec, source, opts):
        return self.items


def apply(store, source_id, items, at="2026-10-22T20:00:00Z", **src_override):
    src = replace(CFG.source(source_id), **src_override) if src_override else CFG.source(source_id)
    with store.transaction():
        return build.apply(store, S, src, Stub(items), [record("", fetched_at=at)], ["raw/x#0"], "2026-10-22")


PAPER = Item(item_key="2610.28708", role="anchor", story_id="arxiv:2610.28708", url="https://arxiv.org/abs/2610.28708",
             title="Transfer Learning for Choice Models Across Retail Stores", kind="paper")
POST = Item(item_key="p1", role="anchor", url="https://openai.com/index/decisions-api",
            title="Build more responsive apps with the Decisions API", kind="announcement")


def thread(key, url=None, title="x", points=10, comments=0, **kw):
    return Item(item_key=key, role="mention", mention_kind="thread", url=f"https://news.ycombinator.com/item?id={key}",
                title=title, links=(url,) if url else (), published_at="2026-10-22T19:00:00Z",
                engagement={"points": points, "comments": comments}, **kw)


def fresh(tmp_path, name="s"):
    s = Store(tmp_path / f"{name}.sqlite")
    apply(s, "arxiv-rss", [PAPER])
    apply(s, "openai-news", [POST])
    return s


def story_of(store, mid):
    m = store.mention(mid)
    return m["story_id"], m["match_method"]


def test_attach_ladder(tmp_path):
    s = fresh(tmp_path)
    apply(s, "hn", [
        thread("1", url="https://openai.com/index/decisions-api/?utm_source=hn"),       # url
        thread("2", url="https://arxiv.org/pdf/2610.28708v2.pdf"),                      # id from URL
        thread("3", title="Paper 2610.28708 is worth reading"),                          # id from text
        thread("4", title="Build more responsive apps with the Decisions API today"),    # title
        thread("5", title="Something unrelated entirely about databases")])              # nothing
    post_id = "url:" + __import__("hashlib").sha1(b"https://openai.com/index/decisions-api").hexdigest()[:16]
    assert story_of(s, "hn|1") == (post_id, "url")
    assert story_of(s, "hn|2") == ("arxiv:2610.28708", "id")
    assert story_of(s, "hn|3") == ("arxiv:2610.28708", "id")
    assert story_of(s, "hn|4") == (post_id, "title")
    assert story_of(s, "hn|5") == (None, None)


def test_comments_take_their_threads_story_and_hints_win(tmp_path):
    s = fresh(tmp_path)
    apply(s, "hn", [thread("10", url="https://arxiv.org/abs/2610.28708")])
    apply(s, "hn-items", [Item(item_key="11", role="mention", mention_kind="comment", parent_id="hn|10", summary="hi")])
    apply(s, "hf-paper-comments", [Item(item_key="c1", role="mention", mention_kind="comment",
                                         story_hint="arxiv:2610.28708", summary="nice")])
    assert story_of(s, "hn-items|11") == ("arxiv:2610.28708", "parent")
    assert story_of(s, "hf-paper-comments|c1") == ("arxiv:2610.28708", "hint")


def test_reference_posts_get_coverage_not_a_story(tmp_path):
    s = fresh(tmp_path)
    apply(s, "import-ai", [Item(item_key="issue-1", role="mention", title="Import AI 478",
                                 links=("https://arxiv.org/abs/2610.28708", "https://openai.com/index/decisions-api", "https://example.com/x"))])
    m = s.mention("import-ai|issue-1")
    assert m["story_id"] is None and m["kind"] == "reference"
    assert sorted(r[0] for r in s.q("SELECT story_id FROM coverage")) == sorted(
        ["arxiv:2610.28708", s.one("SELECT story_id FROM stories WHERE kind='announcement'")[0]])


def test_retro_attaches_once_the_artifact_arrives(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    apply(s, "hn", [thread("20", url="https://arxiv.org/abs/2610.28708")])
    assert story_of(s, "hn|20") == (None, None)
    apply(s, "arxiv-rss", [PAPER], at="2026-10-22T21:00:00Z")
    with s.transaction():
        c = attach.retro(s, S, "2026-10-22T21:00:00Z", {x.id: x.roles for x in CFG.sources})
    assert c["retro_url"] == 1 and story_of(s, "hn|20") == ("arxiv:2610.28708", "url")


def test_signals_are_recorded_on_every_sighting(tmp_path):
    s = fresh(tmp_path)
    for at, up in (("2026-10-22T10:00:00Z", 5), ("2026-10-22T16:00:00Z", 12)):
        apply(s, "hf-daily-papers", [replace(PAPER, signals={"hf_upvotes": up})], at=at)
    assert s.latest_signals("arxiv:2610.28708") == {"hf_upvotes": 12.0}
    apply(s, "hf-paper-detail", [Item(item_key="2610.28708", role="signal", story_id="arxiv:2610.28708",
                                       signals={"github_stars": 150, "hf_models": 0})], at="2026-10-22T17:00:00Z")
    u = signals.paper_usage(s, "arxiv:2610.28708")
    assert u["stars"] == 150 and signals.passes_use_bar(u, S["collect"]["paper_use_bar"])
    assert s.one("SELECT COUNT(*) FROM items WHERE source_id='hf-paper-detail'")[0] == 0   # lookups aren't seen-set items


def test_bands_use_within_source_percentiles(tmp_path):
    s = fresh(tmp_path)
    background = [thread(f"b{i}", title=f"background story number {i}", points=i) for i in range(1, 311)]
    apply(s, "hn", background)
    apply(s, "hn", [thread("hot", url="https://arxiv.org/abs/2610.28708", points=305)])
    d = signals.discussion(s, S, "arxiv:2610.28708", "2026-10-22T20:00:00Z")
    assert d["band"] == "heavy" and d["by_source"]["hn"]["pctl"] >= 95 and d["spread"] == 1
    apply(s, "hn", [thread("cool", url="https://openai.com/index/decisions-api", points=100)])
    post = s.one("SELECT story_id FROM stories WHERE kind='announcement'")[0]
    assert signals.discussion(s, S, post, "2026-10-22T20:00:00Z")["band"] == "some"
    assert signals.discussion(s, S, "nope", "2026-10-22T20:00:00Z")["band"] == "none"


def test_without_a_distribution_spread_decides(tmp_path):
    s = fresh(tmp_path)
    apply(s, "hn", [thread("t", url="https://arxiv.org/abs/2610.28708", points=5)])
    apply(s, "hf-paper-comments", [Item(item_key="c", role="mention", mention_kind="comment",
                                         story_hint="arxiv:2610.28708", summary="x")])
    d = signals.discussion(s, S, "arxiv:2610.28708", "2026-10-22T20:00:00Z")
    assert d["by_source"]["hn"]["pctl"] is None and (d["spread"], d["band"]) == (2, "notable")


def test_trend_from_snapshots(tmp_path):
    s = fresh(tmp_path)
    for at, pts in (("2026-10-22T00:00:00Z", 10), ("2026-10-22T06:00:00Z", 20), ("2026-10-22T12:00:00Z", 80)):
        apply(s, "hn", [thread("r", url="https://arxiv.org/abs/2610.28708", points=pts)], at=at)
    assert signals.trend(s, "arxiv:2610.28708", "2026-10-22T12:00:00Z", S["trend"]) == "rising"
    assert signals.trend(s, "arxiv:2610.28708", "2026-10-22T06:00:00Z", S["trend"]) == "unknown"
    apply(s, "hn", [thread("r", url="https://arxiv.org/abs/2610.28708", points=82)], at="2026-10-22T18:00:00Z")
    assert signals.trend(s, "arxiv:2610.28708", "2026-10-22T18:00:00Z", S["trend"]) == "falling"


def test_threads_below_the_floor_stay_out_of_the_distribution(tmp_path):
    s = fresh(tmp_path)
    apply(s, "hn", [thread(f"n{i}", title=f"ignored submission {i}", points=2) for i in range(500)]
          + [thread(f"d{i}", title=f"discussed story {i}", points=10 + i) for i in range(250)])
    d = signals.Distributions(s, "2026-10-22T20:00:00Z", 14, S["bands"]["floor"])
    assert len(d.of("hn")) == 250 and min(d.of("hn")) == 10


def test_entity_rule_needs_a_unique_name(tmp_path):
    s = fresh(tmp_path)
    apply(s, "ars-technica-ai", [Item(item_key="a", role="anchor", url="https://arstechnica.com/php2shell",
                                       title="Hackers got a four-day head start on epic PHP2Shell vulnerability", kind="press")])
    apply(s, "openai-news", [Item(item_key="f1", role="anchor", url="https://a.test/flint", title="Recursive releases Flint-v1 coding model", kind="announcement"),
                             Item(item_key="f2", role="anchor", url="https://b.test/flint", title="Microsoft ships the Flint-v1 toolkit", kind="announcement")])
    apply(s, "hn", [thread("e1", url="https://wiz.io/blog/php2shell-php-rce", title="PHP2Shell: AI uncovers critical RCE in PHP"),
                    thread("e2", url="https://x.test/y", title="Trying Flint-v1 this weekend")])
    sid, method = story_of(s, "hn|e1")
    assert method == "entity" and s.story(sid)["title"].startswith("Hackers got")
    assert story_of(s, "hn|e2") == (None, None)          # flint-v1 names two stories: ambiguous, no attach
    from debrief.stories.attach import entities
    assert entities("PHP2Shell, DeepSeek-V4.1 and Qwen3.8 beat GPT-5 in 2026.10") == {"php2shell", "qwen3.8"}   # V4.1, GPT-5: too short
    assert entities("SpaceX and DeepSeek on GeForce NOW") == set()      # brand words aren't identifiers


def test_small_moves_are_flat_not_falling(tmp_path):
    s = fresh(tmp_path)
    for at, pts in (("2026-10-22T00:00:00Z", 3), ("2026-10-22T06:00:00Z", 5), ("2026-10-22T12:00:00Z", 5)):
        apply(s, "hn", [thread("q", url="https://arxiv.org/abs/2610.28708", points=pts)], at=at)
    assert signals.trend(s, "arxiv:2610.28708", "2026-10-22T12:00:00Z", S["trend"]) == "flat"


def test_strong_evidence_moves_a_weak_attachment(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    apply(s, "techcrunch-ai", [Item(item_key="fund", role="anchor", url="https://tc.test/deepseek-raises",
                                     title="DeepSeek-V4.1-Flash-1017 maker raises $15B", kind="press")])
    apply(s, "hn", [thread("d1", url="https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017",
                           title="DeepSeek-V4.1-Flash-1017")])
    assert story_of(s, "hn|d1")[1] == "entity"          # the model's story doesn't exist yet
    apply(s, "hf-org-watch", [Item(item_key="deepseek-ai/DeepSeek-V4.1-Flash-1017", role="anchor",
                                    story_id="hf-model:deepseek-ai/deepseek-v4.1-flash-1017",
                                    url="https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017",
                                    title="deepseek-ai/DeepSeek-V4.1-Flash-1017", kind="release")], at="2026-10-22T21:00:00Z")
    with s.transaction():
        c = attach.retro(s, S, "2026-10-22T21:00:00Z", {x.id: x.roles for x in CFG.sources})
    assert c["retro_upgraded"] == 1 and story_of(s, "hn|d1") == ("hf-model:deepseek-ai/deepseek-v4.1-flash-1017", "url")


def test_comment_trees_count_as_their_threads_community(tmp_path):
    s = fresh(tmp_path)
    apply(s, "hn", [thread("t1", url="https://arxiv.org/abs/2610.28708", points=5)])
    apply(s, "hn-items", [Item(item_key="c1", role="mention", mention_kind="comment", parent_id="hn|t1", summary="x")])
    d = signals.discussion(s, S, "arxiv:2610.28708", "2026-10-22T20:00:00Z")
    assert d["where"] == ["hn"] and d["spread"] == 1 and d["by_source"]["hn"]["comments_captured"] == 1


def test_entity_rule_skips_papers(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    apply(s, "arxiv-rss", [replace(PAPER, title="FlashNext-RL: RL systems for Qwen3.8-Flash-Next")])
    apply(s, "hn", [thread("q1", url="https://x.test/run", title="Run Qwen3.8-Flash-Next on 8GB VRAM")])
    assert story_of(s, "hn|q1") == (None, None)
