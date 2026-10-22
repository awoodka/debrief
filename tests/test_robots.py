from debrief.robots import Robots

GITHUB = """
User-agent: Googlebot
Allow: /

User-agent: *
Allow: /*?tab=achievements&achievement=*
Disallow: /*.atom$
Disallow: /*/*/commits/
Disallow: /search$
"""


def test_wildcard_and_end_anchor():
    r = Robots(GITHUB, "debrief")
    assert not r.allowed("https://github.com/vllm-project/vllm/releases.atom")
    assert r.allowed("https://github.com/vllm-project/vllm/releases.atom?x=1")   # $ anchors at the end
    assert not r.allowed("https://github.com/a/b/commits/main")
    assert r.allowed("https://github.com/trending?since=daily")
    assert not r.allowed("https://github.com/search")
    assert r.allowed("https://github.com/search/advanced")


def test_longest_match_wins_and_allow_wins_ties():
    r = Robots("User-agent: *\nDisallow: /pypi/*/json\nAllow: /pypi/anthropic/json\n", "debrief")
    assert r.allowed("https://pypi.org/pypi/anthropic/json")
    assert not r.allowed("https://pypi.org/pypi/openai/json")
    tie = Robots("User-agent: *\nDisallow: /x\nAllow: /x\n", "debrief")
    assert tie.allowed("https://a.test/x")


def test_own_group_replaces_star_group():
    text = "User-agent: *\nDisallow: /\n\nUser-agent: debrief\nDisallow: /private\n"
    r = Robots(text, "debrief")
    assert r.allowed("https://a.test/public") and not r.allowed("https://a.test/private")


def test_blanket_disallow_and_empty_disallow():
    assert not Robots("User-agent: *\nDisallow: /\n", "debrief").allowed("https://lobste.rs/t/ai.json")
    assert Robots("User-agent: *\nDisallow:\n", "debrief").allowed("https://a.test/anything")
    assert Robots("", "debrief").allowed("https://a.test/anything")
    assert Robots("User-agent: *\nDisallow: /\n", "debrief").allowed("https://a.test/robots.txt")


def test_consecutive_user_agents_share_a_group():
    r = Robots("User-agent: a\nUser-agent: *\nDisallow: /feeds/videos.xml\n", "debrief")
    assert not r.allowed("https://www.youtube.com/feeds/videos.xml?channel_id=X")
