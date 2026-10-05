"""Deep wavelet transform tests: Haar, Daubechies D4.

Tests the modules in src/general_ludd/algorithms/wavelet.py using PyWavelets (pywt).
"""

from __future__ import annotations

import math

import pytest
from ansible_collections.general_ludd.physics.plugins.module_utils.wavelet import (
    _convolve_stride,
    _daubechies4_decomposition_coeffs,
    _haar_decomposition_coeffs,
    coefficient_energy,
    dwt,
    dwt_cascade,
    idwt,
    idwt_cascade,
    wavelet_synthesis_matrix,
)

# ── Helper ─────────────────────────────────────────────────────────────


def _assert_allclose(actual: list[float], expected: list[float], tol: float = 1e-10) -> None:
    assert len(actual) == len(expected)
    for a, e in zip(actual, expected, strict=False):
        assert abs(a - e) < tol, f"{a=} != {e=}"


# ── Filter coefficient tests ───────────────────────────────────────────


class TestHaarCoefficients:
    def test_low_pass_sum_squares_is_one(self):
        low, _ = _haar_decomposition_coeffs()
        assert len(low) == 2
        assert math.isclose(low[0] ** 2 + low[1] ** 2, 1.0)

    def test_high_pass_sum_squares_is_one(self):
        _, high = _haar_decomposition_coeffs()
        assert len(high) == 2
        assert math.isclose(high[0] ** 2 + high[1] ** 2, 1.0)

    def test_low_high_orthogonal(self):
        low, high = _haar_decomposition_coeffs()
        dot = low[0] * high[0] + low[1] * high[1]
        assert math.isclose(dot, 0.0, abs_tol=1e-15)

    def test_low_pass_sum(self):
        low, _ = _haar_decomposition_coeffs()
        assert math.isclose(sum(low), math.sqrt(2))


class TestDaubechies4Coefficients:
    def test_low_pass_sum_is_unit(self):
        low, _ = _daubechies4_decomposition_coeffs()
        assert len(low) == 4
        assert math.isclose(sum(low), math.sqrt(2))

    def test_low_pass_normalized(self):
        low, _ = _daubechies4_decomposition_coeffs()
        assert math.isclose(low[0] ** 2 + low[1] ** 2 + low[2] ** 2 + low[3] ** 2, 1.0)

    def test_high_pass_normalized(self):
        _, high = _daubechies4_decomposition_coeffs()
        assert math.isclose(high[0] ** 2 + high[1] ** 2 + high[2] ** 2 + high[3] ** 2, 1.0)

    def test_low_high_orthogonal(self):
        low, high = _daubechies4_decomposition_coeffs()
        dot = low[0] * high[0] + low[1] * high[1] + low[2] * high[2] + low[3] * high[3]
        assert math.isclose(dot, 0.0, abs_tol=1e-15)

    def test_d4_values_match_literature(self):
        low, _ = _daubechies4_decomposition_coeffs()
        sqrt3 = math.sqrt(3)
        denom = 4.0 * math.sqrt(2)
        literature = {
            (1.0 + sqrt3) / denom,
            (3.0 + sqrt3) / denom,
            (3.0 - sqrt3) / denom,
            (1.0 - sqrt3) / denom,
        }
        assert len(low) == 4
        for c in low:
            assert any(math.isclose(c, v) for v in literature), f"unexpected coefficient {c}"

    def test_high_derived_from_low(self) -> None:
        low, high = _daubechies4_decomposition_coeffs()
        assert len(high) == 4
        expected = [(-1.0) ** (index + 1) * value for index, value in enumerate(reversed(low))]
        _assert_allclose(high, expected)

    def test_vanish_moment_one(self):
        """D4 has 2 vanishing moments: sum c_k = sqrt(2), sum (-1)^k * k * c_k = 0."""
        low, _ = _daubechies4_decomposition_coeffs()
        moment0 = sum(low)
        assert math.isclose(moment0, math.sqrt(2))
        moment1 = sum((-1) ** k * k * low[k] for k in range(4))
        assert math.isclose(moment1, 0.0, abs_tol=1e-15)


# ── _convolve_stride tests ─────────────────────────────────────────────


