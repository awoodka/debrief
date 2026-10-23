"""`debrief calibrate`: run the scoring call on a labeled set and compare it with the labels.

The set is a frozen day of candidates with Alex's rulings (tests/fixtures/relevance/<date>.jsonl):
each item has the text the model sees, a label (in or out) and sometimes a band ("line": a 3, worth
one line). The call is the same one triage makes, with the same cache, so a second run is free.

Reported: agreement on relevant, how many of the "in" items were kept (recall: what matters most,
since a wrong "out" is never seen again) and how many "out" items got through, the score of each
"line" item, the same_as groups, and every disagreement with the model's why. Written to
<data>/calibration/<fixture>-<model>.md and printed.
"""
import json
from collections import Counter
from pathlib import Path

from ..config import REPO
from . import score

DEFAULT = REPO / "tests" / "fixtures" / "relevance" / "2026-10-23.jsonl"


def compare(items, answers):
    rows = []
    for it in items:
        a = answers[it["id"]]
        rows.append({**it, "relevant": a["relevant"], "score": a["score"], "why": a["why"], "same_as": a["same_as"]})
    c = Counter((r["label"], r["relevant"]) for r in rows)
    tp, fn, fp, tn = c[("in", True)], c[("in", False)], c[("out", True)], c[("out", False)]
    return {
        "n": len(rows), "agree": tp + tn, "kept_in": tp, "missed_in": fn, "let_through": fp, "dropped_out": tn,
        "recall": round(tp / (tp + fn), 3) if tp + fn else None,
        "precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "scores": dict(sorted(Counter(r["score"] for r in rows if r["relevant"]).items())),
        "line_items": [(r["title"], r["score"]) for r in rows if r.get("band") == "line"],
        "disagreements": [r for r in rows if (r["label"] == "in") != r["relevant"]],
        "same_as": [(r["id"], r["title"], r["same_as"]) for r in rows if r["same_as"]],
        "rows": rows,
    }


def render(fixture, result, cmp):
    u = result["usage"]
    L = [f"# Calibration: {result['model']} on {fixture.name}", "",
         f"Prompt {result['prompt_version']}. {u['input_tokens']:,} input and {u['output_tokens']:,} output tokens "
         f"({u['thinking_tokens']:,} thinking), {u['duration_s']} s, ${u['cost_usd_list_price']} at list price.", "",
         f"- Agreement: **{cmp['agree']} of {cmp['n']}**",
         f"- Relevant items kept: **{cmp['kept_in']} of {cmp['kept_in'] + cmp['missed_in']}** (recall {cmp['recall']})",
         f"- Not-relevant items let through: {cmp['let_through']} of {cmp['let_through'] + cmp['dropped_out']} "
         f"(precision {cmp['precision']})",
         f"- Scores given: {', '.join(f'{k}: {v}' for k, v in cmp['scores'].items())}",
         f"- Items labeled \"a line\" (expected 3): " + ", ".join(f"{t[:40]} → {s}" for t, s in cmp["line_items"]), ""]
    if cmp["disagreements"]:
        L += ["## Disagreements", "", "| Label | Model | Story | Why (the model's) |", "|---|---|---|---|"]
        for r in cmp["disagreements"]:
            model = f"relevant, {r['score']}" if r["relevant"] else "not relevant"
            L.append(f"| {r['label']} | {model} | {r['title'][:80].replace('|', '/')} ({r['section']}) "
                     f"| {r['why'].replace('|', '/')} |")
        L.append("")
    if cmp["same_as"]:
        L += ["## same_as", ""] + [f"- {t[:70]} → `{s}`" for _, t, s in cmp["same_as"]] + [""]
    L += ["## Every item", "", "| Label | Model | Story | Why |", "|---|---|---|---|"]
    for r in cmp["rows"]:
        model = f"{r['score']}" if r["relevant"] else "out"
        L.append(f"| {r['label']} | {model} | {r['title'][:70].replace('|', '/')} | {r['why'].replace('|', '/')} |")
    return "\n".join(L) + "\n"


def run(cfg, fixture=DEFAULT, model=None, fresh=False, runner=None, log=print):
    fixture = Path(fixture)
    items = [json.loads(x) for x in fixture.read_text().splitlines() if x.strip()]
    t = cfg.settings["triage"]["scoring"]
    data = {"candidates": [{k: it[k] for k in ("id", "section", "source", "org", "title", "text")} for it in items],
            "reported": []}
    result = score.call(cfg.data_dir / "cache" / "score", t, data, model=model, runner=runner or score.run_claude,
                        fresh=fresh, log=log)
    cmp = compare(items, result["answers"])
    out = cfg.data_dir / "calibration" / f"{fixture.stem}-{result['model']}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(fixture, result, cmp))
    return result, cmp, out
