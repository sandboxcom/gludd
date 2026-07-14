"""Structural tests for secrets/config.py — OpenBaoConfig."""

from __future__ import annotations

from general_ludd.secrets.config import OpenBaoConfig


class TestOpenBaoConfig:
    def test_import_succeeds(self):
        assert OpenBaoConfig is not None

    def test_default_instantiation_succeeds(self):
        cfg = OpenBaoConfig()
        assert cfg.mode == "auto"
        assert cfg.backend == "openbao"
        assert cfg.binary_path is None
        assert cfg.external_url is None
        assert cfg.external_token is None
        assert cfg.external_tls_verify is True
        assert cfg.local_image == "ghcr.io/openbao/openbao"
        assert cfg.local_image_digest_pin is None
        assert cfg.local_container_runtime == "podman_preferred"
        assert cfg.kv_mount == "secret"
        assert cfg.auth_method == "approle"
        assert cfg.approle_role_name == "agentic-harness"
        assert cfg.weekly_image_update_scan is True
        assert cfg.weekly_image_update_creates_manual_hold is True

    def test_mode_validation_rejects_invalid(self):
        import pytest
        with pytest.raises(ValueError):
            OpenBaoConfig(mode="invalid")
        assert True, "ValueError was raised for invalid mode"

    def test_serialized_external_token_is_redacted(self):
        cfg = OpenBaoConfig(external_token="secret123")
        data = cfg.model_dump()
        assert data["external_token"] == "**REDACTED**"

    def test_serialized_none_token_remains_none(self):
        cfg = OpenBaoConfig(external_token=None)
        data = cfg.model_dump()
        assert data["external_token"] is None

    def test_kv_mount_strips_and_rejects_empty(self):
        import pytest
        assert OpenBaoConfig(kv_mount="  foo  ").kv_mount == "foo"
        with pytest.raises(ValueError):
            OpenBaoConfig(kv_mount="   ")
