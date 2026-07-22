"""Tests for the analytical chemistry physics module."""

from __future__ import annotations

import math

import pytest

from general_ludd.physics.analytical_chemistry import (
    CALIBRATION_STANDARDS,
    CHROMATOGRAPHY_METHODS,
    SPECTROSCOPY_METHODS,
    calibrate_instrument,
    compute_retention_index,
    identify_from_mass_spectrum,
)


def test_reference_tables_cover_core_instrument_families() -> None:
    chromatography_names = {method["name"] for method in CHROMATOGRAPHY_METHODS}
    spectroscopy_names = {method["name"] for method in SPECTROSCOPY_METHODS}

    assert {"GC-MS", "HPLC-UV", "UPLC-MS/MS"}.issubset(chromatography_names)
    assert {"ICP-MS", "FTIR", "Raman"}.issubset(spectroscopy_names)


def test_identify_from_mass_spectrum_matches_common_neutral_losses() -> None:
    peaks = [
        {"mz": 15.1, "intensity": 100.0, "assignment": "", "delta_ppm": 0.0},
        {"mz": 18.0, "intensity": 40.0, "assignment": "", "delta_ppm": 0.0},
        {"mz": 44.99, "intensity": 25.0, "assignment": "", "delta_ppm": 0.0},
    ]

    result = identify_from_mass_spectrum(peaks)

    assert result["match_count"] == 3
    assert result["matched_fragments"] == ["methyl loss", "water loss", "COOH loss"]


def test_identify_from_mass_spectrum_handles_empty_input() -> None:
    assert identify_from_mass_spectrum([]) == {"matched_fragments": [], "match_count": 0}


def test_compute_retention_index_uses_log_interpolation() -> None:
    ri = compute_retention_index(tr=4.0, tr_ref_low=2.0, tr_ref_high=8.0, n_low=7)

    assert math.isclose(ri, 750.0, rel_tol=1e-9)


def test_compute_retention_index_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="positive"):
        compute_retention_index(tr=0.0, tr_ref_low=2.0, tr_ref_high=8.0, n_low=7)

    with pytest.raises(ValueError, match="between references"):
        compute_retention_index(tr=9.0, tr_ref_low=2.0, tr_ref_high=8.0, n_low=7)

    with pytest.raises(ValueError, match=">= 1"):
        compute_retention_index(tr=4.0, tr_ref_low=2.0, tr_ref_high=8.0, n_low=0)


def test_calibrate_instrument_single_and_multi_standard() -> None:
    single = calibrate_instrument([CALIBRATION_STANDARDS[0]], [2.5])
    multi = calibrate_instrument(CALIBRATION_STANDARDS[:3], [10.0, 10000.0, 5.0])

    assert single == {
        "slope": 2.5,
        "intercept": 0.0,
        "r_squared": 1.0,
        "calibration_valid": True,
    }
    assert math.isclose(multi["slope"], 10.0, rel_tol=1e-12)
    assert math.isclose(multi["intercept"], 0.0, abs_tol=1e-9)
    assert multi["r_squared"] == pytest.approx(1.0)
    assert multi["calibration_valid"] is True


def test_calibrate_instrument_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        calibrate_instrument([], [])

    with pytest.raises(ValueError, match="Length mismatch"):
        calibrate_instrument(CALIBRATION_STANDARDS[:2], [1.0])

    duplicate_standards = [CALIBRATION_STANDARDS[0], CALIBRATION_STANDARDS[0]]
    with pytest.raises(ValueError, match="same certified value"):
        calibrate_instrument(duplicate_standards, [1.0, 2.0])
