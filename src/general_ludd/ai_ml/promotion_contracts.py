"""Validated value contracts shared by the AI/ML promotion gate."""

from __future__ import annotations

import enum
from dataclasses import dataclass

from general_ludd.ai_ml.schemas import _require_nonempty_str


class PromotionPhase(enum.StrEnum):
    """The six ordered promotion phases from the AI/ML delivery contract."""

    BUILD = "build"
    VALIDATE = "validate"
    SHADOW = "shadow"
    CANARY = "canary"
    COMPARE = "compare"
    SWAP = "swap"


ROLLBACK_SLO_SECONDS: int = 60


@dataclass(frozen=True)
class CanaryBudgets:
    """Hard online quality, safety, latency, error, and cost thresholds."""

    quality_floor: float
    safety_floor: float
    latency_p99_ceiling_ms: float
    error_rate_ceiling: float
    cost_ceiling_usd_per_kreq: float

    def __post_init__(self) -> None:
        """Reject non-numeric, negative, or out-of-range budgets."""
        for field_name in (
            "quality_floor",
            "safety_floor",
            "latency_p99_ceiling_ms",
            "error_rate_ceiling",
            "cost_ceiling_usd_per_kreq",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int | float) or isinstance(value, bool):
                raise ValueError(f"{field_name} must be a number, got {value!r}")
            if value < 0:
                raise ValueError(f"{field_name} must be >= 0, got {value}")
        if not 0.0 <= self.error_rate_ceiling <= 1.0:
            raise ValueError(
                "error_rate_ceiling must be a fraction in [0.0, 1.0], "
                f"got {self.error_rate_ceiling}"
            )


@dataclass(frozen=True)
class CanaryMetrics:
    """Observed metrics compared with :class:`CanaryBudgets`."""

    quality: float
    safety: float
    latency_p99_ms: float
    error_rate: float
    cost_usd_per_kreq: float

    def __post_init__(self) -> None:
        """Reject non-numeric, negative, or out-of-range metrics."""
        for field_name in ("quality", "safety", "latency_p99_ms", "cost_usd_per_kreq"):
            value = getattr(self, field_name)
            if not isinstance(value, int | float) or isinstance(value, bool):
                raise ValueError(f"{field_name} must be a number, got {value!r}")
            if value < 0:
                raise ValueError(f"{field_name} must be >= 0, got {value}")
        if not isinstance(self.error_rate, int | float) or isinstance(self.error_rate, bool):
            raise ValueError(f"error_rate must be a number, got {self.error_rate!r}")
        if not 0.0 <= self.error_rate <= 1.0:
            raise ValueError(f"error_rate must be in [0.0, 1.0], got {self.error_rate}")


@dataclass(frozen=True)
class CanaryVerdict:
    """Canary health and the names of every breached budget."""

    healthy: bool
    breached_budgets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Require a consistent health flag and named breach evidence."""
        if not isinstance(self.healthy, bool):
            raise ValueError("healthy must be a bool")
        if self.healthy and self.breached_budgets:
            raise ValueError("a healthy verdict must not carry breached_budgets")
        for name in self.breached_budgets:
            _require_nonempty_str(name, "breached_budgets[i]")


@dataclass(frozen=True)
class AliasSwap:
    """One atomic alias swap plus its prior-version drain state."""

    alias: str
    from_version: str
    to_version: str
    in_flight_requests: int = 0
    drained: bool = False

    def __post_init__(self) -> None:
        """Validate immutable alias and in-flight request state."""
        for field_name in ("alias", "from_version", "to_version"):
            _require_nonempty_str(getattr(self, field_name), field_name)
        if not isinstance(self.in_flight_requests, int) or self.in_flight_requests < 0:
            raise ValueError(
                "in_flight_requests must be a non-negative int, "
                f"got {self.in_flight_requests!r}"
            )
        if not isinstance(self.drained, bool):
            raise ValueError("drained must be a bool")
        if self.drained and self.in_flight_requests != 0:
            raise ValueError("a drained swap must have zero in_flight_requests")


@dataclass(frozen=True)
class RollbackResult:
    """The restored version and rollback-initiation SLO result."""

    swapped_back_to: str
    initiated_within_60s: bool
    seconds_to_initiate: float

    def __post_init__(self) -> None:
        """Validate rollback target and initiation timing evidence."""
        _require_nonempty_str(self.swapped_back_to, "swapped_back_to")
        if not isinstance(self.initiated_within_60s, bool):
            raise ValueError("initiated_within_60s must be a bool")
        if not isinstance(self.seconds_to_initiate, int | float) or isinstance(
            self.seconds_to_initiate, bool
        ):
            raise ValueError("seconds_to_initiate must be a number")
        if self.seconds_to_initiate < 0:
            raise ValueError(
                f"seconds_to_initiate must be >= 0, got {self.seconds_to_initiate}"
            )


__all__ = (
    "ROLLBACK_SLO_SECONDS",
    "AliasSwap",
    "CanaryBudgets",
    "CanaryMetrics",
    "CanaryVerdict",
    "PromotionPhase",
    "RollbackResult",
)
