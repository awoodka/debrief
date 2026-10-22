"""The raw store: every HTTP response a run gets, kept exactly as received, before any parsing.

  raw/<run date>/<source_id>/<run_id>.jsonl.gz     one JSON record per line, in request order

A record looks like
  {"v": 1, "seq": 0, "source_id": ..., "run_id": ..., "url": ..., "each": ..., "fetched_at": ...,
   "status": 200, "error": "", "headers": {"etag": ..., "last-modified": ..., "content-type": ...},
   "meta": {...what the adapter needs to parse it...}, "body": "<text>"}
with "body_b64" instead of "body" when the bytes aren't valid UTF-8. A record's reference is
"raw/<date>/<source>/<run_id>.jsonl.gz#<seq>". `collect --replay <date>` re-parses these files
in run order with no network.
"""
import base64
import gzip
import json
import shutil
from datetime import date as Date, timedelta
from pathlib import Path

KEPT_HEADERS = ("etag", "last-modified", "content-type", "content-length", "date")


def make_record(source_id, run_id, seq, fetched, fetched_at, each=None, meta=None):
    """A raw record from an http.Fetched (body kept byte-exact)."""
    rec = {"v": 1, "seq": seq, "source_id": source_id, "run_id": run_id, "url": fetched.url,
           "final_url": fetched.final_url, "each": each, "fetched_at": fetched_at,
           "status": fetched.status, "error": fetched.error,
           "headers": {k: fetched.headers[k] for k in KEPT_HEADERS if k in fetched.headers},
           "meta": meta or {}}
    try:
        rec["body"] = fetched.content.decode("utf-8")
    except UnicodeDecodeError:
        rec["body_b64"] = base64.b64encode(fetched.content).decode("ascii")
    return rec


def body(record):
    """The record's body as bytes."""
    if "body_b64" in record:
        return base64.b64decode(record["body_b64"])
    return (record.get("body") or "").encode("utf-8")


def ok(record):
    return 200 <= record.get("status", 0) < 300


def rel_path(run_date, source_id, run_id):
    return Path("raw") / run_date / source_id / f"{run_id}.jsonl.gz"


def write(data_dir, run_date, source_id, run_id, records):
    """Write one source's records for one run. Returns each record's ref, in order."""
    rel = rel_path(run_date, source_id, run_id)
    path = Path(data_dir) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(path)
    return [f"{rel.as_posix()}#{r['seq']}" for r in records]


def read_file(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def runs_for_day(data_dir, run_date):
    """{run_id: {source_id: (rel_path, records)}} for every run filed under run_date."""
    root = Path(data_dir) / "raw" / run_date
    out = {}
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*/*.jsonl.gz")):
        run_id = path.name.removesuffix(".jsonl.gz")
        rel = path.relative_to(data_dir).as_posix()
        out.setdefault(run_id, {})[path.parent.name] = (rel, read_file(path))
    return dict(sorted(out.items()))


def prune(data_dir, keep_days, today):
    """Delete raw/<date> folders older than keep_days. Returns the dates removed."""
    root = Path(data_dir) / "raw"
    cutoff = Date.fromisoformat(today) - timedelta(days=keep_days)
    removed = []
    for d in sorted(root.glob("????-??-??")) if root.is_dir() else []:
        try:
            day = Date.fromisoformat(d.name)
        except ValueError:
            continue
        if day < cutoff:
            shutil.rmtree(d)
            removed.append(d.name)
    return removed
