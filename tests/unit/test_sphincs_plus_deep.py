"""Deep SPHINCS+ tests: WOTS+ chain/sign/pk_from_sig, XMSS Merkle tree
build/sign/verify, FORS sign/pk_from_sig, SLH-DSA keygen/sign/verify
round-trip, deterministic signing, wrong-key rejection, signature uniqueness,
and parameterised tests.

Pure-Python, stdlib only.
"""

from __future__ import annotations

import hashlib
import os
import struct

import pytest

from general_ludd.algorithms.sphincs_plus import (
    SphincsParams,
    _addr_set_chain,
    _addr_set_leaf,
    _addr_set_type,
    _compute_base_w,
    _fors_leaf,
    _hash_msg,
    _make_addr,
    _PARAMS_SLH_DSA_SHAKE_256s,
    _prf,
    _prf_msg,
    _thash_f,
    _thash_h,
    _wots_checksum,
    _xmss_node,
    fors_pk_from_sig,
    fors_sign,
    slh_keygen,
    slh_sign,
    slh_verify,
    wots_chain,
    wots_gen_pk,
    wots_pk_from_sig,
    wots_sign,
    xmss_gen_pk,
    xmss_pk_from_sig,
    xmss_sign,
)

# ── Test parameter sets ────────────────────────────────────────────────


def _test_params() -> SphincsParams:
    return SphincsParams(n=16, w=16, h_prime=6, d=2, a=2, k=2)


def _test_params_small() -> SphincsParams:
    return SphincsParams(n=8, w=4, h_prime=4, d=2, a=1, k=2)


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def tp() -> SphincsParams:
    return _test_params()


@pytest.fixture
def seed() -> bytes:
    return b"\x00" * 48


@pytest.fixture
def sk_seed() -> bytes:
    return hashlib.shake_256(b"test-sk-seed").digest(32)


@pytest.fixture
def pk_seed() -> bytes:
    return hashlib.shake_256(b"test-pk-seed").digest(32)


# ── WOTS+ tests ────────────────────────────────────────────────────────


class TestWots:
    def test_chain_identity(self) -> None:
        n, w = 16, 16
        pk_seed = os.urandom(32)
        addr = _make_addr()
        start = os.urandom(n)
        result = wots_chain(n, w, pk_seed, addr, start, 0, 0)
        assert result == start

    def test_chain_deterministic(self) -> None:
        n, w = 16, 16
        pk_seed = os.urandom(32)
        addr = _make_addr()
        start = os.urandom(n)
        r1 = wots_chain(n, w, pk_seed, addr, start, 5, 6)
        r2 = wots_chain(n, w, pk_seed, addr, start, 5, 6)
        assert r1 == r2

    def test_chain_changes_with_steps(self) -> None:
        n, w = 16, 16
        pk_seed = os.urandom(32)
        addr = _make_addr()
        start = os.urandom(n)
        r3 = wots_chain(n, w, pk_seed, addr, start, 3, 0)
        r5 = wots_chain(n, w, pk_seed, addr, start, 5, 0)
        assert r3 != r5

    def test_chain_length(self, tp: SphincsParams) -> None:
        pk_seed = os.urandom(32)
        addr = _make_addr()
        start = os.urandom(tp.n)
        result = wots_chain(tp.n, tp.w, pk_seed, addr, start, 10, 0)
        assert len(result) == tp.n

    def test_wots_sign_roundtrip(self, tp: SphincsParams) -> None:
        n, w = tp.n, tp.w
        sk_seed = os.urandom(32)
        pk_seed = os.urandom(32)
        addr = _make_addr()
        msg = os.urandom(n * 2)
        pk = wots_gen_pk(n, w, sk_seed, pk_seed, addr, tp)
        sig = wots_sign(n, w, sk_seed, pk_seed, addr, msg, tp)
        pk2 = wots_pk_from_sig(n, w, pk_seed, addr, sig, msg, tp)
        assert pk == pk2

    def test_wots_pk_rejection(self, tp: SphincsParams) -> None:
        n, w = tp.n, tp.w
        sk_seed = os.urandom(32)
        pk_seed = os.urandom(32)
        addr = _make_addr()
        msg1 = os.urandom(n * 2)
        msg2 = os.urandom(n * 2)
        pk = wots_gen_pk(n, w, sk_seed, pk_seed, addr, tp)
        sig = wots_sign(n, w, sk_seed, pk_seed, addr, msg1, tp)
        pk_from_wrong = wots_pk_from_sig(n, w, pk_seed, addr, sig, msg2, tp)
        assert pk != pk_from_wrong

    def test_wots_checksum(self) -> None:
        w = 16
        coeffs = [0] * 64
        cs = _wots_checksum(coeffs, w, 3)
        assert len(cs) == 3
        s = sum((w - 1) - v for v in coeffs)
        s <<= 4
        for i, c in enumerate(cs):
            assert c == (s >> (4 * i)) & (w - 1)

    def test_compute_base_w(self) -> None:
        w = 16
        val = b"\x12\x34\xab\xcd"
        coeffs = _compute_base_w(w, val, 8)
        assert len(coeffs) == 8
        for c in coeffs:
            assert 0 <= c < w


