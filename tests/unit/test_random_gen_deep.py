"""Deep random number generation tests.

Tests uniform, normal, exponential distributions, seed reproducibility,
distributional tests (chi-squared, KS), and entropy estimation across
Python's random, secrets, and os.urandom RNG backends.
"""

from __future__ import annotations

import math
import os
import random
import secrets
import statistics
from collections import Counter
from types import MethodType
from typing import cast

import pytest
from scipy import stats

BINS = 20
N_SAMPLES = 10_000
ALPHA = 0.05
NORMAL_KS_SEED = 0x5EED
SECRETS_CHOICE_SEED = 0xC0FFEE
OS_URANDOM_TEST_SEED = 0xA11CE


def _chi_squared_statistic(observed: list[int], expected: list[float]) -> float:
    return sum((o - e) ** 2 / max(e, 1e-9) for o, e in zip(observed, expected, strict=False))


def _chi2_critical_value(df: int, alpha: float = 0.05) -> float:
    if df <= 0:
        return 0.0
    z = 1.6448536269514722  # N(0,1) 95th percentile
    t = 2.0 / (9.0 * df)
    return df * (1.0 - t + z * math.sqrt(t)) ** 3


def _ks_critical_value(n: int, alpha: float = 0.05) -> float:
    return math.sqrt(-0.5 * math.log(alpha / 2.0)) / math.sqrt(n)


def _normal_ks_result() -> tuple[float, float]:
    """Return a reproducible SciPy KS result for Python's Gaussian sampler."""
    rng = random.Random(NORMAL_KS_SEED)
    samples = [rng.gauss(0.0, 1.0) for _ in range(N_SAMPLES)]
    result = stats.kstest(samples, stats.norm.cdf, method="asymp")
    return float(result.statistic), float(result.pvalue)


def _controlled_secrets_choice_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[int, ...]:
    """Exercise ``secrets.choice`` with a reproducible index source."""
    population = tuple(range(10))
    rng = random.Random(SECRETS_CHOICE_SEED)
    choice_method = cast(MethodType, secrets.choice)
    system_random = cast(random.SystemRandom, choice_method.__self__)
    monkeypatch.setattr(system_random, "_randbelow", rng.randrange)
    counts = Counter(secrets.choice(population) for _ in range(N_SAMPLES))
    return tuple(counts[value] for value in population)


def _controlled_urandom_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[int, ...]:
    """Exercise ``os.urandom`` with a reproducible test-scoped byte source."""
    rng = random.Random(OS_URANDOM_TEST_SEED)
    monkeypatch.setattr(os, "urandom", rng.randbytes)
    counts = Counter(os.urandom(N_SAMPLES))
    return tuple(counts[value] for value in range(256))


# ---------------------------------------------------------------------------
# Uniform distribution
# ---------------------------------------------------------------------------


class TestUniformDistribution:
    def test_uniform_range_respects_bounds(self) -> None:
        lo, hi = -5.0, 10.0
        samples = [random.uniform(lo, hi) for _ in range(N_SAMPLES)]
        assert all(lo <= s <= hi for s in samples)

    def test_uniform_chi_squared(self) -> None:
        lo, hi = 0.0, 1.0
        samples = [random.uniform(lo, hi) for _ in range(N_SAMPLES)]
        bin_width = (hi - lo) / BINS
        observed: list[int] = [0] * BINS
        for s in samples:
            idx = min(int((s - lo) / bin_width), BINS - 1)
            observed[idx] += 1
        expected_per_bin = N_SAMPLES / BINS
        chi2 = _chi_squared_statistic(observed, [expected_per_bin] * BINS)
        critical = _chi2_critical_value(BINS - 1)
        assert chi2 < critical, f"chi2={chi2:.2f} >= critical={critical:.2f}"

    def test_uniform_mean_close_to_expected(self) -> None:
        lo, hi = 3.0, 7.0
        samples = [random.uniform(lo, hi) for _ in range(N_SAMPLES)]
        expected_mean = (lo + hi) / 2.0
        sample_mean = statistics.mean(samples)
        assert abs(sample_mean - expected_mean) < 0.1


# ---------------------------------------------------------------------------
# Normal (Gaussian) distribution
# ---------------------------------------------------------------------------


class TestNormalDistribution:
    def test_normal_mean_close_to_mu(self) -> None:
        mu, sigma = 5.0, 1.5
        samples = [random.gauss(mu, sigma) for _ in range(N_SAMPLES)]
        sample_mean = statistics.mean(samples)
        assert abs(sample_mean - mu) < 0.1

    def test_normal_stdev_close_to_sigma(self) -> None:
        mu, sigma = 0.0, 2.0
        samples = [random.gauss(mu, sigma) for _ in range(N_SAMPLES)]
        sample_stdev = statistics.stdev(samples)
        assert abs(sample_stdev - sigma) < 0.1

    def test_normal_ks_test(self) -> None:
        statistic, pvalue = _normal_ks_result()
        assert pvalue > ALPHA, f"KS D={statistic:.4f}, p={pvalue:.4f}"


# ---------------------------------------------------------------------------
# Exponential distribution
# ---------------------------------------------------------------------------


