"""The jobs profile: what the pipeline filters, pre-scores and scores against (docs/jobs/PLAN.md,
Revision 2). It holds personal details, so it lives on the server, never in the repo:

  <data>/jobs/profile/profile-0001.json, profile-0002.json, ...   every saved version, never changed
  <data>/jobs/profile/history.jsonl                                one line per save: version, time, id, note

A profile's id is a hash of its canonical JSON. The scoring call's cache is keyed by it, so an edit
rescores and an unchanged re-save doesn't. Saving the same content again writes nothing.
"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone

import jsonschema

from ..config import REPO

SCHEMA = REPO / "schemas" / "jobs_profile.schema.json"
_VERSION = re.compile(r"^profile-(\d{4,})\.json$")


class ProfileError(Exception):
    pass


def _validator():
    return jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text()), format_checker=jsonschema.FormatChecker())


def problems(doc):
    """Every problem with a profile, as "where: what" lines (empty = valid). Schema first, then the
    checks a schema can't express."""
    out = []
    for e in sorted(_validator().iter_errors(doc), key=lambda e: list(e.path)):
        out.append(f"{'/'.join(str(p) for p in e.path) or '(top)'}: {e.message[:200]}")
    if out:
        return out
    names = [p["name"].lower() for p in doc["position_types"]]
    for n in sorted({n for n in names if names.count(n) > 1}):
        out.append(f"position_types: {n!r} appears twice")
    excluded = {w.lower() for w in doc["filters"]["exclude_titles"]}
    for p in doc["position_types"]:
        for t in p["titles"]:
            if t.lower() in excluded:
                out.append(f"position_types/{p['name']}: {t!r} is also in filters/exclude_titles")
    seen = {}
    for c in doc["companies"]:
        k = c["name"].strip().lower()
        if k in seen:
            out.append(f"companies: {c['name']!r} is listed twice")
        seen[k] = True
    blocked = {b.lower() for b in doc["filters"]["blocklist"]}
    for c in doc["companies"]:
        if c["name"].lower() in blocked:
            out.append(f"companies: {c['name']!r} is also on filters/blocklist")
    r = doc["ranking"]
    if r["top_n"] > r["max_candidates"]:
        out.append(f"ranking: top_n ({r['top_n']}) is more than max_candidates ({r['max_candidates']}), so it can't be filled")
    if not doc["filters"]["locations"]["remote_us"] and not doc["filters"]["locations"]["places"]:
        out.append("filters/locations: no remote and no places, so every job would be filtered out")
    return out


def canonical(doc):
    return json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def profile_id(doc):
    return hashlib.sha256(canonical(doc).encode()).hexdigest()[:12]


def _dir(cfg):
    return cfg.data_dir / "profile"


def versions(cfg):
    """[(version number, path)] oldest first."""
    d = _dir(cfg)
    found = [(int(m.group(1)), p) for p in d.glob("profile-*.json") if (m := _VERSION.match(p.name))] if d.is_dir() else []
    return sorted(found)


def latest(cfg):
    """(version, doc), or (0, None) when no profile has been saved."""
    v = versions(cfg)
    if not v:
        return 0, None
    n, path = v[-1]
    return n, json.loads(path.read_text())


def save(cfg, doc, note="", now=None):
    """Check and save a new version. Returns (version, saved): saved is False when the content is
    the same as the latest version's (nothing written). Raises ProfileError listing every problem."""
    bad = problems(doc)
    if bad:
        raise ProfileError("the profile has problems:\n  " + "\n  ".join(bad))
    n, current = latest(cfg)
    if current is not None and canonical(current) == canonical(doc):
        return n, False
    d = _dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    version = n + 1
    path = d / f"profile-{version:04d}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    hist = d / "history.jsonl"
    with open(hist, "a") as f:
        f.write(json.dumps({"version": version, "saved_at": stamp, "id": profile_id(doc), "note": note}) + "\n")
    os.chmod(hist, 0o600)
    return version, True
