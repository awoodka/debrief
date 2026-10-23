"""Triage (M3): pools, per-section order and caps, merges, press events, the ledger, and the
output contract, on a hand-built store."""
import json
from dataclasses import replace

from conftest import record

from debrief import config
from debrief.adapters import Adapter, Item
from debrief.collect import state_path
from debrief.store import Store
from debrief.stories import build
from debrief.triage import candidates, ledger, output, run

AT = "2026-10-23T12:00:00Z"
EARLIER = "2026-10-23T08:00:00Z"


def cfg_for(tmp_path, **triage_override):
    cfg = config.load(env={"DEBRIEF_DATA_DIR": str(tmp_path / "data")})
    if triage_override:
        t = {**cfg.settings["triage"]}
        for key, value in triage_override.items():
            t[key] = {**t[key], **value} if isinstance(value, dict) else value
        cfg = replace(cfg, settings={**cfg.settings, "triage": t})
    return cfg


class Stub(Adapter):
    windowed = False

    def __init__(self, items):
        self.items = items

    def parse(self, rec, source, opts):
        return self.items


def apply(store, cfg, source_id, items, at=EARLIER):
    with store.transaction():
        build.apply(store, cfg.settings, cfg.source(source_id), Stub(items), [record("", fetched_at=at)],
                    [f"raw/{source_id}#0"], "2026-10-23")


def fresh(tmp_path, cfg):
    return Store(state_path(cfg))


def paper(aid, upvotes, title=None, org=None, at=EARLIER):
    return Item(item_key=aid, role="anchor", story_id=f"arxiv:{aid}", url=f"https://arxiv.org/abs/{aid}",
                title=title or f"Paper {aid} about something", summary="An abstract. It has sentences.",
                kind="paper", org=org, signals={"hf_upvotes": upvotes})


def usage(aid, stars=0, models=0):
    return Item(item_key=aid, role="signal", story_id=f"arxiv:{aid}",
                signals={"github_stars": stars, "hf_models": models, "hf_datasets": 0, "hf_spaces": 0})


def post(key, title, published="2026-10-23T06:00:00Z", kind="announcement", url=None):
    return Item(item_key=key, role="anchor", url=url or f"https://example.test/{key}", title=title,
                summary=f"{title}. More words follow here.", published_at=published, kind=kind)


def page(story_id, links, text="Main text of the article."):
    return Item(item_key=story_id, role="page", story_id=story_id, url=f"https://page.test/{story_id}",
                page={"status": 200, "text": text, "links": list(links)})


def sid_of(store, url):
    return store.story_for_url(url)


def select(store, cfg, ledger_doc=None):
    return run.select(store, cfg, AT, ledger_doc or {"kind": "none", "dates": {}, "errors": []})


def alive(groups, section):
    return [g.id for g in sorted((g for g in groups[section] if g.alive), key=lambda g: g.rank)]


# --------------------------------------------------------------------------------------- papers

def test_fusion_ties_share_a_rank_and_missing_signals_add_nothing():
    vals = {"a": {"x": 10, "y": 0}, "b": {"x": 10, "y": 5}, "c": {"x": 3, "y": 0}}
    s = candidates.fusion(vals, ["x", "y"], k=20)
    assert s["a"] == 1 / 21                     # tied for first on x; y missing
    assert s["b"] == 1 / 21 + 1 / 21            # tied for first on x, first on y
    assert s["c"] == 1 / 23                     # two papers above it on x: rank 3


def test_papers_need_evidence_of_use_then_rank_with_an_org_cap(tmp_path):
    cfg = cfg_for(tmp_path, papers={"cap": 3, "max_per_org": 1})
    s = fresh(tmp_path, cfg)
    apply(s, cfg, "hf-daily-papers", [paper("2610.00001", 500), paper("2610.00002", 300, org="Lab"),
                                      paper("2610.00003", 200, org="Lab"), paper("2610.00004", 100),
                                      paper("2610.00005", 50), paper("2610.00006", 900)])
    apply(s, cfg, "hf-paper-detail", [usage("2610.00001", stars=150), usage("2610.00002", models=3),
                                      usage("2610.00003", models=9), usage("2610.00004", stars=120),
                                      usage("2610.00005", stars=101)])     # 00006: upvotes alone
    _, groups = select(s, cfg)
    by = {g.id: g for g in groups["papers"]}
    assert by["arxiv:2610.00006"].stage == "use_bar"
    assert by["arxiv:2610.00002"].stage == "org_cap" or by["arxiv:2610.00003"].stage == "org_cap"
    order = alive(groups, "papers")
    assert len(order) == 3 and order[0] in ("arxiv:2610.00001", "arxiv:2610.00003")
    assert [g.stage for g in groups["papers"] if g.rank and g.rank > 3] == ["cap"]


