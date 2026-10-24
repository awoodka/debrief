"""M5: LessWrong and the Alignment Forum (posts, comments, and their place in triage), and Hugging
Face's trending papers as a signal. The adapters parse responses captured on 2026-10-24."""
import json
from dataclasses import replace
from datetime import datetime, timezone

from conftest import fixture, record
from test_score import Stub, day, lines_of
from test_triage import AT, EARLIER, apply

from debrief import config
from debrief.adapters import REGISTRY, FetchContext, Item
from debrief.collect import state_path
from debrief.http import Fetched
from debrief.store import Store
from debrief.triage import run

CFG = config.load(env={"DEBRIEF_DATA_DIR": "/tmp/debrief-test-data"})
OPTS = CFG.settings["collect"]
POSTS = fixture("lesswrong/posts.json")
COMMENTS = fixture("lesswrong/comments_pY76hHbdD4jduX3YH.json")


def parse(source_id, rec, opts=OPTS, **override):
    src = replace(CFG.source(source_id), **override) if override else CFG.source(source_id)
    return REGISTRY[src.adapter].parse(rec, src, opts)


def posts(source_id="lesswrong", skip_af=True):
    return {i.item_key: i for i in parse(source_id, record(POSTS, meta={"kind": "posts", "skip_af": skip_af}))}


# ------------------------------------------------------------------------------------ the posts

def test_lesswrong_leaves_af_posts_to_the_af_source_and_drops_posts_tagged_as_something_else():
    got = posts()
    assert set(got) == {"nWiPLEgaXdAQNwXBd",      # no tags at all, but about reward hacking
                        "uXiqyoKwpfjWfQXk5",      # tagged Redwood Research only, not AI
                        "GeKveaNdruATcj2Fj", "WRvxRHMCmc3Qt4axz"}
    # Fermi Poker carries World Modeling, another core tag; Monitorability is an Alignment Forum post
    assert "zAczYuHQS6jdG5DTg" in posts(skip_af=False)
    assert "JFYvJqMaLw9nhfrur" in posts("alignment-forum", skip_af=False)    # no tag rule there


def test_a_post_is_a_thread_whose_first_link_is_what_it_is_about():
    got = posts()
    link = got["GeKveaNdruATcj2Fj"]                                    # a link post
    assert (link.role, link.mention_kind) == ("mention", "thread")
    assert link.links[0] == "https://secondlookresearch.com/gpt2-global-workspace"
    assert "https://arxiv.org/abs/2308.09124" in link.links
    assert link.engagement == {"points": 84, "comments": 0}
    assert link.authors == ("Finn Cairns", "yix", "Zephaniah Roe")
    assert link.url == "https://www.lesswrong.com/posts/GeKveaNdruATcj2Fj/even-gpt-2-passes-most-functional-global-workspace-tests"
    essay = got["nWiPLEgaXdAQNwXBd"]                                   # not a link post: itself first
    assert essay.links[0] == essay.url
    assert "https://arxiv.org/abs/2511.18397" in essay.links
    assert not any("lesswrong.com" in u for u in essay.links[1:])      # other posts on the forum aren't stories
    assert "](" not in essay.summary and "\n\n" in essay.summary          # links reduced to text; paragraphs kept
    assert got["WRvxRHMCmc3Qt4axz"].links == (got["WRvxRHMCmc3Qt4axz"].url,)


def test_only_a_posts_first_links_can_attach_it_and_none_of_a_roundups():
    data = json.loads(POSTS)
    essay = next(p for p in data["data"]["posts"]["results"] if p["_id"] == "nWiPLEgaXdAQNwXBd")
    essay["contents"]["markdown"] = " ".join(f"[{i}](https://site{i}.example/x)" for i in range(10))
    roundup = {**essay, "_id": "r1", "title": "AI #191: The Hackening Begins"}
    data["data"]["posts"]["results"] = [essay, roundup]
    got = {i.item_key: i for i in parse("lesswrong", record(json.dumps(data), meta={"kind": "posts"}))}
    assert got["nWiPLEgaXdAQNwXBd"].links[1:] == tuple(f"https://site{i}.example/x" for i in range(OPTS["lesswrong"]["lead_links"]))
    assert got["r1"].links == (essay["pageUrl"],)


