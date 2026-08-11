"""Tests for the materials units service (spec MATE-001 §5).

Verifies:
  - dim_of resolves every registered unit to its dimension.
  - UnknownUnit raised for unregistered tokens.
  - known_units returns a deterministic sorted tuple.
  - convert handles same-dimension conversions (stress, length, temperature).
  - convert handles affine temperature scales (C, F, K).
  - DimensionMismatch raised for cross-dimension conversions.
  - UnknownUnit raised for missing from_unit or to_unit.
  - Negative / zero / large values convert correctly.
"""

from __future__ import annotations

import pytest

from general_ludd.materials.units import (
    DimensionMismatch,
    UnknownUnit,
    convert,
    dim_of,
    known_units,
)


class TestDimOf:
    def test_stress_units(self):
        assert dim_of("Pa") == "stress"
        assert dim_of("MPa") == "stress"
        assert dim_of("GPa") == "stress"
        assert dim_of("ksi") == "stress"
        assert dim_of("psi") == "stress"

    def test_length_units(self):
        assert dim_of("mm") == "length"
        assert dim_of("m") == "length"
        assert dim_of("in") == "length"
        assert dim_of("ft") == "length"
        assert dim_of("um") == "length"

    def test_temperature_units(self):
        assert dim_of("K") == "temperature"
        assert dim_of("C") == "temperature"
        assert dim_of("F") == "temperature"

    def test_unknown_unit_raises(self):
        with pytest.raises(UnknownUnit, match="unknown unit"):
            dim_of("lightyear")

    def test_empty_string_raises(self):
        with pytest.raises(UnknownUnit):
            dim_of("")

    def test_nonexistent_si_prefix_variant_raises(self):
        with pytest.raises(UnknownUnit):
            dim_of("kPa")


class TestKnownUnits:
    def test_returns_nonempty_tuple(self):
        units = known_units()
        assert isinstance(units, tuple)
        assert len(units) >= 10

    def test_sorted(self):
        units = known_units()
        assert units == tuple(sorted(units))

    def test_contains_all_stress_units(self):
        units = set(known_units())
        for u in ("Pa", "MPa", "GPa", "ksi", "psi"):
            assert u in units

    def test_contains_all_length_units(self):
        units = set(known_units())
        for u in ("mm", "m", "in", "ft", "um"):
            assert u in units

    def test_contains_all_temperature_units(self):
        units = set(known_units())
        for u in ("K", "C", "F"):
            assert u in units


class TestConvertStress:
    def test_identity_pa(self):
        assert convert(1.0, "Pa", "Pa") == pytest.approx(1.0)

    def test_mpa_to_pa(self):
        assert convert(1.0, "MPa", "Pa") == pytest.approx(1_000_000.0)

    def test_pa_to_mpa(self):
        assert convert(1_000_000.0, "Pa", "MPa") == pytest.approx(1.0)

    def test_gpa_to_mpa(self):
        assert convert(1.0, "GPa", "MPa") == pytest.approx(1_000.0)

    def test_ksi_to_mpa_approx(self):
        assert convert(1.0, "ksi", "MPa") == pytest.approx(6.894757293168361)

    def test_psi_to_ksi(self):
        assert convert(1_000.0, "psi", "ksi") == pytest.approx(1.0)

    def test_zero_stress(self):
        assert convert(0.0, "MPa", "Pa") == pytest.approx(0.0)
        assert convert(0.0, "Pa", "MPa") == pytest.approx(0.0)

    def test_negative_stress(self):
        assert convert(-1.0, "MPa", "Pa") == pytest.approx(-1_000_000.0)

    def test_large_stress(self):
        assert convert(1_000.0, "GPa", "Pa") == pytest.approx(1_000_000_000_000.0)


class TestConvertLength:
    def test_identity_mm(self):
        assert convert(1.0, "mm", "mm") == pytest.approx(1.0)

    def test_m_to_mm(self):
        assert convert(1.0, "m", "mm") == pytest.approx(1_000.0)

    def test_mm_to_m(self):
        assert convert(1_000.0, "mm", "m") == pytest.approx(1.0)

    def test_in_to_mm(self):
        assert convert(1.0, "in", "mm") == pytest.approx(25.4)

    def test_mm_to_in(self):
        assert convert(25.4, "mm", "in") == pytest.approx(1.0)

    def test_ft_to_mm(self):
        assert convert(1.0, "ft", "mm") == pytest.approx(304.8)

    def test_ft_to_in(self):
        assert convert(1.0, "ft", "in") == pytest.approx(12.0)

    def test_um_to_mm(self):
        assert convert(1_000.0, "um", "mm") == pytest.approx(1.0)

    def test_mm_to_um(self):
        assert convert(1.0, "mm", "um") == pytest.approx(1_000.0)

    def test_um_to_m(self):
        assert convert(1_000_000.0, "um", "m") == pytest.approx(1.0)

    def test_zero_length(self):
        assert convert(0.0, "mm", "in") == pytest.approx(0.0)

    def test_negative_length(self):
        assert convert(-10.0, "mm", "in") == pytest.approx(-10.0 / 25.4)


class TestConvertTemperature:
    def test_identity_k(self):
        assert convert(300.0, "K", "K") == pytest.approx(300.0)

    def test_k_to_c(self):
        assert convert(273.15, "K", "C") == pytest.approx(0.0)

    def test_c_to_k(self):
        assert convert(0.0, "C", "K") == pytest.approx(273.15)

    def test_c_to_f_boiling(self):
        assert convert(100.0, "C", "F") == pytest.approx(212.0)

    def test_f_to_c_freezing(self):
        assert convert(32.0, "F", "C") == pytest.approx(0.0)

    def test_f_to_k(self):
        assert convert(32.0, "F", "K") == pytest.approx(273.15)

    def test_k_to_f(self):
        assert convert(273.15, "K", "F") == pytest.approx(32.0)

    def test_absolute_zero_c(self):
        assert convert(-273.15, "C", "K") == pytest.approx(0.0)

    def test_absolute_zero_f(self):
        assert convert(-459.67, "F", "K") == pytest.approx(0.0)

    def test_negative_fahrenheit(self):
        result = convert(-40.0, "F", "C")
        assert result == pytest.approx(-40.0)

    def test_high_temperature_kelvin(self):
        assert convert(0.0, "K", "C") == pytest.approx(-273.15)


class TestConvertErrors:
    def test_unknown_from_unit(self):
        with pytest.raises(UnknownUnit):
            convert(1.0, "lightyear", "mm")

    def test_unknown_to_unit(self):
        with pytest.raises(UnknownUnit):
            convert(1.0, "mm", "lightyear")

    def test_both_unknown(self):
        with pytest.raises(UnknownUnit):
            convert(1.0, "foo", "bar")

    def test_dimension_mismatch_stress_to_length(self):
        with pytest.raises(DimensionMismatch, match="incompatible dimensions"):
            convert(1.0, "MPa", "mm")

    def test_dimension_mismatch_length_to_stress(self):
        with pytest.raises(DimensionMismatch, match="incompatible dimensions"):
            convert(1.0, "mm", "MPa")

    def test_dimension_mismatch_temperature_to_stress(self):
        with pytest.raises(DimensionMismatch, match="incompatible dimensions"):
            convert(300.0, "K", "MPa")

    def test_dimension_mismatch_stress_to_temperature(self):
        with pytest.raises(DimensionMismatch, match="incompatible dimensions"):
            convert(100.0, "Pa", "C")

    def test_dimension_mismatch_length_to_temperature(self):
        with pytest.raises(DimensionMismatch, match="incompatible dimensions"):
            convert(1.0, "m", "K")