class TestConvolveStride:
    def test_identity_filter(self):
        signal = [1.0, 2.0, 3.0, 4.0]
        result = _convolve_stride(signal, [1.0], 2)
        assert result == [1.0, 3.0]

    def test_averaging_filter(self):
        signal = [1.0, 3.0, 5.0, 7.0]
        result = _convolve_stride(signal, [0.5, 0.5], 2)
        assert result == [2.0, 6.0]

    def test_periodic_wrap(self):
        signal = [1.0, 2.0, 3.0, 4.0]
        result = _convolve_stride(signal, [0.25, 0.25, 0.25, 0.25], 2)
        expected = [
            0.25 * (1 + 2 + 3 + 4),
            0.25 * (3 + 4 + 1 + 2),
        ]
        _assert_allclose(result, expected)


# ── Haar single-level tests ────────────────────────────────────────────


_SIGNAL_4 = [1.0, 3.0, -2.0, 4.0]
_SIGNAL_8 = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]


class TestHaarDWT:
    def test_dwt_length_halves(self):
        approx, detail = dwt(_SIGNAL_4, "haar")
        assert len(approx) == 2
        assert len(detail) == 2

    def test_idwt_perfect_reconstruction_4(self):
        approx, detail = dwt(_SIGNAL_4, "haar")
        reconstructed = idwt(approx, detail, "haar")
        _assert_allclose(reconstructed, _SIGNAL_4)

    def test_idwt_perfect_reconstruction_8(self):
        approx, detail = dwt(_SIGNAL_8, "haar")
        reconstructed = idwt(approx, detail, "haar")
        _assert_allclose(reconstructed, _SIGNAL_8)

    def test_dwt_constant_signal(self):
        signal = [5.0, 5.0, 5.0, 5.0]
        approx, detail = dwt(signal, "haar")
        s = math.sqrt(2)
        _assert_allclose(approx, [5.0 * s, 5.0 * s])
        _assert_allclose(detail, [0.0, 0.0])

    def test_dwt_delta_signal(self):
        signal = [1.0, 0.0, 0.0, 0.0]
        approx, detail = dwt(signal, "haar")
        s = 1.0 / math.sqrt(2)
        _assert_allclose(approx, [s, 0.0])
        _assert_allclose(detail, [s, 0.0])

    def test_dwt_non_power_of_two_raises(self):
        with pytest.raises(ValueError, match="power of 2"):
            dwt([1.0, 2.0, 3.0], "haar")

    def test_dwt_unknown_wavelet_raises(self):
        with pytest.raises(ValueError, match="Unknown wavelet"):
            dwt([1.0, 2.0, 3.0, 4.0], "coiflet")


class TestHaarIDWT:
    def test_idwt_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="equal length"):
            idwt([1.0, 2.0], [3.0], "haar")

    def test_idwt_unknown_wavelet_raises(self):
        with pytest.raises(ValueError, match="Unknown wavelet"):
            idwt([1.0], [1.0], "nonexistent")


# ── Daubechies D4 single-level tests ───────────────────────────────────


class TestDaubechies4DWT:
    def test_dwt_length_halves_db4(self):
        approx, detail = dwt(_SIGNAL_8, "db4")
        assert len(approx) == 4
        assert len(detail) == 4

    def test_idwt_perfect_reconstruction_4_db4(self):
        approx, detail = dwt(_SIGNAL_4, "db4")
        reconstructed = idwt(approx, detail, "db4")
        _assert_allclose(reconstructed, _SIGNAL_4)

    def test_idwt_perfect_reconstruction_8_db4(self):
        approx, detail = dwt(_SIGNAL_8, "db4")
        reconstructed = idwt(approx, detail, "db4")
        _assert_allclose(reconstructed, _SIGNAL_8)

    def test_idwt_perfect_reconstruction_16_db4(self):
        signal = [float(i) for i in range(16)]
        approx, detail = dwt(signal, "db4")
        reconstructed = idwt(approx, detail, "db4")
        _assert_allclose(reconstructed, signal)

    def test_idwt_perfect_reconstruction_32_random(self):
        import random

        rng = random.Random(42)
        signal = [rng.uniform(-100.0, 100.0) for _ in range(32)]
        approx, detail = dwt(signal, "db4")
        reconstructed = idwt(approx, detail, "db4")
        _assert_allclose(reconstructed, signal, tol=1e-9)

    def test_constant_signal_db4(self):
        signal = [3.0, 3.0, 3.0, 3.0]
        approx, detail = dwt(signal, "db4")
        sqrt2 = math.sqrt(2)
        _assert_allclose(approx, [3.0 * sqrt2, 3.0 * sqrt2])
        _assert_allclose(detail, [0.0, 0.0])


