"""The story builder's rules, with a stub adapter so each case states its items directly."""
from dataclasses import replace

from conftest import record

from debrief import config
from debrief.adapters import Adapter, Item
from debrief.store import Store
from debrief.stories import build

CFG = config.load(env={"DEBRIEF_DATA_DIR": "/tmp/debrief-test-data"})


class Stub(Adapter):
    def __init__(self, items, windowed=True):  # windowed: True | "bootstrap" | False
        self.items, self.windowed = items, windowed

    def parse(self, rec, source, opts):
        return self.items


def src(**kw):
    base = CFG.source("openai-news")
    return replace(base, **kw)


def apply(store, source, items, fetched_at="2026-10-22T20:00:00Z", windowed=True, run_date="2026-10-22"):
    with store.transaction():
        return build.apply(store, CFG.settings, source, Stub(items, windowed),
                           [record("", fetched_at=fetched_at)], ["raw/x#0"], run_date)


PAPER = Item(item_key="2610.1", role="anchor", story_id="arxiv:2610.1", url="https://arxiv.org/abs/2610.1",
             title="A paper", summary="Abstract.", published_at="2026-10-22T04:00:00Z", kind="paper")


def test_second_run_with_the_same_items_adds_nothing(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    first = apply(s, src(), [PAPER])
    second = apply(s, src(), [PAPER], fetched_at="2026-10-22T23:00:00Z")
    assert (first["stories_new"], second["new"], second["stories_new"]) == (1, 0, 0)
    assert s.one("SELECT COUNT(*) FROM stories")[0] == 1
    assert s.item("openai-news", "2610.1")["last_seen_at"] == "2026-10-22T23:00:00Z"


def test_two_sources_merge_into_one_story(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    apply(s, src(id="arxiv-rss", tier=2, org=None), [PAPER])
    hf = replace(PAPER, summary="", artifact_urls=("https://huggingface.co/papers/2610.1",),
                 code_urls=("https://github.com/a/b",), published_at="2026-10-20T00:00:00Z",
                 org="Some Lab", extra={"hf_upvotes": 5})
    c = apply(s, src(id="hf-daily-papers", tier=1, org=None), [hf])
    story = s.story("arxiv:2610.1")
    assert c["stories_merged"] == 1 and story["sources"] == ["arxiv-rss", "hf-daily-papers"]
    assert story["tier"] == 1 and story["summary"] == "Abstract." and story["org"] == "Some Lab"
    assert story["published_at"] == "2026-10-20T00:00:00Z"
    assert story["artifact_urls"] == ["https://arxiv.org/abs/2610.1", "https://huggingface.co/papers/2610.1"]
    assert story["code_urls"] == ["https://github.com/a/b"] and story["extra"] == {"hf-daily-papers": {"hf_upvotes": 5}}
    assert {r[0] for r in s.q("SELECT url FROM story_urls")} == set(story["artifact_urls"])


def test_old_entries_from_windowed_sources_are_only_seen(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    old = replace(PAPER, item_key="old", story_id="arxiv:old", published_at="2026-09-01T00:00:00Z")
    c = apply(s, src(), [PAPER, old])
    assert (c["stories_new"], c["too_old"]) == (1, 1) and s.item("openai-news", "old")["role"] == "seen"
    c = apply(Store(tmp_path / "t.sqlite"), src(), [old], windowed=False)
    assert c["stories_new"] == 1


def test_owner_edits_update_the_story_but_others_do_not(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    apply(s, src(), [PAPER])
    apply(s, src(id="other", tier=2), [PAPER])
    apply(s, src(id="other", tier=2), [replace(PAPER, title="Other title")], fetched_at="2026-10-23T01:00:00Z")
    assert s.story("arxiv:2610.1")["title"] == "A paper"
    c = apply(s, src(), [replace(PAPER, title="Corrected title")], fetched_at="2026-10-23T02:00:00Z")
    story = s.story("arxiv:2610.1")
    assert c["stories_changed"] == 1 and story["title"] == "Corrected title"
    assert story["last_changed_at"] == "2026-10-23T02:00:00Z" and story["first_seen_at"] == "2026-10-22T20:00:00Z"


def test_seen_marker_never_overwrites_an_anchor(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    apply(s, src(), [PAPER])
    apply(s, src(), [Item(item_key="2610.1", role="seen", url=PAPER.url)], fetched_at="2026-10-23T00:00:00Z")
    it = s.item("openai-news", "2610.1")
    assert it["role"] == "anchor" and it["last_seen_at"] == "2026-10-23T00:00:00Z"
    assert s.story("arxiv:2610.1")["title"] == "A paper"


def test_mentions_are_stored_unattached(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    m = Item(item_key="post-1", role="mention", url="https://simonwillison.net/2026/Oct/22/x/?utm_source=rss",
             title="A post", summary="Text", authors=("Simon",), published_at="2026-10-22T18:00:00Z")
    c = apply(s, src(id="import-ai", roles=("reference",), tier=3), [m])
    row = dict(s.one("SELECT * FROM mentions"))
    assert c["mentions_new"] == 1 and row["story_id"] is None and row["kind"] == "reference"
    assert row["canonical_url"] == "https://simonwillison.net/2026/Oct/22/x"
    # A source with both roles posts as discussion (and still records coverage).
    apply(s, src(id="simon-willison", roles=("reference", "discussion"), tier=3), [replace(m, item_key="post-2")])
    assert s.mention("simon-willison|post-2")["kind"] == "post"


def test_same_items_into_fresh_stores_give_identical_rows(tmp_path):
    items = [PAPER, replace(PAPER, item_key="2610.2", story_id="arxiv:2610.2", title="Another")]
    dumps = []
    for name in ("a", "b"):
        s = Store(tmp_path / f"{name}.sqlite")
        apply(s, src(), items)
        dumps.append([s.dump(t, o) for t, o in (("stories", "story_id"), ("items", "source_id, item_key"),
                                                ("story_urls", "url"), ("mentions", "mention_id"))])
    assert dumps[0] == dumps[1]


def test_bootstrap_guard_only_applies_on_a_sources_first_run(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    old_post = replace(PAPER, item_key="p1", story_id=None, url="https://lab.test/blog/p1",
                       published_at="2026-07-01T00:00:00Z")
    c = apply(s, src(), [old_post], windowed="bootstrap")
    assert (c["stories_new"], c["too_old"]) == (0, 1)
    # Later runs: a link new to the seen-set is new, whatever date the page appears to carry.
    misdated = replace(old_post, item_key="p2", url="https://lab.test/blog/p2")
    c = apply(s, src(), [misdated], fetched_at="2026-10-23T20:00:00Z", windowed="bootstrap")
    assert (c["stories_new"], c.get("too_old", 0)) == (1, 0)
