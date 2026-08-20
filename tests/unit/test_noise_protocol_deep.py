"""Deep Noise Protocol Framework tests: handshake patterns, AEAD transport,
symmetric state, key derivation, and edge cases.

Backed by the `cryptography` library (X25519, AESGCM, SHA256, HKDF).
"""

from __future__ import annotations

import pytest

from general_ludd.algorithms.noise_protocol import (
    CipherState,
    DecryptError,
    Direction,
    HandshakeError,
    KeyPair,
    NoiseError,
    SymmetricState,
    create_noise_session,
    dh,
)

# ── Key generation ─────────────────────────────────────────────────────


class TestKeyPair:
    def test_generate_produces_32_byte_keys(self) -> None:
        kp = KeyPair.generate()
        assert len(kp.private) == 32
        assert len(kp.public) == 32

    def test_generated_keys_are_not_equal(self) -> None:
        kp1 = KeyPair.generate()
        kp2 = KeyPair.generate()
        assert kp1.private != kp2.private
        assert kp1.public != kp2.public

    def test_generate_is_deterministic_not(self) -> None:
        seen = set()
        for _ in range(10):
            kp = KeyPair.generate()
            seen.add(kp.public)
        assert len(seen) == 10


# ── DH ─────────────────────────────────────────────────────────────────


class TestDH:
    def test_dh_produces_shared_secret(self) -> None:
        alice = KeyPair.generate()
        bob = KeyPair.generate()
        ss_alice = dh(alice.private, bob.public)
        ss_bob = dh(bob.private, alice.public)
        assert ss_alice == ss_bob
        assert len(ss_alice) == 32

    def test_dh_rejects_wrong_key_size(self) -> None:
        kp = KeyPair.generate()
        with pytest.raises(ValueError):
            dh(kp.private, b"\x00" * 16)

    def test_dh_produces_different_secrets_for_different_keys(self) -> None:
        alice = KeyPair.generate()
        bob = KeyPair.generate()
        carol = KeyPair.generate()
        ss_ab = dh(alice.private, bob.public)
        ss_ac = dh(alice.private, carol.public)
        assert ss_ab != ss_ac


# ── CipherState ────────────────────────────────────────────────────────


