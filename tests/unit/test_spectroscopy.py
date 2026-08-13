"""Unit tests for src/general_ludd/chemistry/spectroscopy.py."""

from __future__ import annotations

import pytest

from general_ludd.chemistry.spectroscopy import SUPPORTED_KINDS, SpectraAnalyzer


class TestSupportedKinds:
    def test_contains_expected_kinds(self):
        assert "NMR" in SUPPORTED_KINDS
        assert "IR" in SUPPORTED_KINDS
        assert "MS" in SUPPORTED_KINDS
        assert "Raman" in SUPPORTED_KINDS
        assert "UV-Vis" in SUPPORTED_KINDS
        assert "generic" in SUPPORTED_KINDS

    def test_is_frozenset(self):
        assert isinstance(SUPPORTED_KINDS, frozenset)


class TestSpectraAnalyzerInit:
    def test_valid_kinds(self):
        for k in SUPPORTED_KINDS:
            a = SpectraAnalyzer(k)
            assert a.kind == k

    def test_unsupported_kind_raises(self):
        with pytest.raises(ValueError, match="unsupported spectrum kind"):
            SpectraAnalyzer("XRD")

    def test_default_reference_is_empty(self):
        a = SpectraAnalyzer("NMR")
        assert a.reference == {}

    def test_reference_passed_in(self):
        ref = {"CH3": 1.2, "OH": 4.8}
        a = SpectraAnalyzer("NMR", reference=ref)
        assert a.reference == ref

    def test_default_tolerance(self):
        a = SpectraAnalyzer("NMR")
        assert a.tolerance == 5.0

    def test_custom_tolerance(self):
        a = SpectraAnalyzer("NMR", tolerance=0.5)
        assert a.tolerance == 0.5


class TestXUnitLabels:
    def test_returns_dict(self):
        a = SpectraAnalyzer("NMR")
        labels = a.x_unit_labels()
        assert labels["NMR"] == "ppm"
        assert labels["IR"] == "cm^-1"
        assert labels["MS"] == "m/z"
        assert labels["Raman"] == "cm^-1"
        assert labels["UV-Vis"] == "nm"
        assert labels["generic"] == "x"

    def test_returns_copy_not_reference(self):
        a = SpectraAnalyzer("NMR")
        labels = a.x_unit_labels()
        labels["NMR"] = "changed"
        a2 = SpectraAnalyzer("NMR")
        assert a2.x_unit_labels()["NMR"] == "ppm"


class TestDetectPeaks:
    def test_single_peak(self):
        a = SpectraAnalyzer("NMR")
        peaks = a.detect_peaks([1.0, 2.0, 3.0], [0.0, 5.0, 0.0])
        assert len(peaks) == 1
        assert peaks[0]["x"] == 2.0
        assert peaks[0]["height"] == 5.0

    def test_multiple_peaks(self):
        a = SpectraAnalyzer("IR")
        xs = list(range(10))
        ys = [0.0, 2.0, 1.0, 0.0, 3.0, 1.0, 0.0, 4.0, 2.0, 0.0]
        peaks = a.detect_peaks(xs, ys)
        assert len(peaks) == 3
        assert peaks[0]["x"] == 1.0
        assert peaks[1]["x"] == 4.0
        assert peaks[2]["x"] == 7.0

    def test_threshold_filters_low_peaks(self):
        a = SpectraAnalyzer("NMR")
        peaks = a.detect_peaks([1.0, 2.0, 3.0], [0.0, 0.2, 0.0], threshold=1.0)
        assert len(peaks) == 0

    def test_threshold_allows_high_peaks(self):
        a = SpectraAnalyzer("NMR")
        peaks = a.detect_peaks([1.0, 2.0, 3.0], [0.0, 1.5, 0.0], threshold=1.0)
        assert len(peaks) == 1

    def test_no_endpoint_peaks(self):
        a = SpectraAnalyzer("NMR")
        peaks = a.detect_peaks([1.0, 2.0, 3.0], [10.0, 0.0, 10.0])
        assert len(peaks) == 0

    def test_plateau_not_a_peak(self):
        a = SpectraAnalyzer("NMR")
        peaks = a.detect_peaks([1.0, 2.0, 3.0, 4.0], [0.0, 5.0, 5.0, 0.0])
        assert len(peaks) == 0

    def test_empty_return(self):
        a = SpectraAnalyzer("MS")
        peaks = a.detect_peaks([1.0, 2.0], [0.0, 0.0])
        assert peaks == []

    def test_unequal_length_raises(self):
        a = SpectraAnalyzer("NMR")
        with pytest.raises(ValueError, match="xs and ys must have equal length"):
            a.detect_peaks([1.0, 2.0], [1.0])

    def test_peak_has_expected_keys(self):
        a = SpectraAnalyzer("MS")
        peaks = a.detect_peaks([1.0, 2.0, 3.0], [0.0, 5.0, 0.0])
        assert set(peaks[0].keys()) == {"x", "height", "index", "kind"}
        assert peaks[0]["kind"] == "MS"

    def test_equal_to_threshold_is_peak(self):
        a = SpectraAnalyzer("NMR")
        peaks = a.detect_peaks([1.0, 2.0, 3.0], [0.0, 1.0, 0.0], threshold=1.0)
        assert len(peaks) == 1
        assert peaks[0]["height"] == 1.0


