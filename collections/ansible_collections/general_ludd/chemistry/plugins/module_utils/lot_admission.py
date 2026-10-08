"""Bounded, fail-closed admission for one declared chemical inventory lot.

This module is the canonical implementation shared by the collection module and
the core compatibility surface. It is deliberately pure: evaluating a lot does
not open files, contact services, spawn processes, or retain state.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import date
from typing import Any

SCHEMA_VERSION = "1.0"
MAX_TEXT_CHARS = 128
MAX_RESTRICTIONS = 32
REASON_CODES = (
    "lot_expired",
    "lot_restricted",
    "lot_purity_insufficient",
)

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


class LotAdmissionError(ValueError):
    """Raised when a lot cannot be evaluated within the bounded contract."""


def _bounded_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise LotAdmissionError(f"{field} must be a non-empty string without padding")
    if len(value) > MAX_TEXT_CHARS:
        raise LotAdmissionError(f"{field} must be at most {MAX_TEXT_CHARS} characters")
    return value


def _bounded_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LotAdmissionError(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise LotAdmissionError(f"{field} must be finite")
    if not 0.0 <= number <= 1.0:
        raise LotAdmissionError(f"{field} must be between 0 and 1 inclusive")
    return number


def _strict_date(value: object, field: str) -> date:
    if not isinstance(value, str) or _ISO_DATE.fullmatch(value) is None:
        raise LotAdmissionError(f"{field} must be an ISO date in YYYY-MM-DD form")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise LotAdmissionError(
            f"{field} must be an ISO date in YYYY-MM-DD form"
        ) from exc
    return parsed


def _bounded_restrictions(value: object) -> list[str]:
    if not isinstance(value, list):
        raise LotAdmissionError("restrictions must be a list")
    if len(value) > MAX_RESTRICTIONS:
        raise LotAdmissionError(
            f"restrictions must contain at most {MAX_RESTRICTIONS} restrictions"
        )
    return [_bounded_text(item, "restriction") for item in value]


class InventoryRecord:
    """Validated lot-level compatibility record with JSON-safe output."""

    __slots__ = (
        "chain_of_custody",
        "expiry",
        "location",
        "lot",
        "purity",
        "restrictions",
    )

    def __init__(
        self,
        lot: str,
        purity: float,
        location: str,
        expiry: str,
        restrictions: list[str] | None = None,
        chain_of_custody: list[dict[str, Any]] | None = None,
    ) -> None:
        self.lot = _bounded_text(lot, "lot")
        self.purity = _bounded_number(purity, "purity")
        if not isinstance(location, str):
            raise LotAdmissionError("location must be a string")
        self.location = location
        _strict_date(expiry, "expiry")
        self.expiry = expiry
        declared_restrictions: object = [] if restrictions is None else restrictions
        self.restrictions = _bounded_restrictions(declared_restrictions)
        if chain_of_custody is not None and not isinstance(chain_of_custody, list):
            raise LotAdmissionError("chain_of_custody must be a list")
        self.chain_of_custody = list(chain_of_custody or [])

    def as_dict(self) -> dict[str, Any]:
        """Return the record as a detached JSON-serializable mapping."""
        return {
            "schema_version": SCHEMA_VERSION,
            "lot": self.lot,
            "purity": self.purity,
            "location": self.location,
            "expiry": self.expiry,
            "restrictions": list(self.restrictions),
            "chain_of_custody": list(self.chain_of_custody),
        }


def evaluate_lot_admission(
    *,
    lot: object,
    purity: object,
    expiry: object,
    restrictions: object,
    required_purity: object,
    as_of: object,
) -> dict[str, Any]:
    """Return a deterministic admission verdict for exactly one lot.

    An unsuitable lot is never replaced. The output echoes only the declared
    lot and fixed reason codes, leaving procurement or review to a human.
    """
    lot_id = _bounded_text(lot, "lot")
    actual_purity = _bounded_number(purity, "purity")
    purity_floor = _bounded_number(required_purity, "required_purity")
    expiry_date = _strict_date(expiry, "expiry")
    evaluation_date = _strict_date(as_of, "as_of")
    declared_restrictions = _bounded_restrictions(restrictions)

    reason_codes: list[str] = []
    if expiry_date < evaluation_date:
        reason_codes.append(REASON_CODES[0])
    if declared_restrictions:
        reason_codes.append(REASON_CODES[1])
    if actual_purity < purity_floor:
        reason_codes.append(REASON_CODES[2])

    admitted = not reason_codes
    return {
        "schema_version": SCHEMA_VERSION,
        "lot": lot_id,
        "admitted": admitted,
        "suitable": admitted,
        "requires_review": not admitted,
        "reason_codes": reason_codes,
        "reasons": [{"code": code} for code in reason_codes],
    }


def check_lot_suitability(
    record: InventoryRecord | Mapping[str, Any],
    required_purity: float,
    as_of: str,
) -> dict[str, Any]:
    """Compatibility wrapper around :func:`evaluate_lot_admission`."""
    if isinstance(record, InventoryRecord):
        values: Mapping[str, Any] = record.as_dict()
    elif isinstance(record, Mapping):
        values = record
    else:
        raise LotAdmissionError("record must be an InventoryRecord or mapping")
    return evaluate_lot_admission(
        lot=values.get("lot"),
        purity=values.get("purity"),
        expiry=values.get("expiry"),
        restrictions=values.get("restrictions", []),
        required_purity=required_purity,
        as_of=as_of,
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
