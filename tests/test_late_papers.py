"""Late papers (CRITERIA.md "Late papers"): an older paper comes back as a candidate when its use,
citations, place on HF's trending list or discussion grew after its pool; the lookups that keep
older papers' signals current (HF paper API, Semantic Scholar); and the comment trees of late
threads."""
import json
from dataclasses import replace
from datetime import datetime, timezone

from conftest import record
from test_score import Stub as Runner
from test_score import hn_thread, lines_of
from test_triage import AT, EARLIER, cfg_for, paper, usage
from test_triage import Stub as Items

from debrief.adapters import REGISTRY, FetchContext, Item
from debrief.adapters.hf_paper_pages import stale_papers
from debrief.collect import state_path
from debrief.http import Fetched
from debrief.store import Store
from debrief.stories import build
from debrief.triage import run

OLD = "2026-10-10T08:00:00Z"        # 13 days before AT: past the pool, inside lookback.days
MID = "2026-10-15T08:00:00Z"        # a week's refresh, before the previous run
PREV = "2026-10-22T12:00:00Z"       # the previous triage run's time
NEXT_AT = "2026-10-24T12:00:00Z"
NONE = {"kind": "none", "dates": {}, "errors": []}


def put(store, cfg, source_id, items, at):
    """Items as a fetch at `at` stores them (first_seen is that day)."""
    with store.transaction():
        build.apply(store, cfg.settings, cfg.source(source_id), Items(items), [record("", fetched_at=at)],
                    [f"raw/{source_id}#0"], at[:10])


def signal(aid, **values):
    return Item(item_key=aid, role="signal", story_id=f"arxiv:{aid}", signals=values)


def thread_on(key, aid, points, comments=10, posted=EARLIER):
    return hn_thread(key, f"Discussing paper {aid}", points, f"https://arxiv.org/abs/{aid}", comments, posted)


def late(groups):
    return {g.id: g for g in groups["papers"] if "lookback" in g.info}


def store_for(tmp_path, **override):
    cfg = cfg_for(tmp_path, **override)
    return cfg, Store(state_path(cfg))


# ------------------------------------------------------------------------------------- triggers

def test_a_paper_that_finds_its_use_later_comes_back_once_ranked_after_the_fresh_papers(tmp_path):
    cfg, s = store_for(tmp_path)
    put(s, cfg, "hf-daily-papers", [paper("2610.00001", 500)], EARLIER)            # fresh, in the pool
    put(s, cfg, "hf-paper-detail", [usage("2610.00001", stars=150)], EARLIER)
    put(s, cfg, "hf-daily-papers", [paper("2610.00009", 20)], OLD)
    put(s, cfg, "hf-paper-detail", [usage("2610.00009", stars=10)], OLD)             # the pool's reading: no use
    put(s, cfg, "hf-paper-detail", [usage("2610.00009", stars=180)], EARLIER)        # the weekly refresh: it found use
    _, groups = run.select(s, cfg, AT, NONE, prev_at=PREV)
    g = late(groups)["arxiv:2610.00009"]
    assert g.alive and g.info["lookback"] == ["use"] and g.info["days_old"] == 13
    assert g.rank == 2 and g.info["signals"]["stars"] == 180                         # after the fresh paper (rank 1)
    fresh = next(x for x in groups["papers"] if x.id == "arxiv:2610.00001")
    assert fresh.alive and fresh.rank == 1 and "lookback" not in fresh.info
    _, groups = run.select(s, cfg, NEXT_AT, NONE, prev_at=AT)                         # nothing new read since
    assert not late(groups)


