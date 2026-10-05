"""Runtime compatibility gate for FreeLLMAPI provenance bundles.

The gate decides whether a validated bundle's ABI version is supported by the
current runtime.  It never exposes upstream content.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from general_ludd.models.freellmapi_provenance_bundle import FreeLLMAPIProvenanceBundle

FREELLMAPI_SUPPORTED_ABI_VERSIONS = frozenset({1})


class FreeLLMAPICompatibilityGateStatus(StrEnum):
    """Stable compatibility decision categories."""

    COMPATIBLE = "compatible"
    INCOMPATIBLE_ABI = "incompatible_abi"


@dataclass(frozen=True, slots=True)
class FreeLLMAPICompatibilityGateResult:
    """Content-free compatibility decision for one bundle."""

    status: FreeLLMAPICompatibilityGateStatus
    compatible: bool
    bundle_evidence_id: str
    reason: str

    def __str__(self) -> str:
        """Return a content-free string representation."""
        return (
            f"FreeLLMAPICompatibilityGateResult("
            f"status={self.status.value}, "
            f"bundle_evidence_id={self.bundle_evidence_id}"
            f")"
        )


def check_bundle_compatibility(
    bundle: FreeLLMAPIProvenanceBundle,
) -> FreeLLMAPICompatibilityGateResult:
    """Check whether a validated bundle is compatible with the current runtime.

    Args:
        bundle: A provenance bundle that has already passed
            :func:`validate_provenance_bundle`.

    Returns:
        A content-free compatibility decision.
    """
    if bundle.abi_version in FREELLMAPI_SUPPORTED_ABI_VERSIONS:
        return FreeLLMAPICompatibilityGateResult(
            status=FreeLLMAPICompatibilityGateStatus.COMPATIBLE,
            compatible=True,
            bundle_evidence_id=bundle.evidence_id,
            reason=(
                f"ABI version {bundle.abi_version} is compatible with "
                f"runtime supported versions {sorted(FREELLMAPI_SUPPORTED_ABI_VERSIONS)}"
            ),
        )
    return FreeLLMAPICompatibilityGateResult(
        status=FreeLLMAPICompatibilityGateStatus.INCOMPATIBLE_ABI,
        compatible=False,
        bundle_evidence_id=bundle.evidence_id,
        reason=(
            f"ABI version {bundle.abi_version} is unsupported; "
            f"runtime supports {sorted(FREELLMAPI_SUPPORTED_ABI_VERSIONS)}"
        ),
    )


__all__ = [
    "FREELLMAPI_SUPPORTED_ABI_VERSIONS",
    "FreeLLMAPICompatibilityGateResult",
    "FreeLLMAPICompatibilityGateStatus",
    "check_bundle_compatibility",
]
