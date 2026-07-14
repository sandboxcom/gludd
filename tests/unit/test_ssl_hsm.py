from __future__ import annotations

from general_ludd.ssl.hsm import (
    HSMConfig,
    HSMKey,
    _MockHSMSession,
    configure_pkcs11,
    create_mock_session,
    import_key,
    list_keys,
    sign_with_hsm_key,
)


class TestConfigurePKCS11:
    def test_returns_hsm_config(self) -> None:
        config = configure_pkcs11("/usr/lib/libpkcs11.so", 0)
        assert isinstance(config, HSMConfig)
        assert config.module_path == "/usr/lib/libpkcs11.so"
        assert config.slot_id == 0

    def test_default_pin_is_none(self) -> None:
        config = configure_pkcs11("/usr/lib/libpkcs11.so", 1)
        assert config.pin is None

    def test_pin_is_settable(self) -> None:
        config = configure_pkcs11("/usr/lib/libpkcs11.so", 2, pin="1234")
        assert config.pin == "1234"

    def test_default_label_empty(self) -> None:
        config = configure_pkcs11("/usr/lib/libpkcs11.so", 0)
        assert config.label == ""

    def test_default_token_label_empty(self) -> None:
        config = configure_pkcs11("/usr/lib/libpkcs11.so", 0)
        assert config.token_label == ""