def test_citations_fire_on_a_crossing_and_only_with_a_baseline(tmp_path):
    cfg, s = store_for(tmp_path)
    put(s, cfg, "hf-daily-papers", [paper("2610.00011", 5), paper("2610.00013", 5)], OLD)
    put(s, cfg, "arxiv-rss", [paper("2610.00012", 0)], OLD)                           # arXiv only: citations still reach it
    put(s, cfg, "semantic-scholar", [signal("2610.00012", citations=4), signal("2610.00013", citations=12)], MID)
    put(s, cfg, "semantic-scholar", [signal("2610.00011", citations=25),              # first reading: no baseline
                                     signal("2610.00012", citations=12),              # 4 -> 12 crosses 10
                                     signal("2610.00013", citations=30)], EARLIER)    # already over 10
    _, groups = run.select(s, cfg, AT, NONE, prev_at=PREV)
    got = late(groups)
    assert set(got) == {"arxiv:2610.00012"} and got["arxiv:2610.00012"].info["lookback"] == ["citations"]
    assert got["arxiv:2610.00012"].info["signals"]["citations"] == 12


def test_trending_fires_when_an_older_paper_newly_makes_the_list(tmp_path):
    cfg, s = store_for(tmp_path)
    put(s, cfg, "hf-daily-papers", [paper("2610.00021", 5), paper("2610.00022", 5), paper("2610.00023", 5)], OLD)
    put(s, cfg, "hf-trending-papers", [signal("2610.00021", hf_trending_rank=0),
                                       signal("2610.00023", hf_trending_rank=5)], MID)
    put(s, cfg, "hf-trending-papers", [signal("2610.00021", hf_trending_rank=7),      # 0 -> on the list
                                       signal("2610.00022", hf_trending_rank=3),      # never seen: counts as off it
                                       signal("2610.00023", hf_trending_rank=2)], EARLIER)   # was on it already
    _, groups = run.select(s, cfg, AT, NONE, prev_at=PREV)
    assert {k: g.info["lookback"] for k, g in late(groups).items()} == {
        "arxiv:2610.00021": ["trending"], "arxiv:2610.00022": ["trending"]}


def test_a_new_thread_brings_back_an_older_paper_of_any_age_but_not_a_fresh_one(tmp_path):
    cfg, s = store_for(tmp_path)
    put(s, cfg, "hf-daily-papers", [paper("2610.00031", 26), paper("2610.00033", 5)], OLD)
    put(s, cfg, "hf-daily-papers", [paper("2608.00032", 40)], "2026-08-20T08:00:00Z")   # 64 days old
    put(s, cfg, "hf-daily-papers", [paper("2610.00034", 90)], EARLIER)                  # fresh, no use evidence
    put(s, cfg, "hn", [thread_on("31", "2610.00031", 113), thread_on("32", "2608.00032", 40),
                       thread_on("33", "2610.00033", 3),                                  # under the HN floor
                       thread_on("34", "2610.00034", 60),
                       thread_on("35", "2610.00033", 80, posted="2026-10-19T08:00:00Z")], EARLIER)   # too old a thread
    _, groups = run.select(s, cfg, AT, NONE, prev_at=PREV)
    got = late(groups)
    assert set(got) == {"arxiv:2610.00031", "arxiv:2608.00032"}
    assert got["arxiv:2610.00031"].info["lookback"] == ["discussion"]
    assert got["arxiv:2610.00031"].info["signals"]["hn_points"] == 113
    fresh = next(g for g in groups["papers"] if g.id == "arxiv:2610.00034")
    assert fresh.stage == "use_bar" and "lookback" not in fresh.info              # the fresh-paper rule stands


def test_late_papers_have_their_own_cap(tmp_path):
    cfg, s = store_for(tmp_path, papers={"lookback": {"days": 56, "cap": 1, "min_citations": 10}})
    put(s, cfg, "hf-daily-papers", [paper("2610.00041", 5), paper("2610.00042", 5)], OLD)
    put(s, cfg, "hn", [thread_on("41", "2610.00041", 50), thread_on("42", "2610.00042", 200)], EARLIER)
    _, groups = run.select(s, cfg, AT, NONE, prev_at=PREV)
    got = late(groups)
    assert got["arxiv:2610.00042"].alive and got["arxiv:2610.00042"].rank == 1
    assert (got["arxiv:2610.00041"].stage, got["arxiv:2610.00041"].reason) == ("cap", "late paper #2, over the cap of 1")


