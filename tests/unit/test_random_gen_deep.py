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

BINS = 20
N_SAMPLES = 10_000
ALPHA = 0.05


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


# ---------------------------------------------------------------------------
# Uniform distribution
# ---------------------------------------------------------------------------


class TestUniformDistribution:
    def test_uniform_range_respects_bounds(self):
        lo, hi = -5.0, 10.0
        samples = [random.uniform(lo, hi) for _ in range(N_SAMPLES)]
        assert all(lo <= s <= hi for s in samples)

    def test_uniform_chi_squared(self):
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

    def test_uniform_mean_close_to_expected(self):
        lo, hi = 3.0, 7.0
        samples = [random.uniform(lo, hi) for _ in range(N_SAMPLES)]
        expected_mean = (lo + hi) / 2.0
        sample_mean = statistics.mean(samples)
        assert abs(sample_mean - expected_mean) < 0.1


# ---------------------------------------------------------------------------
# Normal (Gaussian) distribution
# ---------------------------------------------------------------------------


class TestNormalDistribution:
    def test_normal_mean_close_to_mu(self):
        mu, sigma = 5.0, 1.5
        samples = [random.gauss(mu, sigma) for _ in range(N_SAMPLES)]
        sample_mean = statistics.mean(samples)
        assert abs(sample_mean - mu) < 0.1

    def test_normal_stdev_close_to_sigma(self):
        mu, sigma = 0.0, 2.0
        samples = [random.gauss(mu, sigma) for _ in range(N_SAMPLES)]
        sample_stdev = statistics.stdev(samples)
        assert abs(sample_stdev - sigma) < 0.1

    def test_normal_ks_test(self):
        mu, sigma = 0.0, 1.0
        samples = [random.gauss(mu, sigma) for _ in range(N_SAMPLES)]
        sorted_samples = sorted(samples)
        n = len(sorted_samples)
        d_max = 0.0
        for i, x in enumerate(sorted_samples):
            ecdf = (i + 1) / n
            cdf = 0.5 * (1 + math.erf((x - mu) / (sigma * math.sqrt(2))))
            d = abs(ecdf - cdf)
            if d > d_max:
                d_max = d
        critical = _ks_critical_value(n)
        assert d_max < critical, f"KS D={d_max:.4f} >= critical={critical:.4f}"


# ---------------------------------------------------------------------------
# Exponential distribution
# ---------------------------------------------------------------------------


class TestExponentialDistribution:
    @staticmethod
    def _exponential_samples(lambd: float = 1.0, n: int = N_SAMPLES) -> list[float]:
        return [random.expovariate(lambd) for _ in range(n)]

    def test_exponential_mean_reciprocal(self):
        lambd = 0.5
        samples = self._exponential_samples(lambd)
        expected_mean = 1.0 / lambd
        sample_mean = statistics.mean(samples)
        assert abs(sample_mean - expected_mean) < 0.2

    def test_exponential_all_non_negative(self):
        samples = self._exponential_samples(2.0)
        assert all(s >= 0 for s in samples)

    def test_exponential_ks_test(self):
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
    def test_same_seed_produces_same_sequence(self):
        seed = 42
        random.seed(seed)
        seq_a = [random.random() for _ in range(100)]
        random.seed(seed)
        seq_b = [random.random() for _ in range(100)]
        assert seq_a == seq_b

    def test_different_seeds_produce_different_sequences(self):
        random.seed(42)
        seq_a = [random.random() for _ in range(100)]
        random.seed(99)
        seq_b = [random.random() for _ in range(100)]
        assert seq_a != seq_b

    def test_random_state_context_manager(self):
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
    def test_os_urandom_byte_distribution_chi_squared(self):
        n = N_SAMPLES
        raw = os.urandom(n)
        counts: Counter[int] = Counter(raw)
        expected_per_bin = n / 256.0
        observed = [counts.get(i, 0) for i in range(256)]
        chi2 = _chi_squared_statistic(observed, [expected_per_bin] * 256)
        critical = _chi2_critical_value(255)
        assert chi2 < critical, f"chi2={chi2:.2f} >= critical={critical:.2f}"

    def test_secrets_token_bytes_all_non_empty_and_varying(self):
        tokens = [secrets.token_bytes(16) for _ in range(100)]
        assert all(len(t) == 16 for t in tokens)
        assert len(set(tokens)) == len(tokens)

    def test_secrets_token_hex_length(self):
        for nbytes in (1, 4, 16, 32):
            tok = secrets.token_hex(nbytes)
            assert len(tok) == nbytes * 2

    def test_secrets_choice_coverage(self):
        population = list(range(50))
        picks = [secrets.choice(population) for _ in range(500)]
        unique = set(picks)
        assert len(unique) >= 30

    def test_secrets_choice_is_uniform(self):
        pop = list(range(10))
        n = N_SAMPLES
        picks = [secrets.choice(pop) for _ in range(n)]
        counts = Counter(picks)
        expected_per = n / len(pop)
        observed = [counts[i] for i in pop]
        chi2 = _chi_squared_statistic(observed, [expected_per] * len(pop))
        critical = _chi2_critical_value(len(pop) - 1)
        assert chi2 < critical, f"chi2={chi2:.2f} >= critical={critical:.2f}"


# ---------------------------------------------------------------------------
# Additional distribution tests
# ---------------------------------------------------------------------------


class TestAdditionalDistributions:
    def test_triangular_mode(self):
        low, high, mode = 0.0, 10.0, 7.0
        samples = [random.triangular(low, high, mode) for _ in range(N_SAMPLES)]
        assert all(low <= s <= high for s in samples)
        sample_mean = statistics.mean(samples)
        expected_mean = (low + high + mode) / 3.0
        assert abs(sample_mean - expected_mean) < 0.15

    def test_betavariate_bounds(self):
        for _ in range(200):
            val = random.betavariate(2.0, 5.0)
            assert 0 < val < 1

    def test_paretovariate_all_positive(self):
        samples = [random.paretovariate(3.0) for _ in range(500)]
        assert all(s >= 1.0 for s in samples)


# ---------------------------------------------------------------------------
# SystemRandom / secrets.SystemRandom
# ---------------------------------------------------------------------------


class TestSystemRandom:
    def test_system_random_platform(self):
        sr = secrets.SystemRandom()
        samples = [sr.random() for _ in range(200)]
        assert all(0 <= s <= 1 for s in samples)
        assert len(set(samples)) >= 190

    def test_system_random_seed_is_noop(self):
        sr = secrets.SystemRandom()
        seq_a = [sr.random() for _ in range(100)]
        sr.seed(42)  # no-op on SystemRandom, must not crash
        seq_b = [sr.random() for _ in range(100)]
        assert seq_a != seq_b

    def test_system_random_uniform(self):
        sr = secrets.SystemRandom()
        for _ in range(100):
            lo = sr.uniform(-10, -1)
            hi = sr.uniform(1, 10)
            val = sr.uniform(lo, hi)
            assert lo <= val <= hi
