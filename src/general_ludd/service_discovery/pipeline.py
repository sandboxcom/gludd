"""Service discovery pipeline — SearX search -> parse -> diff -> auto-register -> auto-retire -> update catalog."""

from __future__ import annotations

import logging
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

DEFAULT_SEARCH_TERMS = [
    "public API for cloud computing service",
    "free-tier developer API platform",
    "SaaS API developer portal",
    "open source platform API hosting",
    "infrastructure-as-code API service",
]

_RETIRE_INACTIVITY_DAYS = 30


@dataclass
class DiscoveryReport:
    new_services: list[str] = field(default_factory=list)
    retired_services: list[str] = field(default_factory=list)
    changed_services: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    total_discovered: int = 0


class ServiceDiscoveryPipeline:
    def __init__(
        self,
        searx_url: str,
        catalog_path: str = DEFAULT_CATALOG_PATH,
        search_terms: list[str] | None = None,
    ) -> None:
        self._searx = SearXConnector({
            "base_url": searx_url,
            # The bundled managed SearX service binds to loopback; this is an
            # explicit local-service opt-in, while external URLs remain guarded.
            "allow_private": searx_url.startswith("http://localhost"),
        })
        self._catalog = ServiceCatalog(path=catalog_path)
        self._search_terms = search_terms or DEFAULT_SEARCH_TERMS

    def run_discovery_pipeline(self) -> DiscoveryReport:
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
        if len(parts) == 2 and 3 <= len(candidate) <= 80:
            return candidate
    candidate = title[:80].strip()
    return candidate if len(candidate) >= 3 else None