class TestCipherState:
    def test_uninitialized_has_no_key(self) -> None:
        cs = CipherState()
        assert not cs.has_key()

    def test_initialize_key_sets_key_and_resets_nonce(self) -> None:
        cs = CipherState()
        cs.nonce = 5
        cs.initialize_key(b"\x01" * 32)
        assert cs.has_key()
        assert cs.nonce == 0

    def test_initialize_key_rejects_wrong_length(self) -> None:
        cs = CipherState()
        with pytest.raises(NoiseError, match="32"):
            cs.initialize_key(b"\x01" * 16)

    def test_encrypt_without_key_passthrough(self) -> None:
        cs = CipherState()
        ct = cs.encrypt_with_ad(b"ad", b"hello")
        assert ct == b"hello"

    def test_decrypt_without_key_passthrough(self) -> None:
        cs = CipherState()
        pt = cs.decrypt_with_ad(b"ad", b"hello")
        assert pt == b"hello"

    def test_encrypt_decrypt_roundtrip(self) -> None:
        cs = CipherState()
        cs.initialize_key(b"\x02" * 32)
        plaintext = b"secret message for AEAD roundtrip"
        ad = b"associated data"
        ct = cs.encrypt_with_ad(ad, plaintext)
        assert ct != plaintext
        assert len(ct) == len(plaintext) + 16

        cs2 = CipherState()
        cs2.initialize_key(b"\x02" * 32)
        pt = cs2.decrypt_with_ad(ad, ct)
        assert pt == plaintext

    def test_decrypt_detects_tampering(self) -> None:
        cs = CipherState()
        cs.initialize_key(b"\x03" * 32)
        ct = list(cs.encrypt_with_ad(b"ad", b"secret"))
        ct[7] ^= 1

        cs2 = CipherState()
        cs2.initialize_key(b"\x03" * 32)
        with pytest.raises(DecryptError, match="AEAD"):
            cs2.decrypt_with_ad(b"ad", bytes(ct))

    def test_decrypt_detects_wrong_key(self) -> None:
        cs = CipherState()
        cs.initialize_key(b"\x04" * 32)
        ct = cs.encrypt_with_ad(b"ad", b"secret")

        cs2 = CipherState()
        cs2.initialize_key(b"\x05" * 32)
        with pytest.raises(DecryptError, match="AEAD"):
            cs2.decrypt_with_ad(b"ad", ct)

    def test_decrypt_detects_wrong_ad(self) -> None:
        cs = CipherState()
        cs.initialize_key(b"\x06" * 32)
        ct = cs.encrypt_with_ad(b"ad", b"secret")

        cs2 = CipherState()
        cs2.initialize_key(b"\x06" * 32)
        with pytest.raises(DecryptError, match="AEAD"):
            cs2.decrypt_with_ad(b"wrong", ct)

    def test_nonce_advances_after_encrypt(self) -> None:
        cs = CipherState()
        cs.initialize_key(b"\x07" * 32)
        assert cs.nonce == 0
        cs.encrypt_with_ad(b"", b"msg1")
        assert cs.nonce == 1
        cs.encrypt_with_ad(b"", b"msg2")
        assert cs.nonce == 2

    def test_nonce_advances_after_decrypt(self) -> None:
        cs = CipherState()
        cs.initialize_key(b"\x08" * 32)
        ct = cs.encrypt_with_ad(b"", b"msg")
        cs2 = CipherState()
        cs2.initialize_key(b"\x08" * 32)
        assert cs2.nonce == 0
        cs2.decrypt_with_ad(b"", ct)
        assert cs2.nonce == 1

    @pytest.mark.parametrize("operation", ["encrypt", "decrypt"])
    def test_nonce_exhaustion_fails_closed(self, operation: str) -> None:
        cs = CipherState(key=b"\x08" * 32, nonce=2**64 - 1)
        with pytest.raises(NoiseError, match="Nonce exhausted"):
            if operation == "encrypt":
                cs.encrypt_with_ad(b"", b"message")
            else:
                cs.decrypt_with_ad(b"", b"ciphertext")

    def test_split_produces_two_keys(self) -> None:
        cs = CipherState()
        cs.initialize_key(b"\x09" * 32)
        c1, c2 = cs.split()
        assert c1.has_key()
        assert c2.has_key()
        assert c1.nonce == 0
        assert c2.nonce == 0

    def test_split_without_key_raises(self) -> None:
        cs = CipherState()
        with pytest.raises(HandshakeError, match="uninitialized"):
            cs.split()


# ── SymmetricState ─────────────────────────────────────────────────────


class TestSymmetricState:
    def test_initialize_sets_name_hash(self) -> None:
        ss = SymmetricState()
        name = b"Noise_XX_25519_AESGCM_SHA256"
        ss.initialize_symmetric(name)
        assert ss.h != b"\x00" * 32
        assert ss.chaining_key == ss.h

    def test_mix_hash_changes_state(self) -> None:
        ss = SymmetricState()
        ss.initialize_symmetric(b"Noise_XX_25519_AESGCM_SHA256")
        h1 = ss.h
        ss.mix_hash(b"data")
        assert ss.h != h1

    def test_mix_key_initializes_cipher(self) -> None:
        ss = SymmetricState()
        ss.initialize_symmetric(b"Noise_XX_25519_AESGCM_SHA256")
        assert not ss.cipher_state.has_key()
        ss.mix_key(b"\x01" * 32)
        assert ss.cipher_state.has_key()

    def test_mix_key_and_hash_initializes_cipher(self) -> None:
        ss = SymmetricState()
        ss.initialize_symmetric(b"Noise_XX_25519_AESGCM_SHA256")
        h1 = ss.h
        ck1 = ss.chaining_key
        ss.mix_key_and_hash(b"\x02" * 32)
        assert ss.h != h1
        assert ss.chaining_key != ck1
        assert ss.cipher_state.has_key()

    def test_encrypt_and_hash(self) -> None:
        ss = SymmetricState()
        ss.initialize_symmetric(b"Noise_XX_25519_AESGCM_SHA256")
        ss.mix_key(b"\x03" * 32)
        h1 = ss.h
        ct = ss.encrypt_and_hash(b"hello")
        assert ct != b"hello"
        assert ss.h != h1

    def test_decrypt_and_hash(self) -> None:
        ss1 = SymmetricState()
        ss1.initialize_symmetric(b"Noise_XX_25519_AESGCM_SHA256")
        ss1.mix_key(b"\x04" * 32)
        ct = ss1.encrypt_and_hash(b"hello")

        ss2 = SymmetricState()
        ss2.initialize_symmetric(b"Noise_XX_25519_AESGCM_SHA256")
        ss2.mix_key(b"\x04" * 32)
        h_before = ss2.h
        pt = ss2.decrypt_and_hash(ct)
        assert pt == b"hello"
        assert ss2.h != h_before

    def test_get_handshake_hash(self) -> None:
        ss = SymmetricState()
        ss.initialize_symmetric(b"Noise_XX_25519_AESGCM_SHA256")
        h1 = ss.get_handshake_hash()
        assert len(h1) == 32
        ss.mix_hash(b"data")
        assert ss.get_handshake_hash() != h1

    def test_split_produces_separate_keys(self) -> None:
        ss = SymmetricState()
        ss.initialize_symmetric(b"Noise_XX_25519_AESGCM_SHA256")
        ss.mix_key(b"\x05" * 32)
        c1, c2 = ss.split()
        assert c1.has_key()
        assert c2.has_key()
        plaintext = b"post-handshake message"
        ct = c1.encrypt_with_ad(b"", plaintext)
        c1d = CipherState()
        c1d.initialize_key(c1.key)  # type: ignore[arg-type]
        assert c1d.decrypt_with_ad(b"", ct) == plaintext


