"""Crawl compatibility exports backed by the bounded current toolkit."""

from general_ludd.web.policy import WebPolicy
from general_ludd.web.toolkit import normalize_url
from general_ludd.web.tools import crawl_site

CrawlPolicy = WebPolicy

__all__ = ["CrawlPolicy", "crawl_site", "normalize_url"]
