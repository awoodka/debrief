"""Product Hunt — new product/startup launches via the GraphQL API (needs PRODUCTHUNT_TOKEN).
No-op / fail-soft if the token isn't set."""
import os

from ..http_util import session
from ..schema import make_item

API = "https://api.producthunt.com/v2/api/graphql"
QUERY = """query { posts(order: VOTES, first: 25) { edges { node {
  name tagline url votesCount commentsCount createdAt
  topics(first: 5) { edges { node { name } } } } } } }"""


def fetch(log=print):
    token = os.environ.get("PRODUCTHUNT_TOKEN")
    if not token:
        log("  producthunt: no PRODUCTHUNT_TOKEN set — skipping (optional)")
        return []
    try:
        r = session().post(API, json={"query": QUERY},
                           headers={"Authorization": f"Bearer {token}"}, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001
        log(f"  producthunt: ERROR {e}")
        return []
    items = []
    for edge in data.get("data", {}).get("posts", {}).get("edges", []):
        n = edge.get("node", {})
        topics = [t["node"]["name"] for t in n.get("topics", {}).get("edges", []) if t.get("node")]
        items.append(make_item(
            source="producthunt", type="product", title=n.get("name", ""),
            url=n.get("url", ""), summary=n.get("tagline", ""), tags=topics,
            published=n.get("createdAt"),
            raw_signal={"ph_votes": n.get("votesCount", 0),
                        "ph_comments": n.get("commentsCount", 0), "axis": "in_field"},
        ))
    log(f"  producthunt: {len(items)} launches")
    return items
