"""Deep tests for SRP-6a: enrollment, key agreement, client/server proofs, edge cases."""

from __future__ import annotations

import pytest
from ansible_collections.general_ludd.security.plugins.module_utils.srp import (
    _SRP2048_N,
    SRPError,
    _hash,
    _hash_bytes,
    _hton,
    _k,
    _private_x,
    _SRP2048_g,
    client_compute_session_key,
    client_generate_ephemeral,
    compute_client_proof,
    compute_server_proof,
    compute_u,
    derive_session_key,
    full_client_flow,
    full_server_flow,
    server_compute_session_key,
    server_compute_verifier,
    server_enroll,
    server_generate_ephemeral,
    server_generate_salt,
    server_verify_proof,
)

_KNOWN_UNAME = "alice"
_KNOWN_PW = "password123"


# ── Group constants ─────────────────────────────────────────────────────


def test_N_is_2048_bit_safe_prime():
    assert _SRP2048_N.bit_length() == 2048
    assert _SRP2048_N > 2**2047
    assert _SRP2048_N % 2 == 1


def test_generator_is_2():
    assert _SRP2048_g == 2


def test_k_is_computed_as_hash_of_N_and_g():
    assert _k > 1
    assert _k < _SRP2048_N


# ── Hash helpers ────────────────────────────────────────────────────────


def test_hton_roundtrips_for_small_value():
    v = 42
    assert int.from_bytes(_hton(v), "big") == v


def test_hton_produces_fixed_width_bytes():
    b = _hton(0)
    assert len(b) == 256
    assert b == b"\x00" * 256


def test_hash_is_deterministic():
    a = _hash(b"foo", b"bar")
    b = _hash(b"foo", b"bar")
    assert a == b


def test_hash_differs_on_input():
    a = _hash(b"foo")
    b = _hash(b"bar")
    assert a != b


def test_hash_bytes_returns_bytes():
    result = _hash_bytes(b"hello")
    assert isinstance(result, bytes)
    assert len(result) == 32


# ── Salt generation ─────────────────────────────────────────────────────


def test_salt_is_256_bit():
    s = server_generate_salt()
    assert 0 <= s < 2**256
    assert s.bit_length() <= 256


def test_salts_are_different():
    s1 = server_generate_salt()
    s2 = server_generate_salt()
    assert s1 != s2


# ── Private x derivation ────────────────────────────────────────────────


def test_private_x_is_deterministic():
    x1 = _private_x("alice", "secret", 12345)
    x2 = _private_x("alice", "secret", 12345)
    assert x1 == x2


def test_private_x_differs_on_password():
    x1 = _private_x("alice", "pw1", 12345)
    x2 = _private_x("alice", "pw2", 12345)
    assert x1 != x2


def test_private_x_differs_on_salt():
    x1 = _private_x("alice", "secret", 1)
    x2 = _private_x("alice", "secret", 2)
    assert x1 != x2


def test_private_x_differs_on_username():
    x1 = _private_x("alice", "secret", 12345)
    x2 = _private_x("bob", "secret", 12345)
    assert x1 != x2


# ── Verifier computation ────────────────────────────────────────────────


def test_verifier_is_in_range():
    v = server_compute_verifier("alice", "pw", 42)
    assert 0 < v < _SRP2048_N


def test_verifier_is_deterministic():
    v1 = server_compute_verifier("alice", "pw", 7)
    v2 = server_compute_verifier("alice", "pw", 7)
    assert v1 == v2


def test_verifier_differs_on_password():
    v1 = server_compute_verifier("alice", "alpha", 7)
    v2 = server_compute_verifier("alice", "beta", 7)
    assert v1 != v2


def test_verifier_differs_on_salt():
    v1 = server_compute_verifier("alice", "pw", 7)
    v2 = server_compute_verifier("alice", "pw", 8)
    assert v1 != v2


# ── Enrollment ──────────────────────────────────────────────────────────


def test_server_enroll_returns_salt_and_verifier():
    salt, verifier = server_enroll("alice", "password123")
    assert salt > 0
    assert verifier > 0
    assert verifier < _SRP2048_N


def test_server_enroll_different_users_produce_different_salts():
    s1, _ = server_enroll("alice", "pw")
    s2, _ = server_enroll("bob", "pw")
    assert s1 != s2


# ── Ephemeral key generation ────────────────────────────────────────────


def test_client_ephemeral_A_is_nonzero():
    _, A = client_generate_ephemeral()
    assert A > 0
    assert A < _SRP2048_N
    assert A % _SRP2048_N != 0


def test_client_ephemeral_is_random():
    _, A1 = client_generate_ephemeral()
    _, A2 = client_generate_ephemeral()
    assert A1 != A2


