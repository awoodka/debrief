"""Deterministic noise / promo / off-topic filter (work order P0-4). No LLM.

Flags marketing fluff so it never reaches the council. Flagged items STAY in the raw digest_input with
a `noise` reason (auditable — never silently vanished); only the agent digest excludes them.
"""
import re

_PROMO = re.compile(
    r"\b(early.?bird|pricing ends|price goes up|save up to|\d+% off|\d+ ?% discount|limited.time|"
    r"ends (tonight|today|soon|this week)|buy now|coupon|promo code|on sale|flash sale|"
    r"black friday|cyber monday|deal of the|last chance|don.?t miss out|lifetime deal|use code)\b", re.I)
_LIFESTYLE = re.compile(
    r"(\b\d+ ways\b|\b\d+ (best )?tips\b|\btips for\b|\b(parents|families|students|teachers) use\b|"
    r"\bhow (parents|families|students|teachers|you) "
    r"(can )?use\b|\bavoid jet.?lag\b|productivity hacks|life ?hacks|morning routine|\bfor parents\b|"
    r"\bfor families\b|best gifts|holiday (guide|gift)|get the most out of)", re.I)
_GAMING = re.compile(
    r"\b(geforce now|game ?pass|steam (sale|deal|game|next fest)|now on steam|free.to.play|"
    r"season pass|humble bundle)\b", re.I)
_EVENT_PROMO = re.compile(
    r"\b(register (now|today)|save your seat|get your ticket|early.?bird ticket|"
    r"book your (spot|seat)|reserve your (spot|seat))\b", re.I)

_RULES = [("promo/pricing", _PROMO), ("gaming/consumer", _GAMING), ("event promo", _EVENT_PROMO)]


def classify(it):
    if it["type"] == "paper":          # papers are substance — never promo; exempt from the noise filter
        return None
    text = (it.get("title", "") or "") + " . " + (it.get("summary", "") or "")[:200]
    for reason, rx in _RULES:
        if rx.search(text):
            return reason
    if _LIFESTYLE.search(text):
        return "marketing/lifestyle"
    return None


def mark(items):
    """Set it['noise']=reason on fluff items; return the flagged list."""
    dropped = []
    for it in items:
        reason = classify(it)
        if reason:
            it["noise"] = reason
            dropped.append(it)
    return dropped
