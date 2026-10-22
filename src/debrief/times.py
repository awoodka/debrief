"""Normalize the date formats sources use into one: ISO 8601 UTC, "YYYY-MM-DDTHH:MM:SSZ"."""
import calendar
import email.utils
import time
from datetime import date, datetime, timezone


def iso(value):
    """ISO 8601 UTC from a feedparser struct_time, datetime, date, unix seconds, ISO 8601 string
    (with or without Z / offset / fraction / time), or RFC 2822 string. None if unparseable."""
    if value in (None, ""):
        return None
    dt = None
    if isinstance(value, time.struct_time):
        dt = datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
    elif isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value, tz=timezone.utc)
    else:
        s = str(value).strip()
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = email.utils.parsedate_to_datetime(s)
            except (TypeError, ValueError, IndexError):
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(value):
    """datetime (UTC) from an iso() string."""
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
