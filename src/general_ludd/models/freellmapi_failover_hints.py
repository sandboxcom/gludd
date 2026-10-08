"""Convert authenticated FreeLLMAPI quirks into bounded Gludd failover hints.

Only content-free quirk slugs admitted by the signed catalog may influence
Gludd policy.  The resulting hints are bounded numeric overrides that Gludd's
existing :class:`~general_ludd.models.timeout_detector.TimeoutRetryPolicy`
validates before applying to its own retry/failover graph.  Catalog prose,
endpoint values, and credentials never cross this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from general_ludd.models.freellmapi_candidates import FreeModelCandidateSeed


@dataclass(frozen=True, slots=True)
class FailoverPolicyHints:
    """Bounded overrides that Gludd may apply to its own retry/failover policy."""

    max_retries: int | None = None
    failover_after_retries: int | None = None
    base_backoff_seconds: float | None = None


_MIN_RETRIES = 0
_MAX_RETRIES = 10
_MIN_FAILOVER_AFTER = 1
_MAX_FAILOVER_AFTER = 10
_MIN_BACKOFF = 0.0
_MAX_BACKOFF = 60.0


def failover_hints_from_candidate(
    candidate: FreeModelCandidateSeed,
) -> FailoverPolicyHints:
    """Return bounded failover hints derived from the candidate's quirk slugs.

    Unknown slugs are ignored.  Known slugs merge conservatively and are clamped
    to narrow bounds so an upstream quirk cannot disable Gludd's outer deadline
    or failover authority.
    """
    if not isinstance(candidate, FreeModelCandidateSeed):
        raise ValueError("candidate must be a FreeModelCandidateSeed")

    max_retries: int | None = None
    failover_after: int | None = None
    backoff: float | None = None

    for slug in candidate.quirk_slugs:
        if slug == "fast-failover":
            failover_after = min(failover_after or _MAX_FAILOVER_AFTER, 2)
        elif slug == "extra-retries":
            max_retries = max(max_retries or _MIN_RETRIES, 5)
        elif slug == "long-backoff":
            backoff = max(backoff or _MIN_BACKOFF, 4.0)

    return FailoverPolicyHints(
        max_retries=_clamp_int(max_retries, _MIN_RETRIES, _MAX_RETRIES),
        failover_after_retries=_clamp_int(failover_after, _MIN_FAILOVER_AFTER, _MAX_FAILOVER_AFTER),
        base_backoff_seconds=_clamp_float(backoff, _MIN_BACKOFF, _MAX_BACKOFF),
    )


def _clamp_int(value: int | None, minimum: int, maximum: int) -> int | None:
    if value is None:
        return None
    return max(minimum, min(maximum, value))


def _clamp_float(value: float | None, minimum: float, maximum: float) -> float | None:
    if value is None:
        return None
    return float(max(minimum, min(maximum, value)))


__all__ = [
    "FailoverPolicyHints",
    "failover_hints_from_candidate",
]
