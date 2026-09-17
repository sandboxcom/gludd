"""Convert signed FreeLLMAPI evidence into non-runnable Gludd candidates.

This module is deliberately only a discovery adapter.  It does not construct
provider clients, choose a winner, activate a profile, or copy FreeLLMAPI's
router.  Gludd's existing probe, calibration, health, budget, and routing
systems remain the authority after these advisory seeds are discovered.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Final

from general_ludd.models.freellmapi_catalog import (
    CatalogLimits,
    CatalogModel,
    CatalogQuirk,
    CatalogQuirkTarget,
    FreeLLMAPICatalog,
)

FREELLMAPI_CANDIDATE_PROTOCOL: Final = "gludd-freellmapi-candidate-seed-v1"
_PLATFORM_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _stable_digest(value: dict[str, object]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class FreeModelCandidateSeed:
    """Authenticated provider/model metadata that still requires Gludd probing."""

    platform: str
    model_id: str
    display_name: str
    intelligence_rank: int
    speed_rank: int
    size_label: str
    limits: CatalogLimits
    monthly_token_budget: str | None
    context_window: int | None
    supports_vision: bool
    supports_tools: bool
    catalog_version: str
    catalog_payload_sha256: str
    quirk_slugs: tuple[str, ...]

    @property
    def protocol(self) -> str:
        """Return the versioned Gludd advisory-candidate protocol."""
        return FREELLMAPI_CANDIDATE_PROTOCOL

    @property
    def identity(self) -> tuple[str, str]:
        """Return the upstream provider/model namespace pair."""
        return self.platform, self.model_id

    @property
    def candidate_digest(self) -> str:
        """Bind this seed to the exact authenticated catalog and model row."""
        return _stable_digest(
            {
                "catalog_payload_sha256": self.catalog_payload_sha256,
                "catalog_version": self.catalog_version,
                "model_id": self.model_id,
                "platform": self.platform,
                "protocol": self.protocol,
            }
        )


def _target_matches(
    target: CatalogQuirkTarget,
    model: CatalogModel,
) -> bool:
    platform_matches = target.platform is None or target.platform == model.platform
    model_matches = target.model_glob is None or fnmatchcase(
        model.model_id,
        target.model_glob,
    )
    return platform_matches and model_matches


def _matching_quirk_slugs(
    quirks: tuple[CatalogQuirk, ...],
    model: CatalogModel,
) -> tuple[str, ...]:
    """Return identifiers only; upstream prose never enters a model prompt."""
    return tuple(
        sorted(
            quirk.slug
            for quirk in quirks
            if any(_target_matches(target, model) for target in quirk.targets)
        )
    )


def _validate_configured_platforms(
    configured_platforms: frozenset[str],
) -> None:
    if type(configured_platforms) is not frozenset or any(
        not isinstance(platform, str)
        or _PLATFORM_RE.fullmatch(platform) is None
        for platform in configured_platforms
    ):
        raise ValueError(
            "configured_platforms must be one immutable set of canonical slugs"
        )


def _candidate_seed(
    catalog: FreeLLMAPICatalog,
    model: CatalogModel,
) -> FreeModelCandidateSeed:
    return FreeModelCandidateSeed(
        platform=model.platform,
        model_id=model.model_id,
        display_name=model.display_name,
        intelligence_rank=model.intelligence_rank,
        speed_rank=model.speed_rank,
        size_label=model.size_label,
        limits=model.limits,
        monthly_token_budget=model.monthly_token_budget,
        context_window=model.context_window,
        supports_vision=model.supports_vision,
        supports_tools=model.supports_tools,
        catalog_version=catalog.version,
        catalog_payload_sha256=catalog.payload_sha256,
        quirk_slugs=_matching_quirk_slugs(catalog.quirks, model),
    )


def discover_freellmapi_candidates(
    catalog: FreeLLMAPICatalog,
    *,
    configured_platforms: frozenset[str],
) -> tuple[FreeModelCandidateSeed, ...]:
    """Return canonical advisory seeds for providers Gludd can authenticate.

    Catalog order and catalog rank never choose a route here.  The stable
    provider/model order makes discovery replayable while retaining the ranks
    only as priors for Gludd's downstream empirical scorer.
    """
    if not isinstance(catalog, FreeLLMAPICatalog):
        raise ValueError("catalog must be admitted FreeLLMAPICatalog evidence")
    _validate_configured_platforms(configured_platforms)
    eligible = (
        model
        for model in catalog.chat_seeds
        if model.platform in configured_platforms
    )
    canonical = sorted(eligible, key=lambda model: model.identity)
    return tuple(_candidate_seed(catalog, model) for model in canonical)


__all__ = [
    "FREELLMAPI_CANDIDATE_PROTOCOL",
    "FreeModelCandidateSeed",
    "discover_freellmapi_candidates",
]
