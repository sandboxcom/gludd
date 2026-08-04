"""Deep statistical distribution tests — normal, Poisson, exponential, uniform."""

from __future__ import annotations

import math

import numpy as np
import pytest


class TestNormalDistribution:
    MU, SIGMA = 3.0, 1.5
    N = 200_000
    SEED = 42

    @pytest.fixture(autouse=True)
    def _samples(self) -> None:
        self.rng = np.random.default_rng(self.SEED)
        self.data = self.rng.normal(self.MU, self.SIGMA, size=self.N)

    # --- moments ---

    def test_normal_mean_close(self):
        assert abs(np.mean(self.data) - self.MU) < 0.02

    def test_normal_std_close(self):
        assert abs(np.std(self.data, ddof=1) - self.SIGMA) < 0.02

    def test_normal_skewness_near_zero(self):
        m3 = np.mean((self.data - np.mean(self.data)) ** 3)
        s3 = np.std(self.data, ddof=0) ** 3
        assert abs(m3 / s3) < 0.03

    def test_normal_kurtosis_near_zero(self):
        m4 = np.mean((self.data - np.mean(self.data)) ** 4)
        s4 = np.std(self.data, ddof=0) ** 4
        assert abs(m4 / s4 - 3) < 0.05

    # --- PDF ---

    def test_normal_pdf_closed_form(self):
        x = np.linspace(self.MU - 4 * self.SIGMA, self.MU + 4 * self.SIGMA, 1000)
        pdf = (1.0 / (self.SIGMA * math.sqrt(2 * math.pi))) * np.exp(-0.5 * ((x - self.MU) / self.SIGMA) ** 2)
        assert np.all(np.isfinite(pdf))
        assert np.isclose(np.trapezoid(pdf, x), 1.0, atol=0.02)

    def test_normal_pdf_peak_at_mean(self):
        x = np.linspace(self.MU - 4 * self.SIGMA, self.MU + 4 * self.SIGMA, 1000)
        pdf = (1.0 / (self.SIGMA * math.sqrt(2 * math.pi))) * np.exp(-0.5 * ((x - self.MU) / self.SIGMA) ** 2)
        peak_idx = np.argmax(pdf)
        assert abs(x[peak_idx] - self.MU) < 0.1

    # --- CDF ---

    def test_normal_cdf_symmetric(self):
        lo, hi = self.MU - self.SIGMA, self.MU + self.SIGMA
        fraction = np.mean((self.data >= lo) & (self.data <= hi))
        assert 0.67 < fraction < 0.70

    def test_normal_cdf_tails_rare(self):
        beyond_3sigma = np.mean(np.abs(self.data - self.MU) > 3 * self.SIGMA)
        assert beyond_3sigma < 0.01

    def test_normal_median_near_mean(self):
        assert abs(np.median(self.data) - self.MU) < 0.02


class TestPoissonDistribution:
    LAM = 7.0
    N = 200_000
    SEED = 123

    @pytest.fixture(autouse=True)
    def _samples(self) -> None:
        self.rng = np.random.default_rng(self.SEED)
        self.data = self.rng.poisson(self.LAM, size=self.N)

    def test_poisson_mean_close(self):
        assert abs(np.mean(self.data) - self.LAM) < 0.02

    def test_poisson_variance_close_to_mean(self):
        var = np.var(self.data, ddof=0)
        assert abs(var - self.LAM) < 0.05

    def test_poisson_pmf_single_bin(self):
        k = int(self.LAM)
        fraction = np.mean(self.data == k)
        expected = (self.LAM**k) * math.exp(-self.LAM) / math.factorial(k)
        assert abs(fraction - expected) < 0.01

    def test_poisson_non_negative(self):
        assert np.min(self.data) >= 0

    def test_poisson_discrete_integer(self):
        assert np.all(self.data == self.data.astype(int))


