"""Deep tests for Diffie-Hellman: DH, DHE, safe prime generation, key derivation."""

from __future__ import annotations

import dataclasses

import pytest

from general_ludd.algorithms.diffie_hellman import (
    _TEST_GROUP,
    GROUP_2048,
    DHEExchange,
    DHError,
    _is_valid_generator,
    compute_shared_secret,
    derive_key,
    dhe_initiate,
    generate_dh_group,
    generate_keypair,
    generate_safe_prime,
)

# ── Safe prime generation ─────────────────────────────────────────────


def test_safe_prime_bits_too_small():
    with pytest.raises(DHError, match="bits must be >= 8"):
        generate_safe_prime(7)


def test_safe_prime_has_correct_bit_length():
    p = generate_safe_prime(32)
    assert p.bit_length() == 32


def test_safe_prime_is_odd():
    p = generate_safe_prime(24)
    assert p % 2 == 1


def test_safe_prime_p_minus_1_div_2_is_not_trivial():
    p = generate_safe_prime(16)
    q = (p - 1) // 2
    assert q > 2


def test_safe_prime_small():
    p = generate_safe_prime(8)
    assert 128 <= p <= 255
    assert p % 2 == 1


# ── DHGroup generation ────────────────────────────────────────────────


def test_generate_dh_group_bits_too_small():
    with pytest.raises(DHError, match="bits must be >= 16"):
        generate_dh_group(15)


def test_generate_dh_group_creates_valid_group():
    group = generate_dh_group(24, g=2, name="test-group")
    assert group.p.bit_length() == 24
    assert group.g == 2
    assert group.q == (group.p - 1) // 2
    assert group.name == "test-group"
    assert (group.p - 1) % 2 == 0


# ── Generator validation ──────────────────────────────────────────────


def test_valid_generator_for_test_group():
    assert _is_valid_generator(2, _TEST_GROUP.p, _TEST_GROUP.q)
    assert _is_valid_generator(3, _TEST_GROUP.p, _TEST_GROUP.q)


def test_invalid_generator_too_small():
    assert not _is_valid_generator(0, _TEST_GROUP.p, _TEST_GROUP.q)
    assert not _is_valid_generator(1, _TEST_GROUP.p, _TEST_GROUP.q)


def test_invalid_generator_equals_p_minus_1():
    p = _TEST_GROUP.p
    assert not _is_valid_generator(p - 1, p, _TEST_GROUP.q)


def test_generator_validation_for_rfc3526_2048():
    assert _is_valid_generator(2, GROUP_2048.p, GROUP_2048.q)


# ── Keypair generation ────────────────────────────────────────────────


def test_generate_keypair_private_in_range():
    kp = generate_keypair(_TEST_GROUP)
    assert 1 <= kp.private <= _TEST_GROUP.q - 1


def test_generate_keypair_public_matches():
    kp = generate_keypair(_TEST_GROUP)
    expected = pow(_TEST_GROUP.g, kp.private, _TEST_GROUP.p)
    assert kp.public == expected


def test_generate_keypair_group_reference():
    kp = generate_keypair(_TEST_GROUP)
    assert kp.group is _TEST_GROUP


# ── Static DH: shared secret equality ─────────────────────────────────


def test_static_dh_shared_secret_equality():
    alice = generate_keypair(_TEST_GROUP)
    bob = generate_keypair(_TEST_GROUP)

    secret_a = compute_shared_secret(alice.private, bob.public, _TEST_GROUP.p)
    secret_b = compute_shared_secret(bob.private, alice.public, _TEST_GROUP.p)

    assert secret_a == secret_b
    assert secret_a > 0


def test_static_dh_different_keys_different_secrets():
    alice_private = 5
    bob_public = pow(_TEST_GROUP.g, 7, _TEST_GROUP.p)
    charlie_public = pow(_TEST_GROUP.g, 11, _TEST_GROUP.p)

    ab = compute_shared_secret(alice_private, bob_public, _TEST_GROUP.p)
    ac = compute_shared_secret(alice_private, charlie_public, _TEST_GROUP.p)

    assert ab != ac


