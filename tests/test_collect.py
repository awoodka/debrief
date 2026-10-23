"""End to end: collect through a fake network serving the real fixtures, then replay."""
import json
import re
import shutil
from pathlib import Path

import yaml
from conftest import FIXTURES, fixture

from debrief import collect, config
from debrief.http import Client
from debrief.store import Store

SOURCES = [
    {"id": "lab", "name": "Lab", "adapter": "rss", "roles": ["anchor"], "tier": 1, "kind": "announcement",
     "org": "Lab", "url": "https://feeds.test/lab.xml"},
    {"id": "notes", "name": "Notes", "adapter": "rss", "roles": ["reference", "discussion"], "tier": 3,
     "url": "https://feeds.test/notes.atom"},
    {"id": "news", "name": "News", "adapter": "listing_page", "roles": ["anchor"], "tier": 1,
     "kind": "announcement", "url": "https://www.anthropic.com/news",
     "probe": {"kind": "links", "link_pattern": r"^https://www\.anthropic\.com/news/[^/?#]+$"},
     "options": {"exclude": ["/news/(announcements|features|product|policy|safeguards|security|partnerships)$"]}},
    {"id": "hf", "name": "HF", "adapter": "hf_daily_papers", "roles": ["anchor", "discussion"], "tier": 1,
     "kind": "paper", "url": "https://hf.test/api/daily_papers"},
    {"id": "arxiv", "name": "arXiv", "adapter": "arxiv_rss", "roles": ["anchor"], "tier": 2, "kind": "paper",
     "url": "https://rss.arxiv.test/rss/{each}", "each": ["stat.ML"],
     "options": {"oai_base": "https://oai.arxiv.test/oai", "oai_sets": ["stat"]}},
    {"id": "gh", "name": "GH", "adapter": "github_releases", "roles": ["anchor"], "tier": 1, "kind": "release",
     "url": "https://api.github.test/repos/{each}/releases?per_page=5", "each": ["vllm-project/vllm"]},
    {"id": "off", "name": "Off", "adapter": "rss", "roles": ["anchor"], "tier": 2, "enabled": False,
     "url": "https://feeds.test/off.xml"},
    {"id": "later", "name": "Later", "adapter": "bluesky", "roles": ["discussion"], "tier": 3,
     "url": "https://bsky.test/"},
]


class Resp:
    def __init__(self, status, content=b"", headers=None, url=""):
        self.status_code, self.content, self.headers, self.url = status, content, headers or {}, url


class FakeNet:
    """Routes by URL pattern; records every request."""

    def __init__(self, routes):
        self.routes, self.calls, self.headers = routes, [], {}

    def request(self, method, url, **kw):
        self.calls.append(url)
        for pattern, fn in self.routes:
            if re.search(pattern, url):
                status, body = fn(url)
                return Resp(status, body, {"content-type": "text/xml"}, url)
        return Resp(404, b"not found", {}, url)


OAI_PAGE_2 = re.sub(r"<record>.*</record>\s*<resumptionToken[^>]*>[^<]*</resumptionToken>", "",
                    (FIXTURES / "arxiv/oai_arxivraw_page1.xml").read_text(), flags=re.S).encode()


def routes(fail_lab=False):
    return [
        (r"/robots\.txt$", lambda u: (404, b"")),
        (r"feeds\.test/lab\.xml", lambda u: (503, b"down") if fail_lab else (200, fixture("rss/openai_news.xml"))),
        (r"feeds\.test/notes\.atom", lambda u: (200, fixture("rss/simonwillison.atom"))),
        (r"anthropic\.com/news$", lambda u: (200, fixture("listing/anthropic_news.html"))),
        (r"anthropic\.com/news/", lambda u: (200, fixture("listing/anthropic_post.html"))),
        (r"hf\.test/api/daily_papers\?date=", lambda u: (200, fixture("hf/daily_papers_2026-10-21.json"))),
        (r"rss\.arxiv\.test/rss/stat\.ML", lambda u: (200, fixture("arxiv/rss_stat.ML.xml"))),
        (r"oai\.arxiv\.test/oai\?verb=ListRecords&resumptionToken=TOKEN-PAGE-2", lambda u: (200, OAI_PAGE_2)),
        (r"oai\.arxiv\.test/oai\?verb=ListRecords&set=stat", lambda u: (200, fixture("arxiv/oai_arxivraw_page1.xml"))),
        (r"api\.github\.test/repos/vllm-project/vllm/releases", lambda u: (200, fixture("github/releases_vllm.json"))),
    ]