class TestExponentialDistribution:
    RATE = 0.5
    N = 200_000
    SEED = 77

    @pytest.fixture(autouse=True)
    def _samples(self) -> None:
        self.rng = np.random.default_rng(self.SEED)
        self.data = self.rng.exponential(1.0 / self.RATE, size=self.N)

    def test_exponential_mean_close(self):
        assert abs(np.mean(self.data) - 1.0 / self.RATE) < 0.02

    def test_exponential_variance_close(self):
        var = np.var(self.data, ddof=0)
        expected = 1.0 / self.RATE**2
        assert abs(var - expected) / expected < 0.03

    # --- PDF ---

    def test_exponential_pdf_closed_form(self):
        xs = np.linspace(0.01, 10.0, 1000)
        pdf_val = self.RATE * np.exp(-self.RATE * xs)
        assert np.all(pdf_val >= 0)
        assert np.isclose(np.trapezoid(pdf_val, xs), 1.0, atol=0.02)

    # --- CDF ---

    def test_exponential_cdf_median(self):
        med = np.median(self.data)
        expected = math.log(2) / self.RATE
        assert abs(med - expected) / expected < 0.03

    def test_exponential_memoryless(self):
        t0 = 1.0 / self.RATE
        cond = self.data[self.data > t0] - t0
        expected_mean = 1.0 / self.RATE
        assert abs(np.mean(cond) - expected_mean) / expected_mean < 0.03


class TestUniformDistribution:
    LO, HI = 2.0, 8.0
    N = 200_000
    SEED = 99

    @pytest.fixture(autouse=True)
    def _samples(self) -> None:
        self.rng = np.random.default_rng(self.SEED)
        self.data = self.rng.uniform(self.LO, self.HI, size=self.N)

    def test_uniform_mean_close(self):
        expected = (self.LO + self.HI) / 2.0
        assert abs(np.mean(self.data) - expected) < 0.02

    def test_uniform_variance_close(self):
        expected = (self.HI - self.LO) ** 2 / 12.0
        var = np.var(self.data, ddof=0)
        assert abs(var - expected) / expected < 0.02

    def test_uniform_range_within_bounds(self):
        assert self.data.min() >= self.LO - 0.01
        assert self.data.max() <= self.HI + 0.01

    def test_uniform_pdf_constant(self):
        np.linspace(self.LO + 0.1, self.HI - 0.1, 200)
        pdf_val = 1.0 / (self.HI - self.LO)
        n_bins = 50
        hist, _edges = np.histogram(self.data, bins=n_bins, range=(self.LO, self.HI))
        bin_width = (self.HI - self.LO) / n_bins
        density = hist / (self.N * bin_width)
        assert np.all(np.abs(density - pdf_val) < 0.1 * pdf_val)

    def test_uniform_cdf_linear(self):
        mid = (self.LO + self.HI) / 2.0
        fraction = np.mean(self.data <= mid)
        assert abs(fraction - 0.5) < 0.01


class TestSamplingProperties:
    SEED = 42
    SIZE = 100_000

    def test_reproducibility(self):
        a = np.random.default_rng(self.SEED).normal(0, 1, self.SIZE)
        b = np.random.default_rng(self.SEED).normal(0, 1, self.SIZE)
        np.testing.assert_array_equal(a, b)

    def test_reproducibility_independent_streams(self):
        a = np.random.default_rng(self.SEED).normal(0, 1, 100)
        rng2 = np.random.default_rng(self.SEED + 1)
        b = rng2.normal(0, 1, 100)
        assert not np.array_equal(a, b[:100])

    def test_clt_convergence(self):
        n_samples, n_means = 30, 20_000
        rng = np.random.default_rng(self.SEED)
        uniforms = rng.uniform(0, 1, (n_means, n_samples))
        means = uniforms.mean(axis=1)
        m3 = np.mean((means - np.mean(means)) ** 3)
        s3 = np.std(means, ddof=0) ** 3
        assert abs(m3 / s3) < 0.05
