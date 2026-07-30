"""CHEM-009 inventory — lot, purity, location, expiry, restrictions, custody.

Implements CHEM-009 from ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §2 and
supports CHEM-AT-010 ("Inventory tests reject expired/restricted/wrong-purity
lots and never silently substitute").

This module NEVER procures chemicals and NEVER silently substitutes one lot for
another. ``check_lot_suitability`` returns a verdict describing why a lot is
unsuitable; the caller is responsible for any explicit review or re-procurement
decision. Per spec §9 row "Inventory lot expired, restricted, or wrong purity",
unsuitable lots are excluded and require review.
"""

from __future__ import annotations

import importlib.util
import os
from types import ModuleType
from typing import Any

_CORE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "core.py")


def _load_core() -> ModuleType:
    spec = importlib.util.spec_from_file_location("chemistry_core_for_inventory", _CORE_PATH)
    assert spec is not None and spec.loader is not None, "chemistry core spec failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_core = _load_core()

SCHEMA_VERSION = _core.SCHEMA_VERSION


class InventoryRecord:
    """A lot-level inventory record.

    Attributes are intentionally simple primitives so records serialize to JSON
    without custom encoders. ``chain_of_custody`` is an ordered list of
    ``{actor, action, timestamp?}`` entries; corrections append, they do not
    rewrite history (mirrors the ELN rule in spec §8.4).
    """

    __slots__ = ("chain_of_custody", "expiry", "location", "lot", "purity", "restrictions")

    def __init__(
        self,
        lot: str,
        purity: float,
        location: str,
        expiry: str,
        restrictions: list[str] | None = None,
        chain_of_custody: list[dict[str, Any]] | None = None,
    ) -> None:
        self.lot = lot
        self.purity = float(purity)
        self.location = location
        self.expiry = expiry
        self.restrictions = list(restrictions or [])
        self.chain_of_custody = list(chain_of_custody or [])

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "lot": self.lot,
            "purity": self.purity,
            "location": self.location,
            "expiry": self.expiry,
            "restrictions": list(self.restrictions),
            "chain_of_custody": list(self.chain_of_custody),
        }

    # Mirror the .as_dict() alias used in some fixtures.
    as_dict.__doc__ = "Return the record as a JSON-serializable dict."


def _reason(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message}


def check_lot_suitability(
    record: InventoryRecord | dict[str, Any],
    required_purity: float,
    as_of: str,
) -> dict[str, Any]:
    """Check whether ``record`` is suitable for use under the declared constraints.

    A lot is suitable ONLY when all of:

    * ``expiry >= as_of`` (not expired);
    * ``restrictions`` is empty (not under a usage restriction);
    * ``purity >= required_purity`` (meets the protocol's purity floor).

    Unsuitable lots are NEVER silently substituted: the returned verdict always
    echoes the queried ``lot`` and contains no ``substituted_lot`` /
    ``replacement`` field. The caller is responsible for any explicit review
    or re-procurement decision (CHEM-AT-010, spec §9).
    """
    rec = record.as_dict() if isinstance(record, InventoryRecord) else dict(record)

    lot_id = rec.get("lot", "")
    purity = float(rec.get("purity", 0.0))
    expiry = str(rec.get("expiry", ""))
    restrictions = list(rec.get("restrictions", []))

    reasons: list[dict[str, Any]] = []
    if expiry and expiry < as_of:
        reasons.append(_reason("lot_expired", f"lot {lot_id} expired {expiry} (as_of {as_of})"))
    if restrictions:
        reasons.append(
            _reason(
                "lot_restricted",
                f"lot {lot_id} carries restrictions: {','.join(restrictions)}",
            )
        )
    if purity < required_purity:
        reasons.append(
            _reason(
                "lot_purity_insufficient",
                f"lot {lot_id} purity {purity:.4f} below required {required_purity:.4f}",
            )
        )

    suitable = not reasons
    return {
        "schema_version": SCHEMA_VERSION,
        "lot": lot_id,
        "suitable": suitable,
        "reasons": reasons,
    }


__all__ = [
    "InventoryRecord",
    "check_lot_suitability",
]