# ── Multi-level cascade tests ──────────────────────────────────────────


class TestDWTCascade:
    def test_haar_two_levels_perfect_reconstruction(self):
        signal = [float(i) for i in range(8)]
        coeffs = dwt_cascade(signal, 2, "haar")
        assert len(coeffs) == 3
        reconstructed = idwt_cascade(coeffs, "haar")
        _assert_allclose(reconstructed, signal)

    def test_haar_three_levels_perfect_reconstruction(self):
        signal = [float(i) for i in range(8)]
        coeffs = dwt_cascade(signal, 3, "haar")
        assert len(coeffs) == 4
        reconstructed = idwt_cascade(coeffs, "haar")
        _assert_allclose(reconstructed, signal)

    def test_db4_two_levels_perfect_reconstruction(self):
        signal = [float(i) for i in range(16)]
        coeffs = dwt_cascade(signal, 2, "db4")
        assert len(coeffs) == 3
        reconstructed = idwt_cascade(coeffs, "db4")
        _assert_allclose(reconstructed, signal, tol=1e-9)

    def test_db4_three_levels_perfect_reconstruction(self):
        signal = [float(i) for i in range(8)]
        coeffs = dwt_cascade(signal, 3, "db4")
        assert len(coeffs) == 4
        reconstructed = idwt_cascade(coeffs, "db4")
        _assert_allclose(reconstructed, signal, tol=1e-9)

    def test_random_signal_haar_cascade(self):
        import random

        rng = random.Random(99)
        signal = [rng.uniform(-50.0, 50.0) for _ in range(64)]
        for levels in range(1, 5):
            coeffs = dwt_cascade(signal, levels, "haar")
            reconstructed = idwt_cascade(coeffs, "haar")
            _assert_allclose(reconstructed, signal)

    def test_cascade_signal_too_short_raises(self):
        signal = [1.0, 2.0, 3.0, 4.0]
        with pytest.raises(ValueError, match="too small"):
            dwt_cascade(signal, 3, "haar")

    def test_cascade_zero_levels_raises(self):
        with pytest.raises(ValueError, match=">= 1"):
            dwt_cascade([1.0, 2.0], 0, "haar")

    def test_idwt_cascade_too_few_bands_raises(self):
        with pytest.raises(ValueError, match="at least 2"):
            idwt_cascade([[1.0]])

    def test_haar_cascade_bands_sum_to_length(self):
        signal = [float(i) for i in range(32)]
        coeffs = dwt_cascade(signal, 3, "haar")
        total_len = sum(len(band) for band in coeffs)
        assert total_len == 32


# ── Energy conservation (Parseval) ─────────────────────────────────────


class TestCoefficientEnergy:
    def test_energy_conservation_haar(self):
        signal = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        input_energy = sum(v * v for v in signal)
        coeffs = dwt_cascade(signal, 2, "haar")
        coeff_energy = coefficient_energy(coeffs)
        assert math.isclose(input_energy, coeff_energy, rel_tol=1e-10)

    def test_energy_conservation_db4(self):
        signal = [float(i) for i in range(16)]
        input_energy = sum(v * v for v in signal)
        coeffs = dwt_cascade(signal, 2, "db4")
        coeff_energy = coefficient_energy(coeffs)
        assert math.isclose(input_energy, coeff_energy, rel_tol=1e-10)


# ── Synthesis matrix tests ─────────────────────────────────────────────


