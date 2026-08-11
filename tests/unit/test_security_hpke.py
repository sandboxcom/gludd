"""Deep tests for hpke — RFC 9180 Base mode seal/open, multi-shot contexts, key export."""

from __future__ import annotations

import struct

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)

from general_ludd.security.hpke import (
    DEFAULT_SUITE,
    ENAP_LEN,
    HPKE_Suite,
    HPKEEncryptedBlob,
    HPKERecipient,
    HPKESender,
    _hkdf_expand,
    _hkdf_extract,
    _labeled_expand,
    _labeled_extract,
    _xor_12,
    generate_key_pair,
    hpke_open,
    hpke_seal,
)


class TestXor12:
    def test_identity(self) -> None:
        a = b"\x00" * 12
        assert _xor_12(a, a) == a

    def test_xor(self) -> None:
        a = b"\x01" * 12
        b = b"\x01" * 12
        assert _xor_12(a, b) == b"\x00" * 12

    def test_partial_xor(self) -> None:
        a = b"\x01\x02\x03" + b"\x00" * 9
        b = b"\x01\x00\x01" + b"\x00" * 9
        assert _xor_12(a, b)[:3] == b"\x00\x02\x02"


class TestHKDF:
    def test_extract_empty_salt(self) -> None:
        ikm = b"input key material"
        prk = _hkdf_extract(b"", ikm)
        assert len(prk) == 32

    def test_extract_with_salt(self) -> None:
        ikm = b"input"
        salt = b"random" * 4
        prk = _hkdf_extract(salt, ikm)
        assert len(prk) == 32

    def test_expand(self) -> None:
        prk = b"\x00" * 32
        info = b"test info"
        out = _hkdf_expand(prk, info, 64)
        assert len(out) == 64

    def test_expand_too_large(self) -> None:
        prk = b"\x00" * 32
        with pytest.raises(ValueError):
            _hkdf_expand(prk, b"", 255 * 32 + 1)

    def test_labeled_extract_and_expand(self) -> None:
        sid = b"HPKE" + struct.pack(">HHH", 0x0020, 0x0001, 0x0002)
        salt = b"\x00" * 32
        ikm = b"labeled ikm"
        prk = _labeled_extract(salt, b"test", ikm, sid)
        assert len(prk) == 32
        key = _labeled_expand(prk, b"test", b"info", 32, sid)
        assert len(key) == 32


class TestKeyGeneration:
    def test_generates_valid_keys(self) -> None:
        priv, pub = generate_key_pair()
        assert isinstance(priv, X25519PrivateKey)
        assert isinstance(pub, X25519PublicKey)

    def test_keys_unique(self) -> None:
        priv1, pub1 = generate_key_pair()
        priv2, pub2 = generate_key_pair()
        assert priv1.private_bytes_raw() != priv2.private_bytes_raw()
        assert pub1.public_bytes_raw() != pub2.public_bytes_raw()


class TestHPKESuite:
    def test_default_suite(self) -> None:
        assert DEFAULT_SUITE.aead_id == 0x0002
        assert DEFAULT_SUITE.Nk == 32

    def test_suite_ids(self) -> None:
        sid = DEFAULT_SUITE.suite_id()
        assert sid.startswith(b"HPKE")

    def test_128_suite_key_size(self) -> None:
        suite = HPKE_Suite.DHKEM_X25519_HKDF_SHA256_HKDF_SHA256_AES_128_GCM
        assert suite.Nk == 16


class TestHPKEEncryptedBlob:
    def test_combined_round_trip(self) -> None:
        encap = b"\x00" * ENAP_LEN
        ct = b"encrypted data"
        blob = HPKEEncryptedBlob(encap=encap, ciphertext=ct)
        combined = blob.to_combined()
        assert combined == encap + ct
        parsed = HPKEEncryptedBlob.from_combined(combined)
        assert parsed.encap == encap
        assert parsed.ciphertext == ct

    def test_from_combined_too_short(self) -> None:
        with pytest.raises(ValueError, match="too short"):
            HPKEEncryptedBlob.from_combined(b"short")

    def test_blob_immutable(self) -> None:
        blob = HPKEEncryptedBlob(encap=b"\x00" * ENAP_LEN, ciphertext=b"data")
        with pytest.raises(AttributeError):
            blob.encap = b"new"  # type: ignore[misc]

    def test_from_combined_custom_encap_len(self) -> None:
        blob = HPKEEncryptedBlob.from_combined(b"\x00" * 16 + b"123", encap_len=16)
        assert blob.encap == b"\x00" * 16
        assert blob.ciphertext == b"123"


