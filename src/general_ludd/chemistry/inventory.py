"""Compatibility exports for collection-owned chemical lot admission.

New integrations should import
``ansible_collections.general_ludd.chemistry.plugins.module_utils.lot_admission``.
This module intentionally contains no second evaluator.
"""

from ansible_collections.general_ludd.chemistry.plugins.module_utils.lot_admission import (
    MAX_RESTRICTIONS,
    MAX_TEXT_CHARS,
    REASON_CODES,
    SCHEMA_VERSION,
    InventoryRecord,
    LotAdmissionError,
    check_lot_suitability,
    evaluate_lot_admission,
)

__all__ = [
    "MAX_RESTRICTIONS",
    "MAX_TEXT_CHARS",
    "REASON_CODES",
    "SCHEMA_VERSION",
    "InventoryRecord",
    "LotAdmissionError",
    "check_lot_suitability",
    "evaluate_lot_admission",
]