def test_fetch_time_selections_see_what_the_run_stored_before_them(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    ctx = FetchContext(None, s, {}, "run-1", datetime(2026, 10, 24, 12, tzinfo=timezone.utc), OPTS)
    assert ctx.stored_until == "2026-10-24T12:00:00Z"                     # nothing stored yet: the run's start
    with s.transaction():
        s.record_fetch("run-1", "lesswrong", 0, "https://x.test/", "2026-10-24T12:03:10Z", 200, 10, None, "raw#0")
        s.record_fetch("run-0", "hn", 0, "https://x.test/", "2026-10-24T13:00:00Z", 200, 10, None, "raw#0")
    assert ctx.stored_until == "2026-10-24T12:03:10Z"                     # this run's last, not another run's


def test_graphql_errors_and_failures_parse_to_nothing():
    bad = json.dumps({"errors": [{"message": "Syntax Error"}]})
    assert parse("lesswrong", record(bad, meta={"kind": "posts"})) == []
    assert parse("lesswrong", record("", status=500, meta={"kind": "posts"})) == []


class Net:
    """Answers GraphQL POSTs from a list of responses, in order."""

    def __init__(self, bodies):
        self.bodies, self.sent = list(bodies), []

    def post(self, url, json_body=None, headers=None):
        self.sent.append(json_body)
        return Fetched(url=url, final_url=url, status=200, headers={}, content=self.bodies.pop(0).encode(), error="")


def page_of(n):
    return json.dumps({"data": {"posts": {"results": [{"_id": f"p{i}"} for i in range(n)]}}})


def test_lesswrong_reads_pages_until_a_short_one_and_records_whether_af_is_its_own(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    src = replace(CFG.source("lesswrong"), options={**CFG.source("lesswrong").options, "page_size": 2})
    for enabled, skip in (({"alignment-forum"}, True), ((), False)):
        net = Net([page_of(2), page_of(1)])
        ctx = FetchContext(net, s, {}, "run", datetime(2026, 10, 24, 12, tzinfo=timezone.utc), OPTS,
                           settings=CFG.settings, enabled=enabled)
        REGISTRY["lesswrong"].fetch(ctx, src)
        assert [b["variables"]["terms"]["offset"] for b in net.sent] == [0, 2]
        assert net.sent[0]["variables"]["terms"]["after"] == "2026-10-21T12:00:00Z"        # lookback_hours 72
        assert {r["meta"]["skip_af"] for r in ctx.records} == {skip} and not ctx.warnings


# --------------------------------------------------------------------------------- the comments

def test_comments_keep_the_best_top_level_ones_by_karma():
    rec = record(COMMENTS, meta={"kind": "comments", "thread": "lesswrong|pY76hHbdD4jduX3YH"})
    items = parse("lesswrong-comments", rec)
    o = OPTS["lesswrong_comments"]
    assert [i.rank for i in items] == list(range(1, o["top_ranked"] + 1))
    assert items[0].item_key == "XWiCjeQGDk5tuXrtG" and items[0].engagement == {"points": 52}
    assert {i.parent_id for i in items} == {"lesswrong|pY76hHbdD4jduX3YH"} and {i.mention_kind for i in items} == {"comment"}
    assert "fQfJEddcaJkFxGtFd" not in {i.item_key for i in items}           # 96 characters: too short to keep
    narrow = {**OPTS, "lesswrong_comments": {**o, "top_ranked": 2, "top_replied": 1}}
    assert [i.item_key for i in parse("lesswrong-comments", rec, narrow)] == [
        "XWiCjeQGDk5tuXrtG", "KKpXrewE7bmf7h3b4", "o9RhLLGxR9EFp3nyZ"]         # then the one with 19 replies


def lw_post(key, title, points, comments=6, link=None, body="A post about AI. It argues something new."):
    url = f"https://www.lesswrong.com/posts/{key}/slug"
    return Item(item_key=key, role="mention", mention_kind="thread", url=url, title=title, summary=body,
                links=(link or url,), published_at=EARLIER, authors=("someone",),
                engagement={"points": points, "comments": comments})


def test_comments_are_read_for_attached_posts_and_on_daily_runs_the_candidates(tmp_path):
    cfg, _ = day(tmp_path)
    s = Store(state_path(cfg))
    apply(s, cfg, "lesswrong", [lw_post("a1", "On a paper", 40, link="https://arxiv.org/abs/2610.00001"),
                                lw_post("u1", "An essay", 60), lw_post("u2", "Few comments", 90, comments=1)])
    asked = {}
    for cadence in ("daily", "6h"):
        net = Net([json.dumps({"data": {"comments": {"results": []}}})] * 5)
        ctx = FetchContext(net, s, {}, "run", datetime(2026, 10, 23, 12, tzinfo=timezone.utc), cfg.settings["collect"],
                           settings=cfg.settings, cadence=cadence, enabled={"lesswrong", "alignment-forum"},
                           run_date="2026-10-23")
        REGISTRY["lesswrong_comments"].fetch(ctx, cfg.source("lesswrong-comments"))
        asked[cadence] = [b["variables"]["terms"]["postId"] for b in net.sent]
    assert asked == {"daily": ["a1", "u1"], "6h": ["a1"]}              # u2 has 1 comment, under min_comments
    s.close()


# --------------------------------------------------------------------------------------- triage

def test_posts_join_the_call_with_a_quota_per_source_and_carry_their_body(tmp_path):
    cfg, _ = day(tmp_path)
    t = cfg.settings["triage"]
    cfg = replace(cfg, settings={**cfg.settings, "triage": {**t, "discussion": {
        **t["discussion"], "sources": {**t["discussion"]["sources"], "lesswrong": {"max": 1, "prefetch": 2}}}}})
    s = Store(state_path(cfg))
    body = "Gradient routing localizes unintended behaviors in RL.\n\nA second paragraph with the details."
    apply(s, cfg, "lesswrong", [lw_post("best", "Gradient Routing Localizes Unintended Behaviors", 187, body=body),
                                lw_post("next", "A runner-up essay", 60),
                                lw_post("low", "Under the floor", 12),
                                lw_post("cites", "On a paper", 40, link="https://arxiv.org/abs/2610.00001")])
    apply(s, cfg, "alignment-forum", [lw_post("af1", "Deconfusing corrigibility", 15)])
    apply(s, cfg, "lesswrong-comments", [Item(item_key="c1", role="mention", mention_kind="comment",
                                              parent_id="lesswrong|cites", summary="A substantive reply. " * 10,
                                              authors=("critic",), rank=1, replies=2, engagement={"points": 33})])
    s.close()
    stub = Stub({"Gradient Routing": (None, True, 4), "Deconfusing": (None, True, 3), "A paper about agents": (None, True, 3)})
    _, out, _ = run.run(cfg, run_date="2026-10-23", at=AT, runner=stub)
    sent = {c["id"]: c for c in stub.seen[0] if c["section"] == "discussion"}
    assert "lesswrong:best" in sent and "lesswrong:next" not in sent           # LessWrong's quota is 1 here
    assert "alignment-forum:af1" in sent and not any("low" in k or "cites" in k for k in sent)
    best = sent["lesswrong:best"]
    assert (best["source"], best["link"], best["comments"]) == ("lesswrong", "https://lesswrong.com/posts/best/slug", 6)
    assert best["text"].startswith("Gradient routing localizes") and best["signals"] == {"points": 187}
    assert [k for k in sent if k.startswith("hn:")] == ["hn:902", "hn:901", "hn:903"]  # HN's quota and order untouched
    lines = {x["id"]: x for x in lines_of(out)}
    d = json.loads((out / lines["lesswrong:best"]["detail"]).read_text())
    assert d["full_text"][0]["kind"] == "post" and d["full_text"][0]["text"] == body
    assert d["full_text"][0]["url"] == "https://www.lesswrong.com/posts/best/slug"
    assert lines["lesswrong:best"]["gist"] == "Gradient routing localizes unintended behaviors in RL."
    pd = json.loads((out / lines["arxiv:2610.00001"]["detail"]).read_text())    # the post that cites the paper
    assert "lesswrong" in pd["signals"]["where"]
    lw = next(x for x in pd["discussion"]["threads"] if x["src"] == "lesswrong")
    assert lw["points"] == 40 and lw["top_comments"][0]["points"] == 33


def test_a_disabled_discussion_source_has_no_candidates(tmp_path):
    cfg, _ = day(tmp_path)
    s = Store(state_path(cfg))
    apply(s, cfg, "lesswrong", [lw_post("best", "Gradient Routing", 187)])
    s.close()
    cfg = replace(cfg, sources=tuple(replace(x, enabled=False) if x.id == "lesswrong" else x for x in cfg.sources))
    stub = Stub({})
    run.run(cfg, run_date="2026-10-23", at=AT, runner=stub)
    assert not any(c["id"].startswith("lesswrong:") for c in stub.seen[0])


# ------------------------------------------------------------------------------ trending papers

def test_trending_papers_give_a_rank_and_zero_for_pool_papers_off_the_list():
    rec = record(fixture("hf/trending_papers.json"), meta={"pool": ["arxiv:2610.23374", "arxiv:2610.99999"]})
    got = {i.item_key: i for i in parse("hf-trending-papers", rec)}
    assert {i.role for i in got.values()} == {"signal"}
    assert got["2610.14224"].signals == {"hf_trending_rank": 1}
    assert got["2610.23374"].signals == {"hf_trending_rank": 5} and got["2610.23374"].story_id == "arxiv:2610.23374"
    assert got["2610.99999"].signals == {"hf_trending_rank": 0}
    assert len(got) == 9


def test_trending_rank_shows_in_the_paper_signals_but_not_in_the_fusion(tmp_path):
    cfg, _ = day(tmp_path)
    s = Store(state_path(cfg))
    apply(s, cfg, "hf-trending-papers", [Item(item_key="2610.00001", role="signal", story_id="arxiv:2610.00001",
                                              signals={"hf_trending_rank": 3})])
    s.close()
    stub = Stub({"A paper about agents": (None, True, 3)})
    _, out, _ = run.run(cfg, run_date="2026-10-23", at=AT, runner=stub)
    sent = next(c for c in stub.seen[0] if c["id"] == "arxiv:2610.00001")
    assert sent["signals"]["trending_rank"] == 3
    line = next(x for x in lines_of(out) if x["id"] == "arxiv:2610.00001")
    assert line["signals"]["trending_rank"] == 3
    assert "trending_rank" not in cfg.settings["triage"]["papers"]["signals"]