# ── XMSS Merkle tree tests ─────────────────────────────────────────────


class TestXmss:
    def test_xmss_pk_deterministic(self, tp: SphincsParams) -> None:
        sk_seed = os.urandom(32)
        pk_seed = os.urandom(32)
        addr = _make_addr()
        pk1 = xmss_gen_pk(tp.n, tp.h, sk_seed, pk_seed, addr, tp)
        pk2 = xmss_gen_pk(tp.n, tp.h, sk_seed, pk_seed, addr, tp)
        assert pk1 == pk2
        assert len(pk1) == tp.n

    def test_xmss_sign_verify(self, tp: SphincsParams) -> None:
        n, h = tp.n, tp.h
        sk_seed = os.urandom(32)
        pk_seed = os.urandom(32)
        addr = _make_addr()
        leaf = 3
        msg = os.urandom(n * 2)
        wots_sig, auth = xmss_sign(n, h, sk_seed, pk_seed, addr, msg, leaf, tp)
        assert len(auth) == h
        pk = xmss_pk_from_sig(n, h, leaf, wots_sig, auth, pk_seed, addr, msg, tp)
        expected_pk = xmss_gen_pk(n, h, sk_seed, pk_seed, addr, tp)
        assert pk == expected_pk

    def test_xmss_wrong_leaf_rejection(self, tp: SphincsParams) -> None:
        n, h = tp.n, tp.h
        sk_seed = os.urandom(32)
        pk_seed = os.urandom(32)
        addr = _make_addr()
        leaf = 3
        wrong_leaf = 2
        msg = os.urandom(n * 2)
        wots_sig, auth = xmss_sign(n, h, sk_seed, pk_seed, addr, msg, leaf, tp)
        pk_wrong = xmss_pk_from_sig(n, h, wrong_leaf, wots_sig, auth, pk_seed, addr, msg, tp)
        pk_correct = xmss_gen_pk(n, h, sk_seed, pk_seed, addr, tp)
        assert pk_wrong != pk_correct


# ── FORS tests ─────────────────────────────────────────────────────────


class TestFors:
    def test_fors_sign_roundtrip(self, tp: SphincsParams) -> None:
        n = tp.n
        sk_seed = os.urandom(32)
        pk_seed = os.urandom(32)
        addr = _make_addr()
        msg = os.urandom(n * 2)
        sig = fors_sign(n, tp, sk_seed, pk_seed, addr, msg)
        assert isinstance(sig, list)
        assert len(sig) == tp.k * (tp.a + 1)
        pk = fors_pk_from_sig(n, tp, pk_seed, addr, sig, msg)
        assert len(pk) == n

    def test_fors_is_deterministic(self, tp: SphincsParams) -> None:
        n = tp.n
        sk_seed = os.urandom(32)
        pk_seed = os.urandom(32)
        addr = _make_addr()
        msg = os.urandom(n * 2)
        sig1 = fors_sign(n, tp, sk_seed, pk_seed, addr, msg)
        sig2 = fors_sign(n, tp, sk_seed, pk_seed, addr, msg)
        assert sig1 == sig2

    def test_fors_different_msgs(self, tp: SphincsParams) -> None:
        n = tp.n
        sk_seed = os.urandom(32)
        pk_seed = os.urandom(32)
        addr = _make_addr()
        msg1 = os.urandom(n * 2)
        msg2 = os.urandom(n * 2)
        sig1 = fors_sign(n, tp, sk_seed, pk_seed, addr, msg1)
        sig2 = fors_sign(n, tp, sk_seed, pk_seed, addr, msg2)
        assert sig1 != sig2

    def test_fors_pk_from_sig_verification(self, tp: SphincsParams) -> None:
        n = tp.n
        sk_seed = os.urandom(32)
        pk_seed = os.urandom(32)
        addr = _make_addr()
        msg = os.urandom(n * 2)
        sig = fors_sign(n, tp, sk_seed, pk_seed, addr, msg)
        pk = fors_pk_from_sig(n, tp, pk_seed, addr, sig, msg)
        assert pk == fors_pk_from_sig(n, tp, pk_seed, addr, sig, msg)


# ── Hash engine tests ──────────────────────────────────────────────────


