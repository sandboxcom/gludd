"""Structural tests for secrets/config.py — OpenBaoConfig."""

from __future__ import annotations

import pytest

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
        with pytest.raises(ValueError):
            OpenBaoConfig(mode="invalid")

    def test_serialized_external_token_is_redacted(self):
        cfg = OpenBaoConfig(external_token="secret123")
        data = cfg.model_dump()
        assert data["external_token"] == "**REDACTED**"

    def test_serialized_none_token_remains_none(self):
        cfg = OpenBaoConfig(external_token=None)
        data = cfg.model_dump()
        assert data["external_token"] is None

    def test_kv_mount_strips_and_rejects_empty(self):
        assert OpenBaoConfig(kv_mount="  foo  ").kv_mount == "foo"
        with pytest.raises(ValueError):
            OpenBaoConfig(kv_mount="   ")

    # ── mode validation ──────────────────────────────────────────────────

    def test_mode_auto_accepted(self):
        cfg = OpenBaoConfig(mode="auto")
        assert cfg.mode == "auto"

    def test_mode_external_accepted(self):
        cfg = OpenBaoConfig(mode="external")
        assert cfg.mode == "external"

    def test_mode_disabled_accepted(self):
        cfg = OpenBaoConfig(mode="disabled")
        assert cfg.mode == "disabled"

    # ── backend validation ───────────────────────────────────────────────

    def test_backend_openbao_accepted(self):
        cfg = OpenBaoConfig(backend="openbao")
        assert cfg.backend == "openbao"

    def test_backend_vault_accepted(self):
        cfg = OpenBaoConfig(backend="vault")
        assert cfg.backend == "vault"

    def test_backend_invalid_rejected(self):
        with pytest.raises(ValueError):
            OpenBaoConfig(backend="hashicorp")

    # ── auth_method validation ───────────────────────────────────────────

    def test_auth_method_strips_and_rejects_empty(self):
        assert OpenBaoConfig(auth_method="  approle  ").auth_method == "approle"
        with pytest.raises(ValueError):
            OpenBaoConfig(auth_method="   ")

    # ── binary_path ──────────────────────────────────────────────────────

    def test_binary_path_custom(self):
        cfg = OpenBaoConfig(binary_path="/usr/local/bin/bao")
        assert cfg.binary_path == "/usr/local/bin/bao"

    # ── external_tls_verify ──────────────────────────────────────────────

    def test_external_tls_verify_string_path(self):
        cfg = OpenBaoConfig(external_tls_verify="/etc/ssl/ca.pem")
        assert cfg.external_tls_verify == "/etc/ssl/ca.pem"

    def test_external_tls_verify_false(self):
        cfg = OpenBaoConfig(external_tls_verify=False)
        assert cfg.external_tls_verify is False

    # ── approle defaults and bounds ──────────────────────────────────────

    def test_approle_secret_id_ttl_default(self):
        cfg = OpenBaoConfig()
        assert cfg.approle_secret_id_ttl_seconds == 600

    def test_approle_token_ttl_default(self):
        cfg = OpenBaoConfig()
        assert cfg.approle_token_ttl_seconds == 3600

    def test_approle_token_max_ttl_default(self):
        cfg = OpenBaoConfig()
        assert cfg.approle_token_max_ttl_seconds == 3600

    def test_approle_secret_id_num_uses_default(self):
        cfg = OpenBaoConfig()
        assert cfg.approle_secret_id_num_uses == 1

    def test_approle_token_num_uses_default(self):
        cfg = OpenBaoConfig()
        assert cfg.approle_token_num_uses == 128

    def test_approle_secret_id_ttl_minimum(self):
        cfg = OpenBaoConfig(approle_secret_id_ttl_seconds=30)
        assert cfg.approle_secret_id_ttl_seconds == 30

    def test_approle_token_ttl_maximum(self):
        cfg = OpenBaoConfig(approle_token_ttl_seconds=86400, approle_token_max_ttl_seconds=86400)
        assert cfg.approle_token_ttl_seconds == 86400

    def test_approle_secret_id_num_uses_maximum(self):
        cfg = OpenBaoConfig(approle_secret_id_num_uses=100)
        assert cfg.approle_secret_id_num_uses == 100

    def test_approle_token_num_uses_maximum(self):
        cfg = OpenBaoConfig(approle_token_num_uses=100000)
        assert cfg.approle_token_num_uses == 100000

    def test_approle_ttl_below_minimum_rejected(self):
        with pytest.raises(ValueError):
            OpenBaoConfig(approle_token_ttl_seconds=10)

    def test_approle_ttl_above_maximum_rejected(self):
        with pytest.raises(ValueError):
            OpenBaoConfig(approle_token_ttl_seconds=100000)

    def test_approle_uses_below_minimum_rejected(self):
        with pytest.raises(ValueError):
            OpenBaoConfig(approle_token_num_uses=0)

    def test_approle_uses_above_maximum_rejected(self):
        with pytest.raises(ValueError):
            OpenBaoConfig(approle_token_num_uses=200000)

    def test_approle_ttl_ordering_enforced(self):
        with pytest.raises(ValueError, match="TTL must not exceed"):
            OpenBaoConfig(approle_token_ttl_seconds=7200, approle_token_max_ttl_seconds=3600)

    def test_approle_ttl_equal_allowed(self):
        cfg = OpenBaoConfig(approle_token_ttl_seconds=3600, approle_token_max_ttl_seconds=3600)
        assert cfg.approle_token_ttl_seconds == 3600

    # ── approle_role_name ────────────────────────────────────────────────

    def test_approle_role_name_custom(self):
        cfg = OpenBaoConfig(approle_role_name="custom-role")
        assert cfg.approle_role_name == "custom-role"

    # ── local_image ──────────────────────────────────────────────────────

    def test_local_image_custom(self):
        cfg = OpenBaoConfig(local_image="docker.io/library/vault")
        assert cfg.local_image == "docker.io/library/vault"

    def test_local_image_digest_pin(self):
        cfg = OpenBaoConfig(local_image_digest_pin="sha256:deadbeef1234")
        assert cfg.local_image_digest_pin == "sha256:deadbeef1234"

    # ── local_container_runtime ──────────────────────────────────────────

    def test_local_container_runtime_custom(self):
        cfg = OpenBaoConfig(local_container_runtime="docker")
        assert cfg.local_container_runtime == "docker"

    # ── weekly_image_update ──────────────────────────────────────────────

    def test_weekly_image_update_scan_toggled(self):
        cfg = OpenBaoConfig(weekly_image_update_scan=False)
        assert cfg.weekly_image_update_scan is False

    def test_weekly_image_update_creates_manual_hold_toggled(self):
        cfg = OpenBaoConfig(weekly_image_update_creates_manual_hold=False)
        assert cfg.weekly_image_update_creates_manual_hold is False

    # ── kv_mount openbao validation ──────────────────────────────────────

    def test_kv_mount_rejects_reserved_sys(self):
        with pytest.raises(ValueError, match="system mounts cannot be delegated"):
            OpenBaoConfig(kv_mount="sys")

    def test_kv_mount_rejects_absolute(self):
        with pytest.raises(ValueError, match="canonical relative"):
            OpenBaoConfig(kv_mount="/secret")

    def test_kv_mount_rejects_traversal(self):
        with pytest.raises(ValueError, match="invalid or traversal"):
            OpenBaoConfig(kv_mount="secret/../other")

    # ── external mode interaction ────────────────────────────────────────

    def test_external_mode_with_url_and_token(self):
        cfg = OpenBaoConfig(
            mode="external",
            external_url="https://bao.example.com:8200",
            external_token="s.xyz-token",
        )
        assert cfg.mode == "external"
        assert cfg.external_url == "https://bao.example.com:8200"
        assert cfg.external_token == "s.xyz-token"

    # ── model_dump_json redaction ────────────────────────────────────────

    def test_model_dump_json_redacts_token(self):
        cfg = OpenBaoConfig(external_token="s.secret-token")
        json_str = cfg.model_dump_json()
        assert "s.secret-token" not in json_str
        assert "**REDACTED**" in json_str