def test_a_reported_late_paper_needs_new_discussion_to_come_back(tmp_path):
    cfg, s = store_for(tmp_path)
    put(s, cfg, "hf-daily-papers", [paper("2610.00051", 5), paper("2610.00052", 5)], OLD)
    put(s, cfg, "hf-paper-detail", [usage("2610.00051", stars=10)], OLD)
    put(s, cfg, "hf-paper-detail", [usage("2610.00051", models=2)], EARLIER)
    put(s, cfg, "hn", [thread_on("52", "2610.00052", 70)], EARLIER)
    doc = {"kind": "reported", "dates": {"arxiv:2610.00051": ["2026-10-12"], "arxiv:2610.00052": ["2026-10-12"]},
           "errors": []}
    _, groups = run.select(s, cfg, AT, doc, prev_at=PREV)
    got = late(groups)
    assert got["arxiv:2610.00051"].stage == "ledger"
    assert got["arxiv:2610.00052"].alive and (got["arxiv:2610.00052"].status,
                                              got["arxiv:2610.00052"].prev_reported) == ("update", "2026-10-12")


# ------------------------------------------------------------------------- the run and its files

def test_late_papers_reach_the_model_and_the_index_flagged_and_the_next_run_counts_from_this_one(tmp_path):
    cfg, s = store_for(tmp_path)
    put(s, cfg, "hf-daily-papers", [paper("2610.00031", 26, title="Small models are the future of agents")], OLD)
    put(s, cfg, "hn", [thread_on("31", "2610.00031", 113)], EARLIER)
    s.close()
    stub = Runner({"arxiv:2610.00031": (None, True, 4)})
    _, out, _ = run.run(cfg, run_date="2026-10-23", at=AT, runner=stub)
    sent = next(c for c in stub.seen[0] if c["id"] == "arxiv:2610.00031")
    assert sent["section"] == "papers" and sent["signals"]["late"] is True and sent["signals"]["days_old"] == 13
    line = next(x for x in lines_of(out) if x["id"] == "arxiv:2610.00031")
    assert line["status"] == "new" and line["score"] == 4
    assert line["signals"]["late"] is True and line["signals"]["hn_points"] == 113
    s = Store(state_path(cfg))
    assert run.prev_triage_at(s, "2026-10-24") == AT and run.prev_triage_at(s, "2026-10-23") is None


# ---------------------------------------------------------------------------- collect: lookups

class Net:
    def __init__(self, body=b"{}"):
        self.body, self.urls, self.posts = body, [], []

    def get(self, url, **kw):
        self.urls.append(url)
        return Fetched(url=url, final_url=url, status=200, headers={}, content=self.body, error="")

    def post(self, url, json_body=None, headers=None, **kw):
        self.posts.append((url, json_body, headers))
        return Fetched(url=url, final_url=url, status=200, headers={}, content=b"[]", error="")


def ctx_for(cfg, s, net, env=None, cadence="daily"):
    return FetchContext(net, s, env or {}, "run", datetime(2026, 10, 23, 12, tzinfo=timezone.utc),
                        cfg.settings["collect"], settings=cfg.settings, run_date="2026-10-23", cadence=cadence,
                        enabled={x.id for x in cfg.sources if x.enabled})


def stale_setup(tmp_path):
    cfg, s = store_for(tmp_path)
    put(s, cfg, "hf-daily-papers", [paper("2610.00061", 5)], "2026-10-21T08:00:00Z")      # in the pool
    put(s, cfg, "hf-daily-papers", [paper("2610.00062", 5)], "2026-10-20T08:00:00Z")
    put(s, cfg, "hf-paper-detail", [usage("2610.00062")], "2026-10-20T08:00:00Z")         # read 3 days ago
    put(s, cfg, "hf-daily-papers", [paper("2610.00063", 5)], OLD)
    put(s, cfg, "hf-paper-detail", [usage("2610.00063")], "2026-10-12T08:00:00Z")         # read 11 days ago
    put(s, cfg, "hf-daily-papers", [paper("2610.00064", 5)], "2026-10-01T08:00:00Z")      # never read
    put(s, cfg, "hf-daily-papers", [paper("2608.00065", 5)], "2026-08-01T08:00:00Z")      # past lookback.days
    put(s, cfg, "arxiv-rss", [paper("2610.00066", 0)], OLD)                               # HF never listed it
    return cfg, s