# ------------------------------------------------------------------------------------- releases

def model(mid, created, likes, **kw):
    return Item(item_key=mid, role="anchor", story_id="hf-model:" + mid.lower(), url=f"https://huggingface.co/{mid}",
                title=mid, summary="text-generation · transformers", published_at=created, kind="release",
                org=mid.split("/")[0], signals={"hf_likes": likes}, extra={"model_id": mid}, **kw)


def test_releases_count_org_repos_by_creation_and_trending_by_sighting(tmp_path):
    cfg = cfg_for(tmp_path)
    s = fresh(tmp_path, cfg)
    apply(s, cfg, "hf-org-watch", [model("lab/New-1", "2026-10-22T20:00:00Z", 10),
                                   model("lab/Old-1", "2026-10-19T20:00:00Z", 999)])   # created 4 days ago
    apply(s, cfg, "hf-trending-models", [model("team/Hot-2", "2026-10-15T00:00:00Z", 500)])  # old, but new to us
    _, groups = select(s, cfg)
    assert alive(groups, "releases") == ["hf-model:lab/new-1", "hf-model:team/hot-2"]   # tier 1 before likes


# -------------------------------------------------------------------------------- announcements

def test_announcement_sources_take_turns_over_the_cap(tmp_path):
    cfg = cfg_for(tmp_path, announcements={"cap": 4})
    s = fresh(tmp_path, cfg)
    apply(s, cfg, "nvidia-blog", [post(f"n{i}", f"NVIDIA keynote news number {i}", f"2026-10-23T0{i}:00:00Z")
                                  for i in range(1, 6)])
    apply(s, cfg, "openai-news", [post("o1", "An OpenAI post about models", "2026-10-22T20:00:00Z")])
    apply(s, cfg, "aws-ml-blog", [post("a1", "An AWS post about inference", "2026-10-23T01:00:00Z")])
    _, groups = select(s, cfg)
    titles = [next(g for g in groups["announcements"] if g.id == x).story["title"] for x in alive(groups, "announcements")]
    # tier 1 in sources.yml order (OpenAI before NVIDIA), then tier 2 (AWS); NVIDIA's newest first
    assert titles == ["An OpenAI post about models", "NVIDIA keynote news number 5", "An AWS post about inference",
                      "NVIDIA keynote news number 4"]


def test_old_and_blocklisted_announcements_stay_out(tmp_path):
    cfg = cfg_for(tmp_path)
    s = fresh(tmp_path, cfg)
    apply(s, cfg, "nvidia-blog", [post("g", "GFN Thursday: five new games in the cloud"),
                                  post("old", "A post from last week", "2026-10-16T00:00:00Z")])
    _, groups = select(s, cfg)
    assert [(g.story["title"][:3], g.stage) for g in groups["announcements"]] == [("GFN", "rules")]


# --------------------------------------------------------------------------------------- merges

def test_an_announcement_of_one_model_becomes_the_release(tmp_path):
    cfg = cfg_for(tmp_path)
    s = fresh(tmp_path, cfg)
    apply(s, cfg, "hf-org-watch", [model("lab/Big-1", "2026-10-23T02:00:00Z", 50)])
    apply(s, cfg, "openai-news", [post("big", "Introducing Big-1, our open model")])
    ann = sid_of(s, "https://example.test/big")
    apply(s, cfg, "article-pages", [page(ann, ["https://huggingface.co/lab/Big-1-GGUF", "https://example.test/blog"])])
    _, groups = select(s, cfg)
    rel = next(g for g in groups["releases"] if g.alive)
    assert rel.id == "hf-model:lab/big-1" and rel.members == ["hf-model:lab/big-1", ann]
    a = next(g for g in groups["announcements"] if g.id == ann)
    assert (a.stage, a.info["merged_into"]) == ("merge", "hf-model:lab/big-1")


def test_an_announcement_of_one_paper_keeps_its_section_and_takes_the_paper(tmp_path):
    cfg = cfg_for(tmp_path)
    s = fresh(tmp_path, cfg)
    apply(s, cfg, "hf-daily-papers", [paper("2610.11111", 80)])
    apply(s, cfg, "hf-paper-detail", [usage("2610.11111", stars=500)])
    apply(s, cfg, "deepmind-blog", [post("p", "Our new paper on world models")])
    apply(s, cfg, "openai-news", [post("two", "A post linking two papers")])
    ann, two = sid_of(s, "https://example.test/p"), sid_of(s, "https://example.test/two")
    apply(s, cfg, "article-pages", [page(ann, ["https://arxiv.org/pdf/2610.11111v2"]),
                                    page(two, ["https://arxiv.org/abs/2610.11111", "https://arxiv.org/abs/2610.22222"])])
    _, groups = select(s, cfg)
    a = next(g for g in groups["announcements"] if g.id == ann)
    assert a.alive and a.members == [ann, "arxiv:2610.11111"]
    assert next(g for g in groups["papers"] if g.id == "arxiv:2610.11111").stage == "merge"
    assert next(g for g in groups["announcements"] if g.id == two).members == [two]      # two artifacts: no merge


