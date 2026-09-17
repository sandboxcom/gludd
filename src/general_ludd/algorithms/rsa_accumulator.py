"""RSA universal accumulator implementation and its public value contracts."""

from __future__ import annotations

import hashlib
import secrets


class AccumulatorError(ValueError):
    """Raised when accumulator verification fails."""


def _is_probable_prime(n: int, rounds: int = 40) -> bool:
    """Use Miller-Rabin rounds to identify probable primes."""
    if n < 2:
        return False
    if n in (2, 3):
        return True
    if n % 2 == 0:
        return False
    d = n - 1
    s = 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _random_prime(bits: int) -> int:
    """Generate a random prime with the requested bit length."""
    while True:
        number = secrets.randbits(bits)
        number |= (1 << (bits - 1)) | 1
        if _is_probable_prime(number):
            return number


class RSAConfig:
    """RSA modulus N and generator G for the accumulator."""

    def __init__(self, bits: int = 1024) -> None:
        """Generate an RSA modulus and generator with the requested bit size."""
        if bits < 64:
            raise AccumulatorError("bits must be >= 64")
        self.bits = bits
        half = bits // 2
        self._p = _random_prime(half)
        self._q = _random_prime(bits - half)
        self.N = self._p * self._q
        self.G = secrets.randbelow(self.N - 2) + 2

    @property
    def p(self) -> int:
        """Return the first private prime factor."""
        return self._p

    @property
    def q(self) -> int:
        """Return the second private prime factor."""
        return self._q


def _hash_to_prime(value: bytes, bits: int = 256) -> int:
    """Hash a value to an odd integer and advance to a probable prime."""
    digest = hashlib.sha256(value).digest()
    number = int.from_bytes(digest, "big") % (1 << bits)
    number |= 1
    step = 0
    while not _is_probable_prime(number, rounds=25):
        number += 2
        step += 1
        if step > 10000:
            raise AccumulatorError("failed to find prime for " + repr(value))
    return number


class RSAUniversalAccumulator:
    """RSA-based universal set-membership accumulator."""

    def __init__(
        self,
        config: RSAConfig,
        initial_elements: list[bytes] | None = None,
    ) -> None:
        """Initialize an accumulator from configuration and optional elements."""
        self.config = config
        self._elements: set[int] = set()
        for elem in initial_elements or []:
            self._elements.add(_hash_to_prime(elem))
        self.value = self._compute_value()

    def _compute_value(self) -> int:
        if not self._elements:
            return self.config.G
        product = 1
        for prime in self._elements:
            product = (
                product * prime
            ) % ((self.config.p - 1) * (self.config.q - 1))
        return pow(self.config.G, product, self.config.N)

    def element_count(self) -> int:
        """Return the number of distinct accumulated prime representatives."""
        return len(self._elements)

    @property
    def elements(self) -> set[int]:
        """Return a defensive copy of accumulated prime representatives."""
        return set(self._elements)

    def add(self, element: bytes) -> None:
        """Add an element idempotently and update the accumulator value."""
        prime = _hash_to_prime(element)
        if prime in self._elements:
            return
        self._elements.add(prime)
        self.value = pow(self.value, prime, self.config.N)

    def remove(self, element: bytes) -> None:
        """Remove an existing element and update the accumulator value."""
        prime = _hash_to_prime(element)
        if prime not in self._elements:
            raise AccumulatorError("element not in accumulator")
        if len(self._elements) == 1:
            self._elements.discard(prime)
            self.value = self._compute_value()
            return
        self._elements.discard(prime)
        phi = (self.config.p - 1) * (self.config.q - 1)
        inverse = pow(prime, -1, phi)
        self.value = pow(self.value, inverse, self.config.N)

    def witness(self, element: bytes) -> int:
        """Return the membership witness for one accumulated element."""
        prime = _hash_to_prime(element)
        if prime not in self._elements:
            raise AccumulatorError("element not in accumulator")
        if len(self._elements) == 1:
            return self.config.G
        phi = (self.config.p - 1) * (self.config.q - 1)
        inverse = pow(prime, -1, phi)
        return pow(self.value, inverse, self.config.N)

    def verify_witness(self, element: bytes, witness: int) -> bool:
        """Return whether a membership witness matches the current value."""
        prime = _hash_to_prime(element)
        return pow(witness, prime, self.config.N) == self.value

    def non_membership_proof(self, element: bytes) -> tuple[int, int] | None:
        """Return a Bezout-based proof that an element is not accumulated."""
        prime = _hash_to_prime(element)
        if prime in self._elements:
            return None
        full_product = 1
        for member_prime in self._elements:
            full_product *= member_prime
        coefficient, exponent = _extended_gcd(prime, full_product)
        return (pow(self.config.G, coefficient, self.config.N), exponent)

    def verify_non_membership(self, element: bytes, proof: tuple[int, int]) -> bool:
        """Return whether a non-membership proof matches the current value."""
        generator_power, exponent = proof
        prime = _hash_to_prime(element)
        lhs = (
            pow(generator_power, prime, self.config.N)
            * pow(self.value, exponent, self.config.N)
        ) % self.config.N
        return lhs == self.config.G


def _extended_gcd(a: int, b: int) -> tuple[int, int]:
    """Return coefficients x and y such that a*x + b*y equals gcd(a, b)."""
    if b == 0:
        return (1, 0)
    x1, y1 = _extended_gcd(b, a % b)
    return (y1, x1 - (a // b) * y1)


__all__ = ("AccumulatorError", "RSAConfig", "RSAUniversalAccumulator")
