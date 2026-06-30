"""Build the AGENT digest — scored, floored, capped, tiered. This is what the council reads.

Papers must clear a minimal PULSE FLOOR (on HF Daily, OR cited in discourse, OR has a GitHub impl,
OR has citations) and are then capped to the top-N by in-field score, with full abstracts only for the
top few. Everything else stays in the raw `digest_input` (the completeness guarantee) — nothing is lost.
"""
import json
from collections import defaultdict
from pathlib import Path

from .config import (AGENT_PAPER_CAP, AGENT_PAPER_FULL_ABSTRACTS, AGENT_CAPS, AGENT_FULL_CONTENT)

DATA = Path(__file__).resolve().parent.parent / "data"

SECTION_ORDER = [
    ("product", "Startups / product launches"),
    ("discussion", "Discussion (HN · Reddit · Lobsters)"),
    ("funding", "Funding & startup news"),
    ("repo", "Repos / trending"),
    ("lab_news", "Lab / model releases"),
    ("article", "News & analysis"),
    ("paper", "Papers (traction-filtered)"),
    ("event", "Deadlines / events"),
    ("social", "Social (Bluesky)"),
]

_SIG_KEYS = ("hf_upvotes", "discourse_mentions", "github_impls", "hf_linked_models",
            "influential_citations", "hn_points", "ph_votes", "stars_today")


def _paper_has_pulse(p):
    rs = p["raw_signal"]
    return ("hf_daily" in p["sources"] or rs.get("discourse_mentions", 0)
            or rs.get("github_impls", 0) or rs.get("influential_citations", 0)
            or rs.get("citations", 0))


def _sig_str(it):
    rs = it["raw_signal"]
    return " ".join(f"{k}={rs[k]}" for k in _SIG_KEYS if rs.get(k))


def build(items):
    by_type = defaultdict(list)
    for it in items:
        by_type[it["type"]].append(it)

    selected, stats = {}, {}
    for typ, _ in SECTION_ORDER:
        group = by_type.get(typ, [])
        if typ == "paper":
            cand = sorted((p for p in group if _paper_has_pulse(p)),
                          key=lambda p: p["in_field_score"], reverse=True)
            chosen, full_n = cand[:AGENT_PAPER_CAP], AGENT_PAPER_FULL_ABSTRACTS
            stats["paper"] = {"collected": len(group), "passed_floor": len(cand), "sent": len(chosen)}
        else:
            # Cap by SALIENCE (in-field + mainstream) so both gems AND must-knows survive the cut;
            # divergence is the agents' judgment signal, not the capping signal.
            chosen = sorted(group, key=lambda i: i["in_field_score"] + i["mainstream_score"],
                            reverse=True)[:AGENT_CAPS.get(typ, 40)]
            full_n = AGENT_FULL_CONTENT
            stats[typ] = {"collected": len(group), "sent": len(chosen)}
        selected[typ] = [(it, idx < full_n) for idx, it in enumerate(chosen)]
    return selected, stats


def render_md(selected, stats, meta):
    L = ["# Debrief — agent digest (scored · floored · tiered)", "",
         f"_Generated {meta.get('generated_at', '')}_", "",
         "What the council reads. Items are kept by **salience** (in-field + mainstream) so both gems and",
         "must-knows survive the cap; **divergence** (in-field − mainstream) is the gem signal — high means",
         "in-field but under-the-radar. Papers are traction-filtered (pulse floor) + capped; raw set in `digest_input`.", ""]
    p = stats.get("paper")
    if p:
        L += [f"> **Papers:** {p['collected']} collected → **{p['passed_floor']}** cleared the pulse floor "
              f"→ top **{p['sent']}** sent to the council (full abstracts for the top {AGENT_PAPER_FULL_ABSTRACTS}).", ""]
    for typ, label in SECTION_ORDER:
        rows = selected.get(typ, [])
        if not rows:
            continue
        st = stats.get(typ, {})
        cnt = str(st.get("sent", len(rows))) + (f" of {st['collected']}" if st.get("collected", 0) > len(rows) else "")
        L.append(f"## {label} ({cnt})")
        for it, full in rows:
            srcs = "+".join(sorted(set(it.get("sources", [it["source"]]))))
            sig = _sig_str(it)
            L.append(f"- **{it['title']}** — `{srcs}` · div={it['divergence']} "
                     f"(in={it['in_field_score']}/main={it['mainstream_score']})" + (f" · {sig}" if sig else ""))
            if it.get("url"):
                L.append(f"  {it['url']}")
            if full and it.get("summary"):
                L.append(f"  > {it['summary'][:1500]}")
        L.append("")
    return "\n".join(L)


def write(items, meta):
    selected, stats = build(items)
    DATA.mkdir(exist_ok=True)
    (DATA / "agent_digest.md").write_text(render_md(selected, stats, meta))
    flat = [{"full": full, "title": it["title"], "url": it.get("url", ""),
             "sources": it.get("sources", []), "type": it["type"], "arxiv_id": it.get("arxiv_id"),
             "in_field_score": it["in_field_score"], "mainstream_score": it["mainstream_score"],
             "divergence": it["divergence"], "raw_signal": it["raw_signal"],
             "summary": it["summary"] if full else ""}
            for _, rows in selected.items() for it, full in rows]
    (DATA / "agent_digest.json").write_text(json.dumps({"stats": stats, "items": flat}, indent=2, ensure_ascii=False))
    return stats
