"""search_gather — pluggable search provider -> fetch top-N -> aggregate.

gludd bundles NO search SDK. A :class:`SearchProvider` is an operator-pluggable
seam; the default :class:`NullSearchProvider` returns a distinct
``PROVIDER_UNCONFIGURED`` signal so a caller NEVER confuses "no provider wired"
with "no results" (a genuine zero-hit search returns ``ok=True, results=[]``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SearchHit:
    """One search-engine result row from a provider."""

    url: str
    title: str = ""
    snippet: str = ""


@runtime_checkable
class SearchProvider(Protocol):
    """Operator-pluggable search backend.

    ``search`` returns up to ``top_n`` :class:`SearchHit` rows. gludd ships no
    implementation — wire your own (a licensed search API, an internal index).
    """

    def search(self, query: str, top_n: int) -> list[SearchHit]:
        """Return up to ``top_n`` hits for ``query`` (may be empty)."""
        ...


class NullSearchProvider:
    """Sentinel provider: signals 'no provider configured' (NOT 'zero results')."""

    configured: bool = False

    def search(self, query: str, top_n: int) -> list[SearchHit]:
        return []