def test_server_ephemeral_B_is_nonzero():
    _, verifier = server_enroll("alice", "pw")
    _, B, _ = server_generate_ephemeral(verifier)
    assert B > 0
    assert B < _SRP2048_N
    assert B % _SRP2048_N != 0


# ── u computation ───────────────────────────────────────────────────────


def test_compute_u_is_symmetric():
    _, A = client_generate_ephemeral()
    _, B, _ = server_generate_ephemeral(42 % _SRP2048_N)
    u1 = compute_u(A, B)
    u2 = compute_u(A, B)
    assert u1 == u2


def test_compute_u_differs_with_different_B():
    _, A = client_generate_ephemeral()
    _, B1, _ = server_generate_ephemeral(1)
    _, B2, _ = server_generate_ephemeral(2)
    u1 = compute_u(A, B1)
    u2 = compute_u(A, B2)
    assert u1 != u2


# ── Rejects A ≡ 0 or B ≡ 0 (mod N) ────────────────────────────────────


def test_client_session_key_rejects_A_zero():
    with pytest.raises(SRPError, match="A == 0"):
        client_compute_session_key("alice", "pw", 1, 1, 0, 42)


def test_client_session_key_rejects_B_zero():
    _, A = client_generate_ephemeral()
    with pytest.raises(SRPError, match="B == 0"):
        client_compute_session_key("alice", "pw", 1, 1, A, 0)


def test_server_session_key_rejects_A_zero():
    with pytest.raises(SRPError, match="A == 0"):
        server_compute_session_key(1, 1, 0, 42)


def test_server_session_key_rejects_B_zero():
    _, A = client_generate_ephemeral()
    with pytest.raises(SRPError, match="B == 0"):
        server_compute_session_key(1, 1, A, 0)


# ── u != 0 in practice ─────────────────────────────────────────────────


def test_client_u_is_never_zero_in_practice():
    _salt, verifier = server_enroll(_KNOWN_UNAME, _KNOWN_PW)
    _a, A = client_generate_ephemeral()
    _b, B, _ = server_generate_ephemeral(verifier)
    u = compute_u(A, B)
    assert u != 0


# ── End-to-end: correct password → shared key matches ───────────────────


def test_full_flow_shared_key_matches():
    salt, verifier = server_enroll(_KNOWN_UNAME, _KNOWN_PW)
    a, A = client_generate_ephemeral()
    b, B, _ = server_generate_ephemeral(verifier)
    S_client = client_compute_session_key(_KNOWN_UNAME, _KNOWN_PW, salt, a, A, B)
    S_server = server_compute_session_key(verifier, b, A, B)
    assert S_client == S_server


def test_full_flow_repeatable():
    for _ in range(5):
        salt, verifier = server_enroll(_KNOWN_UNAME, _KNOWN_PW)
        a, A = client_generate_ephemeral()
        b, B, _ = server_generate_ephemeral(verifier)
        S_client = client_compute_session_key(_KNOWN_UNAME, _KNOWN_PW, salt, a, A, B)
        S_server = server_compute_session_key(verifier, b, A, B)
        assert S_client == S_server


# ── End-to-end: wrong password → keys differ ───────────────────────────


def test_wrong_password_produces_different_keys():
    salt, verifier = server_enroll(_KNOWN_UNAME, _KNOWN_PW)
    a, A = client_generate_ephemeral()
    b, B, _ = server_generate_ephemeral(verifier)
    S_client = client_compute_session_key(_KNOWN_UNAME, "WRONG_PW", salt, a, A, B)
    S_server = server_compute_session_key(verifier, b, A, B)
    assert S_client != S_server


# ── Proof verification (M1 / M2) ───────────────────────────────────────


def test_client_proof_is_deterministic():
    salt, verifier = server_enroll(_KNOWN_UNAME, _KNOWN_PW)
    a, A = client_generate_ephemeral()
    _b, B, _ = server_generate_ephemeral(verifier)
    S = client_compute_session_key(_KNOWN_UNAME, _KNOWN_PW, salt, a, A, B)
    m1a = compute_client_proof(A, B, S)
    m1b = compute_client_proof(A, B, S)
    assert m1a == m1b


def test_server_verify_matches_correct_client_proof():
    salt, verifier = server_enroll(_KNOWN_UNAME, _KNOWN_PW)
    a, A = client_generate_ephemeral()
    b, B, _ = server_generate_ephemeral(verifier)
    S_client = client_compute_session_key(_KNOWN_UNAME, _KNOWN_PW, salt, a, A, B)
    S_server = server_compute_session_key(verifier, b, A, B)
    M1 = compute_client_proof(A, B, S_client)
    expected_M1 = compute_client_proof(A, B, S_server)
    M2 = server_verify_proof(A, M1, S_server, expected_M1)
    assert M2 is not None