class TestCreateMockSession:
    def test_returns_hsm_session(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        assert isinstance(session, _MockHSMSession)

    def test_mock_has_preloaded_keys(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        keys = session.list_keys()
        assert len(keys) == 3

    def test_preloaded_keys_have_expected_ids(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        key_ids = {k.key_id for k in session.list_keys()}
        assert "rsa-2048-001" in key_ids
        assert "ecdsa-p256-001" in key_ids
        assert "ed25519-001" in key_ids

    def test_preloaded_rsa_key_attributes(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        key = session._keys["rsa-2048-001"]
        assert key.key_type == "RSA"
        assert key.key_size == 2048
        assert key.algorithm == "RSA-PKCS"

    def test_preloaded_ecdsa_key_capabilities(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        key = session._keys["ecdsa-p256-001"]
        assert key.capabilities == ["sign"]

    def test_preloaded_ed25519_key_size(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        key = session._keys["ed25519-001"]
        assert key.key_size == 256
        assert key.algorithm == "Ed25519"


class TestListKeys:
    def test_returns_key_list(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        keys = list_keys(session)
        assert len(keys) == 3
        assert all(isinstance(k, HSMKey) for k in keys)


class TestSignWithHSMKey:
    def test_sign_rsa_key_returns_signature(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        sig = sign_with_hsm_key(session, "rsa-2048-001", b"test data")
        assert isinstance(sig, bytes)
        assert len(sig) > 0
        assert b"MOCK_SIG" in sig

    def test_sign_ecdsa_key_returns_signature(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        sig = sign_with_hsm_key(session, "ecdsa-p256-001", b"test data")
        assert isinstance(sig, bytes)
        assert len(sig) > 0

    def test_sign_ed25519_key_returns_signature(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        sig = sign_with_hsm_key(session, "ed25519-001", b"test data")
        assert isinstance(sig, bytes)
        assert len(sig) > 0

    def test_sign_default_mechanism(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        sig = sign_with_hsm_key(session, "rsa-2048-001", b"data")
        assert b"MOCK_SIG:rsa-2048-001:SHA256-RSA-PKCS" in sig

    def test_sign_custom_mechanism(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        sig = sign_with_hsm_key(session, "rsa-2048-001", b"data", mechanism="SHA512-RSA-PKCS")
        assert b"SHA512-RSA-PKCS" in sig

    def test_sign_unknown_key_raises(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        try:
            sign_with_hsm_key(session, "nonexistent", b"data")
            raise AssertionError("expected KeyError")
        except KeyError:
            pass

    def test_signature_contains_key_id(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        sig = sign_with_hsm_key(session, "ecdsa-p256-001", b"data")
        assert b"ecdsa-p256-001" in sig

    def test_different_keys_produce_different_signatures(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        sig_rsa = sign_with_hsm_key(session, "rsa-2048-001", b"same data")
        sig_ec = sign_with_hsm_key(session, "ecdsa-p256-001", b"same data")
        assert sig_rsa != sig_ec


class TestImportKey:
    def test_import_key_returns_hsm_key(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        key = import_key(session, b"fake-pem-data", "my-import-key")
        assert key is not None
        assert isinstance(key, HSMKey)
        assert key.label == "my-import-key"
        assert key.key_size == 4096

    def test_imported_key_appears_in_key_list(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        import_key(session, b"fake-pem", "test-key")
        keys = list_keys(session)
        assert len(keys) == 4
        key_ids = {k.key_id for k in keys}
        assert "imported-test-key" in key_ids

    def test_imported_key_is_signable(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        import_key(session, b"fake-pem", "signing-key")
        sig = sign_with_hsm_key(session, "imported-signing-key", b"hello")
        assert isinstance(sig, bytes)
        assert len(sig) > 0

    def test_import_key_id_is_predictable(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        key = import_key(session, b"data", "predictable")
        assert key is not None
        assert key.key_id == "imported-predictable"


class TestSessionClose:
    def test_close_clears_keys(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        session.close()
        assert len(session.list_keys()) == 0

    def test_close_sets_closed_flag(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        assert session.closed is False
        session.close()
        assert session.closed is True

    def test_close_twice_is_safe(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        session.close()
        session.close()
        assert session.closed is True
        assert len(session.list_keys()) == 0

    def test_sign_after_close_raises(self) -> None:
        config = HSMConfig(module_path="/fake.so", slot_id=0)
        session = create_mock_session(config)
        session.close()
        try:
            sign_with_hsm_key(session, "rsa-2048-001", b"data")
            raise AssertionError("expected KeyError")
        except KeyError:
            pass


class TestHSMKeyDataclass:
    def test_default_capabilities_empty_list(self) -> None:
        key = HSMKey(key_id="k1", label="l", key_type="RSA", key_size=2048, algorithm="RSA")
        assert key.capabilities == []

    def test_default_created_at_none(self) -> None:
        key = HSMKey(key_id="k1", label="l", key_type="RSA", key_size=2048, algorithm="RSA")
        assert key.created_at is None

    def test_capabilities_settable(self) -> None:
        key = HSMKey(
            key_id="k1", label="l", key_type="RSA", key_size=2048, algorithm="RSA",
            capabilities=["sign", "verify", "decrypt"],
        )
        assert len(key.capabilities) == 3
        assert "decrypt" in key.capabilities

    def test_created_at_settable(self) -> None:
        key = HSMKey(
            key_id="k1", label="l", key_type="RSA", key_size=2048, algorithm="RSA",
            created_at="2025-01-01T00:00:00Z",
        )
        assert key.created_at == "2025-01-01T00:00:00Z"


class TestHSMConfigDataclass:
    def test_minimal_config(self) -> None:
        config = HSMConfig(module_path="/lib/pkcs11.so", slot_id=0)
        assert config.module_path == "/lib/pkcs11.so"
        assert config.slot_id == 0
        assert config.pin is None
        assert config.label == ""
        assert config.token_label == ""

    def test_full_config(self) -> None:
        config = HSMConfig(
            module_path="/lib/pkcs11.so",
            slot_id=3,
            pin="secret",
            label="my-hsm",
            token_label="My HSM Token",
        )
        assert config.pin == "secret"
        assert config.label == "my-hsm"
        assert config.token_label == "My HSM Token"


class TestConfigurePKCS11Convenience:
    def test_configure_returns_hsmconfig_ready_for_mock_session(self) -> None:
        config = configure_pkcs11("/usr/lib/softhsm2.so", 0, pin="12345")
        session = create_mock_session(config)
        keys = list_keys(session)
        assert len(keys) == 3