# ── Handshake patterns ─────────────────────────────────────────────────


class TestHandshakeNN:
    """NN — no static keys, mutual authentication via ephemeral DH only."""

    def test_nn_full_handshake(self) -> None:
        initiator = create_noise_session("NN", initiator=True)
        responder = create_noise_session("NN", initiator=False)

        msg1 = initiator.write_message(b"ping")
        assert len(msg1) >= 32
        payload1 = responder.read_message(msg1)
        assert payload1 == b"ping"

        msg2 = responder.write_message(b"pong")
        assert len(msg2) >= 16
        payload2 = initiator.read_message(msg2)
        assert payload2 == b"pong"

        assert initiator.completed()
        assert responder.completed()

    def test_nn_transport_after_handshake(self) -> None:
        initiator = create_noise_session("NN", initiator=True)
        responder = create_noise_session("NN", initiator=False)

        responder.read_message(initiator.write_message(b""))
        initiator.read_message(responder.write_message(b""))

        c1_send, c1_recv = initiator.split()
        c2_recv, c2_send = responder.split()

        msg = b"hello over Noise transport"
        ct = c1_send.encrypt_with_ad(b"", msg)
        assert c2_recv.decrypt_with_ad(b"", ct) == msg

        ct2 = c2_send.encrypt_with_ad(b"", msg)
        assert c1_recv.decrypt_with_ad(b"", ct2) == msg


class TestHandshakeNK:
    """NK — initiator knows responder's static key."""

    def test_nk_full_handshake(self) -> None:
        responder_static = KeyPair.generate()

        initiator = create_noise_session("NK", initiator=True, remote_static=responder_static.public)
        responder = create_noise_session("NK", initiator=False, local_static=responder_static)

        msg1 = initiator.write_message(b"ping")
        payload1 = responder.read_message(msg1)
        assert payload1 == b"ping"

        msg2 = responder.write_message(b"pong")
        payload2 = initiator.read_message(msg2)
        assert payload2 == b"pong"

        assert initiator.completed()
        assert responder.completed()

    def test_nk_transport(self) -> None:
        rs = KeyPair.generate()
        i = create_noise_session("NK", True, remote_static=rs.public)
        r = create_noise_session("NK", False, local_static=rs)

        r.read_message(i.write_message(b""))
        i.read_message(r.write_message(b""))

        i_send, i_recv = i.split()
        r_recv, r_send = r.split()

        msg = b"NK transport message"
        ct = i_send.encrypt_with_ad(b"", msg)
        assert r_recv.decrypt_with_ad(b"", ct) == msg

        ct2 = r_send.encrypt_with_ad(b"", msg)
        assert i_recv.decrypt_with_ad(b"", ct2) == msg