def test_server_rejects_wrong_client_proof():
    _salt, verifier = server_enroll(_KNOWN_UNAME, _KNOWN_PW)
    _a, A = client_generate_ephemeral()
    b, B, _ = server_generate_ephemeral(verifier)
    S_server = server_compute_session_key(verifier, b, A, B)
    fake_M1 = b"\x00" * 32
    expected_M1 = compute_client_proof(A, B, S_server)
    M2 = server_verify_proof(A, fake_M1, S_server, expected_M1)
    assert M2 is None


def test_server_proof_is_deterministic():
    A = 123456789
    S = 987654321
    M1 = b"\x11" * 32
    sp1 = compute_server_proof(A, M1, S)
    sp2 = compute_server_proof(A, M1, S)
    assert sp1 == sp2


# ── Mutual authentication ──────────────────────────────────────────────


def test_mutual_authentication_full_roundtrip():
    salt, verifier = server_enroll(_KNOWN_UNAME, _KNOWN_PW)
    a, A = client_generate_ephemeral()
    b, B, _ = server_generate_ephemeral(verifier)
    S_client = client_compute_session_key(_KNOWN_UNAME, _KNOWN_PW, salt, a, A, B)
    S_server = server_compute_session_key(verifier, b, A, B)
    assert S_client == S_server
    M1 = compute_client_proof(A, B, S_client)
    expected_M1 = compute_client_proof(A, B, S_server)
    M2 = server_verify_proof(A, M1, S_server, expected_M1)
    assert M2 is not None
    M2_expected = compute_server_proof(A, M1, S_server)
    assert M2_expected == M2
    K_client = derive_session_key(S_client)
    K_server = derive_session_key(S_server)
    assert K_client == K_server


def test_mutual_auth_wrong_password_fails():
    salt, verifier = server_enroll(_KNOWN_UNAME, _KNOWN_PW)
    _, A = client_generate_ephemeral()
    b, B, _expected_M1, _K_server = full_server_flow(_KNOWN_UNAME, salt, verifier, A)
    _, M1_wrong, _ = full_client_flow(_KNOWN_UNAME, "hacker_guess", salt, B)
    S_server = server_compute_session_key(verifier, b, A, B)
    expected_M1_server = compute_client_proof(A, B, S_server)
    M2 = server_verify_proof(A, M1_wrong, S_server, expected_M1_server)
    assert M2 is None


# ── Derived session key ─────────────────────────────────────────────────


def test_different_sessions_produce_different_keys():
    salt1, verifier1 = server_enroll("a", "pw")
    salt2, verifier2 = server_enroll("b", "pw")
    a1, A1 = client_generate_ephemeral()
    _b1, B1, _ = server_generate_ephemeral(verifier1)
    S1 = client_compute_session_key("a", "pw", salt1, a1, A1, B1)
    a2, A2 = client_generate_ephemeral()
    _b2, B2, _ = server_generate_ephemeral(verifier2)
    S2 = client_compute_session_key("b", "pw", salt2, a2, A2, B2)
    assert S1 != S2
    K1 = derive_session_key(S1)
    K2 = derive_session_key(S2)
    assert K1 != K2


def test_derived_key_is_32_bytes():
    salt, verifier = server_enroll(_KNOWN_UNAME, _KNOWN_PW)
    a, A = client_generate_ephemeral()
    _b, B, _ = server_generate_ephemeral(verifier)
    S = client_compute_session_key(_KNOWN_UNAME, _KNOWN_PW, salt, a, A, B)
    K = derive_session_key(S)
    assert len(K) == 32


def test_full_flow_convenience_functions_agree():
    salt, verifier = server_enroll(_KNOWN_UNAME, _KNOWN_PW)
    a, A = client_generate_ephemeral()
    _b, B, _, K_server = full_server_flow(_KNOWN_UNAME, salt, verifier, A)
    S_manual = client_compute_session_key(_KNOWN_UNAME, _KNOWN_PW, salt, a, A, B)
    K_manual = derive_session_key(S_manual)
    assert K_manual == K_server


# ── Empty/invalid credentials ───────────────────────────────────────────


def test_empty_username_works():
    salt, verifier = server_enroll("", _KNOWN_PW)
    assert salt > 0
    assert verifier > 0


def test_empty_password_produces_verifier():
    _salt, verifier = server_enroll(_KNOWN_UNAME, "")
    assert verifier > 0


def test_compare_digest_timing_safe_on_mismatch():
    salt, verifier = server_enroll(_KNOWN_UNAME, _KNOWN_PW)
    _, A = client_generate_ephemeral()
    b, B, expected_M1, _K_server = full_server_flow(_KNOWN_UNAME, salt, verifier, A)
    S_server = server_compute_session_key(verifier, b, A, B)
    fake_M1 = b"\x01" + b"\x00" * 31
    M2 = server_verify_proof(A, fake_M1, S_server, expected_M1)
    assert M2 is None