def test_older_papers_are_read_again_weekly_longest_unread_first(tmp_path):
    cfg, s = stale_setup(tmp_path)
    args = (s, "2026-10-23", 3, 56, "hf-paper-detail", 7)
    assert stale_papers(*args, 10, listed_by="hf-daily-papers") == ["arxiv:2610.00064", "arxiv:2610.00063"]
    assert stale_papers(*args, 1, listed_by="hf-daily-papers") == ["arxiv:2610.00064"]
    assert stale_papers(*args, 10) == ["arxiv:2610.00064", "arxiv:2610.00066", "arxiv:2610.00063"]
    net = Net()
    REGISTRY["hf_paper_api"].fetch(ctx_for(cfg, s, net), cfg.source("hf-paper-detail"))
    assert [u.rsplit("/", 1)[1] for u in net.urls] == ["2610.00061", "2610.00064", "2610.00063"]   # the pool, then the stale


def test_semantic_scholar_reads_citations_in_batches_and_skips_without_a_key(tmp_path):
    cfg, s = stale_setup(tmp_path)
    src = cfg.source("semantic-scholar")
    assert src.enabled
    net = Net()
    ctx = ctx_for(cfg, s, net)
    REGISTRY["semantic_scholar"].fetch(ctx, src)
    assert not net.posts and not ctx.records and ctx.warnings == ["no SEMANTIC_SCHOLAR_API_KEY: citation counts skipped"]
    ctx = ctx_for(cfg, s, net, env={"SEMANTIC_SCHOLAR_API_KEY": "k"})
    REGISTRY["semantic_scholar"].fetch(ctx, replace(src, options={"batch": 2}))
    # none read yet, so every paper past the pool (00062 is 3 days old) and inside lookback.days is due
    assert [p[1] for p in net.posts] == [{"ids": ["ARXIV:2610.00062", "ARXIV:2610.00063"]},
                                         {"ids": ["ARXIV:2610.00064", "ARXIV:2610.00066"]}]
    assert net.posts[0][2] == {"x-api-key": "k"}
    assert [r["meta"]["ids"] for r in ctx.records] == [["arxiv:2610.00062", "arxiv:2610.00063"],
                                                       ["arxiv:2610.00064", "arxiv:2610.00066"]]
    body = json.dumps([{"paperId": "abc", "citationCount": 12}, None])
    rec = record(body, meta={"ids": ["arxiv:2610.00064", "arxiv:2610.00066"]})
    items = REGISTRY["semantic_scholar"].parse(rec, src, cfg.settings["collect"])
    assert [(i.story_id, i.signals) for i in items] == [("arxiv:2610.00064", {"citations": 12}),
                                                        ("arxiv:2610.00066", {"citations": 0})]   # unknown: a baseline of 0
    assert REGISTRY["semantic_scholar"].parse(record(json.dumps([None]), meta={"ids": ["a", "b"]}), src, {}) == []


def test_a_late_thread_on_an_older_paper_gets_its_comment_tree(tmp_path):
    cfg, s = store_for(tmp_path)
    put(s, cfg, "hf-daily-papers", [paper("2610.00031", 26)], OLD)
    put(s, cfg, "hn", [thread_on("31", "2610.00031", 113, comments=45),
                       thread_on("36", "2610.00031", 90, comments=30, posted="2026-10-12T08:00:00Z")], EARLIER)
    net = Net(b'{"kids": []}')
    REGISTRY["hn_comments"].fetch(ctx_for(cfg, s, net, cadence="6h"), cfg.source("hn-items"))
    assert sorted({u.rsplit("/", 1)[1].split(".")[0] for u in net.urls}) == ["31"]       # not the 11-day-old thread
