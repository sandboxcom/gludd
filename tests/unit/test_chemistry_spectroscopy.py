"""Unit tests for ``general_ludd.chemistry.spectroscopy`` (CHEM-014).

Covers CHEM-014 (spectroscopy analyzer: peak detection, integration,
reference assignment, spectrum matching) from
``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §8.3.

Loaded by file path so the suite is robust to ``sys.path`` variations
inside worktrees.
"""

from __future__ import annotations

import importlib.util
import math
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_SPECTROSCOPY_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "spectroscopy.py")


def _load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"{name} spec failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


spect = _load_module(_SPECTROSCOPY_PATH, "chemistry_spectroscopy_under_test")


# ---------------------------------------------------------------------------
# Constructor & kind validation (CHEM-AT-015)
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_all_supported_kinds_accepted(self):
        for kind in spect.SUPPORTED_KINDS:
            a = spect.SpectraAnalyzer(kind)
            assert a.kind == kind

    def test_unsupported_kind_raises_valueerror(self):
        try:
            spect.SpectraAnalyzer("XRD")
            raise AssertionError("expected ValueError")
        except ValueError as e:
            assert "unsupported spectrum kind 'XRD'" in str(e)

    def test_default_tolerance_is_five(self):
        a = spect.SpectraAnalyzer("IR")
        assert a.tolerance == 5.0

    def test_custom_tolerance_stored(self):
        a = spect.SpectraAnalyzer("MS", tolerance=0.1)
        assert a.tolerance == 0.1

    def test_reference_table_stored_as_copy(self):
        ref = {"C=O": 1700.0, "O-H": 3400.0}
        a = spect.SpectraAnalyzer("IR", reference=ref)
        assert a.reference == ref
        ref["N-H"] = 3300.0
        assert "N-H" not in a.reference


# ---------------------------------------------------------------------------
# x_unit_labels
# ---------------------------------------------------------------------------


class TestXUnitLabels:
    def test_all_supported_kinds_have_label(self):
        labels = spect.SpectraAnalyzer("NMR").x_unit_labels()
        for kind in spect.SUPPORTED_KINDS:
            assert kind in labels

    def test_label_values_match_spec(self):
        labels = spect.SpectraAnalyzer("IR").x_unit_labels()
        assert labels["NMR"] == "ppm"
        assert labels["IR"] == "cm^-1"
        assert labels["MS"] == "m/z"
        assert labels["Raman"] == "cm^-1"
        assert labels["UV-Vis"] == "nm"
        assert labels["generic"] == "x"


# ---------------------------------------------------------------------------
# detect_peaks
# ---------------------------------------------------------------------------


class TestDetectPeaks:
    def test_single_peak_middle(self):
        a = spect.SpectraAnalyzer("NMR")
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [0.1, 0.3, 5.0, 0.2, 0.1]
        peaks = a.detect_peaks(xs, ys, threshold=1.0)
        assert len(peaks) == 1
        assert peaks[0]["x"] == 3.0
        assert peaks[0]["height"] == 5.0
        assert peaks[0]["kind"] == "NMR"

    def test_two_peaks(self):
        a = spect.SpectraAnalyzer("IR")
        xs = [100.0, 200.0, 300.0, 400.0, 500.0, 600.0]
        ys = [0.1, 3.0, 0.2, 4.0, 0.3, 0.1]
        peaks = a.detect_peaks(xs, ys, threshold=1.0)
        assert len(peaks) == 2
        assert peaks[0]["x"] == 200.0
        assert peaks[1]["x"] == 400.0

    def test_plateau_not_peak(self):
        a = spect.SpectraAnalyzer("IR")
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [0.1, 5.0, 5.0, 5.0, 0.1]
        peaks = a.detect_peaks(xs, ys, threshold=1.0)
        assert len(peaks) == 0

    def test_endpoints_never_peaks(self):
        a = spect.SpectraAnalyzer("UV-Vis", tolerance=1.0)
        xs = [10.0, 20.0, 30.0, 40.0]
        ys = [5.0, 0.1, 0.1, 5.0]
        peaks = a.detect_peaks(xs, ys, threshold=0.0)
        assert len(peaks) == 0

    def test_threshold_filters_below(self):
        a = spect.SpectraAnalyzer("NMR")
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        ys = [0.1, 0.5, 4.0, 0.3, 0.1]
        peaks = a.detect_peaks(xs, ys, threshold=3.0)
        assert len(peaks) == 1
        assert peaks[0]["x"] == 3.0

    def test_equal_xs_ys_length_required(self):
        a = spect.SpectraAnalyzer("NMR")
        try:
            a.detect_peaks([1.0, 2.0], [1.0, 2.0, 3.0], threshold=0.5)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_empty_spectrum(self):
        a = spect.SpectraAnalyzer("MS")
        peaks = a.detect_peaks([], [], threshold=0.0)
        assert peaks == []

    def test_single_point_spectrum_no_peaks(self):
        a = spect.SpectraAnalyzer("MS")
        peaks = a.detect_peaks([5.0], [10.0], threshold=0.0)
        assert peaks == []

    def test_two_point_spectrum_no_peaks(self):
        a = spect.SpectraAnalyzer("MS")
        peaks = a.detect_peaks([5.0, 6.0], [10.0, 9.0], threshold=0.0)
        assert peaks == []


