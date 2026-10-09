"""Compatibility exports for collection-owned tolerance analysis.

New integrations should import
``ansible_collections.general_ludd.materials.plugins.module_utils.tolerance_model``.
This module intentionally contains no second implementation.
"""

from ansible_collections.general_ludd.materials.plugins.module_utils.tolerance_model import (
    MAX_DIMENSION_PAIRS,
    MAX_PAYLOAD_BYTES,
    MAX_UNIT_CHARS,
    STATE_FAIL_CLOSED,
    STATE_OK,
    TOLERANCE_OPERATIONS,
    ToleranceChain,
    ToleranceModelError,
    assess_assembly,
    evaluate_tolerance_model,
    process_capability,
)

__all__ = [
    "MAX_DIMENSION_PAIRS",
    "MAX_PAYLOAD_BYTES",
    "MAX_UNIT_CHARS",
    "STATE_FAIL_CLOSED",
    "STATE_OK",
    "TOLERANCE_OPERATIONS",
    "ToleranceChain",
    "ToleranceModelError",
    "assess_assembly",
    "evaluate_tolerance_model",
    "process_capability",
]
