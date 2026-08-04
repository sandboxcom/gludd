"""Deep number theory tests: GCD/LCM, modular inverse, prime sieve,
Miller-Rabin primality, Chinese remainder theorem.

Tests are self-contained (pure stdlib) — they verify the mathematical
properties directly without requiring a src/ module.
"""

from __future__ import annotations

import math
import random

# ============================================================================
# Pure-function implementations under test
# ============================================================================


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return abs(a)


def _lcm(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return abs(a * b) // _gcd(a, b)


def _egcd(a: int, b: int) -> tuple[int, int, int]:
    if b == 0:
        return (a, 1, 0)
    g, x1, y1 = _egcd(b, a % b)
    return (g, y1, x1 - (a // b) * y1)


def _modinv(a: int, m: int) -> int | None:
    g, x, _ = _egcd(a, m)
    if g != 1:
        return None
    return x % m


def _prime_sieve(limit: int) -> list[int]:
    if limit < 2:
        return []
    sieve = bytearray(b"\x01") * (limit + 1)
    sieve[0:2] = b"\x00\x00"
    for p in range(2, int(limit**0.5) + 1):
        if sieve[p]:
            sieve[p * p : limit + 1 : p] = b"\x00" * (((limit - p * p) // p) + 1)
    return [i for i, v in enumerate(sieve) if v]


def _modexp(base: int, exp: int, modulus: int) -> int:
    result = 1
    base %= modulus
    while exp > 0:
        if exp & 1:
            result = (result * base) % modulus
        base = (base * base) % modulus
        exp >>= 1
    return result


def _miller_rabin(n: int, k: int = 10) -> bool:
    if n < 2:
        return False
    if n == 2 or n == 3:
        return True
    if n % 2 == 0:
        return False
    d = n - 1
    s = 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(k):
        a = random.randrange(2, n - 1) if n > 3 else 2
        x = _modexp(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(s - 1):
            x = _modexp(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _crt(remainders: list[int], moduli: list[int]) -> int | None:
    if len(remainders) != len(moduli) or len(moduli) == 0:
        return None
    for i in range(len(moduli)):
        for j in range(i + 1, len(moduli)):
            if math.gcd(moduli[i], moduli[j]) != 1:
                return None
    M = 1
    for m in moduli:
        M *= m
    x = 0
    for a_i, m_i in zip(remainders, moduli, strict=False):
        M_i = M // m_i
        inv = _modinv(M_i % m_i, m_i)
        if inv is None:
            return None
        x = (x + a_i * M_i * inv) % M
    return x % M


def _is_prime_sieve(limit: int) -> set[int]:
    return set(_prime_sieve(limit))


# ============================================================================
# Tests
# ============================================================================


class TestGCD:
    def test_gcd_coprime(self):
        assert _gcd(17, 13) == 1

    def test_gcd_common_factor(self):
        assert _gcd(48, 18) == 6

    def test_gcd_identity(self):
        assert _gcd(0, 7) == 7
        assert _gcd(7, 0) == 7

    def test_gcd_negative(self):
        assert _gcd(-48, 18) == 6
        assert _gcd(48, -18) == 6

    def test_gcd_power_of_two(self):
        assert _gcd(64, 16) == 16


class TestLCM:
    def test_lcm_coprime(self):
        assert _lcm(7, 13) == 91

    def test_lcm_common_factor(self):
        assert _lcm(12, 18) == 36

    def test_lcm_zero(self):
        assert _lcm(0, 5) == 0
        assert _lcm(5, 0) == 0

    def test_lcm_identity(self):
        assert _lcm(1, 42) == 42

    def test_lcm_gcd_product(self):
        a, b = 36, 60
        assert _gcd(a, b) * _lcm(a, b) == abs(a * b)


class TestModularInverse:
    def test_modinv_simple(self):
        assert _modinv(3, 7) == 5

    def test_modinv_none_coprime(self):
        assert _modinv(2, 4) is None

    def test_modinv_large_prime(self):
        inv = _modinv(1234567, 999999937)
        assert inv is not None
        assert (1234567 * inv) % 999999937 == 1

    def test_modinv_identity(self):
        assert _modinv(1, 9973) == 1

    def test_modinv_negatives(self):
        inv = _modinv(-3, 7)
        assert inv is not None
        assert ((-3 % 7) * inv) % 7 == 1


class TestPrimeSieve:
    def test_sieve_below_2(self):
        assert _prime_sieve(1) == []

    def test_sieve_small(self):
        assert _prime_sieve(10) == [2, 3, 5, 7]

    def test_sieve_count_under_100(self):
        assert len(_prime_sieve(100)) == 25

    def test_sieve_count_under_1000(self):
        assert len(_prime_sieve(1000)) == 168

    def test_sieve_first_10_primes(self):
        assert _prime_sieve(30)[:10] == [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]

    def test_sieve_no_composites(self):
        primes = set(_prime_sieve(200))
        for p in primes:
            if p < 2:
                continue
            for d in range(2, int(p**0.5) + 1):
                assert p % d != 0, f"{p} divisible by {d}"


class TestMillerRabin:
    def test_small_primes(self):
        small = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37]
        for p in small:
            assert _miller_rabin(p, k=5), f"{p} should be prime"

    def test_small_composites(self):
        composites = [4, 6, 8, 9, 10, 12, 14, 15, 21, 25, 27, 49]
        for c in composites:
            assert not _miller_rabin(c, k=5), f"{c} should be composite"

    def test_large_prime(self):
        assert _miller_rabin(999999937, k=10), "999999937 is prime"

    def test_large_composite(self):
        assert not _miller_rabin(999999937 * 999999929, k=10)

    def test_even_n(self):
        assert not _miller_rabin(100, k=5)

    def test_n_1(self):
        assert not _miller_rabin(1, k=5)

    def test_agrees_with_sieve_up_to_1000(self):
        sieve_primes = _is_prime_sieve(1000)
        for n in range(2, 1001):
            assert _miller_rabin(n, k=10) == (n in sieve_primes), f"mismatch at {n}"


class TestChineseRemainder:
    def test_simple_case(self):
        r = _crt([2, 3, 2], [3, 5, 7])
        assert r is not None
        assert r % 3 == 2
        assert r % 5 == 3
        assert r % 7 == 2

    def test_pairwise_non_coprime_moduli(self):
        assert _crt([1, 2], [4, 6]) is None

    def test_empty_input(self):
        assert _crt([], []) is None

    def test_length_mismatch(self):
        assert _crt([1], [3, 5]) is None

    def test_large_moduli(self):
        r = _crt([123, 456, 789], [991, 997, 1009])
        assert r is not None
        assert r % 991 == 123
        assert r % 997 == 456
        assert r % 1009 == 789

    def test_identity(self):
        r = _crt([0], [5])
        assert r == 0

    def test_unique_solution_range(self):
        r = _crt([1, 2, 3], [5, 7, 11])
        assert r is not None
        assert 0 <= r < 5 * 7 * 11


class TestFullPipeline:
    """End-to-end test using all primitives together."""

    def test_generate_and_verify_rsa_components(self):
        primes = [p for p in _prime_sieve(200) if p > 50]
        p, q = primes[0], primes[-1]
        p * q
        phi = (p - 1) * (q - 1)
        e = 65537
        assert _gcd(e, phi) == 1
        d = _modinv(e, phi)
        assert d is not None
        assert (e * d) % phi == 1

    def test_crt_reconstruct_moduli_product(self):
        moduli = [17, 19, 23, 29]
        product = 1
        for m in moduli:
            product *= m
        r = _crt([1, 2, 3, 4], moduli)
        assert r is not None
        for i, m in enumerate(moduli):
            assert r % m == i + 1
        assert 0 <= r < product