class TestHandshakeKK:
    """KK — both parties know each other's static keys."""

    def test_kk_full_handshake(self) -> None:
        init_static = KeyPair.generate()
        resp_static = KeyPair.generate()

        initiator = create_noise_session(
            "KK",
            True,
            local_static=init_static,
            remote_static=resp_static.public,
        )
        responder = create_noise_session(
            "KK",
            False,
            local_static=resp_static,
            remote_static=init_static.public,
        )

        msg1 = initiator.write_message(b"hello KK")
        payload1 = responder.read_message(msg1)
        assert payload1 == b"hello KK"

        msg2 = responder.write_message(b"hi back")
        payload2 = initiator.read_message(msg2)
        assert payload2 == b"hi back"

        assert initiator.completed()
        assert responder.completed()

    def test_kk_transport(self) -> None:
        i_s = KeyPair.generate()
        r_s = KeyPair.generate()
        i = create_noise_session("KK", True, local_static=i_s, remote_static=r_s.public)
        r = create_noise_session("KK", False, local_static=r_s, remote_static=i_s.public)

        r.read_message(i.write_message(b""))
        i.read_message(r.write_message(b""))

        i_send, _i_recv = i.split()
        r_recv, _r_send = r.split()

        for payload in [b"msg1", b"msg2", b"msg3", b"longer message for transport"]:
            ct = i_send.encrypt_with_ad(b"", payload)
            assert r_recv.decrypt_with_ad(b"", ct) == payload


class TestHandshakeXX:
    """XX — mutual authentication with 3-message pattern."""

    def test_xx_full_handshake(self) -> None:
        init_static = KeyPair.generate()
        resp_static = KeyPair.generate()

        initiator = create_noise_session("XX", True, local_static=init_static)
        responder = create_noise_session("XX", False, local_static=resp_static)

        msg1 = initiator.write_message(b"")
        payload1 = responder.read_message(msg1)
        assert payload1 == b""

        msg2 = responder.write_message(b"")
        payload2 = initiator.read_message(msg2)
        assert payload2 == b""

        msg3 = initiator.write_message(b"XX payload")
        payload3 = responder.read_message(msg3)
        assert payload3 == b"XX payload"

        assert initiator.completed()
        assert responder.completed()

        assert initiator.rs is not None
        assert responder.rs is not None

    def test_xx_transport(self) -> None:
        i_s = KeyPair.generate()
        r_s = KeyPair.generate()
        i = create_noise_session("XX", True, local_static=i_s)
        r = create_noise_session("XX", False, local_static=r_s)

        r.read_message(i.write_message(b""))
        i.read_message(r.write_message(b""))
        r.read_message(i.write_message(b""))

        i_send, _i_recv = i.split()
        r_recv, _r_send = r.split()

        msg = b"XX transport payload"
        ct = i_send.encrypt_with_ad(b"", msg)
        assert r_recv.decrypt_with_ad(b"", ct) == msg


class TestHandshakeIK:
    """IK — initiator knows responder's static key, sends its own static."""

    def test_ik_full_handshake(self) -> None:
        resp_static = KeyPair.generate()
        init_static = KeyPair.generate()

        initiator = create_noise_session("IK", True, local_static=init_static, remote_static=resp_static.public)
        responder = create_noise_session("IK", False, local_static=resp_static)

        msg1 = initiator.write_message(b"ik msg")
        payload1 = responder.read_message(msg1)
        assert payload1 == b"ik msg"

        msg2 = responder.write_message(b"response")
        payload2 = initiator.read_message(msg2)
        assert payload2 == b"response"

        assert initiator.completed()
        assert responder.completed()
        assert responder.rs is not None

    def test_ik_transport(self) -> None:
        r_s = KeyPair.generate()
        i_s = KeyPair.generate()
        i = create_noise_session("IK", True, local_static=i_s, remote_static=r_s.public)
        r = create_noise_session("IK", False, local_static=r_s)

        r.read_message(i.write_message(b""))
        i.read_message(r.write_message(b""))

        i_send, _i_recv = i.split()
        r_recv, _r_send = r.split()

        msg = b"IK transport"
        ct = i_send.encrypt_with_ad(b"", msg)
        assert r_recv.decrypt_with_ad(b"", ct) == msg


