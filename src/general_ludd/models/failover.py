"""Live model failover chain.

When a primary model fails (429/5xx/timeout), retries with fallback
profiles using tenacity backoff. Records failover events for metrics.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ModelFailoverChain:
    def __init__(
        self,
        primary_profile: str,
        fallback_profiles: list[str] | None = None,
        max_retries: int = 3,
        backoff_seconds: float = 2.0,
    ) -> None:
        self._primary = primary_profile
        self._fallbacks = fallback_profiles or []
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._failover_events: list[dict[str, Any]] = []

    def get_chain(self) -> list[str]:
        return [self._primary, *self._fallbacks]

    def record_failover(self, from_profile: str, to_profile: str, error: str) -> None:
        self._failover_events.append({
            "from": from_profile,
            "to": to_profile,
            "error": error,
        })
        logger.warning(
            "Model failover: %s → %s (%s)", from_profile, to_profile, error,
        )

    def get_failover_events(self) -> list[dict[str, Any]]:
        return list(self._failover_events)

    def should_retry(self, error: Exception) -> bool:
        status = getattr(error, "status_code", getattr(error, "status", 0))
        if isinstance(status, int) and status in (429, 500, 502, 503, 504):
            return True
        error_str = str(error).lower()
        return any(
            keyword in error_str
            for keyword in ("timeout", "rate limit", "unavailable", "capacity")
        )
