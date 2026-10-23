"""Adapters, one per source type. A source's `adapter` in sources.yml names one of these."""
from .article_pages import ArticlePages
from .arxiv import ArxivRss
from .base import Adapter, FetchContext, Item
from .github_releases import GithubReleases
from .hf_hub import HfOrgWatch, HfTrending
from .hf_paper_pages import HfPaperApi, HfPaperPage
from .hf_papers import HfDailyPapers
from .hn import Hn, HnComments
from .listing_page import ListingPage
from .model_sources import GithubOrg, Leaderboard, OpenRouter
from .rss import Rss

REGISTRY = {a.name: a for a in (
    Rss(), ListingPage(), HfDailyPapers(), ArxivRss(), GithubReleases(),          # M1
    Hn(), HnComments(), HfPaperApi(), HfPaperPage(),                                # M2: discussion, paper signals
    HfOrgWatch(), HfTrending(), OpenRouter(), GithubOrg(), Leaderboard(),           # M2: model releases
    ArticlePages())}                                                                # M3: article pages

__all__ = ["REGISTRY", "Adapter", "FetchContext", "Item"]
