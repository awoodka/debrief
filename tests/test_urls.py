from datetime import datetime, timezone

from debrief import urls

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def test_each_expands_one_url_per_entry():
    assert urls.expand("https://github.com/{each}/releases.atom", ["a/b", "c/d"], NOW) == [
        ("a/b", "https://github.com/a/b/releases.atom"), ("c/d", "https://github.com/c/d/releases.atom")]


def test_date_placeholders_and_literal_braces():
    got = urls.fill("x?d={yesterday}..{today}&w={week_ago}&since={since_24h}&q={ posts }", NOW)
    assert got == "x?d=2026-10-21..2026-10-22&w=2026-10-15&since=1792584000&q={ posts }"
