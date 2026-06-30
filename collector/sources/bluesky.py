"""Bluesky author feeds via the auth-free public AppView. Researcher-chatter (in-field) signal.

Real researcher-chatter comes via searchPosts in step 3 (needs the free app password). For now this
pulls author feeds for any configured handles; an empty/incorrect list simply contributes nothing."""
from ..http_util import get
from ..config import BLUESKY_HANDLES
from ..schema import make_item

API = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"


def _post_url(post, handle):
    uri = post.get("uri", "")
    rkey = uri.rsplit("/", 1)[-1] if uri else ""
    return f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else ""


def fetch(window_days, handles=None, log=print):
    handles = handles if handles is not None else BLUESKY_HANDLES
    if not handles:
        log("  bluesky: no handles configured — activates in step 3 (searchPosts + app password)")
        return []
    items, failed = [], 0
    for h in handles:
        try:
            data = get(API, params={"actor": h, "limit": 30}, timeout=30).json()
        except Exception as e:  # noqa: BLE001
            failed += 1
            log(f"  bluesky[{h}]: ERROR {e}")
            continue
        for fi in data.get("feed", []):
            post = fi.get("post", {})
            rec = post.get("record", {})
            text = rec.get("text", "")
            items.append(make_item(
                source="bluesky", type="social",
                title=text[:120], url=_post_url(post, h), summary=text, author=h,
                published=rec.get("createdAt"),
                raw_signal={"likes": post.get("likeCount", 0),
                            "reposts": post.get("repostCount", 0), "axis": "in_field"},
            ))
    log(f"  bluesky: {len(items)} posts from {len(handles)} handles"
        + (f" ({failed} failed)" if failed else ""))
    return items