class TestIntegrate:
    def test_simple_trapezoid(self):
        a = SpectraAnalyzer("NMR")
        area = a.integrate([0.0, 1.0], [0.0, 2.0])
        assert area == pytest.approx(1.0)

    def test_two_segments(self):
        a = SpectraAnalyzer("IR")
        area = a.integrate([0.0, 1.0, 2.0], [0.0, 2.0, 2.0])
        assert area == pytest.approx(3.0)

    def test_constant_y(self):
        a = SpectraAnalyzer("UV-Vis")
        area = a.integrate([0.0, 10.0], [5.0, 5.0])
        assert area == pytest.approx(50.0)

    def test_full_range_bounds_default(self):
        a = SpectraAnalyzer("NMR")
        area_full = a.integrate([0.0, 1.0, 2.0], [0.0, 2.0, 0.0])
        area_explicit = a.integrate([0.0, 1.0, 2.0], [0.0, 2.0, 0.0], x_min=0.0, x_max=2.0)
        assert area_full == pytest.approx(area_explicit)

    def test_clipped_window(self):
        a = SpectraAnalyzer("NMR")
        area = a.integrate([0.0, 1.0, 2.0, 3.0], [0.0, 2.0, 2.0, 0.0], x_min=1.0, x_max=2.0)
        assert area == pytest.approx(2.0)

    def test_window_no_overlap(self):
        a = SpectraAnalyzer("NMR")
        area = a.integrate([0.0, 1.0], [10.0, 20.0], x_min=5.0, x_max=10.0)
        assert area == 0.0

    def test_equal_length_validation(self):
        a = SpectraAnalyzer("NMR")
        with pytest.raises(ValueError, match="xs and ys must have equal length"):
            a.integrate([1.0, 2.0], [1.0])

    def test_x_max_less_than_x_min_raises(self):
        a = SpectraAnalyzer("NMR")
        with pytest.raises(ValueError, match="x_max must be >= x_min"):
            a.integrate([0.0, 1.0], [0.0, 1.0], x_min=5.0, x_max=0.0)

    def test_zero_span_no_contribution(self):
        a = SpectraAnalyzer("NMR")
        area = a.integrate([1.0, 1.0, 2.0], [0.0, 0.0, 0.0])
        assert area == 0.0

    def test_partially_clipped_first_segment(self):
        a = SpectraAnalyzer("NMR")
        area = a.integrate([0.0, 10.0], [0.0, 10.0], x_min=3.0, x_max=10.0)
        expected = 0.5 * (3.0 + 10.0) * 7.0
        assert area == pytest.approx(expected)


class TestAssign:
    def test_successful_assignment(self):
        a = SpectraAnalyzer("NMR", reference={"CH3": 1.2, "OH": 4.8}, tolerance=5.0)
        result = a.assign(1.3)
        assert result["status"] == "succeeded"
        assert result["assignment"] == "CH3"
        assert result["observed_x"] == 1.3
        assert "reference_x" in result
        assert result["delta"] == pytest.approx(0.1)

    def test_no_reference_table(self):
        a = SpectraAnalyzer("NMR")
        result = a.assign(1.3)
        assert result["status"] == "degraded"
        assert result["assignment"] is None
        assert "no reference table loaded" in result["limitations"][0]
        assert len(result["errors"]) == 1

    def test_tolerance_exceeded(self):
        a = SpectraAnalyzer("NMR", reference={"CH3": 1.2}, tolerance=0.05)
        result = a.assign(10.0)
        assert result["status"] == "degraded"
        assert result["assignment"] == "unknown"
        assert result["nearest"] == "CH3"
        assert result["nearest_delta"] == pytest.approx(8.8)

    def test_exact_reference_match(self):
        a = SpectraAnalyzer("IR", reference={"C=O": 1750.0}, tolerance=10.0)
        result = a.assign(1750.0)
        assert result["status"] == "succeeded"
        assert result["assignment"] == "C=O"
        assert result["delta"] == 0.0

    def test_nearest_label_picked(self):
        a = SpectraAnalyzer("NMR", reference={"A": 1.0, "B": 2.0, "C": 3.0}, tolerance=5.0)
        result = a.assign(2.2)
        assert result["assignment"] == "B"

    def test_observed_x_is_float(self):
        a = SpectraAnalyzer("NMR", reference={"X": 1.0})
        result = a.assign(1)
        assert isinstance(result["observed_x"], float)

    def test_schema_version_present(self):
        a = SpectraAnalyzer("NMR", reference={"X": 1.0})
        result = a.assign(1.0)
        assert result["schema_version"] == "1.0"