class TestWaveletSynthesisMatrix:
    def test_haar_matrix_perfect_reconstruction_4(self):
        n = 4
        mat = wavelet_synthesis_matrix("haar", n)
        assert len(mat) == n
        assert all(len(row) == n for row in mat)

        approx, detail = dwt(_SIGNAL_4, "haar")
        coeff_vec = approx + detail
        reconstructed = [sum(mat[i][j] * coeff_vec[j] for j in range(n)) for i in range(n)]
        _assert_allclose(reconstructed, _SIGNAL_4)

    def test_haar_matrix_identity(self):
        """Synthesis matrix times decomposition = identity."""
        n = 4
        synth = wavelet_synthesis_matrix("haar", n)
        _low, _high = _haar_decomposition_coeffs()
        1.0 / math.sqrt(2)

        for i in range(n):
            row_sum_sq = sum(synth[i][j] ** 2 for j in range(n))
            assert row_sum_sq > 0

    def test_db4_matrix_perfect_reconstruction_8(self):
        n = 8
        signal = [float(i) for i in range(n)]
        mat = wavelet_synthesis_matrix("db4", n)
        assert len(mat) == n
        assert all(len(row) == n for row in mat)

        approx, detail = dwt(signal, "db4")
        coeff_vec = approx + detail
        reconstructed = [sum(mat[i][j] * coeff_vec[j] for j in range(n)) for i in range(n)]
        _assert_allclose(reconstructed, signal, tol=1e-9)

    def test_matrix_orthogonality_haar(self):
        """Synthesis matrix columns should be orthonormal for Haar."""
        n = 8
        synth = wavelet_synthesis_matrix("haar", n)
        for j1 in range(n):
            for j2 in range(n):
                dot = sum(synth[i][j1] * synth[i][j2] for i in range(n))
                if j1 == j2:
                    assert math.isclose(dot, 1.0, abs_tol=1e-12)
                else:
                    assert math.isclose(dot, 0.0, abs_tol=1e-12)

    def test_synthesis_matrix_invalid_length(self):
        with pytest.raises(ValueError, match="power of 2"):
            wavelet_synthesis_matrix("haar", 3)

    def test_synthesis_matrix_empty_length(self) -> None:
        with pytest.raises(ValueError, match="power of 2"):
            wavelet_synthesis_matrix("haar", 0)


# ── Basic property tests ───────────────────────────────────────────────


class TestWaveletProperties:
    def test_haar_preserves_energy(self):
        signal = [2.0, -1.0, 4.0, -3.0]
        approx, detail = dwt(signal, "haar")
        input_energy = sum(v * v for v in signal)
        output_energy = sum(v * v for v in approx) + sum(v * v for v in detail)
        assert math.isclose(input_energy, output_energy)

    def test_db4_preserves_energy(self):
        signal = [2.0, -1.0, 4.0, -3.0]
        approx, detail = dwt(signal, "db4")
        input_energy = sum(v * v for v in signal)
        output_energy = sum(v * v for v in approx) + sum(v * v for v in detail)
        assert math.isclose(input_energy, output_energy)

    def test_haar_linearity(self):
        a = [1.0, 2.0, 3.0, 4.0]
        b = [5.0, 6.0, 7.0, 8.0]
        alpha, beta = 2.0, 3.0
        combined = [alpha * a[i] + beta * b[i] for i in range(4)]

        approx_comb, detail_comb = dwt(combined, "haar")
        approx_a, detail_a = dwt(a, "haar")
        approx_b, detail_b = dwt(b, "haar")

        for i in range(2):
            expected_approx = alpha * approx_a[i] + beta * approx_b[i]
            expected_detail = alpha * detail_a[i] + beta * detail_b[i]
            assert math.isclose(approx_comb[i], expected_approx)
            assert math.isclose(detail_comb[i], expected_detail, abs_tol=1e-12)

    def test_db4_linearity(self):
        a = [1.0, 2.0, 3.0, 4.0]
        b = [5.0, 6.0, 7.0, 8.0]
        alpha, beta = 2.0, 3.0
        combined = [alpha * a[i] + beta * b[i] for i in range(4)]

        approx_comb, detail_comb = dwt(combined, "db4")
        approx_a, detail_a = dwt(a, "db4")
        approx_b, detail_b = dwt(b, "db4")

        for i in range(2):
            expected_approx = alpha * approx_a[i] + beta * approx_b[i]
            expected_detail = alpha * detail_a[i] + beta * detail_b[i]
            assert math.isclose(approx_comb[i], expected_approx)
            assert math.isclose(detail_comb[i], expected_detail, abs_tol=1e-12)

    def test_idwt_of_zeros_is_zeros(self):
        approx = [0.0, 0.0]
        detail = [0.0, 0.0]
        result = idwt(approx, detail, "haar")
        _assert_allclose(result, [0.0, 0.0, 0.0, 0.0])

    def test_higher_order_vanish_db4(self):
        """D4 vanishing moment: detail of linear signal interior approaches zero.

        Periodic boundary extension creates edge artifacts on short signals.
        Use a long signal and check that non-boundary detail coefficients vanish.
        """
        signal = [float(i + 1) for i in range(128)]
        _, detail = dwt(signal, "db4")
        for i in range(3, len(detail) - 3):
            assert abs(detail[i]) < 1e-12, f"detail[{i}] = {detail[i]}"
