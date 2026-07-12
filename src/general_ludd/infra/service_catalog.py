"""Service discovery catalog — DiscoveredService data structures and persistence."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CATALOG_PATH = ".gludd/service_catalog.yml"


@dataclass
class DiscoveredService:
    name: str
    url: str
    api_docs_url: str | None = None
    pricing_url: str | None = None
    status: Literal["active", "inactive", "unknown"] = "unknown"
    discovered_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    description: str | None = None
    source_engine: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "url": self.url,
            "api_docs_url": self.api_docs_url,
            "pricing_url": self.pricing_url,
            "status": self.status,
            "discovered_at": self.discovered_at.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "description": self.description,
            "source_engine": self.source_engine,
        }
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscoveredService:
        discovered_at = _parse_datetime(data.get("discovered_at"))
        last_seen = _parse_datetime(data.get("last_seen"))
        return cls(
            name=data["name"],
            url=data["url"],
            api_docs_url=data.get("api_docs_url"),
            pricing_url=data.get("pricing_url"),
            status=data.get("status", "unknown"),
            discovered_at=discovered_at or datetime.now(UTC),
            last_seen=last_seen or datetime.now(UTC),
            description=data.get("description"),
            source_engine=data.get("source_engine"),
        )


class ServiceCatalog:
    def __init__(self, path: str = DEFAULT_CATALOG_PATH) -> None:
        self.path = path
        self.services: dict[str, DiscoveredService] = {}
        if os.path.exists(path):
            try:
                self.load()
            except Exception:
                logger.warning("Failed to load service catalog from %s", path, exc_info=True)

    def add(self, service: DiscoveredService) -> None:
        service.last_seen = datetime.now(UTC)
        self.services[service.name] = service

    def remove(self, name: str) -> bool:
        if name in self.services:
            self.services[name].status = "inactive"
            self.services[name].last_seen = datetime.now(UTC)
            return True
        return False

    def get(self, name: str) -> DiscoveredService | None:
        return self.services.get(name)

    def list_active(self) -> list[DiscoveredService]:
        return [svc for svc in self.services.values() if svc.status == "active"]

    def list_inactive(self) -> list[DiscoveredService]:
        return [svc for svc in self.services.values() if svc.status == "inactive"]

    def save(self) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        data = {"services": [svc.to_dict() for svc in self.services.values()]}
        if self.path.endswith(".json"):
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
        else:
            with open(self.path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)

    def load(self) -> None:
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f) if self.path.endswith(".json") else yaml.safe_load(f)
        if not isinstance(data, dict):
            return
        raw_services = data.get("services", data)
        if not isinstance(raw_services, list):
            return
        loaded: dict[str, DiscoveredService] = {}
        for item in raw_services:
            svc = DiscoveredService.from_dict(item)
            loaded[svc.name] = svc
        self.services = loaded


def diff_catalog(
    old: ServiceCatalog, new: ServiceCatalog
) -> tuple[list[DiscoveredService], list[DiscoveredService], list[DiscoveredService]]:
    added: list[DiscoveredService] = []
    removed: list[DiscoveredService] = []
    changed: list[DiscoveredService] = []

    for name, new_svc in new.services.items():
        if name not in old.services:
            added.append(new_svc)
        else:
            old_svc = old.services[name]
            if old_svc.url != new_svc.url or old_svc.status != new_svc.status:
                changed.append(new_svc)

    for name, old_svc in old.services.items():
        if name not in new.services:
            removed.append(old_svc)

    return added, removed, changed


def merge_catalog(target: ServiceCatalog, source: ServiceCatalog) -> None:
    for name, src_svc in source.services.items():
        if name not in target.services or (
            target.services[name].status == "inactive" and src_svc.status == "active"
        ):
            target.add(src_svc)
        else:
            tgt_svc = target.services[name]
            changed = tgt_svc.url != src_svc.url or tgt_svc.status != src_svc.status
            if changed:
                target.add(src_svc)

    src_names = set(source.services.keys())
    for name, tgt_svc in target.services.items():
        if name not in src_names and tgt_svc.status == "active":
            tgt_svc.status = "inactive"
            tgt_svc.last_seen = datetime.now(UTC)


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        logger.debug("Failed to parse datetime from %r", value)
        return None