def make_cfg(tmp_path):
    cdir = tmp_path / "config"
    cdir.mkdir()
    shutil.copy(Path(config.CONFIG_DIR) / "settings.yml", cdir / "settings.yml")
    shutil.copy(Path(config.CONFIG_DIR) / "triage.yml", cdir / "triage.yml")
    (cdir / "sources.yml").write_text(yaml.safe_dump(SOURCES))
    return config.load(cdir, env={"DEBRIEF_DATA_DIR": str(tmp_path / "data")})


def run(cfg, net):
    client = Client(cfg.user_agent, cfg.settings["http"], sleep=lambda s: None, session=net)
    return collect.run(cfg, run_date="2026-10-22", client=client, log=lambda *a, **k: None)


TABLES = (("stories", "story_id"), ("items", "source_id, item_key"), ("mentions", "mention_id"),
          ("story_urls", "url"), ("engagement", "mention_id, observed_at"),
          ("story_signals", "story_id, observed_at, source_id, name"), ("coverage", "story_id, mention_id"),
          ("story_pages", "story_id, url"))


def dump(path):
    s = Store(path)
    out = [s.dump(t, o) for t, o in TABLES]
    s.close()
    return out


def test_two_runs_then_replay(tmp_path):
    cfg = make_cfg(tmp_path)
    first = run(cfg, FakeNet(routes()))
    by_id = {s["id"]: s for s in first["sources"]}
    assert {k: by_id[k]["status"] for k in by_id} == {
        "lab": "ok", "notes": "ok", "news": "ok", "hf": "ok", "arxiv": "ok", "gh": "ok",
        "off": "disabled", "later": "not_built"}
    assert by_id["news"]["requests"] == 1 + 5              # the page, then the 5 newest posts (bootstrap)
    assert by_id["notes"]["counts"]["mentions_new"] == 3
    assert by_id["arxiv"]["counts"]["stories_new"] == 3    # new, new, cross; the two replacements are seen

    live = cfg.data_dir / "state" / "debrief.sqlite"
    stories_after_first = Store(live).one("SELECT COUNT(*) FROM stories")[0]
    second = run(cfg, FakeNet(routes()))
    assert second["totals"].get("new", 0) == 0 and second["totals"].get("stories_new", 0) == 0
    assert Store(live).one("SELECT COUNT(*) FROM stories")[0] == stories_after_first
    assert second["run_id"] != first["run_id"]

    out = collect.replay(cfg, "2026-10-22", tmp_path / "replay.sqlite", log=lambda *a: None)
    assert out["runs"] == [first["run_id"], second["run_id"]]
    assert dump(tmp_path / "replay.sqlite") == dump(live)

    reports = sorted(p.name for p in (cfg.data_dir / "runs" / "2026-10-22").iterdir())
    assert reports == sorted([f"collect-{first['run_id']}.json", f"collect-{first['run_id']}.md",
                              f"collect-{second['run_id']}.json", f"collect-{second['run_id']}.md"])


def test_missed_announcements_are_backfilled_from_oai(tmp_path):
    cfg = make_cfg(tmp_path)
    store = Store(cfg.data_dir / "state" / "debrief.sqlite")
    store.save_source_state("arxiv", "earlier", "ok", None, "2026-10-16T22:00:00Z",
                            cursor={"last_announce": "2026-10-16"})
    store.close()
    net = FakeNet(routes())
    report = run(cfg, net)
    oai = [u for u in net.calls if "oai.arxiv.test" in u]
    assert oai == ["https://oai.arxiv.test/oai?verb=ListRecords&set=stat&metadataPrefix=arXivRaw"
                   "&from=2026-10-19&until=2026-10-21",
                   "https://oai.arxiv.test/oai?verb=ListRecords&resumptionToken=TOKEN-PAGE-2"]
    entry = next(s for s in report["sources"] if s["id"] == "arxiv")
    assert any("backfilling" in w for w in entry["warnings"])
    store = Store(cfg.data_dir / "state" / "debrief.sqlite")
    assert store.story("arxiv:2610.24336")["extra"]["arxiv"]["via"] == "oai-backfill"
    assert store.cursor("arxiv") == {"last_announce": "2026-10-22"}


