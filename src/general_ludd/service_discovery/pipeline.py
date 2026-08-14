"""Service discovery pipeline — SearX search -> parse -> diff -> auto-register -> auto-retire -> update catalog."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from general_ludd.connectors.searx import SearXConnector, SearXResult
from general_ludd.infra.service_catalog import (
    DEFAULT_CATALOG_PATH,
    DiscoveredService,
    ServiceCatalog,
    diff_catalog,
)

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_TERMS: list[tuple[str, str]] = [
    ("cloud-compute-api", "public API for cloud computing service"),
    ("developer-free-tier", "free-tier developer API platform"),
    ("saas-developer-api", "SaaS API developer portal"),
    ("open-source-api-hosting", "open source platform API hosting"),
    ("infrastructure-as-code-api", "infrastructure-as-code API service"),
]

_RETIRE_INACTIVITY_DAYS = 30


@dataclass
class DiscoveryReport:
    """Summarize one complete service-discovery reconciliation."""

    new_services: list[str] = field(default_factory=list)
    retired_services: list[str] = field(default_factory=list)
    changed_services: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    total_discovered: int = 0


class ServiceDiscoveryPipeline:
    """Discover services through SearXNG and reconcile the local catalog."""

    def __init__(
        self,
        searx_url: str,
        catalog_path: str = DEFAULT_CATALOG_PATH,
        search_terms: Sequence[str | tuple[str, str]] | None = None,
    ) -> None:
        """Initialize a validated discovery pipeline.

        Args:
            searx_url: Base URL of the guarded SearXNG service.
            catalog_path: Path to the catalog reconciled by a later run.
            search_terms: Legacy query strings or labeled ``(identifier, query)``
                pairs. ``None`` and empty sequences select the built-in defaults.

        Raises:
            TypeError: If a search-term entry has an unsupported shape or type.
            ValueError: If a search-term tuple has the wrong arity or blank text.
        """
        selected_terms: Sequence[object] = search_terms or DEFAULT_SEARCH_TERMS
        normalized_terms = _normalize_search_terms(selected_terms)
        self._searx = SearXConnector({
            "base_url": searx_url,
            # The bundled managed SearX service binds to loopback; this is an
            # explicit local-service opt-in, while external URLs remain guarded.
            "allow_private": searx_url.startswith("http://localhost"),
        })
        self._catalog = ServiceCatalog(path=catalog_path)
        self._search_terms = normalized_terms

    def run_discovery_pipeline(self) -> DiscoveryReport:
        """Search configured queries and reconcile all successful results.

        Returns:
            A report of catalog changes and isolated query/serialization errors.
        """
        report = DiscoveryReport()
        errors: list[str] = []

        results: list[SearXResult] = []
        for term in self._search_terms:
            try:
                batch = self._searx.search(term)
                results.extend(batch)
            except Exception as exc:
                msg = f"SearX search failed for term {term!r}: {exc}"
                logger.warning(msg, exc_info=True)
                errors.append(msg)

        if not results and errors:
            report.errors = errors
            return report

        snapshot = ServiceCatalog()
        now = datetime.now(UTC)

        for r in results:
            try:
                name = _extract_service_name(r)
                if not name:
                    continue
                svc = DiscoveredService(
                    name=name,
                    url=r.url,
                    description=r.snippet[:500] if r.snippet else None,
                    source_engine=r.engine,
                    status="active",
                    last_seen=now,
                )
                snapshot.add(svc)
            except Exception as exc:
                msg = f"Failed to parse result into service: {exc}"
                logger.debug(msg)
                errors.append(msg)

        try:
            old = ServiceCatalog(path=self._catalog.path)
        except Exception:
            old = ServiceCatalog(path=self._catalog.path)

        added, removed, changed = diff_catalog(old, snapshot)

        for svc in removed:
            if svc.status == "active":
                svc.status = "inactive"
                svc.last_seen = now
                old.add(svc)
                report.retired_services.append(svc.name)

        for svc in added:
            old.add(svc)
            report.new_services.append(svc.name)

        for svc in changed:
            old.add(svc)
            report.changed_services.append(svc.name)

        self._auto_retire_stale(old, now)

        try:
            old.save()
        except Exception as exc:
            msg = f"Failed to save catalog: {exc}"
            logger.warning(msg, exc_info=True)
            errors.append(msg)

        report.total_discovered = len(old.services)
        report.errors = errors

        if report.new_services:
            logger.info(
                "Discovery: %d new service(s): %s",
                len(report.new_services),
                ", ".join(report.new_services),
            )
        if report.retired_services:
            logger.info(
                "Discovery: %d retired service(s): %s",
                len(report.retired_services),
                ", ".join(report.retired_services),
            )
        if report.changed_services:
            logger.info(
                "Discovery: %d changed service(s): %s",
                len(report.changed_services),
                ", ".join(report.changed_services),
            )

        return report

    def _auto_retire_stale(self, catalog: ServiceCatalog, now: datetime) -> None:
        cutoff = now - timedelta(days=_RETIRE_INACTIVITY_DAYS)
        for svc in list(catalog.services.values()):
            if svc.status == "active" and svc.last_seen < cutoff:
                svc.status = "inactive"
                svc.last_seen = now
                logger.info("Auto-retired stale service: %s", svc.name)


def _extract_service_name(result: SearXResult) -> str | None:
    title = result.title.strip()
    if not title:
        return None
    for delimiter in (" - ", " | ", " · ", " :: ", " — "):
        parts = title.split(delimiter, 1)
        candidate = parts[0].strip()
        if len(parts) == 2 and 2 <= len(candidate) <= 80:
            return candidate
    candidate = title[:80].strip()
    return candidate if len(candidate) >= 2 else None


def _normalize_search_terms(search_terms: Sequence[object]) -> list[str]:
    """Validate labeled/default and legacy plain-string search terms."""
    normalized: list[str] = []
    for index, entry in enumerate(search_terms):
        if isinstance(entry, str):
            label = entry
            query = entry
        elif isinstance(entry, tuple):
            if len(entry) != 2:
                raise ValueError(
                    f"search term at index {index} must contain exactly two strings"
                )
            label, query = entry
            if not isinstance(label, str) or not isinstance(query, str):
                raise TypeError(
                    f"search term at index {index} must contain exactly two strings"
                )
        else:
            raise TypeError(
                f"search term at index {index} must be a string or two-string tuple"
            )

        if not label.strip() or not query.strip():
            raise ValueError(f"search term at index {index} must not contain blank strings")
        normalized.append(query)

    return normalized
