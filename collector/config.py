"""Static config for the collector. No secrets here — those live in .env (step 3+)."""
from pathlib import Path

# The locked six arXiv categories.
ARXIV_CATEGORIES = ["cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.NE", "stat.ML"]

# Split windows: papers get a long window so traction can mature; news stays tight.
PAPER_WINDOW_DAYS = 14
NEWS_WINDOW_DAYS = 4

# Safety bound on arXiv volume per run (overridable via CLI). Run time is no concern, but a
# cap keeps the first inspection runs sane. Raise once the pipeline is trusted.
MAX_ARXIV_RESULTS = 2500

# Polite HTTP.
USER_AGENT = "debrief/0.1 (personal research digest; +https://github.com/awoodka/debrief)"
ARXIV_MIN_INTERVAL_SEC = 3.0  # arXiv asks for >= 3s between requests

# Lab / company blogs (RSS). type=lab_news. Labs without official RSS (Anthropic, Meta, Mistral, xAI)
# come via the Olshansk/rss-feeds community feeds so major RELEASES aren't missed.
_OLSHANSK = "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/"
# (label, url, axis) — release/announcement feeds = mainstream (public reveal); research/eng = in_field.
LAB_FEEDS = [
    ("openai",          "https://openai.com/news/rss.xml",              "mainstream"),
    ("anthropic_news",  _OLSHANSK + "feed_anthropic_news.xml",          "mainstream"),
    ("anthropic_eng",   _OLSHANSK + "feed_anthropic_engineering.xml",   "in_field"),
    ("google_gemini",   "https://blog.google/products/gemini/rss/",     "mainstream"),
    ("deepmind",        "https://deepmind.google/blog/rss.xml",         "mainstream"),
    ("google_research", "https://research.google/blog/rss/",            "in_field"),
    ("meta_ai",         _OLSHANSK + "feed_meta_ai.xml",                 "mainstream"),
    ("mistral",         _OLSHANSK + "feed_mistral.xml",                 "mainstream"),
    ("xai",             _OLSHANSK + "feed_xainews.xml",                 "mainstream"),
    ("nvidia",          "https://blogs.nvidia.com/feed/",               "mainstream"),
    ("huggingface",     "https://huggingface.co/blog/feed.xml",         "mainstream"),
]