def test_a_failing_source_is_loud_and_keeps_its_cursor(tmp_path):
    cfg = make_cfg(tmp_path)
    report = run(cfg, FakeNet(routes(fail_lab=True)))
    lab = next(s for s in report["sources"] if s["id"] == "lab")
    assert lab["status"] == "failed" and "HTTP 503" in lab["errors"][0]
    assert report["status"] == "degraded"
    store = Store(cfg.data_dir / "state" / "debrief.sqlite")
    row = store.one("SELECT last_status, consecutive_failures FROM source_state WHERE source_id='lab'")
    assert tuple(row) == ("failed", 1)
    assert store.one("SELECT COUNT(*) FROM http_validators WHERE url LIKE '%lab.xml'")[0] == 0
    md = (cfg.data_dir / "runs" / "2026-10-22" / f"collect-{report['run_id']}.md").read_text()
    assert "| `lab` | failed |" in md
    saved = json.loads((cfg.data_dir / "runs" / "2026-10-22" / f"collect-{report['run_id']}.json").read_text())
    assert saved["statuses"]["failed"] == 1


# --- M2: discussion attached end to end ------------------------------------------------------

DISCUSSION = SOURCES[:6] + [
    {"id": "hn", "name": "HN", "adapter": "hn", "roles": ["discussion"], "tier": 3, "url": "https://hn.test/",
     "options": {"cadence": ["daily", "6h"]}},
    {"id": "hn-items", "name": "HN trees", "adapter": "hn_comments", "roles": ["discussion"], "tier": 3,
     "url": "https://hn.test/", "options": {"cadence": ["daily", "6h"]}},
    {"id": "digest", "name": "Digest", "adapter": "rss", "roles": ["reference"], "tier": 3,
     "url": "https://feeds.test/digest.atom"},
]

