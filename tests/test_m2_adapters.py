"""M2 adapters' parse() against real responses captured on 2026-10-22."""
import json
from dataclasses import replace

from conftest import fixture, record

from debrief import config
from debrief.adapters import REGISTRY
from debrief.adapters.hf_hub import family, model_story_id

CFG = config.load(env={"DEBRIEF_DATA_DIR": "/tmp/debrief-test-data"})
OPTS = CFG.settings["collect"]


def parse(source_id, rec, **override):
    src = replace(CFG.source(source_id), **override) if override else CFG.source(source_id)
    return REGISTRY[src.adapter].parse(rec, src, OPTS)


def test_hn_slice_keeps_every_story_with_engagement():
    items = parse("hn", record(fixture("hn/search_slice.json"), meta={"kind": "slice"}))
    assert len(items) == 5 and {i.role for i in items} == {"mention"} and {i.mention_kind for i in items} == {"thread"}
    big = next(i for i in items if i.item_key == "50195959")
    assert big.engagement == {"points": 902, "comments": 332}
    assert big.url == "https://news.ycombinator.com/item?id=50195959"
    assert big.links == ("https://quad9.net/news/blog/au-revoir-france/",)
    ask = next(i for i in items if i.item_key == "50201751")
    assert ask.links == ()


def test_hn_comments_follow_the_ranked_order_and_keep_debated_ones():
    kids = json.loads(fixture("hn/firebase_item.json"))["kids"]
    rec = record(fixture("hn/item_tree.json"), meta={"kind": "tree", "thread": "hn|50195959", "ranked_kids": kids})
    items = parse("hn-items", rec)
    o = OPTS["hn_comments"]
    ranked = [i for i in items if i.rank <= o["top_ranked"]]
    assert [i.rank for i in ranked] == list(range(1, o["top_ranked"] + 1))
    tree_ids = {c["id"] for c in json.loads(fixture("hn/item_tree.json"))["children"]}
    assert items[0].item_key == str(next(k for k in kids if k in tree_ids))   # HN's #1, not Algolia's first
    assert {i.parent_id for i in items} == {"hn|50195959"} and {i.mention_kind for i in items} == {"comment"}
    assert len(items) <= o["top_ranked"] + o["top_replied"]
    extra = items[o["top_ranked"]:]
    assert all(i.replies > 0 for i in extra)


def test_firebase_record_alone_parses_to_nothing():
    assert parse("hn-items", record(fixture("hn/firebase_item.json"), meta={"kind": "kids"})) == []


def test_hf_paper_api_gives_signals_only():
    (it,) = parse("hf-paper-detail", record(fixture("hf/paper_detail.json"), each="arxiv:2501.12948"))
    assert (it.role, it.story_id) == ("signal", "arxiv:2501.12948")
    assert it.signals == {"hf_upvotes": 463, "github_stars": 91933, "hf_models": 533, "hf_datasets": 17, "hf_spaces": 4323}


def test_hf_paper_page_comments_attach_to_their_paper():
    items = parse("hf-paper-comments", record(fixture("hf/paper_page.html"), meta={"paper": "arxiv:2501.12948"}))
    assert [i.authors for i in items] == [("akhaliq",), ("aipapersacademy",)]      # librarian-bot skipped
    assert {i.story_hint for i in items} == {"arxiv:2501.12948"}
    assert items[0].summary == "https://github.com/deepseek-ai/DeepSeek-R1"
    assert items[0].engagement == {"likes": 17} and [i.rank for i in items] == [1, 3]


def test_format_suffixes_fold_into_the_original():
    assert family("NVIDIA-Nemotron-Labs-Dory1.0-4B-NVFP4") == ("NVIDIA-Nemotron-Labs-Dory1.0-4B", ["NVFP4"])
    assert family("Model-7B-Instruct-GGUF") == ("Model-7B-Instruct", ["GGUF"])
    assert family("Thing-MLX-4bit") == ("Thing", ["MLX-4bit"])
    assert model_story_id("nvidia/NVIDIA-Nemotron-Labs-Dory1.0-4B-BF16") == model_story_id("nvidia/NVIDIA-Nemotron-Labs-Dory1.0-4B-GGUF")


def test_org_watch_marks_derivatives_seen():
    items = parse("hf-org-watch", record(fixture("hfhub/org_nvidia.json"), each="nvidia"))
    roles = {i.item_key: (i.role, i.extra.get("reason")) for i in items}
    derived = [k for k, v in roles.items() if v[1] == "derivative"]
    assert derived and all(roles[k][0] == "seen" for k in derived)
    dory = next(i for i in items if i.item_key.endswith("Dory1.0-4B-NVFP4"))
    assert dory.story_id == "hf-model:nvidia/nvidia-nemotron-labs-dory1.0-4b" and dory.extra["format"] == ["NVFP4"]
    deep = parse("hf-org-watch", record(fixture("hfhub/org_deepseek-ai.json"), each="deepseek-ai"))
    assert deep[0].role == "anchor" and deep[0].signals["hf_likes"] > 0 and deep[0].kind == "release"


def test_trending_needs_a_new_org_model():
    meta = {"kind": "list", "orgs": {"deepseek-ai": True, "RecursiveSI": True, "OpenMOSS-Team": False}}
    items = parse("hf-trending-models", record(fixture("hfhub/trending.json"), meta=meta, fetched_at="2026-10-22T20:00:00Z"))
    why = {i.item_key: (i.role, i.extra.get("reason")) for i in items}
    assert why["deepseek-ai/DeepSeek-V4.1-Flash-1017"] == ("anchor", None)
    assert why["RecursiveSI/flint"] == ("anchor", None)
    assert why["black-forest-labs/flux-3-image-klein-comfy"] == ("seen", "derivative")
    assert why["OpenMOSS-Team/MOSS-Image-Base"] == ("seen", "not an organization account")
    assert why["Lightricks/LTX-2.5"] == ("seen", "older than max_age_days")


def test_openrouter_folds_variants_and_links_hf_weights():
    items = parse("openrouter-models", record(fixture("openrouter/models.json")))
    by = {i.item_key: i for i in items}
    assert "recursive/flint-v1.0" in by and not any(":" in k for k in by)
    assert by["recursive/flint-v1.0"].story_id == "hf-model:recursivesi/flint"
    assert by["deepseek/deepseek-v4.1-flash-1017"].story_id == model_story_id("deepseek-ai/DeepSeek-V4.1-Flash-1017")
    assert by["elorian/glimpse"].story_id.startswith("url:")
    assert not by["recursive/flint-v1.0"].title.endswith("(free)")


def test_github_org_new_repos():
    items = parse("deepseek-github", record(fixture("github/org_repos_deepseek.json")))
    assert items[0].story_id == "gh-repo:deepseek-ai/deepinfer" and items[0].signals["github_stars"] > 0


def test_leaderboard_takes_only_the_top_n():
    items = parse("lmarena", record(fixture("lmarena/rows.json")))
    assert [i.item_key for i in items] == [
        "top5:Gemini 4 Argon (High)", "top5:Claude Opus 5.5 (High)", "top5:Claude Fable 5.1 (Max)",
        "top5:Claude Opus 5 (Max)", "top5:Claude Opus 5 (High)"]
    assert items[0].kind == "leaderboard" and items[0].signals["lmarena_rank"] == 1


def test_reference_posts_keep_their_links():
    items = parse("simon-willison", record(fixture("rss/simonwillison.atom")))
    assert any(i.links for i in items)
    assert all(u.startswith("https://") for i in items for u in i.links)
