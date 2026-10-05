"""Provider-neutral inputs, factors, and telemetry for FreeLLMAPI scoring."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum

_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def _require_finite_nonnegative(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return number


def _require_digest(value: object) -> None:
    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        raise ValueError("candidate identity must be a lowercase SHA-256 digest")


def _require_finite_positive(name: str, value: object) -> None:
    if _require_finite_nonnegative(name, value) <= 0.0:
        raise ValueError(f"{name} must be positive")


def _require_unit_interval(name: str, value: object) -> None:
    if _require_finite_nonnegative(name, value) > 1.0:
        raise ValueError(f"{name} must be at most 1")


class FreeLLMScoringSource(StrEnum):
    """Source of a returned factor batch."""

    GLUDD_FALLBACK = "gludd_fallback"
    FREELLMAPI_SHADOW = "freellmapi_shadow"


class FreeLLMScoringFault(StrEnum):
    """Content-free reason that the exact Gludd fallback was used."""

    DISABLED = "disabled"
    CIRCUIT_OPEN = "circuit_open"
    ENGINE_UNAVAILABLE = "engine_unavailable"
    ENGINE_FAILURE = "engine_failure"
    INVALID_RESULT = "invalid_result"


@dataclass(frozen=True, slots=True)
class FreeLLMScoringInput:
    """One identity-only candidate input accepted by the vendored kernel."""

    candidate_identity_digest: str
    successes: float
    failures: float
    community_successes: float
    community_failures: float
    tokens_per_second: float
    ttfb_ms: float | None
    used_tokens: float
    budget_tokens: float
    rate_window_used_fraction: float | None
    rate_limit_penalty: float

    def __post_init__(self) -> None:
        """Reject raw identities and unbounded or non-finite measurements."""
        _require_digest(self.candidate_identity_digest)
        for name in (
            "successes",
            "failures",
            "community_successes",
            "community_failures",
            "tokens_per_second",
            "used_tokens",
            "budget_tokens",
            "rate_limit_penalty",
        ):
            _require_finite_nonnegative(name, getattr(self, name))
        if self.ttfb_ms is not None:
            _require_finite_nonnegative("ttfb_ms", self.ttfb_ms)
        if self.rate_window_used_fraction is not None:
            _require_unit_interval(
                "rate_window_used_fraction", self.rate_window_used_fraction
            )
        if self.rate_limit_penalty > 10.0:
            raise ValueError("rate_limit_penalty must be at most 10")


@dataclass(frozen=True, slots=True)
class FreeLLMScoringFactors:
    """Pure factors returned by either Gludd or the shadow kernel."""

    candidate_identity_digest: str
    reliability_alpha: float
    reliability_beta: float
    expected_reliability: float
    speed: float
    headroom: float
    rate_window_headroom: float
    rate_limit: float

    def __post_init__(self) -> None:
        """Reject factors that cannot safely participate in routing."""
        _require_digest(self.candidate_identity_digest)
        _require_finite_positive("reliability_alpha", self.reliability_alpha)
        _require_finite_positive("reliability_beta", self.reliability_beta)
        for name in (
            "expected_reliability",
            "speed",
            "headroom",
            "rate_window_headroom",
            "rate_limit",
        ):
            _require_unit_interval(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class FreeLLMScoringBatchResult:
    """Bounded scoring result with no raw model, prompt, or error content."""

    factors: tuple[FreeLLMScoringFactors, ...]
    source: FreeLLMScoringSource
    fault: FreeLLMScoringFault | None
    bundle_digest: str
    candidate_count: int


@dataclass(frozen=True, slots=True)
class FreeLLMScoringTrace:
    """Content-free operational trace emitted for every scoring attempt."""

    source: FreeLLMScoringSource
    fault: FreeLLMScoringFault | None
    bundle_digest: str
    candidate_count: int


__all__ = [
    "FreeLLMScoringBatchResult",
    "FreeLLMScoringFactors",
    "FreeLLMScoringFault",
    "FreeLLMScoringInput",
    "FreeLLMScoringSource",
    "FreeLLMScoringTrace",
]