DIGEST = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>d</title>
<entry><id>d1</id><title>This week</title><link href="https://digest.test/1"/><updated>2026-10-22T18:00:00Z</updated>
<summary type="html">&lt;a href="https://arxiv.org/abs/2610.28708"&gt;a paper&lt;/a&gt;</summary></entry></feed>"""


def hn_slice(title_for_match):
    j = json.loads(fixture("hn/search_slice.json"))
    for h in j["hits"]:
        if h["objectID"] == "50195959":
            h["url"] = "https://arxiv.org/abs/2610.28708"            # exact URL
        if h["objectID"] == "50202140":
            h["url"] = "https://arxiv.org/pdf/2610.28708v2.pdf"     # arXiv id in the URL
        if h["objectID"] == "50202325":
            h["url"], h["title"] = "https://elsewhere.test/x", title_for_match   # title rule
    return json.dumps(j).encode()


def discussion_routes(title):
    return routes() + [
        (r"feeds\.test/digest\.atom", lambda u: (200, DIGEST)),
        (r"hn\.algolia\.com/api/v1/search_by_date", lambda u: (200, hn_slice(title))),
        (r"hn\.algolia\.com/api/v1/search\?tags=story,\(", lambda u: (200, hn_slice(title))),
        (r"firebaseio\.com/v0/item/50195959\.json", lambda u: (200, fixture("hn/firebase_item.json"))),
        (r"hn\.algolia\.com/api/v1/items/50195959", lambda u: (200, fixture("hn/item_tree.json"))),
    ]


def make_discussion_cfg(tmp_path):
    cdir = tmp_path / "config"
    cdir.mkdir()
    shutil.copy(Path(config.CONFIG_DIR) / "settings.yml", cdir / "settings.yml")
    shutil.copy(Path(config.CONFIG_DIR) / "triage.yml", cdir / "triage.yml")
    (cdir / "sources.yml").write_text(yaml.safe_dump(DISCUSSION))
    return config.load(cdir, env={"DEBRIEF_DATA_DIR": str(tmp_path / "data")})


def test_discussion_attaches_end_to_end_and_replays(tmp_path, monkeypatch):
    # Polls are hours apart in real life; give each fetch its own second so snapshots don't collide.
    from datetime import datetime, timedelta, timezone
    from debrief.adapters.base import FetchContext
    tick = iter(range(100000))
    t0 = datetime(2026, 10, 22, 20, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(FetchContext, "fetched_at", property(
        lambda self: (t0 + timedelta(minutes=next(tick))).strftime("%Y-%m-%dT%H:%M:%SZ")))
    cfg = make_discussion_cfg(tmp_path)
    arxiv_title = None
    from debrief.adapters import REGISTRY as R
    from conftest import record as rec_
    for it in R["arxiv_rss"].parse(rec_(fixture("arxiv/rss_stat.ML.xml"), meta={"kind": "rss"}), cfg.source("arxiv"),
                                    cfg.settings["collect"]):
        if it.story_id == "arxiv:2610.28708":
            arxiv_title = it.title
    first = run(cfg, FakeNet(discussion_routes(arxiv_title)))
    by = {x["id"]: x for x in first["sources"]}
    assert [x["id"] for x in first["sources"] if "counts" in x][-3:] == ["hn", "hn-items", "digest"]   # phase order
    live = cfg.data_dir / "state" / "debrief.sqlite"
    s = Store(live)
    m = {r["mention_id"]: (r["story_id"], r["match_method"]) for r in s.q("SELECT * FROM mentions WHERE source_id='hn'")}
    assert m["hn|50195959"] == ("arxiv:2610.28708", "url")
    assert m["hn|50202140"] == ("arxiv:2610.28708", "id")
    assert m["hn|50202325"] == ("arxiv:2610.28708", "title")
    assert m["hn|50201751"] == (None, None)
    comments = s.q("SELECT story_id, parent_id, rank FROM mentions WHERE source_id='hn-items' ORDER BY rank")
    assert comments and {c["story_id"] for c in comments} == {"arxiv:2610.28708"}
    assert {c["parent_id"] for c in comments} == {"hn|50195959"}
    assert s.one("SELECT source_id FROM coverage WHERE story_id='arxiv:2610.28708'")[0] == "digest"
    assert by["hn-items"]["requests"] == 2          # Firebase order + Algolia tree for the one big attached thread
    s.close()

    six = run_cadence(cfg, FakeNet(discussion_routes(arxiv_title)), "6h")
    assert [x["id"] for x in six["sources"] if "counts" in x] == ["hn", "hn-items"]
    s = Store(live)
    assert s.one("SELECT COUNT(*) FROM engagement WHERE mention_id='hn|50195959'")[0] >= 2   # run 1 + the 6h poll
    s.close()

    collect.replay(cfg, "2026-10-22", tmp_path / "replay.sqlite", log=lambda *a: None)
    assert dump(tmp_path / "replay.sqlite") == dump(live)


def run_cadence(cfg, net, cadence):
    client = Client(cfg.user_agent, cfg.settings["http"], sleep=lambda s: None, session=net)
    return collect.run(cfg, run_date="2026-10-22", client=client, log=lambda *a, **k: None, cadence=cadence)


def test_rebuild_reproduces_the_live_state_and_keeps_cursors(tmp_path):
    cfg = make_cfg(tmp_path)
    run(cfg, FakeNet(routes()))
    live = cfg.data_dir / "state" / "debrief.sqlite"
    before, cursor = dump(live), Store(live).cursor("hf")
    out = collect.rebuild(cfg, log=lambda *a: None)
    assert dump(live) == before and Store(live).cursor("hf") == cursor
    assert (cfg.data_dir / "state" / out["backup"]).exists()


def test_a_refused_page_is_a_warning_not_a_partial_source(tmp_path, monkeypatch):
    from dataclasses import replace
    from datetime import datetime, timezone

    from debrief.adapters import REGISTRY, Adapter

    class Pages(Adapter):
        name, phase, page_by_page = "pages_test", "enrich", True

        def fetch(self, ctx, source):
            for url in ("https://ok.test/a", "https://refuses.test/b"):
                ctx.get(source, url, conditional=False)

        def parse(self, record, source, opts):
            return []

    cfg = config.load(env={"DEBRIEF_DATA_DIR": str(tmp_path / "data")})
    net = FakeNet([(r"ok\.test", lambda u: (200, b"<p>fine</p>")), (r"refuses\.test", lambda u: (403, b"no"))])
    client = Client(cfg.user_agent, cfg.settings["http"], sleep=lambda s: None, session=net)
    store = Store(tmp_path / "s.sqlite")
    monkeypatch.setitem(REGISTRY, "pages_test", Pages())
    src = replace(cfg.source("article-pages"), adapter="pages_test")
    now = datetime.now(timezone.utc)
    entry = collect.collect_source(cfg, store, client, src, "r1", "2026-10-23", now, lambda *a, **k: None)
    assert entry["status"] == "ok" and entry["errors"] == [] and "HTTP 403" in entry["warnings"][0]
    monkeypatch.setattr(Pages, "page_by_page", False)
    entry = collect.collect_source(cfg, store, client, src, "r2", "2026-10-23", now, lambda *a, **k: None)
    assert entry["status"] == "partial"                              # what any other source would be