# ---------------------------------------------------------------------------
# integrate (trapezoidal)
# ---------------------------------------------------------------------------


class TestIntegrate:
    def test_full_range_integration(self):
        a = spect.SpectraAnalyzer("NMR")
        xs = [0.0, 1.0, 2.0, 3.0]
        ys = [1.0, 2.0, 3.0, 4.0]
        area = a.integrate(xs, ys)
        expected = 0.5 * (1 + 2) * 1 + 0.5 * (2 + 3) * 1 + 0.5 * (3 + 4) * 1
        assert math.isclose(area, expected)

    def test_constant_signal(self):
        a = spect.SpectraAnalyzer("UV-Vis")
        xs = [0.0, 5.0, 10.0]
        ys = [3.0, 3.0, 3.0]
        area = a.integrate(xs, ys)
        assert math.isclose(area, 30.0)

    def test_subrange(self):
        a = spect.SpectraAnalyzer("IR")
        xs = [0.0, 100.0, 200.0, 300.0, 400.0]
        ys = [0.0, 1.0, 2.0, 1.0, 0.0]
        area = a.integrate(xs, ys, x_min=100.0, x_max=300.0)
        expected = 0.5 * (1 + 2) * 100 + 0.5 * (2 + 1) * 100
        assert math.isclose(area, expected)

    def test_empty_range(self):
        a = spect.SpectraAnalyzer("NMR")
        xs = [0.0, 1.0, 2.0]
        ys = [1.0, 2.0, 3.0]
        area = a.integrate(xs, ys, x_min=10.0, x_max=20.0)
        assert math.isclose(area, 0.0)

    def test_equal_xs_ys_length_required(self):
        a = spect.SpectraAnalyzer("NMR")
        try:
            a.integrate([1.0, 2.0], [1.0])
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_x_max_lt_x_min_raises(self):
        a = spect.SpectraAnalyzer("NMR")
        try:
            a.integrate([1.0, 2.0, 3.0], [1.0, 1.0, 1.0], x_min=2.5, x_max=1.0)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_partial_first_trapezoid_clipping(self):
        a = spect.SpectraAnalyzer("NMR")
        xs = [0.0, 2.0, 4.0]
        ys = [0.0, 2.0, 4.0]
        area = a.integrate(xs, ys, x_min=1.0, x_max=3.0)
        expected = 0.5 * (1.0 + 3.0) * 2.0
        assert math.isclose(area, expected)

    def test_zero_span_between_points_skipped(self):
        a = spect.SpectraAnalyzer("NMR")
        xs = [0.0, 0.0, 1.0, 1.0]
        ys = [0.0, 1.0, 2.0, 3.0]
        area = a.integrate(xs, ys)
        assert math.isclose(area, 1.5)


# ---------------------------------------------------------------------------
# assign (reference peak assignment)
# ---------------------------------------------------------------------------


class TestAssign:
    def test_exact_match(self):
        ref = {"C=O": 1715.0, "C-H": 2900.0}
        a = spect.SpectraAnalyzer("IR", reference=ref, tolerance=10.0)
        result = a.assign(1715.0)
        assert result["status"] == "succeeded"
        assert result["assignment"] == "C=O"
        assert math.isclose(result["delta"], 0.0)

    def test_nearest_within_tolerance(self):
        ref = {"peak_a": 100.0, "peak_b": 200.0}
        a = spect.SpectraAnalyzer("MS", reference=ref, tolerance=15.0)
        result = a.assign(108.0)
        assert result["status"] == "succeeded"
        assert result["assignment"] == "peak_a"
        assert math.isclose(result["delta"], 8.0)
        assert math.isclose(result["reference_x"], 100.0)

    def test_outside_tolerance(self):
        ref = {"peak_a": 100.0}
        a = spect.SpectraAnalyzer("MS", reference=ref, tolerance=5.0)
        result = a.assign(120.0)
        assert result["status"] == "degraded"
        assert result["assignment"] == "unknown"

    def test_no_reference_table(self):
        a = spect.SpectraAnalyzer("NMR")
        result = a.assign(7.2)
        assert result["status"] == "degraded"
        assert result["assignment"] is None
        assert "no reference table loaded" in result["limitations"][0]

    def test_tie_goes_to_first(self):
        ref = {"A": 100.0, "B": 100.0}
        a = spect.SpectraAnalyzer("MS", reference=ref, tolerance=5.0)
        result = a.assign(100.0)
        assert result["status"] == "succeeded"
        assert result["assignment"] in {"A", "B"}
        assert math.isclose(result["delta"], 0.0)

    def test_schema_version_present(self):
        a = spect.SpectraAnalyzer("MS", reference={"m1": 50.0}, tolerance=2.0)
        result = a.assign(50.0)
        assert result["schema_version"] == spect.SCHEMA_VERSION


# ---------------------------------------------------------------------------
# match_spectrum (cosine similarity)
# ---------------------------------------------------------------------------


