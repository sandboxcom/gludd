"""Legacy finite-field Diffie-Hellman compatibility using integer math.

The pinned RFC 3526 group 14 remains available for compatibility. Runtime
group generation is deliberately limited to small demonstration groups; it is
not a secure replacement for a maintained key-exchange implementation.
"""


from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Final

_MAX_DEMO_GROUP_BITS: Final[int] = 64
_SAFE_PRIME_MAX_ATTEMPTS: Final[int] = 65_536


class DHError(Exception):
    """Base exception for Diffie-Hellman operations."""


# ── RFC 3526 MODP groups (safe primes with generator 2) ────────────────


@dataclass(slots=True, frozen=True)
class DHGroup:
    """A finite cyclic group for Diffie-Hellman key exchange.

    Attributes:
        p: Safe prime modulus (p = 2q + 1 where q is also prime).
        g: Generator (usually 2 for MODP groups).
        q: (p - 1) // 2, the prime order of the Schnorr subgroup.
        name: Human-readable group name.
    """

    p: int
    g: int
    q: int
    name: str = "custom"


RFC3526_2048_P: Final[int] = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA051015728E5A8AACAA68FFFFFFFFFFFFFFFF",
    16,
)

RFC3526_2048_Q: Final[int] = (RFC3526_2048_P - 1) // 2

GROUP_2048: Final[DHGroup] = DHGroup(
    p=RFC3526_2048_P,
    g=2,
    q=RFC3526_2048_Q,
    name="rfc3526-2048",
)

_TEST_SAFE_PRIME: Final[int] = 59
_TEST_GROUP: Final[DHGroup] = DHGroup(
    p=_TEST_SAFE_PRIME,
    g=2,
    q=(_TEST_SAFE_PRIME - 1) // 2,
    name="test-59",
)

_SMALL_PRIMES: Final[frozenset[int]] = frozenset(
    {
        2,
        3,
        5,
        7,
        11,
        13,
        17,
        19,
        23,
        29,
        31,
        37,
        41,
        43,
        47,
        53,
        59,
        61,
        67,
        71,
        73,
        79,
        83,
        89,
        97,
        101,
        103,
        107,
        109,
        113,
        127,
        131,
        137,
        139,
        149,
        151,
        157,
        163,
        167,
        173,
        179,
        181,
        191,
        193,
        197,
        199,
        211,
        223,
        227,
        229,
        233,
        239,
        241,
        251,
        257,
        263,
        269,
        271,
        277,
        281,
        283,
        293,
        307,
        311,
        313,
        317,
        331,
        337,
        347,
        349,
        353,
        359,
        367,
        373,
        379,
        383,
        389,
        397,
        401,
        409,
        419,
        421,
        431,
        433,
        439,
        443,
        449,
        457,
        461,
        463,
        467,
        479,
        487,
        491,
        499,
        503,
        509,
        521,
        523,
        541,
    }
)


def _is_probable_prime(n: int, rounds: int = 40) -> bool:
    """Miller-Rabin probabilistic primality test."""
    if n < 2:
        return False
    if n in _SMALL_PRIMES:
        return True
    if n % 2 == 0:
        return False
    for small in _SMALL_PRIMES:
        if small * small > n:
            break
        if n % small == 0:
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


def generate_safe_prime(bits: int) -> int:
    """Generate a safe prime using the ``cryptography`` DH backend.

    Historical description only: PyCA FFDH is no longer imported or called.
    Runtime search is demonstration-only, bounded to 64 bits and finite tries.
    """
    if bits < 8:
        raise DHError(f"bits must be >= 8, got {bits}")
    if bits > _MAX_DEMO_GROUP_BITS:
        raise DHError(
            "runtime safe-prime generation is limited to 64 bits for "
            "demonstrations; use a maintained provider for secure protocols"
        )

    for _ in range(_SAFE_PRIME_MAX_ATTEMPTS):
        q = secrets.randbits(bits - 1) | (1 << (bits - 2)) | 1
        if _is_probable_prime(q):
            p = 2 * q + 1
            if p.bit_length() == bits and _is_probable_prime(p):
                return p
    raise DHError(
        f"failed to generate an {bits}-bit safe prime after "
        f"{_SAFE_PRIME_MAX_ATTEMPTS} attempts"
    )


def generate_dh_group(bits: int, g: int = 2, name: str = "custom") -> DHGroup:
    """Generate a demonstration-only safe-prime group of at most 64 bits."""
    if bits < 16:
        raise DHError(f"bits must be >= 16, got {bits}")
    p = generate_safe_prime(bits)
    q = (p - 1) // 2
    if not _is_valid_generator(g, p, q):
        raise DHError(f"g={g} is not a valid generator for p={p}")
    return DHGroup(p=p, g=g, q=q, name=name)


def _is_valid_generator(g: int, p: int, q: int) -> bool:
    if g < 2 or g >= p - 1:
        return False
    r = pow(g, q, p)
    return r == 1 or r == p - 1


@dataclass(slots=True)
class DHKeyPair:
    """A Diffie-Hellman key pair."""

    private: int
    public: int
    group: DHGroup


def _is_pinned_group_2048(group: DHGroup) -> bool:
    """Return whether ``group`` has the exact RFC 3526 group-14 numbers."""
    return (
        group.p == GROUP_2048.p
        and group.g == GROUP_2048.g
        and group.q == GROUP_2048.q
    )


def generate_keypair(group: DHGroup) -> DHKeyPair:
    """Generate an integer key pair for a demo group or pinned group 14."""
    if (
        group.p.bit_length() > _MAX_DEMO_GROUP_BITS
        and not _is_pinned_group_2048(group)
    ):
        raise DHError(
            "unknown large DH group; only the pinned RFC 3526 group 14 "
            "is supported"
        )

    private = secrets.randbelow(group.q - 1) + 1
    public = pow(group.g, private, group.p)
    return DHKeyPair(private=private, public=public, group=group)


def compute_shared_secret(private_key: int, peer_public: int, p: int) -> int:
    """Compute the DH shared secret via modular exponentiation."""
    if p.bit_length() > _MAX_DEMO_GROUP_BITS and p != GROUP_2048.p:
        raise DHError(
            "unknown large DH modulus; only the pinned RFC 3526 group-14 "
            "modulus is supported"
        )
    return pow(peer_public, private_key, p)


@dataclass(slots=True)
class DHEExchange:
    """An ephemeral Diffie-Hellman key exchange session."""

    own_keypair: DHKeyPair
    group: DHGroup

    def compute(self, peer_public: int) -> int:
        """Derive the shared secret from the peer's public value."""
        return compute_shared_secret(self.own_keypair.private, peer_public, self.group.p)


def dhe_initiate(group: DHGroup) -> DHEExchange:
    """Start an ephemeral DH exchange by generating a key pair for the group."""
    return DHEExchange(
        own_keypair=generate_keypair(group),
        group=group,
    )


def derive_key(shared_secret: int, length: int = 32) -> bytes:
    """Derive a fixed-length symmetric key from a shared secret via SHA-256."""
    import hashlib

    if length > 255 * 32:
        raise DHError(f"length {length} exceeds HKDF output limit 8160")
    secret_bytes = shared_secret.to_bytes((shared_secret.bit_length() + 7) // 8, "big")
    result = b""
    counter = 1
    while len(result) < length:
        result += hashlib.sha256(secret_bytes + counter.to_bytes(1, "big")).digest()
        counter += 1
    return result[:length]
