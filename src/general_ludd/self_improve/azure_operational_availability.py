"""Compatibility facade for infrastructure-owned Azure availability evidence.

New callers should import :mod:`general_ludd.infra.azure_operational_availability`.
The legacy path remains stable for downstream self-improvement integrations.
"""

from general_ludd.infra.azure_operational_availability import (
    AzureAvailabilityAssessment,
    AzureAvailabilityIndex,
    AzureAvailabilityScope,
    AzureAvailabilityTerminal,
    AzureInfrastructurePhase,
    AzureOperationalEvidenceError,
    build_azure_availability_scope,
    load_azure_availability_index,
    record_azure_availability_terminal,
)

__all__ = (
    "AzureAvailabilityAssessment",
    "AzureAvailabilityIndex",
    "AzureAvailabilityScope",
    "AzureAvailabilityTerminal",
    "AzureInfrastructurePhase",
    "AzureOperationalEvidenceError",
    "build_azure_availability_scope",
    "load_azure_availability_index",
    "record_azure_availability_terminal",
)
