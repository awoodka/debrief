#!/usr/bin/env python3
"""Debrief collector entrypoint — wide-net, deterministic, fail-soft. No LLM.

Writes data/digest_input.json (full) and data/digest_input.md (human-inspectable).
Enrichment (traction signals + divergence + snapshots) is step 3; this is the raw net.
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:  # load .env so credentialed sources (GitHub PAT, Product Hunt, Bluesky) see their keys
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # noqa: BLE001
    pass

from collector import config, score, digest, enrich, store  # noqa: E402
from collector.schema import dedupe  # noqa: E402
from collector.sources import (  # noqa: E402
    arxiv, hf_daily, rss, hackernews, github_trending, events, bluesky, reddit, producthunt,
)

DATA = Path(__file__).resolve().parent.parent / "data"

SECTION_ORDER = [
    ("product", "Startups / product launches"),
    ("discussion", "Discussion (HN · Reddit · Lobsters)"),
    ("funding", "Funding & startup news"),
    ("repo", "Repos / trending"),
    ("lab_news", "Lab / company news"),
    ("article", "News & newsletters"),
    ("paper", "Papers"),
    ("event", "Deadlines / events"),
    ("social", "Social (Bluesky)"),
]
SECTION_CAP = {"paper": 80}
DEFAULT_CAP = 40


def warn_if_api_key():
    import os
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("⚠️  ANTHROPIC_API_KEY is set. The collector makes no Anthropic calls, but /debrief's\n"
              "    agents would bill to the paid API instead of your Max subscription. Unset it\n"
              "    before running /debrief.\n")


def run(days_papers, days_news, max_arxiv):
    counts, errors, all_items = {}, {}, []

    def source(name, fn):
        print(f"[{name}]")
        try:
            got = fn()
            counts[name] = len(got)
            all_items.extend(got)
        except Exception as e:  # noqa: BLE001 — fail-soft: one dead source never sinks the run
            errors[name] = f"{type(e).__name__}: {e}"
            counts[name] = 0
            print(f"  {name}: FAILED — {errors[name]}")

    source("arxiv", lambda: arxiv.fetch(days_papers, max_arxiv))
    source("hf_daily", lambda: hf_daily.fetch())
    for label, url in config.LAB_FEEDS:
        source(f"rss:{label}", lambda u=url, l=label: rss.fetch_feed(l, u, "lab_news", "neutral", days_news))
    for label, url in config.NEWS_FEEDS:
        source(f"rss:{label}", lambda u=url, l=label: rss.fetch_feed(l, u, "article", "mainstream", days_news))
    for label, url, axis in config.NEWSLETTER_FEEDS:
        source(f"rss:{label}", lambda u=url, l=label, a=axis: rss.fetch_feed(l, u, "article", a, days_news))
    for label, url in config.STARTUP_FEEDS:
        source(f"rss:{label}", lambda u=url, l=label: rss.fetch_feed(l, u, "funding", "mainstream", days_news))
    source("hackernews", lambda: hackernews.fetch(days_news))
    source("reddit", lambda: reddit.fetch(days_news))
    for label, url in config.LOBSTERS_RSS:
        source(label, lambda u=url, l=label: rss.fetch_feed(l, u, "discussion", "in_field", days_news))
    source("producthunt", lambda: producthunt.fetch())
    source("github_trending", lambda: github_trending.fetch())
    source("events", lambda: events.fetch())
    source("bluesky", lambda: bluesky.fetch(days_news))

    raw = len(all_items)
    items = dedupe(all_items)
    meta = {
        "raw_count": raw, "deduped_count": len(items),
        "per_source": counts, "errors": errors,
        "params": {"days_papers": days_papers, "days_news": days_news, "max_arxiv": max_arxiv},
    }
    return items, meta


def _signals(rs):
    parts = []
    for k in ("hf_upvotes", "hn_points", "hn_comments", "stars_today", "likes"):
        if rs.get(k) not in (None, "", 0):
            parts.append(f"{k}={rs[k]}")
    if rs.get("axis"):
        parts.append(rs["axis"])
    return " · ".join(parts)


def render_md(items, meta):
    by_type = defaultdict(list)
    for it in items:
        by_type[it["type"]].append(it)

    L = ["# Debrief — raw collector digest (`digest_input`)", "",
         f"_Generated {meta['generated_at']} · params `{meta['params']}`_", "",
         f"**{meta['raw_count']} raw items → {meta['deduped_count']} after dedupe/merge.**", "",
         "## Per-source counts"]
    for k, v in meta["per_source"].items():
        L.append(f"- `{k}`: {v}")
    if meta["errors"]:
        L += ["", "## Failed sources (fail-soft — run continued)"]
        for k, v in meta["errors"].items():
            L.append(f"- `{k}`: {v}")
    L.append("")

    for typ, label in SECTION_ORDER:
        group = sorted(by_type.get(typ, []), key=lambda it: it.get("published") or "", reverse=True)
        if not group:
            continue
        cap = SECTION_CAP.get(typ, DEFAULT_CAP)
        L.append(f"## {label} ({len(group)})")
        for it in group[:cap]:
            srcs = "+".join(sorted(set(it.get("sources", [it["source"]]))))
            date = (it.get("published") or "")[:10]
            sig = _signals(it.get("raw_signal", {}))
            head = f"- **{it['title']}**  "
            metaline = f"  `{srcs}` · {date}" + (f" · {sig}" if sig else "")
            if it.get("author"):
                metaline += f" · {it['author'][:60]}"
            L += [head, metaline + "  "]
            if it.get("url"):
                L.append(f"  {it['url']}  ")
            snip = (it.get("summary") or "").replace("\n", " ").strip()
            if snip:
                L.append(f"  > {snip[:240]}{'…' if len(snip) > 240 else ''}  ")
        if len(group) > cap:
            L.append(f"  _…+{len(group) - cap} more in digest_input.json_")
        L.append("")
    return "\n".join(L)


def write_outputs(items, meta):
    DATA.mkdir(exist_ok=True)
    meta["generated_at"] = datetime.now(timezone.utc).isoformat()
    (DATA / "digest_input.json").write_text(
        json.dumps({"meta": meta, "items": items}, indent=2, ensure_ascii=False))
    (DATA / "digest_input.md").write_text(render_md(items, meta))


def main():
    ap = argparse.ArgumentParser(description="Debrief collector (wide-net, fail-soft, no LLM).")
    ap.add_argument("--days-papers", type=int, default=config.PAPER_WINDOW_DAYS)
    ap.add_argument("--days-news", type=int, default=config.NEWS_WINDOW_DAYS)
    ap.add_argument("--max-arxiv", type=int, default=config.MAX_ARXIV_RESULTS)
    a = ap.parse_args()

    warn_if_api_key()
    t0 = datetime.now(timezone.utc)
    items, meta = run(a.days_papers, a.days_news, a.max_arxiv)
    print("[enrich]")
    enrich.enrich(items)                   # 3b-ii: HF artifacts + citations on candidate papers
    score.score_all(items)                 # in_field / mainstream / divergence (now with real signals)
    store.snapshot(items)                  # persist per-item signals -> velocity across runs
    write_outputs(items, meta)             # digest_input.{md,json} — raw, full, scored (completeness)
    stats = digest.write(items, meta)      # agent_digest.{md,json} — floored, capped, tiered (what agents read)
    dt = (datetime.now(timezone.utc) - t0).total_seconds()

    print(f"\n=== done in {dt:.0f}s ===")
    print(f"raw {meta['raw_count']} → deduped {meta['deduped_count']}")
    print("per-source:", json.dumps(meta["per_source"]))
    if meta["errors"]:
        print("failed:", json.dumps(meta["errors"]))
    pp = stats.get("paper", {})
    if pp:
        print(f"papers: {pp['collected']} collected → {pp['passed_floor']} cleared pulse floor → {pp['sent']} to agents")
    print("wrote digest_input.{md,json} (raw, complete) + agent_digest.{md,json} (what the council reads)")


if __name__ == "__main__":
    main()
