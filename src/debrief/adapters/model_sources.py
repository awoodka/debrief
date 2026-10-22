"""Other model-release sources: OpenRouter's model list, a GitHub org's new repos, and the LMArena
leaderboard.

openrouter: a model id new to the seen-set is a launch (open or closed, served by API). Variants
like ":free" fold into the base id. A model OpenRouter links to a Hugging Face repo takes that
repo's story id, so the API launch and the weights release become one story.

github_org: an org's newest repos (DeepSeek ships major code there); forks and archived repos are
skipped.

leaderboard: LMArena's published leaderboard (the lmarena-ai/leaderboard-dataset on Hugging Face).
A model entering the overall top N for the first time is a story; staying there isn't. On the first
run everything currently in the top N is only recorded, since "entering" needs a previous run.
"""
import json
import re
from urllib.parse import quote

from .. import canon, sanitize, times
from ..store import raw
from .base import Adapter, Item
from .hf_hub import model_story_id

_VARIANT_NAME = re.compile(r"\s*\((free|beta|thinking|extended|nitro|floor)\)$", re.I)


class OpenRouter(Adapter):
    name = "openrouter"

    def fetch(self, ctx, source):
        ctx.get(source, source.url)

    def parse(self, record, source, opts):
        if not raw.ok(record):
            return []
        out = []
        for m in json.loads(raw.body(record)).get("data") or []:
            base = (m.get("id") or "").split(":")[0]
            if not base:
                continue
            hf = (m.get("hugging_face_id") or "").strip()
            page = f"https://openrouter.ai/{base}"
            out.append(Item(
                item_key=base, role="anchor", story_id=model_story_id(hf) if "/" in hf else canon.story_id(page),
                url=page, title=sanitize.text(_VARIANT_NAME.sub("", m.get("name") or base), opts["title_max_chars"]),
                summary=sanitize.text(m.get("description"), opts["summary_max_chars"]),
                published_at=times.iso(m.get("created")), kind=source.kind or "release", org=base.split("/")[0],
                artifact_urls=(f"https://huggingface.co/{hf}",) if "/" in hf else (),
                extra={"context_length": m.get("context_length"), "hugging_face_id": hf or None,
                       "pricing": {k: (m.get("pricing") or {}).get(k) for k in ("prompt", "completion")}}))
        return out


class GithubOrg(Adapter):
    name = "github_org"

    def fetch(self, ctx, source):
        ctx.get(source, source.url)

    def parse(self, record, source, opts):
        if not raw.ok(record):
            return []
        out = []
        for r in json.loads(raw.body(record)) or []:
            if r.get("fork") or r.get("archived") or not r.get("full_name"):
                continue
            out.append(Item(
                item_key=r["full_name"], role="anchor", story_id=f"gh-repo:{r['full_name'].lower()}",
                url=r.get("html_url") or "", title=sanitize.text(r["full_name"], opts["title_max_chars"]),
                summary=sanitize.text(r.get("description"), opts["summary_max_chars"]),
                published_at=times.iso(r.get("created_at")), kind=source.kind or "release", org=source.org,
                code_urls=(r.get("html_url"),) if r.get("html_url") else (),
                signals={"github_stars": r.get("stargazers_count") or 0}))
        return out


class Leaderboard(Adapter):
    name = "leaderboard"
    windowed = False
    first_run_seen = True

    def fetch(self, ctx, source):
        ctx.get(source, source.url, conditional=False)

    def parse(self, record, source, opts):
        if not raw.ok(record):
            return []
        top_n = source.options.get("top_n", 5)
        rows = [x.get("row") or {} for x in json.loads(raw.body(record)).get("rows") or []]
        top = sorted((r for r in rows if r.get("category") == "overall" and (r.get("rank") or 99) <= top_n),
                     key=lambda r: (r["rank"], r.get("model_name") or ""))
        out = []
        for r in top:
            name, rank = r.get("model_name") or "", int(r["rank"])
            out.append(Item(
                item_key=f"top{top_n}:{name}", role="anchor",
                url=f"https://lmarena.ai/leaderboard?model={quote(name)}",
                title=sanitize.text(f"{name} enters the LMArena top {top_n} at #{rank}", opts["title_max_chars"]),
                summary=f"Arena rating {r.get('rating', 0):.0f} from {int(r.get('vote_count') or 0)} votes"
                        + (f"; {r['organization']}" if r.get("organization") else ""),
                published_at=times.iso(r.get("leaderboard_publish_date")), kind=source.kind or "leaderboard",
                org=r.get("organization"),
                signals={"lmarena_rank": rank, "lmarena_rating": r.get("rating") or 0}))
        return out
