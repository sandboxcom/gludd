"""Tests for TLS 1.3 handshake state machine using cryptography."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509 import load_pem_x509_certificate

from general_ludd.ssl.tls13_handshake import (
    CIPHER_SUITE_MAP,
    TLS_AES_128_GCM_SHA256,
    TLS_AES_256_GCM_SHA384,
    HandshakeConfig,
    HandshakeCryptoError,
    HandshakeError,
    HandshakePeerError,
    HandshakeState,
    HandshakeStateError,
    KeyExchange,
    NamedGroup,
    RecordProtection,
    Tls13Handshake,
    TranscriptHash,
    compute_finished_verify_data,
    compute_tls13_keys,
    generate_key_share,
    generate_self_signed_cert,
)

# ═══════════════════════════════════════════════════════════════════════════
# TranscriptHash tests
# ═══════════════════════════════════════════════════════════════════════════


class TestTranscriptHash:
    def test_empty_digest_is_correct_length_sha256(self):
        th = TranscriptHash("sha256")
        d = th.digest()
        assert len(d) == 32
        assert d == hashes.Hash(hashes.SHA256()).finalize()

    def test_empty_digest_is_correct_length_sha384(self):
        th = TranscriptHash("sha384")
        d = th.digest()
        assert len(d) == 48
        assert d == hashes.Hash(hashes.SHA384()).finalize()

    def test_update_changes_digest(self):
        th = TranscriptHash("sha256")
        d1 = th.digest()
        th.update(b"hello")
        d2 = th.digest()
        assert d1 != d2

    def test_update_replaces_hash(self):
        th = TranscriptHash("sha256")
        th.update(b"data1")
        th.update(b"data2")
        d = th.digest()
        expected = hashes.Hash(hashes.SHA256())
        expected.update(b"data2")
        assert d == expected.finalize()

    def test_rejects_unsupported_hash(self):
        with pytest.raises(HandshakeError, match="Unsupported"):
            TranscriptHash("md5")


# ═══════════════════════════════════════════════════════════════════════════
# RecordProtection tests
# ═══════════════════════════════════════════════════════════════════════════


class TestRecordProtection:
    def test_encrypt_decrypt_roundtrip_aes128gcm(self):
        key = b"\x01" * 16
        iv = b"\x02" * 12
        rp = RecordProtection("aes-128-gcm", key, iv)
        ct = rp.encrypt(b"hello tls 1.3")
        rp2 = RecordProtection("aes-128-gcm", key, iv)
        pt = rp2.decrypt(ct)
        assert pt == b"hello tls 1.3"

    def test_encrypt_decrypt_roundtrip_chacha20(self):
        key = b"\x01" * 32
        iv = b"\x02" * 12
        rp = RecordProtection("chacha20-poly1305", key, iv)
        ct = rp.encrypt(b"hello tls 1.3")
        rp2 = RecordProtection("chacha20-poly1305", key, iv)
        pt = rp2.decrypt(ct)
        assert pt == b"hello tls 1.3"

    def test_sequence_number_increments(self):
        key = b"\x01" * 16
        iv = b"\x02" * 12
        rp = RecordProtection("aes-128-gcm", key, iv)
        assert rp.sequence_number == 0
        rp.encrypt(b"msg")
        assert rp.sequence_number == 1
        rp.encrypt(b"msg")
        assert rp.sequence_number == 2

    def test_reset_zeroes_sequence(self):
        key = b"\x01" * 16
        iv = b"\x02" * 12
        rp = RecordProtection("aes-128-gcm", key, iv)
        rp.encrypt(b"a")
        rp.encrypt(b"b")
        rp.reset()
        assert rp.sequence_number == 0

    def test_different_iv_produces_different_ciphertexts(self):
        key = b"\x01" * 16
        rp1 = RecordProtection("aes-128-gcm", key, b"\x02" * 12)
        rp2 = RecordProtection("aes-128-gcm", key, b"\x03" * 12)
        ct1 = rp1.encrypt(b"msg")
        ct2 = rp2.encrypt(b"msg")
        assert ct1 != ct2

    def test_decrypt_tampered_fails(self):
        key = b"\x01" * 16
        iv = b"\x02" * 12
        rp = RecordProtection("aes-128-gcm", key, iv)
        ct = rp.encrypt(b"hello")
        tampered = ct[:-1] + bytes([ct[-1] ^ 0x01])
        rp2 = RecordProtection("aes-128-gcm", key, iv)
        with pytest.raises(ValueError):
            rp2.decrypt(tampered)

    def test_authentication_failure_poison_record_protection(self):
        key = b"\x01" * 16
        iv = b"\x02" * 12
        sender = RecordProtection("aes-128-gcm", key, iv)
        ciphertext = sender.encrypt(b"authenticated")
        tampered = ciphertext[:-1] + bytes([ciphertext[-1] ^ 0x01])
        receiver = RecordProtection("aes-128-gcm", key, iv)

        with pytest.raises(HandshakeCryptoError):
            receiver.decrypt(tampered)
        with pytest.raises(HandshakeCryptoError, match="unusable"):
            receiver.decrypt(ciphertext)
        with pytest.raises(HandshakeCryptoError, match="unusable"):
            receiver.encrypt(b"must not reuse failed state")

    def test_rejects_unknown_aead(self):
        with pytest.raises(HandshakeError, match="Unsupported AEAD"):
            RecordProtection("rc4", b"\x01" * 16, b"\x02" * 12)

    def test_encrypt_with_associated_data(self):
        key = b"\x01" * 16
        iv = b"\x02" * 12
        rp = RecordProtection("aes-128-gcm", key, iv)
        ad = b"header"
        ct = rp.encrypt(b"payload", associated_data=ad)
        rp2 = RecordProtection("aes-128-gcm", key, iv)
        pt = rp2.decrypt(ct, associated_data=ad)
        assert pt == b"payload"


# ═══════════════════════════════════════════════════════════════════════════
# KeyExchange tests
# ═══════════════════════════════════════════════════════════════════════════


class TestKeyExchange:
    def test_x25519_exchange_produces_shared_secret(self):
        alice = KeyExchange(NamedGroup.X25519)
        bob = KeyExchange(NamedGroup.X25519)
        ss_a = alice.exchange(bob.public_bytes)
        ss_b = bob.exchange(alice.public_bytes)
        assert len(ss_a) == 32
        assert ss_a == ss_b

    def test_x25519_public_bytes_is_32(self):
        ke = KeyExchange(NamedGroup.X25519)
        assert len(ke.public_bytes) == 32

    def test_x448_exchange_produces_shared_secret(self):
        alice = KeyExchange(NamedGroup.X448)
        bob = KeyExchange(NamedGroup.X448)
        ss_a = alice.exchange(bob.public_bytes)
        ss_b = bob.exchange(alice.public_bytes)
        assert len(ss_a) == 56
        assert ss_a == ss_b

    def test_x448_public_bytes_is_56(self):
        ke = KeyExchange(NamedGroup.X448)
        assert len(ke.public_bytes) == 56

    def test_each_exchange_produces_unique_secret(self):
        ke1 = KeyExchange(NamedGroup.X25519)
        ke2 = KeyExchange(NamedGroup.X25519)
        ss1 = ke1.exchange(ke2.public_bytes)
        ke3 = KeyExchange(NamedGroup.X25519)
        ke4 = KeyExchange(NamedGroup.X25519)
        ss2 = ke3.exchange(ke4.public_bytes)
        assert ss1 != ss2

    def test_group_property_returns_correct_group(self):
        ke = KeyExchange(NamedGroup.X25519)
        assert ke.group == NamedGroup.X25519


# ═══════════════════════════════════════════════════════════════════════════
# generate_key_share tests
# ═══════════════════════════════════════════════════════════════════════════


class TestGenerateKeyShare:
    def test_default_is_x25519(self):
        ks = generate_key_share()
        assert ks.group == NamedGroup.X25519
        assert len(ks.public_key) == 32

    def test_x448(self):
        ks = generate_key_share(NamedGroup.X448)
        assert ks.group == NamedGroup.X448
        assert len(ks.public_key) == 56


# ═══════════════════════════════════════════════════════════════════════════
# Key schedule tests
# ═══════════════════════════════════════════════════════════════════════════


class TestKeySchedule:
    def test_compute_tls13_keys_aes128(self):
        shared = b"\x00" * 32
        secrets = compute_tls13_keys(shared, TLS_AES_128_GCM_SHA256)
        assert len(secrets.client_handshake_key) == 16
        assert len(secrets.server_handshake_key) == 16
        assert len(secrets.client_handshake_iv) == 12
        assert len(secrets.server_handshake_iv) == 12
        assert len(secrets.client_application_key) == 16
        assert len(secrets.server_application_key) == 16
        assert len(secrets.exporter_master_secret) == 32
        assert len(secrets.resumption_master_secret) == 32

    def test_compute_tls13_keys_aes256(self):
        shared = b"\x00" * 32
        secrets = compute_tls13_keys(shared, TLS_AES_256_GCM_SHA384)
        assert len(secrets.client_handshake_key) == 24
        assert len(secrets.server_handshake_key) == 24
        assert len(secrets.exporter_master_secret) == 48

    def test_compute_tls13_keys_different_shared_produces_different_keys(self):
        s1 = compute_tls13_keys(b"\x00" * 32, TLS_AES_128_GCM_SHA256)
        s2 = compute_tls13_keys(b"\x01" * 32, TLS_AES_128_GCM_SHA256)
        assert s1.client_handshake_key != s2.client_handshake_key
        assert s1.server_handshake_key != s2.server_handshake_key

    def test_compute_tls13_keys_rejects_unknown_cipher(self):
        with pytest.raises(HandshakeError, match="Unknown cipher"):
            compute_tls13_keys(b"\x00" * 32, 0x9999)

    def test_finished_verify_data_sha256(self):
        key = b"\x00" * 32
        th = b"\x01" * 32
        vd = compute_finished_verify_data(key, th, "sha256")
        assert len(vd) == 32

    def test_finished_verify_data_sha384(self):
        key = b"\x00" * 48
        th = b"\x01" * 48
        vd = compute_finished_verify_data(key, th, "sha384")
        assert len(vd) == 48

    def test_finished_verify_data_deterministic(self):
        vd1 = compute_finished_verify_data(b"\x00" * 32, b"\x01" * 32, "sha256")
        vd2 = compute_finished_verify_data(b"\x00" * 32, b"\x01" * 32, "sha256")
        assert vd1 == vd2

    def test_finished_verify_data_different_transcript(self):
        vd1 = compute_finished_verify_data(b"\x00" * 32, b"\x01" * 32, "sha256")
        vd2 = compute_finished_verify_data(b"\x00" * 32, b"\x02" * 32, "sha256")
        assert vd1 != vd2


# ═══════════════════════════════════════════════════════════════════════════
# Handshake FSM state-transition tests
# ═══════════════════════════════════════════════════════════════════════════


class TestHandshakeStateTransitions:
    def test_initial_state_is_idle(self):
        hs = Tls13Handshake()
        assert hs.state == HandshakeState.IDLE

    def test_build_client_hello_transitions_to_client_hello_sent(self):
        hs = Tls13Handshake()
        hs.build_client_hello()
        assert hs.state == HandshakeState.CLIENT_HELLO_SENT

    def test_client_hello_returns_bytes(self):
        hs = Tls13Handshake()
        ch = hs.build_client_hello()
        assert isinstance(ch, bytes)
        assert len(ch) > 0
        assert ch[0] == 1  # HandshakeType client_hello

    def test_cannot_send_client_hello_twice(self):
        hs = Tls13Handshake()
        hs.build_client_hello()
        with pytest.raises(HandshakeStateError):
            hs.build_client_hello()

    def test_process_server_hello_transitions(self):
        hs = Tls13Handshake()
        hs.build_client_hello()
        hs.process_server_hello(b"\x02" + b"\x00" * 4)
        assert hs.state == HandshakeState.SERVER_HELLO_RCVD

    def test_process_encrypted_extensions_transitions(self):
        hs = Tls13Handshake()
        hs.build_client_hello()
        peer_ks = KeyExchange(NamedGroup.X25519).public_bytes
        hs.process_server_hello(_mock_server_hello(peer_ks))
        hs.derive_handshake_keys(peer_ks)
        _init_server_encrypt(hs)
        ee = _server_rp.encrypt(b"\x08\x00\x00\x00")
        hs.process_encrypted_extensions(ee)
        assert hs.state == HandshakeState.EE_RCVD

    @pytest.mark.parametrize(
        ("plaintext", "match"),
        [
            (b"\x08\x00", "truncated"),
            (b"\x0b\x00\x00\x00", "unexpected handshake type"),
            (b"\x08\x00\x00\x01", "length mismatch"),
        ],
    )
    def test_encrypted_extensions_rejects_malformed_handshake_frames(
        self,
        plaintext: bytes,
        match: str,
    ) -> None:
        hs = Tls13Handshake()
        hs.build_client_hello()
        peer_ks = KeyExchange(NamedGroup.X25519).public_bytes
        hs.process_server_hello(_mock_server_hello(peer_ks))
        hs.derive_handshake_keys(peer_ks)
        _init_server_encrypt(hs)

        with pytest.raises(HandshakePeerError, match=match):
            hs.process_encrypted_extensions(_server_rp.encrypt(plaintext))
        assert hs.state == HandshakeState.SERVER_HELLO_RCVD

    def test_process_certificate_transitions(self):
        hs = _handshake_through_ee()
        cert_pem, _ = generate_self_signed_cert("test.example")
        cert_msg = _server_rp.encrypt(b"\x0b\x00\x00\x04\x00\x00\x00\x00")
        hs.process_certificate(cert_msg, pem_chain=[cert_pem])
        assert hs.state == HandshakeState.CERT_RCVD

    def test_process_certificate_verify_transitions(self):
        hs = _handshake_through_cert()
        cv = _server_rp.encrypt(_server_certificate_verify_message(hs))
        hs.process_certificate_verify(cv)
        assert hs.state == HandshakeState.CV_RCVD

    def test_process_finished_transitions(self):
        hs = _handshake_through_cv()
        fin = _server_rp.encrypt(_server_finished_message(hs))
        hs.process_finished(fin)
        assert hs.state == HandshakeState.SERVER_FIN_RCVD

    def test_invalid_finished_does_not_transition(self):
        hs = _handshake_through_cv()
        fin = _server_rp.encrypt(b"\x14\x00\x00\x20" + b"\x00" * 32)

        with pytest.raises(HandshakeCryptoError, match="Finished"):
            hs.process_finished(fin)
        assert hs.state == HandshakeState.CV_RCVD

    def test_build_client_finished_transitions_to_connected(self):
        hs = _handshake_through_server_finished()
        hs.build_client_finished()
        assert hs.state == HandshakeState.CONNECTED

    def test_is_connected_after_full_handshake(self):
        hs = _handshake_through_server_finished()
        assert not hs.is_connected
        hs.build_client_finished()
        assert hs.is_connected

    def test_out_of_order_transition_raises_error(self):
        hs = Tls13Handshake()
        with pytest.raises(HandshakeStateError):
            hs.process_server_hello(b"bad")


# ═══════════════════════════════════════════════════════════════════════════
# Full handshake integration tests
# ═══════════════════════════════════════════════════════════════════════════


class TestFullHandshake:
    def test_full_handshake_completes(self):
        peer_key_exchange = KeyExchange(NamedGroup.X25519)
        client = Tls13Handshake()
        client_finished, secrets = client.do_full_handshake(peer_key_exchange.public_bytes)
        assert client.is_connected
        assert len(client_finished) > 0
        assert len(secrets.client_application_key) == 16

    def test_full_handshake_application_data_roundtrip(self):
        peer_key_exchange = KeyExchange(NamedGroup.X25519)
        client = Tls13Handshake()
        client.do_full_handshake(peer_key_exchange.public_bytes)

        secrets = client.secrets
        assert secrets is not None
        client_peer = RecordProtection(
            "aes-128-gcm",
            secrets.client_application_key,
            secrets.client_application_iv,
        )
        server_peer = RecordProtection(
            "aes-128-gcm",
            secrets.server_application_key,
            secrets.server_application_iv,
        )

        outbound = client.encrypt_application_data(b"hello world")
        assert client_peer.decrypt(outbound) == b"hello world"
        inbound = server_peer.encrypt(b"hello world")
        assert client.decrypt_application_data(inbound) == b"hello world"

    def test_application_records_use_distinct_nonces(self):
        peer_key_exchange = KeyExchange(NamedGroup.X25519)
        client = Tls13Handshake()
        client.do_full_handshake(peer_key_exchange.public_bytes)

        assert client.encrypt_application_data(b"repeat") != client.encrypt_application_data(b"repeat")

    def test_full_handshake_with_aes256(self):
        config = HandshakeConfig(cipher_suites=[TLS_AES_256_GCM_SHA384])
        client = Tls13Handshake(config)
        peer_ke = KeyExchange(NamedGroup.X25519)
        _cf, secrets = client.do_full_handshake(peer_ke.public_bytes)
        assert client.is_connected
        assert len(secrets.client_application_key) == 24

    def test_full_handshake_different_messages_produce_different_finished(self):
        c1 = Tls13Handshake()
        ke1 = KeyExchange(NamedGroup.X25519)
        cf1, _ = c1.do_full_handshake(ke1.public_bytes)

        c2 = Tls13Handshake()
        ke2 = KeyExchange(NamedGroup.X25519)
        cf2, _ = c2.do_full_handshake(ke2.public_bytes)

        assert cf1 != cf2

    def test_cipher_suite_property(self):
        client = Tls13Handshake()
        assert client.cipher_suite is None
        client.build_client_hello()
        assert client.cipher_suite == TLS_AES_128_GCM_SHA256

    def test_secrets_none_before_keys_derived(self):
        client = Tls13Handshake()
        assert client.secrets is None

    def test_peer_certificate_none_by_default(self):
        client = Tls13Handshake()
        assert client.peer_certificate is None

    def test_encrypt_before_keys_raises(self):
        client = Tls13Handshake()
        client.build_client_hello()
        with pytest.raises(HandshakeStateError):
            client.encrypt_handshake(b"data")

    def test_encrypt_application_data_before_connected_raises(self):
        client = Tls13Handshake()
        with pytest.raises(HandshakeStateError):
            client.encrypt_application_data(b"data")

    def test_application_data_rejected_until_finished_authenticated(self):
        client = _handshake_through_ee()
        with pytest.raises(HandshakeStateError):
            client.encrypt_application_data(b"data")


# ═══════════════════════════════════════════════════════════════════════════
# Cipher suite mapping tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCipherSuiteMap:
    def test_three_suites_defined(self):
        assert len(CIPHER_SUITE_MAP) == 3

    def test_aes128_gcm_sha256(self):
        assert CIPHER_SUITE_MAP[0x1301] == ("aes-128-gcm", "sha256")

    def test_aes256_gcm_sha384(self):
        assert CIPHER_SUITE_MAP[0x1302] == ("aes-256-gcm", "sha384")

    def test_chacha20_poly1305_sha256(self):
        assert CIPHER_SUITE_MAP[0x1303] == ("chacha20-poly1305", "sha256")


# ═══════════════════════════════════════════════════════════════════════════
# HandshakeConfig tests
# ═══════════════════════════════════════════════════════════════════════════


class TestHandshakeConfig:
    def test_defaults(self):
        cfg = HandshakeConfig()
        assert cfg.cipher_suites == [TLS_AES_128_GCM_SHA256]
        assert cfg.named_group == NamedGroup.X25519
        assert cfg.server_name == "localhost"

    def test_custom_server_name(self):
        cfg = HandshakeConfig(server_name="example.com")
        assert cfg.server_name == "example.com"

    def test_custom_cipher_suites(self):
        cfg = HandshakeConfig(cipher_suites=[TLS_AES_256_GCM_SHA384])
        assert cfg.cipher_suites == [TLS_AES_256_GCM_SHA384]


# ═══════════════════════════════════════════════════════════════════════════
# NamedGroup tests
# ═══════════════════════════════════════════════════════════════════════════


class TestNamedGroup:
    def test_x25519_value(self):
        assert NamedGroup.X25519.value == 0x001D

    def test_x448_value(self):
        assert NamedGroup.X448.value == 0x001E

    def test_secp256r1_value(self):
        assert NamedGroup.SECP256R1.value == 0x0017


# ═══════════════════════════════════════════════════════════════════════════
# Certificate generation tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCertGeneration:
    def test_self_signed_cert_is_pem(self):
        pem, _key = generate_self_signed_cert("test.local")
        assert pem.startswith(b"-----BEGIN CERTIFICATE-----")
        assert pem.endswith(b"-----END CERTIFICATE-----\n")

    def test_self_signed_cert_loads(self):
        pem, _ = generate_self_signed_cert("test.local")
        cert = load_pem_x509_certificate(pem)
        cn = cert.subject.get_attributes_for_oid(
            __import__("cryptography.x509.oid", fromlist=["NameOID"]).NameOID.COMMON_NAME
        )[0].value
        assert cn == "test.local"

    def test_self_signed_cert_can_sign(self):
        _pem, key = generate_self_signed_cert("test.local")
        from cryptography.hazmat.primitives.asymmetric import ec as ecc

        assert isinstance(key, ecc.EllipticCurvePrivateKey)


# ═══════════════════════════════════════════════════════════════════════════
# Record protection sequence number wraps test
# ═══════════════════════════════════════════════════════════════════════════


class TestRecordProtectionSequence:
    def test_sequence_does_not_overflow_small_range(self):
        key = b"\x01" * 16
        iv = b"\x02" * 12
        rp = RecordProtection("aes-128-gcm", key, iv)
        for _ in range(100):
            rp.encrypt(b"msg")
        assert rp.sequence_number == 100


# ═══════════════════════════════════════════════════════════════════════════
# Handshake errors
# ═══════════════════════════════════════════════════════════════════════════


class TestHandshakeErrors:
    def test_handshake_error_is_exception(self):
        assert issubclass(HandshakeError, Exception)

    def test_state_error_is_handshake_error(self):
        assert issubclass(HandshakeStateError, HandshakeError)

    def test_crypto_error_is_handshake_error(self):
        assert issubclass(HandshakeCryptoError, HandshakeError)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _mock_server_hello(peer_key_share: bytes) -> bytes:
    from general_ludd.ssl.tls13_handshake import (
        _encode_ext,
        _encode_uint8_bytes,
        _encode_uint16_leb,
        _encode_uint16_val,
        _encode_uint24_val,
    )

    cs = TLS_AES_128_GCM_SHA256
    extensions_data = _encode_ext(
        _encode_uint16_val(43) + _encode_uint16_leb(_encode_uint16_val(0x0304))
    ) + _encode_ext(
        _encode_uint16_val(51)
        + _encode_uint16_leb(_encode_uint16_val(NamedGroup.X25519.value) + _encode_uint16_leb(peer_key_share))
    )
    payload = (
        b"\x03\x03"
        + b"\x00" * 32
        + _encode_uint8_bytes(b"")
        + _encode_uint16_val(cs)
        + b"\x00"
        + _encode_uint16_leb(extensions_data)
    )
    return b"\x02" + _encode_uint24_val(len(payload)) + payload


def _server_encrypt(hs: Tls13Handshake, plaintext: bytes) -> bytes:
    secrets = hs.secrets
    assert secrets is not None
    return _server_rp.encrypt(plaintext)


_server_rp: RecordProtection | None = None
_server_signing_key: ec.EllipticCurvePrivateKey | None = None


def _init_server_encrypt(hs: Tls13Handshake) -> None:
    global _server_rp
    secrets = hs.secrets
    assert secrets is not None
    _server_rp = RecordProtection("aes-128-gcm", secrets.server_handshake_key, secrets.server_handshake_iv)


def _handshake_through_ee() -> Tls13Handshake:
    global _server_rp
    hs = Tls13Handshake()
    hs.build_client_hello()
    peer_ks = KeyExchange(NamedGroup.X25519).public_bytes
    hs.process_server_hello(_mock_server_hello(peer_ks))
    hs.derive_handshake_keys(peer_ks)
    _init_server_encrypt(hs)
    ee = _server_rp.encrypt(b"\x08\x00\x00\x00")
    hs.process_encrypted_extensions(ee)
    return hs


def _handshake_through_cert() -> Tls13Handshake:
    global _server_signing_key
    hs = _handshake_through_ee()
    cert_pem, _server_signing_key = generate_self_signed_cert("test.example")
    cert_msg = _server_rp.encrypt(b"\x0b\x00\x00\x04\x00\x00\x00\x00")
    hs.process_certificate(cert_msg, pem_chain=[cert_pem])
    return hs


def _server_certificate_verify_message(hs: Tls13Handshake) -> bytes:
    assert _server_signing_key is not None
    signature = _server_signing_key.sign(
        hs.build_server_certificate_verify_content(),
        ec.ECDSA(hashes.SHA256()),
    )
    payload = b"\x04\x03" + len(signature).to_bytes(2, "big") + signature
    return b"\x0f" + len(payload).to_bytes(3, "big") + payload


def _handshake_through_cv() -> Tls13Handshake:
    hs = _handshake_through_cert()
    cv = _server_rp.encrypt(_server_certificate_verify_message(hs))
    hs.process_certificate_verify(cv)
    return hs


def _server_finished_message(hs: Tls13Handshake) -> bytes:
    verify_data = hs.build_server_finished_verify_data()
    return b"\x14" + len(verify_data).to_bytes(3, "big") + verify_data


def _handshake_through_server_finished() -> Tls13Handshake:
    hs = _handshake_through_cv()
    fin = _server_rp.encrypt(_server_finished_message(hs))
    hs.process_finished(fin)
    return hs
