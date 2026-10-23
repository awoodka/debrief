"""Canonical URLs and story ids (brief 5.1 and 5.2).

canonical_url() makes URL variants of the same page compare equal. story_id() names the artifact
a URL points at:

  arxiv:<id>                     arxiv.org abs/pdf/html, export.arxiv.org, alphaxiv, hf.co/papers
  hf-model:<org>/<name>          huggingface.co/<org>/<name>
  hf-space:<org>/<name>          huggingface.co/spaces/<org>/<name>
  hf-dataset:<org>/<name>        huggingface.co/datasets/<org>/<name>
  gh-release:<owner>/<repo>@<tag>
  gh-repo:<owner>/<repo>
  pkg:pypi/<name>[@<version>]    pypi.org/project/...
  pkg:npm/<name>[@<version>]     npmjs.com/package/...
  url:<16 hex of sha1(canonical url)>   anything else

Ids are lowercase (GitHub and Hugging Face names are case-insensitive) except a release tag.
Short links (t.co, bit.ly) need a network round trip to resolve, so that happens where mentions
are attached (M2), not here.
"""
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {"ref", "ref_src", "ref_url", "fbclid", "gclid", "dclid", "msclkid", "mc_cid",
                   "mc_eid", "igshid", "_hsenc", "_hsmi", "mkt_tok", "yclid", "twclid", "si"}
TRACKING_PREFIXES = ("utm_",)
X_HOSTS = {"twitter.com", "mobile.twitter.com", "x.com", "mobile.x.com"}

ARXIV_ID = r"(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[a-z]{2})?/\d{7})"
_ARXIV_PATH = re.compile(rf"^/(?:abs|pdf|html|format)/{ARXIV_ID}(?:v\d+)?(?:\.pdf)?/?$", re.I)
_ARXIV_ANY = re.compile(rf"(?<![\w.]){ARXIV_ID}(?:v\d+)?(?![\w])", re.I)
_HF_RESERVED = {"papers", "spaces", "datasets", "models", "blog", "docs", "api", "learn", "join",
                "login", "settings", "pricing", "organizations", "collections", "posts", "tasks",
                "chat", "enterprise", "new", "search", "changelog", "inference", "storage"}
_GH_RESERVED = {"about", "features", "orgs", "topics", "trending", "sponsors", "marketplace",
                "settings", "login", "explore", "collections", "events", "apps", "enterprise",
                "pricing", "security", "customer-stories", "readme", "site", "search", "pulls",
                "issues", "notifications", "new", "organizations", "solutions", "resources"}


def canonical_url(url):
    """Lowercase scheme and host, drop www./m., default ports, fragments, tracking params, and a
    trailing slash; https for http; x.com for twitter.com. Returns "" for non-http(s) input."""
    url = (url or "").strip()
    try:
        p = urlsplit(url)
        port = p.port                 # raises on a malformed port ("localhost:8080**" in a page's text)
    except ValueError:
        return ""
    if p.scheme.lower() not in ("http", "https") or not p.hostname:
        return ""
    host = p.hostname.lower()
    for prefix in ("www.", "m."):
        if host.startswith(prefix) and host.count(".") >= 2:
            host = host[len(prefix):]
    if host in X_HOSTS:
        host = "x.com"
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS and not k.lower().startswith(TRACKING_PREFIXES)]
    path = re.sub(r"/{2,}", "/", p.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit(("https", netloc, path, urlencode(query), ""))


def arxiv_id(text):
    """The first arXiv id in a string (version stripped), or None."""
    m = _ARXIV_ANY.search(text or "")
    return m.group(1).lower() if m else None


def _parts(cu):
    p = urlsplit(cu)
    return p.hostname or "", [s for s in p.path.split("/") if s]


def story_id(url):
    """The story id for an artifact URL (see the module docstring). "" for an unusable URL."""
    cu = canonical_url(url)
    if not cu:
        return ""
    host, seg = _parts(cu)
    path = urlsplit(cu).path

    if host in ("arxiv.org", "export.arxiv.org"):
        m = _ARXIV_PATH.match(path)
        if m:
            return f"arxiv:{m.group(1).lower()}"
    if host == "alphaxiv.org" and len(seg) >= 2 and seg[0] in ("abs", "overview"):
        aid = arxiv_id(seg[1])
        if aid:
            return f"arxiv:{aid}"

    if host == "huggingface.co" and seg:
        if seg[0] == "papers" and len(seg) >= 2 and arxiv_id(seg[1]):
            return f"arxiv:{arxiv_id(seg[1])}"
        if seg[0] in ("spaces", "datasets") and len(seg) >= 3:
            return f"hf-{seg[0][:-1]}:{seg[1].lower()}/{seg[2].lower()}"
        if seg[0] not in _HF_RESERVED and len(seg) >= 2:
            return f"hf-model:{seg[0].lower()}/{seg[1].lower()}"

    if host == "github.com" and len(seg) >= 2 and seg[0].lower() not in _GH_RESERVED:
        repo = f"{seg[0].lower()}/{seg[1].lower().removesuffix('.git')}"
        if len(seg) >= 5 and seg[2] == "releases" and seg[3] == "tag":
            return f"gh-release:{repo}@{'/'.join(seg[4:])}"
        return f"gh-repo:{repo}"

    if host == "pypi.org" and len(seg) >= 2 and seg[0] == "project":
        name = re.sub(r"[-_.]+", "-", seg[1]).lower()
        return f"pkg:pypi/{name}" + (f"@{seg[2]}" if len(seg) >= 3 else "")
    if host == "npmjs.com" and len(seg) >= 2 and seg[0] == "package":
        rest = seg[1:]
        name = "/".join(rest[:2]) if rest[0].startswith("@") else rest[0]
        tail = rest[2:] if rest[0].startswith("@") else rest[1:]
        version = tail[1] if len(tail) >= 2 and tail[0] == "v" else None
        return f"pkg:npm/{name.lower()}" + (f"@{version}" if version else "")

    return "url:" + hashlib.sha1(cu.encode()).hexdigest()[:16]