class TestMatchSpectrum:
    def test_identical_spectra(self):
        a = SpectraAnalyzer("NMR")
        result = a.match_spectrum([1.0, 2.0], [0.5, 1.0], [1.0, 2.0], [0.5, 1.0])
        assert result["status"] == "succeeded"
        assert result["similarity"] == pytest.approx(1.0)

    def test_orthogonal_spectra(self):
        a = SpectraAnalyzer("NMR")
        result = a.match_spectrum([0.0, 1.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0])
        assert result["similarity"] == pytest.approx(0.0)

    def test_resampled_different_grids(self):
        a = SpectraAnalyzer("IR")
        result = a.match_spectrum([0.0, 2.0], [0.0, 2.0], [0.0, 1.0], [0.0, 1.0])
        assert 0.7 < result["similarity"] < 1.0

    def test_perfect_overlap_different_grids(self):
        a = SpectraAnalyzer("IR")
        result = a.match_spectrum([0.0, 1.0], [0.0, 1.0], [1.0, 2.0], [1.0, 2.0])
        assert 0.7 < result["similarity"] < 1.0

    def test_zero_vector_returns_zero_similarity(self):
        a = SpectraAnalyzer("MS")
        result = a.match_spectrum([1.0, 2.0], [0.0, 0.0], [1.0, 2.0], [1.0, 2.0])
        assert result["similarity"] == 0.0

    def test_both_zero_vectors(self):
        a = SpectraAnalyzer("MS")
        result = a.match_spectrum([1.0, 2.0], [0.0, 0.0], [1.0, 2.0], [0.0, 0.0])
        assert result["similarity"] == 0.0

    def test_unequal_length_raises(self):
        a = SpectraAnalyzer("NMR")
        with pytest.raises(ValueError, match="xs and ys must have equal length"):
            a.match_spectrum([1.0, 2.0], [1.0], [1.0, 2.0], [0.0, 1.0])

    def test_result_has_expected_keys(self):
        a = SpectraAnalyzer("UV-Vis")
        result = a.match_spectrum([1.0, 2.0], [0.5, 1.0], [1.0, 2.0], [0.5, 1.0])
        assert set(result.keys()) == {
            "schema_version",
            "status",
            "similarity",
            "kind",
            "method_id",
            "run_id",
            "grid_points",
            "errors",
        }
        assert result["kind"] == "UV-Vis"
        assert result["status"] == "succeeded"
        assert result["grid_points"] == 2

    def test_partial_overlap(self):
        a = SpectraAnalyzer("NMR")
        result = a.match_spectrum([0.0, 1.0], [1.0, 2.0], [1.0, 2.0], [2.0, 1.0])
        assert 0.5 < result["similarity"] < 1.0

    def test_run_id_is_unique(self):
        a = SpectraAnalyzer("NMR")
        r1 = a.match_spectrum([1.0], [1.0], [1.0], [1.0])
        r2 = a.match_spectrum([1.0], [1.0], [1.0], [1.0])
        assert r1["run_id"] != r2["run_id"]

    def test_schema_version(self):
        a = SpectraAnalyzer("NMR")
        result = a.match_spectrum([1.0], [1.0], [1.0], [1.0])
        assert result["schema_version"] == "1.0"


class TestEdgeCases:
    def test_single_point_spectrum(self):
        a = SpectraAnalyzer("MS")
        result = a.match_spectrum([1.0], [3.0], [1.0], [3.0])
        assert result["similarity"] == pytest.approx(1.0)
        assert result["grid_points"] == 1

    def test_detect_peaks_two_points_no_peaks(self):
        a = SpectraAnalyzer("NMR")
        peaks = a.detect_peaks([1.0, 2.0], [5.0, 5.0])
        assert peaks == []

    def test_integrate_single_segment_clipped_right(self):
        a = SpectraAnalyzer("NMR")
        area = a.integrate([0.0, 10.0], [0.0, 10.0], x_min=0.0, x_max=3.0)
        expected = 0.5 * (0.0 + 3.0) * 3.0
        assert area == pytest.approx(expected)
