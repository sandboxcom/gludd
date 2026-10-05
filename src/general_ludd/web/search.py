"""Search-provider compatibility exports for the current toolkit."""

from general_ludd.web.toolkit import NullSearchProvider, SearchProvider
from general_ludd.web.tools import search_gather
from general_ludd.web.types import SearchHit

NullProvider = NullSearchProvider

__all__ = [
    "NullProvider",
    "NullSearchProvider",
    "SearchHit",
    "SearchProvider",
    "search_gather",
]
