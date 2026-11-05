"""`debrief jobs calibrate`: compare the pipeline's decisions with Alex's labels.

Labels live on the server, next to the profile: <data>/jobs/calibration/labels.jsonl, one line per
job: {"posting_id", "label": "yes" | "no", "note"}. "yes" means Alex could apply and would keep it
(Revision 3's rule: full-time, above the minimum pay, a level and requirements that fit).

For each labeled job the pipeline's decision is, in order:
  - cut by a code filter (filters.check, with the description when there is one): "no";
  - the scoring call's keep for the current profile version: "yes" or "no";
  - neither (never scored): left out of the agreement.
The report counts agreement, wrong keeps (labeled no, kept) and wrong drops (labeled yes, dropped:
the costly kind, since a dropped job is never seen), and lists every disagreement with the reason
the pipeline gave. Written to <data>/jobs/calibration/report-<profile id>.md and printed.
"""
import json
from collections import Counter
from datetime import datetime, timezone

from . import filters, profile as profiles
from .collect import early_sources
from .store import JobsStore, state_path


def labels_path(cfg):
    return cfg.data_dir / "calibration" / "labels.jsonl"


def load_labels(cfg):
    path = labels_path(cfg)
    if not path.exists():
        return []
    out = {}
    for line in path.read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            out[d["posting_id"]] = d                    # a later line for the same job wins
    return list(out.values())


def run(cfg, now=None):
    now = now or datetime.now(timezone.utc)
    version, prof = profiles.latest(cfg)
    pid = profiles.profile_id(prof)
    store = JobsStore(state_path(cfg))
    E = early_sources(cfg)
    rows, c = [], Counter()
    for lab in load_labels(cfg):
        p = store.posting(lab["posting_id"])
        if p is None:
            c["unknown posting"] += 1
            continue
        v = filters.check(p, prof, now, text=store.texts_for(p["posting_id"]),
                          listed_early=bool(store.posting_sources(p["posting_id"]) & E))
        a = store.score_for(p["posting_id"], pid)
        if not v.ok:
            decided, by, why = "no", f"filter: {v.stage}", v.reason
        elif a is not None:
            decided, by, why = ("yes" if a["keep"] else "no"), "model", "; ".join(a["mismatches"]) or a["why"]
        else:
            c["not scored"] += 1
            continue
        want = lab["label"]
        c["agree" if want == decided else "wrong keep" if decided == "yes" else "wrong drop"] += 1
        rows.append({"posting_id": p["posting_id"], "title": p["title"], "company": p["company"], "label": want,
                     "decided": decided, "by": by, "why": why, "score": a["score"] if a else None,
                     "note": lab.get("note", "")})
    store.close()
    n = c["agree"] + c["wrong keep"] + c["wrong drop"]
    L = [f"# Calibration · profile v{version} ({pid}) · {now:%Y-%m-%d}", "",
         f"Labeled jobs compared: {n}. Agree: {c['agree']}. Wrong keeps (labeled no, kept): {c['wrong keep']}. "
         f"Wrong drops (labeled yes, dropped): {c['wrong drop']}."
         + (f" Not scored: {c['not scored']}." if c["not scored"] else "")
         + (f" Unknown postings: {c['unknown posting']}." if c["unknown posting"] else ""), ""]
    bad = [r for r in rows if r["label"] != r["decided"]]
    if bad:
        L += ["| Label | Decided | By | Job | Reason | Note |", "|---|---|---|---|---|---|"]
        for r in sorted(bad, key=lambda r: (r["label"] != "yes", r["by"])):
            L.append(f"| {r['label']} | {r['decided']} | {r['by']} | {r['title']} — {r['company']} | "
                     f"{(r['why'] or '').replace('|', '/')[:140]} | {r['note']} |".replace("\n", " "))
    text = "\n".join(L) + "\n"
    out = cfg.data_dir / "calibration" / f"report-{pid}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    return {"counts": dict(c), "rows": rows, "text": text, "path": str(out)}
