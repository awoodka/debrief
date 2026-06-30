"""Conference deadlines from huggingface/ai-deadlines (per-venue YAML files).
Curated major venues; keeps only conferences with an upcoming deadline."""
from datetime import datetime

import yaml

from ..http_util import get
from ..config import EVENTS_BASE, EVENTS_VENUES
from ..schema import make_item


def _parse(date_str):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime((date_str or "").strip(), fmt)
        except ValueError:
            continue
    return None


def fetch(log=print):
    now = datetime.now()
    items, failed = [], 0
    for venue in EVENTS_VENUES:
        try:
            data = yaml.safe_load(get(EVENTS_BASE + venue + ".yml", timeout=30).text)
        except Exception:  # noqa: BLE001 — per-venue fail-soft
            failed += 1
            continue
        for conf in data if isinstance(data, list) else []:
            if not isinstance(conf, dict):
                continue
            upcoming = []
            for dl in conf.get("deadlines", []) or []:
                d = _parse(str(dl.get("date", "")))
                if d and d >= now:
                    upcoming.append((d, dl.get("label") or dl.get("type") or "deadline"))
            if not upcoming:
                continue
            upcoming.sort()
            nd, nlabel = upcoming[0]
            place = ", ".join(x for x in [conf.get("city"), conf.get("country")] if x)
            items.append(make_item(
                source="events", type="event",
                title=f"{conf.get('title', '')} {conf.get('year', '')}".strip(),
                url=conf.get("link", ""),
                summary=f"{conf.get('full_name', '')} — next: {nlabel} {nd.date()}"
                        + (f" · {place}" if place else ""),
                raw_signal={"next_deadline": nd.isoformat(), "next_label": nlabel, "place": place},
            ))
    log(f"  events: {len(items)} conferences with upcoming deadlines"
        + (f" ({failed} venues failed)" if failed else ""))
    return items