class TestHashEngine:
    def test_prf_deterministic(self) -> None:
        seed = os.urandom(32)
        addr = _make_addr()
        r1 = _prf(seed, addr)
        r2 = _prf(seed, addr)
        assert r1 == r2
        assert len(r1) == 32

    def test_prf_different_addr_different_output(self) -> None:
        seed = os.urandom(32)
        addr1 = _make_addr()
        addr2 = _make_addr(leaf_idx=5)
        assert _prf(seed, addr1) != _prf(seed, addr2)

    def test_thash_f_deterministic(self) -> None:
        n = 16
        pk_seed = os.urandom(32)
        addr = _make_addr()
        r1 = _thash_f(n, pk_seed, addr, b"hello")
        r2 = _thash_f(n, pk_seed, addr, b"hello")
        assert r1 == r2
        assert len(r1) == n

    def test_thash_h_deterministic(self) -> None:
        n = 16
        pk_seed = os.urandom(32)
        addr = _make_addr()
        r1 = _thash_h(n, pk_seed, addr, b"left", b"right")
        r2 = _thash_h(n, pk_seed, addr, b"left", b"right")
        assert r1 == r2
        assert len(r1) == n

    def test_hash_msg(self) -> None:
        r = os.urandom(16)
        pk_seed = os.urandom(32)
        pk_root = os.urandom(16)
        msg = b"test message"
        h = _hash_msg(r, pk_seed, pk_root, msg)
        assert len(h) == 16

    def test_prf_msg(self) -> None:
        sk_prf = os.urandom(16)
        msg = b"test message"
        out = _prf_msg(sk_prf, b"", msg)
        assert len(out) == len(sk_prf)


# ── Address scheme tests ───────────────────────────────────────────────


class TestAddress:
    def test_make_addr_zero(self) -> None:
        addr = _make_addr()
        assert len(bytes(addr)) == 32
        assert struct.unpack_from(">I", addr, 0)[0] == 0
        assert struct.unpack_from(">I", addr, 16)[0] == 0
        assert struct.unpack_from(">I", addr, 20)[0] == 0

    def test_make_addr_with_layer(self) -> None:
        addr = _make_addr(layer=3)
        assert struct.unpack_from(">I", addr, 0)[0] == 3

    def test_make_addr_with_tree(self) -> None:
        addr = _make_addr(tree_idx=42)
        assert struct.unpack_from(">Q", addr, 4)[0] == 42

    def test_make_addr_with_type(self) -> None:
        addr = _make_addr(addr_type=7)
        assert struct.unpack_from(">I", addr, 16)[0] == 7

    def test_make_addr_with_leaf(self) -> None:
        addr = _make_addr(leaf_idx=99)
        assert struct.unpack_from(">I", addr, 20)[0] == 99

    def test_addr_set_type(self) -> None:
        addr = _make_addr()
        _addr_set_type(addr, 5)
        assert struct.unpack_from(">I", addr, 16)[0] == 5

    def test_addr_set_leaf(self) -> None:
        addr = _make_addr()
        _addr_set_leaf(addr, 17)
        assert struct.unpack_from(">I", addr, 20)[0] == 17

    def test_addr_set_chain(self) -> None:
        addr = _make_addr()
        _addr_set_chain(addr, 33)
        assert struct.unpack_from(">I", addr, 24)[0] == 33


# ── SPHINCS+ top-level tests ───────────────────────────────────────────


