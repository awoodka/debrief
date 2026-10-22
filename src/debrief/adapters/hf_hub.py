"""Model releases on the Hugging Face Hub: the watched labs' new repos, and trending models.

Both name a model's story hf-model:<org>/<family>, where the family is the repo name with
quantization and format suffixes removed (DeepSeek-V4.1-Flash and DeepSeek-V4.1-Flash-GGUF are
one story). A repo tagged as a derivative of another model (base_model:quantized, :finetune,
:adapter, :merge) is recorded as seen, never a release.

hf_org_watch: each watched org's newest repos (the age guard keeps old ones out on a first run).
hf_trending: the trending list. A model counts as a release only if it was created in the last
hf_trending.max_age_days days and its author is an organization; the account type is looked up
(one request per surviving author) and kept in the list record's meta, so parsing stays pure.
"""
import json
import re
from datetime import timedelta
from urllib.parse import quote

from .. import sanitize, times, urls
from ..store import raw
from .base import Adapter, Item

_FORMAT = re.compile(r"[-_.](gguf|nvfp4|mxfp4|fp8|fp4|int4|int8|w4a16|w8a8|awq|gptq|mlx(?:-\d+bit)?|exl2|onnx|bf16|fp16|\d+bit)$", re.I)
_DERIVED = ("base_model:quantized:", "base_model:finetune:", "base_model:adapter:", "base_model:merge:")


def family(name):
    """Repo name without its trailing format suffixes, and the suffixes removed."""
    removed = []
    while (m := _FORMAT.search(name)):
        removed.insert(0, m.group(1))
        name = name[: m.start()]
    return name, removed


def model_story_id(model_id):
    org, _, name = model_id.partition("/")
    return f"hf-model:{org.lower()}/{family(name)[0].lower()}"


def is_derivative(m):
    return any(t.startswith(_DERIVED) for t in m.get("tags") or [])


def model_item(m, source, opts, role, reason=None):
    mid = m.get("id") or m.get("modelId") or ""
    org, _, name = mid.partition("/")
    fam, removed = family(name)
    lic = next((t.split(":", 1)[1] for t in m.get("tags") or [] if t.startswith("license:")), None)
    bits = [m.get("pipeline_tag") or "model", m.get("library_name"), f"license {lic}" if lic else None]
    return Item(
        item_key=mid, role=role, story_id=model_story_id(mid), url=f"https://huggingface.co/{mid}",
        title=sanitize.text(f"{org}/{fam}", opts["title_max_chars"]),
        summary=" · ".join(b for b in bits if b), published_at=times.iso(m.get("createdAt")),
        kind=source.kind or "release", org=org,
        signals={"hf_likes": m.get("likes") or 0, "hf_downloads": m.get("downloads") or 0,
                 "hf_trending": m.get("trendingScore") or 0},
        extra={"model_id": mid, "pipeline_tag": m.get("pipeline_tag"), "format": removed or None,
               **({"reason": reason} if reason else {})})


class HfOrgWatch(Adapter):
    name = "hf_org_watch"

    def fetch(self, ctx, source):
        for org, url in urls.expand(source.url, source.each):
            ctx.get(source, url, each=org)

    def parse(self, record, source, opts):
        if not raw.ok(record):
            return []
        return [model_item(m, source, opts, "seen" if is_derivative(m) else "anchor",
                           "derivative" if is_derivative(m) else None)
                for m in json.loads(raw.body(record)) or []]


class HfTrending(Adapter):
    name = "hf_trending"
    windowed = False      # its own rule instead: created within max_age_days

    def fetch(self, ctx, source):
        o = {**ctx.opts["hf_trending"], **source.options}
        rec = ctx.get(source, source.url, meta={"kind": "list"}, conditional=False)
        if not raw.ok(rec):
            return
        cutoff = times.iso(ctx.now.timestamp() - o["max_age_days"] * 86400)
        authors = sorted({(m.get("id") or "").split("/")[0] for m in json.loads(raw.body(rec)) or []
                          if not is_derivative(m) and (times.iso(m.get("createdAt")) or "") >= cutoff})
        orgs = {}
        for a in authors:
            r = ctx.get(source, f"https://huggingface.co/api/organizations/{quote(a)}/overview",
                        meta={"kind": "account", "author": a}, conditional=False)
            orgs[a] = {200: True, 404: False}.get(r.get("status"))     # 404: a personal account
        rec["meta"]["orgs"] = orgs

    def parse(self, record, source, opts):
        meta = record.get("meta") or {}
        if meta.get("kind") != "list" or not raw.ok(record):
            return []
        o = opts["hf_trending"]
        cutoff = times.parse(record["fetched_at"]) - timedelta(days=o["max_age_days"])
        orgs = meta.get("orgs") or {}
        out = []
        for m in json.loads(raw.body(record)) or []:
            author = (m.get("id") or "").split("/")[0]
            created = times.iso(m.get("createdAt"))
            if is_derivative(m):
                reason = "derivative"
            elif not created or times.parse(created) < cutoff:
                reason = "older than max_age_days"
            elif orgs.get(author) is not True:
                reason = "not an organization account" if orgs.get(author) is False else "account type unknown"
            else:
                reason = None
            out.append(model_item(m, source, opts, "seen" if reason else "anchor", reason))
        return out