class TestHandshakeIN:
    """IN — initiator sends static in first message."""

    def test_in_full_handshake(self) -> None:
        init_static = KeyPair.generate()

        initiator = create_noise_session("IN", True, local_static=init_static)
        responder = create_noise_session("IN", False, remote_static=None)

        msg1 = initiator.write_message(b"in h0")
        assert len(msg1) >= 32
        payload1 = responder.read_message(msg1)
        assert payload1 == b"in h0"

        msg2 = responder.write_message(b"in h1")
        payload2 = initiator.read_message(msg2)
        assert payload2 == b"in h1"

        assert initiator.completed()
        assert responder.completed()

    def test_in_transport(self) -> None:
        i_s = KeyPair.generate()
        i = create_noise_session("IN", True, local_static=i_s)
        r = create_noise_session("IN", False)

        r.read_message(i.write_message(b""))
        i.read_message(r.write_message(b""))

        i_send, _i_recv = i.split()
        r_recv, _r_send = r.split()

        msg = b"IN transport data"
        ct = i_send.encrypt_with_ad(b"", msg)
        assert r_recv.decrypt_with_ad(b"", ct) == msg


# ── Error handling ─────────────────────────────────────────────────────


class TestErrorHandling:
    def test_write_at_wrong_turn_raises(self) -> None:
        create_noise_session("NN", True)
        r = create_noise_session("NN", False)
        # responder tries to write first
        with pytest.raises(NoiseError, match="Cannot write"):
            r.write_message(b"")

    def test_read_at_wrong_turn_raises(self) -> None:
        i = create_noise_session("NN", True)
        create_noise_session("NN", False)
        # initiator tries to read first
        with pytest.raises(NoiseError, match="Cannot read"):
            i.read_message(b"\x00" * 64)

    def test_read_too_short_message_raises(self) -> None:
        create_noise_session("NN", True)
        r = create_noise_session("NN", False)
        with pytest.raises(NoiseError, match="too short"):
            r.read_message(b"short")

    def test_write_after_complete_raises(self) -> None:
        i = create_noise_session("NN", True)
        r = create_noise_session("NN", False)
        r.read_message(i.write_message(b""))
        i.read_message(r.write_message(b""))
        with pytest.raises(NoiseError, match="No more messages"):
            i.write_message(b"")

    def test_read_after_complete_raises(self) -> None:
        i = create_noise_session("NN", True)
        r = create_noise_session("NN", False)
        r.read_message(i.write_message(b""))
        i.read_message(r.write_message(b""))
        with pytest.raises(NoiseError, match="No more messages"):
            r.read_message(b"")

    def test_completed_false_before_handshake(self) -> None:
        i = create_noise_session("XX", True)
        assert not i.completed()

    def test_handshake_write_enforces_noise_message_limit(self) -> None:
        initiator = create_noise_session("NN", True)
        with pytest.raises(NoiseError, match="65,535"):
            initiator.write_message(b"x" * 65_504)

    def test_handshake_read_enforces_noise_message_limit(self) -> None:
        responder = create_noise_session("NN", False)
        with pytest.raises(NoiseError, match="65,535"):
            responder.read_message(b"x" * 65_536)


# ── Prologue and additional data ───────────────────────────────────────


class TestPrologue:
    def test_prologue_agreement_and_channel_binding(self) -> None:
        p1_i = create_noise_session("NN", True, prologue=b"version 1.0")
        p1_r = create_noise_session("NN", False, prologue=b"version 1.0")
        p2_i = create_noise_session("NN", True, prologue=b"version 2.0")
        p2_r = create_noise_session("NN", False, prologue=b"version 2.0")

        p1_r.read_message(p1_i.write_message(b""))
        p1_i.read_message(p1_r.write_message(b""))
        p2_r.read_message(p2_i.write_message(b""))
        p2_i.read_message(p2_r.write_message(b""))

        assert p1_i._handshake_hash() == p1_r._handshake_hash()
        assert p2_i._handshake_hash() == p2_r._handshake_hash()
        assert p1_i._handshake_hash() != p2_i._handshake_hash()

        mismatched_i = create_noise_session("NN", True, prologue=b"version 1.0")
        mismatched_r = create_noise_session("NN", False, prologue=b"version 2.0")
        mismatched_r.read_message(mismatched_i.write_message(b""))
        with pytest.raises(DecryptError, match="AEAD"):
            mismatched_i.read_message(mismatched_r.write_message(b""))


class TestDirection:
    def test_opposite(self) -> None:
        assert Direction.INITIATOR.opposite() == Direction.RESPONDER
        assert Direction.RESPONDER.opposite() == Direction.INITIATOR
