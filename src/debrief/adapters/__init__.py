"""Adapters, one per source type. A source's `adapter` in sources.yml names one of these."""
from .arxiv import ArxivRss
from .base import Adapter, FetchContext, Item
from .github_releases import GithubReleases
from .hf_papers import HfDailyPapers
from .listing_page import ListingPage
from .rss import Rss

REGISTRY = {a.name: a for a in (Rss(), ListingPage(), HfDailyPapers(), ArxivRss(), GithubReleases())}

__all__ = ["REGISTRY", "Adapter", "FetchContext", "Item"]
