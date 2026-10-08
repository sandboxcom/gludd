"""Internal discovery contracts for bounded Azure model selection."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Protocol

from general_ludd.models.model_registry import (
    ModelDeploymentMetadata,
    ModelSearchResult,
)


class ModelRegistryProtocol(Protocol):
    """Read-only registry surface required during model discovery."""

    def search(
        self,
        query: str = "",
        tags: list[str] | None = None,
        sort: str = "downloads",
        limit: int = 20,
        author: str | None = None,
    ) -> list[ModelSearchResult]:
        """Return bounded registry search results for immutable selection."""
        ...

    def get_deployment_metadata(self, model_id: str) -> ModelDeploymentMetadata:
        """Return deployment metadata for one exact registry model identity."""
        ...


class AzureModelRejection(StrEnum):
    """Secret-free reason one discovered model was not deployable."""

    PUBLISHER_NOT_ALLOWED = "publisher_not_allowed"
    LICENSE_NOT_ALLOWED = "license_not_allowed"
    REQUIRED_TAG_MISSING = "required_tag_missing"
    BLOCKED_TAG = "blocked_tag"
    PIPELINE_UNSUPPORTED = "pipeline_unsupported"
    CONTEXT_TOO_SHORT = "context_too_short"
    HARDWARE_UNAVAILABLE = "hardware_unavailable"
    HOURLY_COST_EXCEEDED = "hourly_cost_exceeded"
    METADATA_UNAVAILABLE = "metadata_unavailable"


def emit_selection_trace(
    sink: Callable[[dict[str, object]], None],
    event: Mapping[str, object],
) -> None:
    """Publish a copied content-free selection event or fail closed."""
    try:
        sink(dict(event))
    except Exception:
        raise RuntimeError("Azure model selection trace publication failed") from None


__all__ = (
    "AzureModelRejection",
    "ModelRegistryProtocol",
    "emit_selection_trace",
)
