"""Immutable advisory value objects for authenticated FreeLLMAPI catalogs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class CatalogTier(StrEnum):
    """Upstream catalog freshness tiers."""

    LIVE = "live"
    MONTHLY = "monthly"


@dataclass(frozen=True, slots=True)
class CatalogLimits:
    """Advertised provider quota hints; observed Gludd accounting remains final."""

    rpm: int | None
    rpd: int | None
    tpm: int | None
    tpd: int | None


@dataclass(frozen=True, slots=True)
class CatalogModel:
    """One authenticated but still-unprobed upstream model row."""

    platform: str
    model_id: str
    display_name: str
    intelligence_rank: int
    speed_rank: int
    size_label: str
    limits: CatalogLimits
    monthly_token_budget: str | None
    context_window: int | None
    enabled: bool
    supports_vision: bool
    supports_tools: bool
    modality: str
    media_note: str | None
    request_style: str | None

    @property
    def identity(self) -> tuple[str, str]:
        """Return the upstream namespace pair, not a runnable Gludd identity."""
        return self.platform, self.model_id


@dataclass(frozen=True, slots=True)
class CatalogQuirkTarget:
    """A bounded selector attached to one advisory provider quirk."""

    platform: str | None
    model_glob: str | None


@dataclass(frozen=True, slots=True)
class CatalogQuirk:
    """Authenticated provider behavior text that remains an advisory hint."""

    slug: str
    title: str
    body: str
    severity: str
    targets: tuple[CatalogQuirkTarget, ...]


@dataclass(frozen=True, slots=True)
class FreeLLMAPICatalog:
    """Immutable exact-byte evidence admitted from the pinned upstream feed."""

    version: str
    generated_at: datetime
    tier: CatalogTier
    models: tuple[CatalogModel, ...]
    quirks: tuple[CatalogQuirk, ...]
    payload_sha256: str

    @property
    def chat_seeds(self) -> tuple[CatalogModel, ...]:
        """Return enabled text rows suitable for Gludd-owned probing only."""
        return tuple(
            model
            for model in self.models
            if model.enabled and model.modality == "text"
        )


class CatalogSchemaError(ValueError):
    """Mark an untrusted FreeLLMAPI catalog schema failure."""


__all__ = (
    "CatalogLimits",
    "CatalogModel",
    "CatalogQuirk",
    "CatalogQuirkTarget",
    "CatalogSchemaError",
    "CatalogTier",
    "FreeLLMAPICatalog",
)
