"""Units service for the materials engineering collection (spec MATE-001 §5).

Single source of truth for unit conversion. All property/load/geometry values
MUST carry an explicit unit string drawn from this module's registry; any
conversion that crosses dimensions (stress ↔ length, temperature ↔ stress)
raises :class:`DimensionMismatch` per MATE-SAFE-006 (fail closed).

The conversion table is deliberately a plain dict so it is auditable and easy
to extend; no magic. SI base units are the canonical pivot for each dimension.
"""

from __future__ import annotations

from typing import Final


class DimensionMismatch(ValueError):
    """Raised when converting between units of different physical dimensions."""


class UnknownUnit(ValueError):
    """Raised when a unit token is not in the registry."""


# Each dimension maps unit -> (factor_to_canonical, offset).
# value_in_canonical = value_in_unit * factor + offset
# value_in_unit       = (value_in_canonical - offset) / factor
#
# For affine scales (temperature °C/°F), the offset captures the zero-shift.
# Canonical units: stress -> Pa, length -> mm, temperature -> K.

_UNIT_TABLE: Final[dict[str, dict[str, tuple[float, float]]]] = {
    "stress": {
        "Pa": (1.0, 0.0),
        "MPa": (1_000_000.0, 0.0),
        "GPa": (1_000_000_000.0, 0.0),
        "ksi": (6_894_757.293168361, 0.0),  # 1 ksi = 1000 psi = 6894757.293... Pa
        "psi": (6_894.757293168361, 0.0),
    },
    "length": {
        "mm": (1.0, 0.0),
        "m": (1_000.0, 0.0),
        "in": (25.4, 0.0),
        "ft": (304.8, 0.0),
        "um": (1e-3, 0.0),
    },
    "temperature": {
        "K": (1.0, 0.0),
        "C": (1.0, 273.15),
        "F": (5.0 / 9.0, 459.67 * 5.0 / 9.0),  # K = (F + 459.67) * 5/9
    },
}


def _build_index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for dim, table in _UNIT_TABLE.items():
        for unit in table:
            idx[unit] = dim
    return idx


_DIM_OF: Final[dict[str, str]] = _build_index()


def dim_of(unit: str) -> str:
    """Return the physical dimension name for ``unit``.

    Raises:
        UnknownUnit: if ``unit`` is not registered.
    """
    dim = _DIM_OF.get(unit)
    if dim is None:
        raise UnknownUnit(f"unknown unit: {unit!r}")
    return dim


def known_units() -> tuple[str, ...]:
    """Return all registered unit tokens (sorted, deterministic for tests)."""
    return tuple(sorted(_DIM_OF.keys()))


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert ``value`` from ``from_unit`` to ``to_unit``.

    The two units MUST share a physical dimension. Cross-dimension conversion
    (e.g. MPa -> mm) is a hard error per MATE-SAFE-006.

    Raises:
        UnknownUnit: either token is unregistered.
        DimensionMismatch: units are in different dimensions.
    """
    from_dim = dim_of(from_unit)
    to_dim = dim_of(to_unit)
    if from_dim != to_dim:
        raise DimensionMismatch(f"incompatible dimensions: {from_unit!r} ({from_dim}) -> {to_unit!r} ({to_dim})")

    f_factor, f_offset = _UNIT_TABLE[from_dim][from_unit]
    t_factor, t_offset = _UNIT_TABLE[to_dim][to_unit]

    canonical = value * f_factor + f_offset
    return (canonical - t_offset) / t_factor


__all__ = [
    "DimensionMismatch",
    "UnknownUnit",
    "convert",
    "dim_of",
    "known_units",
]
