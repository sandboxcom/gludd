"""SPHINCS+ tests after pyspx refactor -- public API only.

slh_keygen, slh_sign, slh_verify, SphincsParams, _PARAMS_SLH_DSA_SHAKE_256s.
"""

from __future__ import annotations

import os

from general_ludd.algorithms.sphincs_plus import (
    SphincsParams,
    _PARAMS_SLH_DSA_SHAKE_256s,
    slh_keygen,
    slh_sign,
    slh_verify,
)

# -- SLH-DSA top-level tests ---------------------------------------------


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

    def test_sign_randomized(self) -> None:
        pk, sk = slh_keygen()
        msg = b"randomized test"
        sig1 = slh_sign(msg, sk)
        sig2 = slh_sign(msg, sk)
        assert sig1 != sig2
        assert slh_verify(msg, sig1, pk)
        assert slh_verify(msg, sig2, pk)

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

    def test_truncated_signature_rejected(self) -> None:
        pk, sk = slh_keygen()
        msg = b"test message"
        sig = slh_sign(msg, sk)
        truncated = sig[:8]
        raised = False
        try:
            slh_verify(msg, truncated, pk)
        except Exception:
            raised = True
        assert raised
        assert len(truncated) < len(sig)

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


# -- Parameters dataclass tests ------------------------------------------


class TestParams:
    def test_default_pk_bytes_positive(self) -> None:
        assert _PARAMS_SLH_DSA_SHAKE_256s.pk_bytes > 0

    def test_default_sk_bytes_positive(self) -> None:
        assert _PARAMS_SLH_DSA_SHAKE_256s.sk_bytes > 0

    def test_default_sig_bytes_positive(self) -> None:
        assert _PARAMS_SLH_DSA_SHAKE_256s.sig_bytes > 0

    def test_custom_n_stored(self) -> None:
        tp = SphincsParams(n=32)
        assert tp.n == 32

    def test_custom_params_have_positive_sizes(self) -> None:
        tp = SphincsParams(n=8)
        assert tp.pk_bytes > 0
        assert tp.sk_bytes > 0
        assert tp.sig_bytes > 0

    def test_params_api_compatibility(self) -> None:
        tp = SphincsParams(n=16)
        pk, sk = slh_keygen(tp)
        sig = slh_sign(b"compat", sk, tp)
        assert slh_verify(b"compat", sig, pk, tp)