# ── DHE exchange ──────────────────────────────────────────────────────


def test_dhe_initiate_creates_exchange():
    ex = dhe_initiate(_TEST_GROUP)
    assert isinstance(ex, DHEExchange)
    assert ex.group is _TEST_GROUP
    assert 1 <= ex.own_keypair.private <= _TEST_GROUP.q - 1


def test_dhe_two_peers_same_secret():
    alice = dhe_initiate(_TEST_GROUP)
    bob = dhe_initiate(_TEST_GROUP)

    sa = alice.compute(bob.own_keypair.public)
    sb = bob.compute(alice.own_keypair.public)

    assert sa == sb
    assert sa > 0


def test_dhe_two_exchanges_different_secrets():
    g = generate_dh_group(32, g=2)
    a1 = dhe_initiate(g)
    b1 = dhe_initiate(g)
    a2 = dhe_initiate(g)
    b2 = dhe_initiate(g)

    s1 = a1.compute(b1.own_keypair.public)
    s2 = a2.compute(b2.own_keypair.public)

    assert s1 != s2


# ── RFC 3526 group 2048 ───────────────────────────────────────────────


def test_rfc3526_2048_p_is_safe_prime_structure():
    p = GROUP_2048.p
    assert p.bit_length() == 2048
    assert (p - 1) % 2 == 0
    assert GROUP_2048.q == (p - 1) // 2


def test_rfc3526_2048_generator_is_valid():
    assert _is_valid_generator(GROUP_2048.g, GROUP_2048.p, GROUP_2048.q)


# ── Test group (p=59) correctness ─────────────────────────────────────


def test_test_group_properties():
    assert _TEST_GROUP.p == 59
    assert _TEST_GROUP.g == 2
    assert _TEST_GROUP.q == 29
    assert _TEST_GROUP.name == "test-59"


def test_test_group_generates_subgroup():
    p, g, q = _TEST_GROUP.p, _TEST_GROUP.g, _TEST_GROUP.q
    result = pow(g, q, p)
    assert result != 1


# ── Key derivation ────────────────────────────────────────────────────


def test_derive_key_default_length():
    key = derive_key(12345)
    assert len(key) == 32
    assert isinstance(key, bytes)


def test_derive_key_short_length():
    key = derive_key(12345, length=16)
    assert len(key) == 16


def test_derive_key_deterministic():
    k1 = derive_key(999, length=40)
    k2 = derive_key(999, length=40)
    assert k1 == k2


def test_derive_key_different_secrets_different_output():
    k1 = derive_key(100, length=32)
    k2 = derive_key(200, length=32)
    assert k1 != k2


def test_derive_key_length_too_large():
    with pytest.raises(DHError, match="exceeds HKDF output limit"):
        derive_key(1, length=8161)


def test_derive_key_full_dh_roundtrip():
    alice = dhe_initiate(_TEST_GROUP)
    bob = dhe_initiate(_TEST_GROUP)

    sa = alice.compute(bob.own_keypair.public)
    sb = bob.compute(alice.own_keypair.public)
    assert sa == sb

    ka = derive_key(sa, length=32)
    kb = derive_key(sb, length=32)
    assert ka == kb


# ── Edge cases ────────────────────────────────────────────────────────


def test_compute_shared_secret_basic_identity():
    p, g = _TEST_GROUP.p, _TEST_GROUP.g
    assert compute_shared_secret(7, pow(g, 7, p), p) == compute_shared_secret(7, pow(g, 7, p), p)


def test_generate_keypair_in_rfc3526_group():
    kp = generate_keypair(GROUP_2048)
    assert kp.public > 1
    assert kp.public < GROUP_2048.p
    assert 1 <= kp.private <= GROUP_2048.q - 1


def test_generate_dh_group_different_names():
    g1 = generate_dh_group(24, name="custom-a")
    g2 = generate_dh_group(24, name="custom-b")
    assert g1.name == "custom-a"
    assert g2.name == "custom-b"
    assert g1.p != g2.p


def test_dh_group_frozen():
    g = generate_dh_group(24)
    with pytest.raises(dataclasses.FrozenInstanceError):
        g.name = "changed"
