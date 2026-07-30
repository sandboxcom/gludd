"""CHEM-007 stoichiometry — amounts, concentration, yield, uncertainty.

Implements CHEM-007 from ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §2 and
§10. Every numerical result carries a unit and, where applicable, an
uncertainty. CHEM-AT-007: stoichiometry round-trips units and propagates
uncertainty within suite-pinned tolerance.

Functions:

* ``calculate_amounts`` — convert between mass (g) and amount of substance
  (mol) using the molar mass of a formula. Supports either ``mass_g`` or
  ``moles`` as the input.
* ``calculate_concentration`` — molarity (mol/L) from moles and volume,
  with the missing-variable solved for whichever of {moles, volume_L,
  concentration} is omitted.
* ``calculate_yield`` — percent yield (actual / theoretical * 100) with
  Gaussian uncertainty propagation; yields above 100% are reported with a
  limitation flag per spec §9.

This module delegates formula parsing and atomic-weight tables to
``general_ludd.chemistry.core`` rather than re-implementing them.
"""

from __future__ import annotations

import importlib.util
import math
import os
from typing import Any

_CORE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "core.py",
)


def _load_core():
    spec = importlib.util.spec_from_file_location("chemistry_core_for_stoichiometry", _CORE_PATH)
    assert spec is not None and spec.loader is not None, "chemistry core spec failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_core = _load_core()
SCHEMA_VERSION = _core.SCHEMA_VERSION


def _value_record(
    name: str,
    value: float,
    unit: str,
    uncertainty: float = 0.0,
    method_id: str = _core.CANONICALIZER,
) -> dict[str, Any]:
    return _core._value_record(name, value, unit, uncertainty=uncertainty, method_id=method_id)


def _propagate_relative(value: float, *rel_uncertainties: float) -> float:
    """Gaussian uncertainty propagation: sqrt(sum(rel^2)) * value."""
    if not any(rel_uncertainties):
        return 0.0
    quad = sum(r * r for r in rel_uncertainties if r)
    return value * math.sqrt(quad)


# ---------------------------------------------------------------------------
# calculate_amounts
# ---------------------------------------------------------------------------


def calculate_amounts(
    mass_g: float | None = None,
    moles: float | None = None,
    formula: str = "",
    mass_uncertainty: float = 0.0,
    moles_uncertainty: float = 0.0,
) -> dict[str, Any]:
    """Convert between mass (g) and amount of substance (mol).

    Provide exactly one of ``mass_g`` or ``moles``. The other is computed
    from ``formula``'s molar mass.

    * ``mass_g`` given  -> returns amount_substance in ``mol``.
    * ``moles`` given   -> returns mass in ``g``.

    Uncertainty propagates from the atomic-weight table and any caller-
    supplied measurement uncertainty.
    """
    if not formula:
        raise ValueError("formula is required to convert mass <-> moles")

    mm = _core.molar_mass(formula)
    mm_value = mm["value"]
    mm_unc = mm.get("uncertainty", 0.0) or 0.0

    if mass_g is not None and moles is None:
        if mm_value <= 0:
            raise ValueError("molar mass must be positive")
        value = mass_g / mm_value
        rel_mass = (mass_uncertainty / mass_g) if mass_g else 0.0
        rel_mm = (mm_unc / mm_value) if mm_value else 0.0
        unc = _propagate_relative(value, rel_mass, rel_mm)
        return _value_record(
            "amount_substance",
            value,
            "mol",
            uncertainty=unc,
        )

    if moles is not None and mass_g is None:
        if mm_value <= 0:
            raise ValueError("molar mass must be positive")
        value = moles * mm_value
        rel_moles = (moles_uncertainty / moles) if moles else 0.0
        rel_mm = (mm_unc / mm_value) if mm_value else 0.0
        unc = _propagate_relative(value, rel_moles, rel_mm)
        return _value_record("mass", value, "g", uncertainty=unc)

    raise ValueError("provide exactly one of mass_g or moles")


# ---------------------------------------------------------------------------
# calculate_concentration
# ---------------------------------------------------------------------------


def calculate_concentration(
    moles: float | None = None,
    volume_L: float | None = None,
    concentration: float | None = None,
    moles_uncertainty: float = 0.0,
    volume_uncertainty: float = 0.0,
    concentration_uncertainty: float = 0.0,
) -> dict[str, Any]:
    """Molarity solver.

    Provide any two of {``moles``, ``volume_L``, ``concentration``}; the
    third is solved. Units:

    * concentration given/solved -> ``mol/L``
    * moles solved               -> ``mol``
    * volume solved              -> ``L``

    Uncertainty is propagated when the input(s) carry uncertainties.
    """
    provided = [x for x in (moles, volume_L, concentration) if x is not None]
    if len(provided) != 2:
        raise ValueError("provide exactly two of moles, volume_L, concentration")

    if concentration is None:
        if moles is None or volume_L is None:
            raise ValueError("moles and volume_L required to solve concentration")
        if volume_L == 0:
            raise ValueError("volume_L must be non-zero")
        value = moles / volume_L
        rel_moles = (moles_uncertainty / moles) if moles else 0.0
        rel_vol = (volume_uncertainty / volume_L) if volume_L else 0.0
        unc = _propagate_relative(value, rel_moles, rel_vol)
        return _value_record("concentration", value, "mol/L", uncertainty=unc)

    if moles is None:
        if volume_L is None or concentration is None:
            raise ValueError("volume_L and concentration required to solve moles")
        if concentration == 0:
            raise ValueError("concentration must be non-zero")
        value = concentration * volume_L
        rel_c = (concentration_uncertainty / concentration) if concentration else 0.0
        rel_vol = (volume_uncertainty / volume_L) if volume_L else 0.0
        unc = _propagate_relative(value, rel_c, rel_vol)
        return _value_record("amount_substance", value, "mol", uncertainty=unc)

    # volume is the missing variable
    if moles is None or concentration is None:
        raise ValueError("moles and concentration required to solve volume")
    if concentration == 0:
        raise ValueError("concentration must be non-zero")
    value = moles / concentration
    rel_moles = (moles_uncertainty / moles) if moles else 0.0
    rel_c = (concentration_uncertainty / concentration) if concentration else 0.0
    unc = _propagate_relative(value, rel_moles, rel_c)
    return _value_record("volume", value, "L", uncertainty=unc)


# ---------------------------------------------------------------------------
# calculate_yield
# ---------------------------------------------------------------------------


def calculate_yield(
    actual_g: float,
    theoretical_g: float,
    actual_unc: float = 0.0,
    theoretical_unc: float = 0.0,
) -> dict[str, Any]:
    """Percent yield ``100 * actual / theoretical`` with uncertainty.

    * ``theoretical_g`` must be > 0.
    * Yields above 100% are reported with a ``limitations`` flag rather than
      silently accepted (CHEM spec §9: ambiguous/over-theoretical results
      must surface a constraint).
    """
    if theoretical_g <= 0:
        raise ValueError("theoretical yield must be positive")

    pct = 100.0 * actual_g / theoretical_g
    rel_actual = (actual_unc / actual_g) if actual_g else 0.0
    rel_theo = (theoretical_unc / theoretical_g) if theoretical_g else 0.0
    unc = _propagate_relative(pct, rel_actual, rel_theo)

    limitations: list[str] = []
    if pct > 100.0:
        limitations.append("yield>100: actual exceeds theoretical — check inputs")

    record = _value_record("yield", pct, "percent", uncertainty=unc)
    if limitations:
        record["limitations"] = limitations
    return record


__all__ = [
    "calculate_amounts",
    "calculate_concentration",
    "calculate_yield",
]
