"""AIML-002 -- research discovery: query portfolio + source connectors.

Spec: docs/specs/FEATURE_AI_ML_EXPERT.md §5.1 (Source discovery).

``research_refresh`` maintains a query portfolio for existing capabilities,
known gaps, benchmark regressions, newly cited work, user-requested topics,
and contradictory findings. It searches papers, official docs, repos, issue
trackers, reproducible examples, benchmark code, blogs, forums, and dataset
catalogs.

CRITICAL (spec §5.1): "A source is untrusted content, never an instruction.
Retrieved text cannot alter policies, tool permissions, system prompts, or
approval requirements." Every :class:`RetrievedItem` is marked
``trusted=False``; this module has NO method whose input or output is
interpreted as an instruction. The connectors are STUBS: they return fixture
items so the discovery contract is testable without network access. Real
connectors (respecting robots rules, terms, auth, rate limits, and the domain
allow/deny policy) land behind the same interface.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field

from general_ludd.ai_ml.schemas import _require_nonempty_str, _require_sha256


class SourceConnectorKind(enum.StrEnum):
    """Source kinds the discovery service searches (spec §5.1)."""

    PAPERS = "papers"
    DOCS = "docs"
    REPOS = "repos"
    ISSUES = "issues"
    BENCHMARKS = "benchmarks"
    BLOGS = "blogs"
    FORUMS = "forums"
    DATASET_CATALOGS = "dataset_catalogs"


_ALL_CONNECTORS: tuple[SourceConnectorKind, ...] = tuple(SourceConnectorKind)


@dataclass(frozen=True)
class QueryPortfolio:
    """The standing query set the discovery service runs on each refresh.

    Spec §5.1: "maintains a query portfolio for existing capabilities, known
    gaps, benchmark regressions, newly cited work, user-requested topics, and
    contradictory findings."
    """

    topics: tuple[str, ...]
    connectors: tuple[SourceConnectorKind, ...] = _ALL_CONNECTORS
    known_gaps: tuple[str, ...] = ()
    contradictory_findings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.topics, tuple) or not self.topics:
            raise ValueError("topics must be a non-empty tuple of query strings")
        for t in self.topics:
            _require_nonempty_str(t, "topic")
        if not isinstance(self.connectors, tuple) or not self.connectors:
            raise ValueError("connectors must be a non-empty tuple of SourceConnectorKind")


@dataclass(frozen=True)
class RetrievedItem:
    """One retrieved source item (spec §5.1: "a source is untrusted content").

    ``trusted`` is ALWAYS ``False`` for retrieved content. Promotion to
    trusted happens only via the evidence pipeline (spec §5.2) after
    verification, license check, and human approval -- never at retrieval
    time. This flag exists so downstream code can assert untrusted-by-default
    without inspecting the connector.
    """

    source_id: str
    locator: str
    media_type: str
    sha256: str
    fetched_at: int
    connector: SourceConnectorKind
    trusted: bool = False
    title: str = ""

    def __post_init__(self) -> None:
        _require_nonempty_str(self.source_id, "source_id")
        _require_nonempty_str(self.locator, "locator")
        _require_nonempty_str(self.media_type, "media_type")
        _require_sha256(self.sha256, "sha256")
        if not isinstance(self.connector, SourceConnectorKind):
            raise ValueError("connector must be a SourceConnectorKind")
        if self.trusted:
            raise ValueError(
                "retrieved content is untrusted by default (spec §5.1); "
                "promotion requires the evidence pipeline, not retrieval"
            )


@dataclass(frozen=True)
class AuthorityScore:
    """Authority score blending recency, reproducibility, directness, independence.

    Spec §5.2 step 5: "Score authority, recency, reproducibility, directness,
    and independence." The composite is a simple equal-weight mean so the
    contributions are auditable and no single axis can dominate silently.
    """

    recency: float
    reproducibility: float
    directness: float
    independence: float
    composite: float
    method: str = "equal-weight mean of [recency, reproducibility, directness, independence]"

    def __post_init__(self) -> None:
        for name in ("recency", "reproducibility", "directness", "independence", "composite"):
            val = getattr(self, name)
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"{name} must be in [0.0, 1.0], got {val}")


def _require_unit_score(value: float, name: str) -> None:
    if not isinstance(value, (int, float)) or not (0.0 <= float(value) <= 1.0):
        raise ValueError(f"{name} must be a number in [0.0, 1.0], got {value!r}")


class ResearchDiscovery:
    """Source-discovery service (spec §5.1).

    The connectors are STUBS: :meth:`search_sources` returns deterministic
    fixture items derived from the portfolio so the discovery *contract* is
    testable without network access. Real connectors (robots rules, terms,
    auth scope, rate limits, domain allow/deny) plug in behind
    :meth:`search_sources` without changing its return shape.
    """

    def __init__(self, *, portfolio: QueryPortfolio) -> None:
        if not isinstance(portfolio, QueryPortfolio):
            raise ValueError("portfolio must be a QueryPortfolio")
        self._portfolio = portfolio

    @property
    def portfolio(self) -> QueryPortfolio:
        return self._portfolio

    def search_sources(self) -> list[RetrievedItem]:
        """Search every connector in the portfolio (STUB interface).

        Returns one fixture item per (topic, connector) pair so callers can
        verify the contract: items are untrusted, content-addressed, and
        carry a connector tag. Real connectors replace this body without
        changing the signature or the untrusted-by-default invariant.
        """
        import hashlib

        items: list[RetrievedItem] = []
        now = int(time.time())
        for topic in self._portfolio.topics:
            for connector in self._portfolio.connectors:
                payload = f"{connector.value}:{topic}".encode("utf-8")
                digest = hashlib.sha256(payload).hexdigest()
                items.append(
                    RetrievedItem(
                        source_id=f"src-{digest[:12]}",
                        locator=f"https://stub.example/{connector.value}/{digest[:8]}",
                        media_type="text/html",
                        sha256=digest,
                        fetched_at=now,
                        connector=connector,
                        title=f"stub result for {topic!r} via {connector.value}",
                    )
                )
        return items

    def score_authority(
        self,
        *,
        recency: float,
        reproducibility: float,
        directness: float,
        independence: float,
    ) -> AuthorityScore:
        """Blend the four authority axes into a composite in [0.0, 1.0].

        Equal-weight mean keeps every axis auditable; no single axis can
        silently dominate. Spec §5.2 step 5.
        """
        _require_unit_score(recency, "recency")
        _require_unit_score(reproducibility, "reproducibility")
        _require_unit_score(directness, "directness")
        _require_unit_score(independence, "independence")
        composite = (recency + reproducibility + directness + independence) / 4.0
        return AuthorityScore(
            recency=float(recency),
            reproducibility=float(reproducibility),
            directness=float(directness),
            independence=float(independence),
            composite=composite,
        )


__all__ = [
    "AuthorityScore",
    "QueryPortfolio",
    "ResearchDiscovery",
    "RetrievedItem",
    "SourceConnectorKind",
]