# General tech press (RSS) — mainstream-axis signal.
NEWS_FEEDS = [
    ("techcrunch_ai",      "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("venturebeat_ai",     "https://venturebeat.com/category/ai/feed/"),
    ("mit_tech_review_ai", "https://www.technologyreview.com/topic/artificial-intelligence/feed"),
    ("stratechery",        "https://stratechery.com/feed/"),
]

# Startup / funding news (RSS) — the free, reliable substitute for LinkedIn-style startup monitoring.
STARTUP_FEEDS = [
    ("crunchbase_news",     "https://news.crunchbase.com/feed/"),
    ("techcrunch_funding",  "https://techcrunch.com/category/fundraising/feed/"),
    ("techcrunch_startups", "https://techcrunch.com/category/startups/feed/"),
    ("yc_blog",             "https://www.ycombinator.com/blog/rss/"),
]

# Newsletters (RSS). axis tags which side of the divergence axis they inform:
#   "in_field"   = researcher-facing (mild in-field visibility)
#   (mass-market newsletters TLDR AI / The Batch / AlphaSignal are web-archive scrapes — added in step 3)
NEWSLETTER_FEEDS = [
    ("import_ai",      "https://importai.substack.com/feed",         "in_field"),
    ("ahead_of_ai",    "https://magazine.sebastianraschka.com/feed", "in_field"),
    ("interconnects",  "https://www.interconnects.ai/feed",          "in_field"),
    ("latent_space",   "https://www.latent.space/feed",              "in_field"),
    ("last_week_in_ai","https://lastweekin.ai/feed",                 "in_field"),
    ("simon_willison", "https://simonwillison.net/atom/everything/", "in_field"),
]

# Hacker News (Algolia) — mainstream attention. Stories above the points floor in the news window.
HN_QUERIES = ["LLM", "language model", "open source AI", "AI model", "transformer", "AI agents", "AI funding"]
HN_POINTS_FLOOR = 40

# Reddit via public RSS (NO API key / app). Rate-limited by IP, so fetched spaced + fail-soft.
# top/day|week surfaces what the community upvoted (RSS omits the numeric score → discourse presence).
REDDIT_RSS = [
    ("reddit:LocalLLaMA",      "https://www.reddit.com/r/LocalLLaMA/top/.rss?t=day"),
    ("reddit:MachineLearning", "https://www.reddit.com/r/MachineLearning/top/.rss?t=week"),
    ("reddit:artificial",      "https://www.reddit.com/r/artificial/top/.rss?t=week"),
    ("reddit:startups",        "https://www.reddit.com/r/startups/top/.rss?t=week"),
    ("reddit:ycombinator",     "https://www.reddit.com/r/ycombinator/top/.rss?t=week"),
]
REDDIT_MIN_INTERVAL_SEC = 9.0  # Reddit RSS rate-limits hard; space requests well apart (fail-soft anyway)

# Reddit engagement is unavailable (RSS has no scores, .json is 403), so route by post KIND (H0-3).
# A post reaching a subreddit's top/day|week feed already carries community weight; these are the bases.
REDDIT_SUBSTANCE_BASE = {"research": 6.0, "model_drop": 3.0, "chatter": 0.0}
REDDIT_ATTENTION_BASE = {"research": 4.0, "model_drop": 5.0, "chatter": 3.0}

# Lobsters — HN-like tech discourse, clean RSS, no limits.
LOBSTERS_RSS = [
    ("lobsters:ai", "https://lobste.rs/t/ai.rss"),
]

# GitHub Trending (scrape; no API needed).
GITHUB_TRENDING_URLS = [
    "https://github.com/trending?since=daily",
    "https://github.com/trending?since=weekly",
]

# HF Daily Papers — loop the last N days to enrich more papers with upvotes.
HF_DAILY_DAYS = 7

# Events — huggingface/ai-deadlines (live fork). Split into per-venue files under src/data/conferences/.
EVENTS_BASE = "https://raw.githubusercontent.com/huggingface/ai-deadlines/main/src/data/conferences/"
EVENTS_VENUES = [
    "neurips", "icml", "iclr", "cvpr", "iccv", "eccv", "acl", "emnlp", "naacl", "aaai",
    "ijcai", "kdd", "sigir", "wacv", "colm", "rlc", "interspeech", "icassp", "www", "wsdm",
    "coling", "aistats", "uai", "corl", "icra", "iros",
]

# Bluesky author handles for researcher chatter. Empty for now — the real signal comes via
# searchPosts in step 3 (needs the app password). Wrong handles are harmless (fail-soft).
BLUESKY_HANDLES = []

# ---- Scoring & agent-digest (step 3b) ----------------------------------------------------------
# Divergence weights — v1 STARTING POINT; tune after the first real run. Fast in-field signals lead;
# citations are a lagging bonus. (hf_models/datasets/spaces, github_impls, citations are filled by the
# 3b-ii API enrichment; they default to 0 until then.)
# GEM scoring. Magnitudes are LOG-SCALED (lg = ln(1+x)). AXIS CONTRACT (pinned — do NOT re-mix):
#   in_field   = SUBSTANCE only: artifacts, citations, github impls, DISCOURSE (the field building/discussing)
#   insider    = POPULARITY: hf_upvotes, github_stars, ph_votes, reddit engagement — context, NOT a gem signal
#   mainstream = newsletters / HN points / press (the crowd/press knows)
#   divergence = in_field - mainstream    (insider NEVER enters divergence)
# A hidden gem gains in-field traction (DISCUSSION, not upvotes) before mainstream.
SUBSTANCE_WEIGHTS = {   # in_field: engineers BUILDING on it + the field DISCUSSING it — the ONLY gem inputs
    "hf_models": 7.0, "hf_datasets": 4.0, "hf_spaces": 3.0,   # lg — models/datasets/spaces built on it
    "github_impls": 6.0,                                       # lg — independent implementations
    "influential_citations": 6.0, "citations": 1.0,           # lg — builds-on-it citations (lagging)
    "discourse_mentions": 4.0,                                 # linear — the field discussing it (traction, not upvotes)
}
INSIDER_WEIGHTS = {   # POPULARITY only — shown for context, NEVER enters in_field or divergence
    "hf_upvotes": 2.5, "ph_votes": 2.0, "github_stars": 1.5,  # lg — one-click practitioner popularity
    "reddit_score": 2.0,                                       # lg — technical-community engagement
    "show_launch_hn": 2.0,                                     # flat — builder showing work
}
MAINSTREAM_WEIGHTS = {   # crowd / press
    "newsletter": 8.0,         # capped count — covered by an AI newsletter (strong "the crowd knows")
    "hn_bucket": 3.0,          # bucketed hn_points (0/1/2/3/4)
    "press": 5.0,              # capped count — covered by tech press (TechCrunch/VB/MIT-TR)
    "lab_release": 12.0,       # official lab announcement = public reveal
    "mainstream_base": 3.0,    # the newsletter/press item itself
}

# Source classification for cross-referencing (P0-1): which sources count as newsletter / press coverage.
NEWSLETTER_SOURCES = {
    "rss:import_ai", "rss:ahead_of_ai", "rss:interconnects", "rss:latent_space",
    "rss:last_week_in_ai", "rss:simon_willison",
}
PRESS_SOURCES = {
    "rss:techcrunch_ai", "rss:venturebeat_ai", "rss:mit_tech_review_ai", "rss:stratechery",
    "rss:crunchbase_news", "rss:techcrunch_funding", "rss:techcrunch_startups",
}
# The pulse floor + caps that bound what the AGENTS read. The raw digest_input keeps EVERYTHING.
AGENT_PAPER_CAP = 50               # top-N papers sent to the council
AGENT_PAPER_FULL_ABSTRACTS = 50    # all digest papers carry their abstract (the summarizer condenses)
AGENT_PAPER_FRESH_RESERVE = 15     # of the cap, min slots RESERVED for the fresh/unscored tail (in_field=0 —
                                   # no measurable substance yet; the council judges these on the abstract)
INSIDER_FRESH_CEILING = 5          # fresh-tail LABEL split (display only): insider < this = 🌱 genuinely fresh
                                   # (no signal, judge generously); >= this = 🔥 popular·unbuilt (judge skeptically)
AGENT_CAPS = {                     # per-type ceiling for the agent digest (non-papers run smaller)
    "release": 50, "discussion": 80, "repo": 40, "article": 40, "funding": 30,
    "product": 20, "lab_news": 35, "event": 12, "social": 40,
}
AGENT_FULL_CONTENT = 100           # per non-paper type: effectively ALL digest items carry their summary
                                   # (the summarizer stage needs source text for every item; was 15)
PER_SOURCE_CAP = 4                 # no single feed may occupy > N slots in a section (Willison's 14 -> 4)

# ---- Caching, snapshots, enrichment (step 3b-ii) -----------------------------------------------
# GET responses are cached so re-runs reuse them (and stop the arXiv/Reddit rate-limit tripping).
CACHE_DB = str(Path(__file__).resolve().parent.parent / "data" / "cache.db")
DEFAULT_CACHE_TTL = 6 * 3600       # 6h: re-runs within the window reuse cache; daily runs fetch fresh
CACHE_MAX_AGE_DAYS = 7             # prune cache rows older than this

# Velocity snapshot store (per-item signals over time -> acceleration).
SNAPSHOT_DB = str(Path(__file__).resolve().parent.parent / "data" / "debrief.db")
SNAPSHOT_RETENTION_DAYS = 90
VELOCITY_MIN_SIGNAL = 3            # velocity ignores a signal until its ABSOLUTE value clears this (kills 0->1 blips)

# ---- Fulltext stage (fetch + extract the linked page for agent-digest items) --------------------
# Deterministic depth for the summarizer stage: for every item selected into the agent digest
# (except the skip rules below), fetch item["url"] via the cached HTTP layer and extract the main
# text to data/fulltext/<id>.txt. Papers skip (abstracts suffice); events skip (dates suffice).
FULLTEXT_DIR = str(Path(__file__).resolve().parent.parent / "data" / "fulltext")
FULLTEXT_MAX_CHARS = 6000          # cap per item — plenty for a summary, bounds the summarizer's input
FULLTEXT_MIN_CHARS = 400           # extraction shorter than this counts as failed (item keeps its thin summary)
FULLTEXT_TIMEOUT = 20
FULLTEXT_RETRIES = 2
FULLTEXT_SKIP_TYPES = {"paper", "event"}
FULLTEXT_SKIP_DOMAINS = {          # JS-walled / login-walled / no extractable value (bare domains;
    "producthunt.com",             #  matching strips "www." and covers subdomains)
    "twitter.com", "x.com", "bsky.app",
    "youtube.com", "youtu.be", "vimeo.com",
}
FULLTEXT_PAYWALL_DOMAINS = {
    "wsj.com", "ft.com", "bloomberg.com", "nytimes.com", "theinformation.com", "economist.com",
}
FULLTEXT_DOMAIN_INTERVALS = {"reddit.com": 9.0}   # per-domain fetch spacing (seconds); default below
FULLTEXT_DEFAULT_INTERVAL = 1.0

# Traction enrichment runs on CANDIDATE papers only (on HF Daily OR cited in discourse).
# HF paper-pages (fast: linked artifacts + official-repo stars) + Semantic Scholar (lagging: citations)
# are always on. GitHub code-search for independent impls is OFF by default — it's 30/min rate-limited
# and ~0 on fresh papers (the gem target), and HF's githubStars already covers "engineers building on it".
ENRICH_GITHUB_SEARCH = False   # tested 1/25 nonzero on fresh papers @ ~5.7 min/run — HF artifacts carry substance
