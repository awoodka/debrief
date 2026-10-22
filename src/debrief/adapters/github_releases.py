"""GitHub releases through the REST API (robots.txt disallows the .atom feeds). One request per
tracked repo, conditional on its ETag, which GitHub doesn't count against the rate limit when
nothing changed. Drafts are skipped; prereleases are kept and flagged for triage."""
import json

from .. import sanitize, times, urls
from ..store import raw
from .base import Adapter, Item


class GithubReleases(Adapter):
    name = "github_releases"

    def fetch(self, ctx, source):
        for repo, url in urls.expand(source.url, source.each):
            ctx.get(source, url, each=repo)

    def parse(self, record, source, opts):
        if not raw.ok(record):
            return []
        data = json.loads(raw.body(record))
        repo = record.get("each") or ""
        out = []
        for r in data if isinstance(data, list) else []:
            if r.get("draft"):
                continue
            tag = r.get("tag_name") or ""
            name = (r.get("name") or "").strip()
            title = f"{repo} {tag}" + (f": {name}" if name and name != tag else "")
            out.append(Item(
                item_key=str(r["id"]), role="anchor", story_id=f"gh-release:{repo.lower()}@{tag}",
                url=r.get("html_url") or "", title=sanitize.text(title, opts["title_max_chars"]),
                summary=sanitize.text(r.get("body"), opts["summary_max_chars"]),
                published_at=times.iso(r.get("published_at") or r.get("created_at")),
                kind=source.kind or "release", org=source.org,
                code_urls=(f"https://github.com/{repo}",),
                extra={"repo": repo, "tag": tag, "prerelease": bool(r.get("prerelease"))}))
        return out