class TestSlhDsa:
    def test_keygen_produces_valid_keys(self) -> None:
        pk, sk = slh_keygen()
        assert len(pk) == _PARAMS_SLH_DSA_SHAKE_256s.pk_bytes
        assert len(sk) == _PARAMS_SLH_DSA_SHAKE_256s.sk_bytes

    def test_sign_verify_roundtrip(self) -> None:
        pk, sk = slh_keygen()
        msg = b"Hello, SPHINCS+ !!!"
        sig = slh_sign(msg, sk)
        assert isinstance(sig, bytes)
        assert slh_verify(msg, sig, pk)

    def test_sign_verify_empty_message(self) -> None:
        pk, sk = slh_keygen()
        sig = slh_sign(b"", sk)
        assert slh_verify(b"", sig, pk)

    def test_sign_verify_long_message(self) -> None:
        pk, sk = slh_keygen()
        msg = os.urandom(4096)
        sig = slh_sign(msg, sk)
        assert slh_verify(msg, sig, pk)

    def test_sign_deterministic(self) -> None:
        _pk, sk = slh_keygen()
        msg = b"deterministic test"
        sig1 = slh_sign(msg, sk)
        sig2 = slh_sign(msg, sk)
        assert sig1 == sig2

    def test_different_messages_different_signatures(self) -> None:
        _pk, sk = slh_keygen()
        msg1 = b"message one"
        msg2 = b"message two"
        sig1 = slh_sign(msg1, sk)
        sig2 = slh_sign(msg2, sk)
        assert sig1 != sig2

    def test_different_keys_different_signatures(self) -> None:
        _, sk1 = slh_keygen()
        _, sk2 = slh_keygen()
        msg = b"same message"
        sig1 = slh_sign(msg, sk1)
        sig2 = slh_sign(msg, sk2)
        assert sig1 != sig2

    def test_wrong_key_fails_verification(self) -> None:
        _pk1, sk1 = slh_keygen()
        pk2, _ = slh_keygen()
        msg = b"verify with wrong key"
        sig = slh_sign(msg, sk1)
        assert not slh_verify(msg, sig, pk2)

    def test_tampered_message_fails(self) -> None:
        pk, sk = slh_keygen()
        msg = b"original message"
        sig = slh_sign(msg, sk)
        assert not slh_verify(b"tampered message", sig, pk)

    def test_tampered_signature_fails(self) -> None:
        pk, sk = slh_keygen()
        msg = b"test message"
        sig = slh_sign(msg, sk)
        tampered = bytearray(sig)
        tampered[0] ^= 0xFF
        assert not slh_verify(msg, bytes(tampered), pk)

    def test_truncated_signature_fails(self) -> None:
        pk, sk = slh_keygen()
        msg = b"test message"
        sig = slh_sign(msg, sk)
        assert not slh_verify(msg, sig[:8], pk)

    def test_multiple_sign_verify_cycles(self) -> None:
        pk, sk = slh_keygen()
        messages = [os.urandom(64) for _ in range(10)]
        for msg in messages:
            sig = slh_sign(msg, sk)
            assert slh_verify(msg, sig, pk)

    def test_keygen_produces_different_keys(self) -> None:
        pk1, sk1 = slh_keygen()
        pk2, sk2 = slh_keygen()
        assert pk1 != pk2
        assert sk1 != sk2

    def test_signature_minimum_length(self) -> None:
        _pk, sk = slh_keygen()
        sig = slh_sign(b"hello", sk)
        assert len(sig) >= _PARAMS_SLH_DSA_SHAKE_256s.n


# ── Small-parameter tests (faster keygen) ──────────────────────────────


class TestSmallParams:
    def test_keygen_small(self, tp: SphincsParams) -> None:
        pk, sk = slh_keygen(tp)
        assert len(pk) == tp.pk_bytes
        assert len(sk) == tp.sk_bytes

    def test_sign_verify_small(self, tp: SphincsParams) -> None:
        pk, sk = slh_keygen(tp)
        msg = b"small params test"
        sig = slh_sign(msg, sk, tp)
        assert slh_verify(msg, sig, pk, tp)

    def test_keygen_tiny_params(self) -> None:
        tp = _test_params_small()
        pk, _sk = slh_keygen(tp)
        assert len(pk) == tp.pk_bytes


# ── Parameters dataclass tests ─────────────────────────────────────────


class TestParams:
    def test_params_wots_len(self) -> None:
        tp = _test_params()
        assert tp.wots_len1 == 64
        assert tp.wots_len2 == 3
        assert tp.wots_len == 67

    def test_params_h(self) -> None:
        tp = _test_params()
        assert tp.h == tp.h_prime // tp.d

    def test_params_pk_bytes(self) -> None:
        tp = _test_params()
        assert tp.pk_bytes == 2 * tp.n

    def test_params_sk_bytes(self) -> None:
        tp = _test_params()
        assert tp.sk_bytes == 4 * tp.n

    def test_params_sig_bytes_is_positive(self) -> None:
        tp = _test_params()
        assert tp.sig_bytes > 0


# ── XMSS node computation ─────────────────────────────────────────────


class TestXmssNode:
    def test_node_height_zero(self, tp: SphincsParams) -> None:
        sk_seed = os.urandom(32)
        pk_seed = os.urandom(32)
        addr = _make_addr()
        node = _xmss_node(tp.n, tp.h, sk_seed, pk_seed, addr, 0, 0, tp)
        assert len(node) == tp.n

    def test_node_deterministic(self, tp: SphincsParams) -> None:
        sk_seed = os.urandom(32)
        pk_seed = os.urandom(32)
        addr = _make_addr()
        n1 = _xmss_node(tp.n, tp.h, sk_seed, pk_seed, addr, 3, 0, tp)
        n2 = _xmss_node(tp.n, tp.h, sk_seed, pk_seed, addr, 3, 0, tp)
        assert n1 == n2


# ── FORS leaf computation ──────────────────────────────────────────────


class TestForsLeaf:
    def test_fors_leaf_deterministic(self, tp: SphincsParams) -> None:
        sk_seed = os.urandom(32)
        pk_seed = os.urandom(32)
        addr = _make_addr()
        leaf1 = _fors_leaf(tp.n, sk_seed, pk_seed, addr, 7, tp.a)
        leaf2 = _fors_leaf(tp.n, sk_seed, pk_seed, addr, 7, tp.a)
        assert leaf1 == leaf2
        assert len(leaf1) == tp.n