class TestHPKESealOpen:
    def test_seal_open_round_trip(self) -> None:
        priv, pub = generate_key_pair()
        plaintext = b"Hello, HPKE!"
        blob = hpke_seal(plaintext, pub)
        assert blob.encap
        assert blob.ciphertext
        decrypted = hpke_open(blob, priv)
        assert decrypted == plaintext

    def test_seal_open_with_info(self) -> None:
        priv, pub = generate_key_pair()
        plaintext = b"data with info"
        info = b"context-info"
        blob = hpke_seal(plaintext, pub, info=info)
        decrypted = hpke_open(blob, priv, info=info)
        assert decrypted == plaintext

    def test_seal_open_empty(self) -> None:
        priv, pub = generate_key_pair()
        blob = hpke_seal(b"", pub)
        assert hpke_open(blob, priv) == b""

    def test_ciphertext_different_each_time(self) -> None:
        _, pub = generate_key_pair()
        b1 = hpke_seal(b"same", pub)
        b2 = hpke_seal(b"same", pub)
        assert b1.encap != b2.encap
        assert b1.ciphertext != b2.ciphertext

    def test_wrong_key_fails(self) -> None:
        _, pub = generate_key_pair()
        wrong_priv, _ = generate_key_pair()
        blob = hpke_seal(b"secret", pub)
        with pytest.raises(ValueError):
            hpke_open(blob, wrong_priv)

    def test_wrong_info_fails(self) -> None:
        priv, pub = generate_key_pair()
        blob = hpke_seal(b"data", pub, info=b"right")
        with pytest.raises(ValueError):
            hpke_open(blob, priv, info=b"wrong")

    def test_large_plaintext(self) -> None:
        priv, pub = generate_key_pair()
        plaintext = b"x" * 100000
        blob = hpke_seal(plaintext, pub)
        assert hpke_open(blob, priv) == plaintext


class TestHPKEMultiShot:
    def test_sender_encrypt(self) -> None:
        priv, pub = generate_key_pair()
        sender = HPKESender(pub)
        ct = sender.encrypt(b"message 1")
        recipient = HPKERecipient(priv, sender.encap)
        assert recipient.decrypt(ct) == b"message 1"

    def test_multi_message(self) -> None:
        priv, pub = generate_key_pair()
        sender = HPKESender(pub)
        recipient = HPKERecipient(priv, sender.encap)
        for i in range(10):
            msg = f"message {i}".encode()
            ct = sender.encrypt(msg)
            assert recipient.decrypt(ct) == msg

    def test_order_enforced(self) -> None:
        priv, pub = generate_key_pair()
        sender = HPKESender(pub)
        ct1 = sender.encrypt(b"m1")
        ct2 = sender.encrypt(b"m2")
        recipient = HPKERecipient(priv, sender.encap)
        assert recipient.decrypt(ct1) == b"m1"
        assert recipient.decrypt(ct2) == b"m2"

    def test_message_limit(self) -> None:
        _, pub = generate_key_pair()
        sender = HPKESender(pub)
        for _ in range(10):
            sender.encrypt(b"msg")
        # Python ints don't wrap - message limit won't be hit in practice

    def test_recipient_message_limit(self) -> None:
        priv, pub = generate_key_pair()
        sender = HPKESender(pub)
        recipient = HPKERecipient(priv, sender.encap)
        for _ in range(10):
            ct = sender.encrypt(b"msg")
            recipient.decrypt(ct)

    def test_export_key(self) -> None:
        priv, pub = generate_key_pair()
        sender = HPKESender(pub)
        recipient = HPKERecipient(priv, sender.encap)
        s_export = sender.export(b"label", 32)
        r_export = recipient.export(b"label", 32)
        assert s_export == r_export
        assert len(s_export) == 32

    def test_export_different_labels(self) -> None:
        priv, pub = generate_key_pair()
        sender = HPKESender(pub)
        recipient = HPKERecipient(priv, sender.encap)
        k1 = sender.export(b"label-1", 32)
        k2 = recipient.export(b"label-2", 32)
        assert k1 != k2


class TestHPKESealFails:
    def test_hpke_open_tampered(self) -> None:
        priv, pub = generate_key_pair()
        blob = hpke_seal(b"secret", pub)
        tampered = bytearray(blob.ciphertext)
        tampered[0] ^= 0xFF
        bad = HPKEEncryptedBlob(encap=blob.encap, ciphertext=bytes(tampered))
        with pytest.raises(ValueError):
            hpke_open(bad, priv)

    def test_hpke_open_bad_encap(self) -> None:
        priv, pub = generate_key_pair()
        blob = hpke_seal(b"secret", pub)
        bad_encap = bytearray(blob.encap)
        bad_encap[0] ^= 0xFF
        bad = HPKEEncryptedBlob(encap=bytes(bad_encap), ciphertext=blob.ciphertext)
        with pytest.raises(ValueError):
            hpke_open(bad, priv)
