"""Deep tests for Shamir secret sharing: split, combine, invariants."""

from __future__ import annotations

import itertools
import random

import pytest
from ansible_collections.general_ludd.security.plugins.module_utils.shamir import (
    DEFAULT_PRIME,
    ShamirError,
    combine,
    split,
)

PRIMES = (2**127 - 1, 2**61 - 1, 2**31 - 1, 2**521 - 1, 65537)


class TestSplit:
    def test_split_basic(self) -> None:
        shares = split(42, threshold=3, num_shares=5)
        assert len(shares) == 5
        xs = [s[0] for s in shares]
        assert len(set(xs)) == 5
        assert all(1 <= x < DEFAULT_PRIME for x in xs)

    def test_split_produces_distinct_x(self) -> None:
        shares = split(123, threshold=5, num_shares=10)
        xs = [s[0] for s in shares]
        assert len(xs) == len(set(xs)) == 10

    def test_split_distinct_points(self) -> None:
        shares = split(0, threshold=3, num_shares=7)
        points = set(shares)
        assert len(points) == 7

    def test_split_threshold_equals_num_shares(self) -> None:
        shares = split(99, threshold=4, num_shares=4)
        assert len(shares) == 4

    def test_split_min_shares(self) -> None:
        shares = split(7, threshold=2, num_shares=2)
        assert len(shares) == 2

    @pytest.mark.parametrize("threshold,num_shares", [(0, 3), (-1, 5)])
    def test_split_invalid_threshold(self, threshold: int, num_shares: int) -> None:
        with pytest.raises(ShamirError, match="threshold"):
            split(42, threshold=threshold, num_shares=num_shares)

    @pytest.mark.parametrize("threshold,num_shares", [(3, 2), (5, 3), (10, 9)])
    def test_split_threshold_exceeds_shares(self, threshold: int, num_shares: int) -> None:
        with pytest.raises(ShamirError, match="threshold"):
            split(42, threshold=threshold, num_shares=num_shares)

    def test_split_secret_negative(self) -> None:
        with pytest.raises(ShamirError, match="secret"):
            split(-1, threshold=3, num_shares=5)

    def test_split_secret_too_large(self) -> None:
        p = 101
        with pytest.raises(ShamirError, match="secret"):
            split(200, threshold=3, num_shares=5, prime=p)

    @pytest.mark.parametrize("prime", PRIMES)
    def test_split_various_primes(self, prime: int) -> None:
        secret = prime // 2
        shares = split(secret, threshold=3, num_shares=7, prime=prime)
        assert len(shares) == 7
        assert all(0 <= s[1] < prime for s in shares)


class TestCombine:
    @pytest.mark.parametrize("secret", [0, 1, 42, 123456789, 2**127 - 2, 2**61 - 3])
    def test_combine_exact(self, secret: int) -> None:
        shares = split(secret, threshold=3, num_shares=5)
        recovered = combine(shares[:3])
        assert recovered == secret

    def test_combine_more_than_threshold(self) -> None:
        shares = split(77, threshold=3, num_shares=10)
        for k in range(3, 11):
            recovered = combine(shares[:k])
            assert recovered == 77

    def test_combine_different_subsets(self) -> None:
        shares = split(1337, threshold=4, num_shares=8)
        for subset in itertools.combinations(shares, 4):
            recovered = combine(list(subset))
            assert recovered == 1337

    def test_combine_fewer_than_threshold_wrong(self) -> None:
        shares = split(99, threshold=4, num_shares=6)
        recovered = combine(shares[:2])
        assert recovered != 99

    def test_combine_empty_raises(self) -> None:
        with pytest.raises(ShamirError, match="Need at least"):
            combine([])

    def test_combine_single_share_threshold_one(self) -> None:
        shares = split(255, threshold=1, num_shares=3)
        recovered = combine([shares[0]])
        assert recovered == 255

    def test_combine_duplicate_x_raises(self) -> None:
        shares = split(42, threshold=3, num_shares=5)
        dup = [shares[0], shares[0], shares[1]]
        with pytest.raises(ShamirError, match="distinct"):
            combine(dup)

    def test_combine_large_secret_large_prime(self) -> None:
        prime = 2**521 - 1
        secret = prime - 1
        shares = split(secret, threshold=5, num_shares=10, prime=prime)
        recovered = combine(shares[:5], prime=prime)
        assert recovered == secret

    @pytest.mark.parametrize("prime", PRIMES)
    def test_combine_various_primes(self, prime: int) -> None:
        secret = prime // 3
        shares = split(secret, threshold=4, num_shares=8, prime=prime)
        recovered = combine(shares[:4], prime=prime)
        assert recovered == secret

    def test_combine_prime_mismatch_raises(self) -> None:
        shares = split(42, threshold=3, num_shares=5, prime=2**127 - 1)
        with pytest.raises(ShamirError, match="prime"):
            combine(shares[:3], prime=2**61 - 1)

    def test_combine_value_out_of_range_raises(self) -> None:
        shares = split(42, threshold=3, num_shares=5, prime=101)
        bad_share = (99, 777, 101)
        with pytest.raises(ShamirError, match="range"):
            combine([bad_share, shares[0], shares[1]], prime=101)


class TestInvariants:
    """Property-based / fuzz invariants."""

    def test_random_roundtrip(self) -> None:
        prime = 2**127 - 1
        for _ in range(50):
            secret = random.randrange(prime)
            threshold = random.randint(2, 10)
            num_shares = random.randint(threshold, min(threshold + 10, 30))
            shares = split(secret, threshold=threshold, num_shares=num_shares, prime=prime)
            subset = random.sample(shares, threshold)
            assert combine(subset, prime=prime) == secret

    def test_x_uniqueness_over_many_splits(self) -> None:
        prime = 2**127 - 1
        all_x = set()
        for _ in range(20):
            shares = split(42, threshold=5, num_shares=10, prime=prime)
            for x, _, _ in shares:
                all_x.add(x)
        assert len(all_x) == 200

    def test_all_subsets_recover_same_secret(self) -> None:
        shares = split(5555, threshold=4, num_shares=7)
        results = {combine(list(subset)) for subset in itertools.combinations(shares, 4)}
        assert results == {5555}

    def test_shares_from_different_secrets_differ(self) -> None:
        shares_a = split(42, threshold=3, num_shares=5)
        shares_b = split(43, threshold=3, num_shares=5)
        assert set(shares_a) != set(shares_b)

    def test_zero_secret(self) -> None:
        shares = split(0, threshold=3, num_shares=5)
        assert combine(shares[:3]) == 0

    def test_max_secret(self) -> None:
        p = 2**127 - 1
        shares = split(p - 1, threshold=3, num_shares=5, prime=p)
        assert combine(shares[:3], prime=p) == p - 1

    def test_scramble_x_0(self) -> None:
        prime = 2**127 - 1
        shares = split(42, threshold=3, num_shares=5, prime=prime)
        for i in range(len(shares)):
            x, _y, _ = shares[i]
            if x == 0:
                assert combine(shares[:3], prime=prime, x_0=0) == 42
