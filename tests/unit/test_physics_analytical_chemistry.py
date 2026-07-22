"""Tests for analytical chemistry helper calculations."""

from __future__ import annotations

import pytest

from general_ludd.physics.analytical_chemistry import (
    CalibrationStandard,
    calibrate_instrument,
    compute_retention_index,
    identify_from_mass_spectrum,
)


def _standard(value: float) -> CalibrationStandard:
    return {
        "name": f"{value:g} ppm standard",
        "certified_value": value,
        "uncertainty": 0.01,
        "unit": "ppm",
        "matrix": "water",
    }


def test_mass_spectrum_matches_common_fragments_by_mz_window() -> None:
    result = identify_from_mass_spectrum(
        [
            {"mz": 14.9, "intensity": 1200.0, "assignment": "", "delta_ppm": 0.0},
            {"mz": 18.2, "intensity": 400.0, "assignment": "", "delta_ppm": 0.0},
            {"mz": 500.0, "intensity": 50.0, "assignment": "", "delta_ppm": 0.0},
        ]
    )

    assert result["match_count"] == 2
    assert result["matched_fragments"] == ["methyl loss", "water loss"]


def test_retention_index_uses_log_interpolation() -> None:
    ri = compute_retention_index(8.0, 5.0, 10.0, 10)

    assert ri == pytest.approx(1067.8, abs=0.1)


def test_retention_index_rejects_out_of_bracket_retention_time() -> None:
    with pytest.raises(ValueError, match="not between references"):
        compute_retention_index(4.0, 5.0, 10.0, 10)


def test_calibrate_instrument_fits_linear_response() -> None:
    calibration = calibrate_instrument([_standard(1.0), _standard(2.0), _standard(3.0)], [2.0, 4.0, 6.0])

    assert calibration["slope"] == pytest.approx(2.0)
    assert calibration["intercept"] == pytest.approx(0.0)
    assert calibration["r_squared"] == pytest.approx(1.0)


def test_calibrate_instrument_rejects_constant_standards() -> None:
    with pytest.raises(ValueError, match="same certified value"):
        calibrate_instrument([_standard(1.0), _standard(1.0)], [2.0, 3.0])