def press_setup(tmp_path, cfg):
    s = fresh(tmp_path, cfg)
    apply(s, cfg, "techcrunch-ai", [post("tc", "OpenAI raises money", kind="press", url="https://techcrunch.com/raise"),
                                    post("tc2", "Unrelated TechCrunch story", kind="press", url="https://techcrunch.com/other")])
    apply(s, cfg, "the-verge-ai", [post("v", "OpenAI's new round, explained", "2026-10-23T07:00:00Z", kind="press",
                                        url="https://theverge.com/raise")])
    apply(s, cfg, "wired-ai", [post("w", "Inside the OpenAI deal", kind="press", url="https://wired.com/deal")])
    apply(s, cfg, "ars-technica-ai", [post("ar", "A follow-up on the deal", kind="press", url="https://arstechnica.com/f"),
                                      post("ar2", "Ars on something else", "2026-10-23T05:00:00Z", kind="press",
                                           url="https://arstechnica.com/g")])
    ids = {k: sid_of(s, u) for k, u in (("tc", "https://techcrunch.com/raise"), ("tc2", "https://techcrunch.com/other"),
                                        ("v", "https://theverge.com/raise"), ("w", "https://wired.com/deal"),
                                        ("ar", "https://arstechnica.com/f"), ("ar2", "https://arstechnica.com/g"))}
    apply(s, cfg, "article-pages", [
        page(ids["tc"], ["https://openai.com/index/funding-round", "https://techcrunch.com/newsletters"]),
        page(ids["v"], ["https://openai.com/index/funding-round/?utm_source=x"]),       # shares the OpenAI post
        page(ids["w"], ["https://theverge.com/raise"]),                                  # links the Verge article
        page(ids["tc2"], ["https://techcrunch.com/newsletters", "https://x.com/techcrunch"]),   # nav-ish only
        page(ids["ar"], ["https://wired.com/", "https://arstechnica.com/g"]),           # a home page; its own site
        page(ids["ar2"], [])])
    return s, ids


def test_press_articles_that_share_or_link_a_url_are_one_event(tmp_path):
    cfg = cfg_for(tmp_path)
    s, ids = press_setup(tmp_path, cfg)
    apply(s, cfg, "techmeme", [Item(item_key="tm", role="mention", url="https://techmeme.com/1", title="x",
                                    links=("https://arstechnica.com/f",), published_at=EARLIER)])
    _, groups = select(s, cfg)
    order = alive(groups, "press_policy")
    event = next(g for g in groups["press_policy"] if g.id == order[0])
    assert sorted(event.members) == sorted([ids["tc"], ids["v"], ids["w"]])
    assert event.info["outlets"] == ["techcrunch-ai", "the-verge-ai", "wired-ai"]
    # then Techmeme coverage beats newer articles with neither; a link to the outlet's own other
    # article doesn't make one event
    assert order[1:] == [ids["ar"], ids["tc2"], ids["ar2"]]


# --------------------------------------------------------------------------------------- ledger

def test_reported_stories_stay_out_unless_new_discussion_arrives(tmp_path):
    cfg = cfg_for(tmp_path)
    s = fresh(tmp_path, cfg)
    apply(s, cfg, "openai-news", [post("a", "First post about things"), post("b", "Second post about things")])
    a, b = sid_of(s, "https://example.test/a"), sid_of(s, "https://example.test/b")
    runs = cfg.data_dir / "runs"
    (runs / "2026-10-22").mkdir(parents=True)
    (runs / "2026-10-22" / "stories_index.jsonl").write_text(json.dumps({"id": a}) + "\n" + json.dumps({"id": b}) + "\n")
    doc = ledger.load(cfg.data_dir, "2026-10-23")
    assert doc["kind"] == "index" and set(doc["dates"]) == {a, b}
    apply(s, cfg, "hn", [Item(item_key="9", role="mention", mention_kind="thread", url="https://news.ycombinator.com/item?id=9",
                              title="Second post about things", links=("https://example.test/b",),
                              published_at=EARLIER, engagement={"points": 50, "comments": 3})], at="2026-10-23T09:00:00Z")
    _, groups = select(s, cfg, doc)
    by = {g.id: g for g in groups["announcements"]}
    assert by[a].stage == "ledger"
    assert by[b].alive and (by[b].status, by[b].prev_reported) == ("update", "2026-10-22")
    # once the skill writes reported.json, only those files count
    (runs / "2026-10-22" / "reported.json").write_text(json.dumps({"date": "2026-10-22", "story_ids": [b]}))
    v = output.Validator()
    assert set(ledger.load(cfg.data_dir, "2026-10-23", v.validate)["dates"]) == {b}
    (runs / "2026-10-22" / "reported.json").write_text(json.dumps({"date": "Oct 22", "story_ids": [b]}))
    assert ledger.load(cfg.data_dir, "2026-10-23", v.validate)["errors"]