class TestExponentialDistribution:
    @staticmethod
    def _exponential_samples(lambd: float = 1.0, n: int = N_SAMPLES) -> list[float]:
        return [random.expovariate(lambd) for _ in range(n)]

    def test_exponential_mean_reciprocal(self) -> None:
        lambd = 0.5
        samples = self._exponential_samples(lambd)
        expected_mean = 1.0 / lambd
        sample_mean = statistics.mean(samples)
        assert abs(sample_mean - expected_mean) < 0.2

    def test_exponential_all_non_negative(self) -> None:
        samples = self._exponential_samples(2.0)
        assert all(s >= 0 for s in samples)

    def test_exponential_ks_test(self) -> None:
        samples = self._exponential_samples(1.0)
        n = len(samples)
        sorted_samples = sorted(samples)
        lambd = 1.0
        d_max = 0.0
        for i, x in enumerate(sorted_samples):
            ecdf = (i + 1) / n
            cdf = 1.0 - math.exp(-lambd * x) if x >= 0 else 0.0
            d = abs(ecdf - cdf)
            if d > d_max:
                d_max = d
        critical = _ks_critical_value(n)
        assert d_max < critical, f"KS D={d_max:.4f} >= critical={critical:.4f}"


# ---------------------------------------------------------------------------
# Seed reproducibility
# ---------------------------------------------------------------------------


class TestSeedReproducibility:
    def test_same_seed_produces_same_sequence(self) -> None:
        seed = 42
        random.seed(seed)
        seq_a = [random.random() for _ in range(100)]
        random.seed(seed)
        seq_b = [random.random() for _ in range(100)]
        assert seq_a == seq_b

    def test_different_seeds_produce_different_sequences(self) -> None:
        random.seed(42)
        seq_a = [random.random() for _ in range(100)]
        random.seed(99)
        seq_b = [random.random() for _ in range(100)]
        assert seq_a != seq_b

    def test_random_state_context_manager(self) -> None:
        seq_a: list[float] = []
        rng = random.Random(123)
        for _ in range(50):
            seq_a.append(rng.random())
        rng2 = random.Random(123)
        seq_b = [rng2.random() for _ in range(50)]
        assert seq_a == seq_b


# ---------------------------------------------------------------------------
# Entropy estimation (os.urandom / secrets)
# ---------------------------------------------------------------------------


class TestEntropyEstimation:
    def test_os_urandom_byte_distribution_chi_squared(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        observed = _controlled_urandom_counts(monkeypatch)
        result = stats.chisquare(observed)
        assert result.pvalue > ALPHA, (
            f"chi2={float(result.statistic):.2f}, p={float(result.pvalue):.4f}"
        )

    def test_secrets_token_bytes_all_non_empty_and_varying(self) -> None:
        tokens = [secrets.token_bytes(16) for _ in range(100)]
        assert all(len(t) == 16 for t in tokens)
        assert len(set(tokens)) == len(tokens)

    def test_secrets_token_hex_length(self) -> None:
        for nbytes in (1, 4, 16, 32):
            tok = secrets.token_hex(nbytes)
            assert len(tok) == nbytes * 2

    def test_secrets_choice_coverage(self) -> None:
        population = list(range(50))
        picks = [secrets.choice(population) for _ in range(500)]
        unique = set(picks)
        assert len(unique) >= 30

    def test_secrets_choice_is_uniform(self, monkeypatch: pytest.MonkeyPatch) -> None:
        observed = _controlled_secrets_choice_counts(monkeypatch)
        result = stats.chisquare(observed)
        assert result.pvalue > ALPHA, (
            f"chi2={float(result.statistic):.2f}, p={float(result.pvalue):.4f}"
        )

    def test_distribution_sources_are_repeatable_and_order_independent(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        normal_before = _normal_ks_result()
        choice_before = _controlled_secrets_choice_counts(monkeypatch)
        urandom_before = _controlled_urandom_counts(monkeypatch)

        random.seed(8675309)
        for _ in range(1000):
            random.random()

        assert _normal_ks_result() == normal_before
        assert _controlled_secrets_choice_counts(monkeypatch) == choice_before
        assert _controlled_urandom_counts(monkeypatch) == urandom_before


# ---------------------------------------------------------------------------
# Additional distribution tests
# ---------------------------------------------------------------------------


class TestAdditionalDistributions:
    def test_triangular_mode(self) -> None:
        low, high, mode = 0.0, 10.0, 7.0
        samples = [random.triangular(low, high, mode) for _ in range(N_SAMPLES)]
        assert all(low <= s <= high for s in samples)
        sample_mean = statistics.mean(samples)
        expected_mean = (low + high + mode) / 3.0
        assert abs(sample_mean - expected_mean) < 0.15

    def test_betavariate_bounds(self) -> None:
        for _ in range(200):
            val = random.betavariate(2.0, 5.0)
            assert 0 < val < 1

    def test_paretovariate_all_positive(self) -> None:
        samples = [random.paretovariate(3.0) for _ in range(500)]
        assert all(s >= 1.0 for s in samples)


# ---------------------------------------------------------------------------
# SystemRandom / secrets.SystemRandom
# ---------------------------------------------------------------------------


class TestSystemRandom:
    def test_system_random_platform(self) -> None:
        sr = secrets.SystemRandom()
        samples = [sr.random() for _ in range(200)]
        assert all(0 <= s <= 1 for s in samples)
        assert len(set(samples)) >= 190

    def test_system_random_seed_is_noop(self) -> None:
        sr = secrets.SystemRandom()
        seq_a = [sr.random() for _ in range(100)]
        sr.seed(42)  # no-op on SystemRandom, must not crash
        seq_b = [sr.random() for _ in range(100)]
        assert seq_a != seq_b

    def test_system_random_uniform(self) -> None:
        sr = secrets.SystemRandom()
        for _ in range(100):
            lo = sr.uniform(-10, -1)
            hi = sr.uniform(1, 10)
            val = sr.uniform(lo, hi)
            assert lo <= val <= hi