class TestMatchSpectrum:
    def test_identical_spectra(self):
        a = spect.SpectraAnalyzer("IR")
        xs = [0.0, 1.0, 2.0]
        ys = [1.0, 2.0, 3.0]
        result = a.match_spectrum(xs, ys, xs, ys)
        assert result["status"] == "succeeded"
        assert math.isclose(result["similarity"], 1.0)

    def test_orthogonal_spectra(self):
        a = spect.SpectraAnalyzer("IR")
        xs = [0.0, 1.0]
        ys_a = [1.0, 0.0]
        ys_b = [0.0, 1.0]
        result = a.match_spectrum(xs, ys_a, xs, ys_b)
        assert math.isclose(result["similarity"], 0.0)

    def test_different_grids_resampled(self):
        a = spect.SpectraAnalyzer("NMR")
        xs_a = [0.0, 10.0]
        ys_a = [1.0, 0.0]
        xs_b = [0.0, 5.0, 10.0]
        ys_b = [1.0, 0.5, 0.0]
        result = a.match_spectrum(xs_a, ys_a, xs_b, ys_b)
        assert result["status"] == "succeeded"
        assert 0.0 < result["similarity"] < 1.0

    def test_zero_vector_returns_zero(self):
        a = spect.SpectraAnalyzer("IR")
        xs = [0.0, 1.0, 2.0]
        zero_ys = [0.0, 0.0, 0.0]
        ys = [1.0, 2.0, 3.0]
        result = a.match_spectrum(xs, zero_ys, xs, ys)
        assert math.isclose(result["similarity"], 0.0)

    def test_both_zero_vectors_returns_zero(self):
        a = spect.SpectraAnalyzer("IR")
        xs = [0.0, 1.0]
        zero_ys = [0.0, 0.0]
        result = a.match_spectrum(xs, zero_ys, xs, zero_ys)
        assert math.isclose(result["similarity"], 0.0)

    def test_unequal_length_raises(self):
        a = spect.SpectraAnalyzer("IR")
        try:
            a.match_spectrum([1.0, 2.0], [1.0], [3.0, 4.0], [2.0, 3.0])
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def test_empty_spectra(self):
        a = spect.SpectraAnalyzer("MS")
        result = a.match_spectrum([], [], [], [])
        assert math.isclose(result["similarity"], 0.0)

    def test_run_id_is_unique(self):
        a = spect.SpectraAnalyzer("UV-Vis")
        xs = [1.0, 2.0]
        ys = [0.5, 0.6]
        r1 = a.match_spectrum(xs, ys, xs, ys)
        r2 = a.match_spectrum(xs, ys, xs, ys)
        assert r1["run_id"] != r2["run_id"]

    def test_grid_points_tracked(self):
        a = spect.SpectraAnalyzer("Raman")
        xs_a = [100.0, 200.0, 300.0]
        ys = [1.0, 2.0, 3.0]
        xs_b = [100.0, 250.0, 300.0]
        result = a.match_spectrum(xs_a, ys, xs_b, ys)
        assert result["grid_points"] == 4

    def test_similarity_in_range(self):
        a = spect.SpectraAnalyzer("IR")
        xs = [0.0, 1.0, 2.0, 3.0]
        ys_a = [1.0, 2.0, 1.0, 0.0]
        ys_b = [1.1, 1.9, 1.1, 0.1]
        result = a.match_spectrum(xs, ys_a, xs, ys_b)
        assert result["similarity"] > 0.99

    def test_negative_values_handled(self):
        a = spect.SpectraAnalyzer("IR")
        xs = [0.0, 1.0, 2.0]
        ys_a = [-1.0, 0.0, 1.0]
        ys_b = [-1.0, 0.0, 1.0]
        result = a.match_spectrum(xs, ys_a, xs, ys_b)
        assert math.isclose(result["similarity"], 1.0)


# ---------------------------------------------------------------------------
# Edge cases and invariants
# ---------------------------------------------------------------------------


class TestInvariants:
    def test_schema_version_constant(self):
        assert spect.SCHEMA_VERSION == "1.0"

    def test_supported_kinds_is_frozenset(self):
        assert isinstance(spect.SUPPORTED_KINDS, frozenset)

    def test_supported_kinds_contains_six(self):
        assert len(spect.SUPPORTED_KINDS) == 6

    def test_detect_peaks_returns_list_of_dicts(self):
        a = spect.SpectraAnalyzer("NMR")
        xs = [1.0, 2.0, 3.0]
        ys = [0.1, 5.0, 0.1]
        peaks = a.detect_peaks(xs, ys)
        assert isinstance(peaks, list)
        assert all(isinstance(p, dict) for p in peaks)
        for p in peaks:
            assert "x" in p and "height" in p and "index" in p and "kind" in p

    def test_match_spectrum_method_id_present(self):
        a = spect.SpectraAnalyzer("IR")
        result = a.match_spectrum([1.0, 2.0], [1.0, 2.0], [1.0, 2.0], [1.0, 2.0])
        assert result["method_id"] == spect.METHOD_ID