# ------------------------------------------------------------------------------------- outputs

def full_day(tmp_path):
    cfg = cfg_for(tmp_path)
    s, ids = press_setup(tmp_path, cfg)
    apply(s, cfg, "hf-daily-papers", [paper("2610.00001", 500), paper("2610.00002", 300)])
    apply(s, cfg, "hf-paper-detail", [usage("2610.00001", stars=150), usage("2610.00002", models=3)])
    apply(s, cfg, "hf-org-watch", [model("lab/New-1", "2026-10-22T20:00:00Z", 10)])
    apply(s, cfg, "openai-news", [post("o1", "An OpenAI post about models")])
    apply(s, cfg, "hn", [Item(item_key="7", role="mention", mention_kind="thread", url="https://news.ycombinator.com/item?id=7",
                              title="x", links=("https://arxiv.org/abs/2610.00001",), published_at=EARLIER,
                              engagement={"points": 120, "comments": 40})])
    apply(s, cfg, "hn-items", [Item(item_key="8", role="mention", mention_kind="comment", parent_id="hn|7",
                                    summary="A sharp comment.", authors=("pg",), rank=1, replies=4)])
    s.close()
    return cfg, ids


def test_outputs_validate_are_ordered_and_byte_identical_on_a_rerun(tmp_path):
    cfg, ids = full_day(tmp_path)
    h, out = run.run(cfg, run_date="2026-10-23", at=AT)
    assert h["status"] == "degraded" and h["mode"] == "rules_only" and h["kept"] == 8
    lines = [json.loads(x) for x in (out / "stories_index.jsonl").read_text().splitlines()]
    assert [x["section"] for x in lines] == ["papers", "papers", "releases", "announcements",
                                             "press_policy", "press_policy", "press_policy", "press_policy"]
    paper1 = next(x for x in lines if x["id"] == "arxiv:2610.00001")
    assert paper1["discussion"]["where"] == ["hn"] and paper1["signals"]["hn_points"] == 120
    d = json.loads((out / paper1["detail"]).read_text())
    assert d["discussion"]["threads"][0]["top_comments"][0]["text"] == "A sharp comment."
    assert d["full_text"][0]["kind"] == "abstract"
    event = next(x for x in lines if x.get("merged"))
    assert len(event["outlets"]) == 3
    ev = json.loads((out / event["detail"]).read_text())
    assert ev["full_text"] and ev["full_text"][0]["kind"] == "page"
    log = [json.loads(x) for x in (out / "triage_log.jsonl").read_text().splitlines()]
    assert {x["outcome"] for x in log} == {"kept", "merged"}
    report = (out / "run_report.md").read_text()
    assert report.startswith("```json") and "## Stages" in report
    before = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    run.run(cfg, run_date="2026-10-23", at=AT)
    after = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert before == after


def test_detail_file_names_are_safe():
    assert output.detail_path("hf-model:deepseek-ai/deepseek-v4.1-flash") == "stories/hf-model-deepseek-ai-deepseek-v4.1-flash.json"
    assert output.detail_path("arxiv:2610.12345") == "stories/arxiv-2610.12345.json"


def test_source_status_comes_from_the_latest_run_that_included_it(tmp_path):
    cfg = cfg_for(tmp_path)
    s = fresh(tmp_path, cfg)
    full = {"cadence": "daily", "sources": [{"id": "hn", "status": "ok", "requests": 6, "counts": {"items": 9}},
                                            {"id": "article-pages", "status": "failed", "requests": 9, "counts": {},
                                             "errors": ["build crashed"]}]}
    one = {"cadence": "daily", "sources": [{"id": "hn", "status": "disabled"},
                                           {"id": "article-pages", "status": "ok", "requests": 2, "counts": {"items": 2}}]}
    for run_id, rep in (("20261023T080000Z", full), ("20261023T090000Z", one)):
        s.run("INSERT INTO runs (run_id, kind, run_date, started_at, report) VALUES (?, 'collect', '2026-10-23', ?, ?)",
              (run_id, run_id, json.dumps(rep)))
    ctx = candidates.Ctx(s, cfg, AT)
    got = {x["id"]: x["status"] for x in output.collect_summary(ctx, "2026-10-23")["sources"]}
    assert got == {"hn": "ok", "article-pages": "ok"}
