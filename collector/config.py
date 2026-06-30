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
LAB_FEEDS = [
    ("openai",          "https://openai.com/news/rss.xml"),
    ("anthropic_news",  _OLSHANSK + "feed_anthropic_news.xml"),
    ("anthropic_eng",   _OLSHANSK + "feed_anthropic_engineering.xml"),
    ("google_gemini",   "https://blog.google/products/gemini/rss/"),
    ("deepmind",        "https://deepmind.google/blog/rss.xml"),
    ("google_research", "https://research.google/blog/rss/"),
    ("meta_ai",         _OLSHANSK + "feed_meta_ai.xml"),
    ("mistral",         _OLSHANSK + "feed_mistral.xml"),
    ("xai",             _OLSHANSK + "feed_xainews.xml"),
    ("nvidia",          "https://blogs.nvidia.com/feed/"),
    ("huggingface",     "https://huggingface.co/blog/feed.xml"),
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
IN_FIELD_WEIGHTS = {
    "on_hf_daily": 5.0,            # HF community surfaced the paper
    "hf_upvotes": 1.0,
    "hf_models": 4.0, "hf_datasets": 3.0, "hf_spaces": 2.0,   # engineers building on it
    "github_impls": 5.0,
    "discourse_mentions": 4.0,     # researchers/builders citing it in discussion
    "influential_citations": 6.0, "citations": 0.3,           # lagging
    "stars": 0.004,                # repo "stars today/week"
    "github_stars": 0.01,          # official repo's stars (from HF paper-page) — "building on it"
    "show_launch_hn": 3.0,         # Show/Launch HN = builder discourse
    "in_field_axis": 1.0,          # base for in-field-tagged items
}
MAINSTREAM_WEIGHTS = {
    "hn_points": 0.05,
    "ph_votes": 0.02,
    "mainstream_mentions": 3.0,    # picked up by tech press / mass-market newsletter
    "mainstream_axis": 1.0,
}
# The pulse floor + caps that bound what the AGENTS read. The raw digest_input keeps EVERYTHING.
AGENT_PAPER_CAP = 50               # top-N papers (by in-field score) sent to the council
AGENT_PAPER_FULL_ABSTRACTS = 25    # of those, how many get the FULL abstract (rest are one-liners)
AGENT_CAPS = {                     # per-type ceiling for the agent digest (non-papers run smaller)
    "discussion": 80, "repo": 40, "article": 40, "funding": 30,
    "product": 20, "lab_news": 35, "event": 12, "social": 40,
}
AGENT_FULL_CONTENT = 15            # per non-paper type, how many get full summary (rest terse)

# ---- Caching, snapshots, enrichment (step 3b-ii) -----------------------------------------------
# GET responses are cached so re-runs reuse them (and stop the arXiv/Reddit rate-limit tripping).
CACHE_DB = str(Path(__file__).resolve().parent.parent / "data" / "cache.db")
DEFAULT_CACHE_TTL = 6 * 3600       # 6h: re-runs within the window reuse cache; daily runs fetch fresh
CACHE_MAX_AGE_DAYS = 7             # prune cache rows older than this

# Velocity snapshot store (per-item signals over time -> acceleration).
SNAPSHOT_DB = str(Path(__file__).resolve().parent.parent / "data" / "debrief.db")
SNAPSHOT_RETENTION_DAYS = 90

# Traction enrichment runs on CANDIDATE papers only (on HF Daily OR cited in discourse).
# HF paper-pages (fast: linked artifacts + official-repo stars) + Semantic Scholar (lagging: citations)
# are always on. GitHub code-search for independent impls is OFF by default — it's 30/min rate-limited
# and ~0 on fresh papers (the gem target), and HF's githubStars already covers "engineers building on it".
ENRICH_GITHUB_SEARCH = False
