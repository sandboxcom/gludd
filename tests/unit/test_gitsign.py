from __future__ import annotations

import pytest

from general_ludd.secrets.gitsign import (
    GitsignConfig,
    _scoped_path,
    read_gitsign_config,
    write_gitsign_config,
)


class TestGitsignConfig:
    def test_default_values(self) -> None:
        cfg = GitsignConfig()
        assert cfg.fulcio_url == "https://fulcio.sigstore.dev"
        assert cfg.rekor_url == "https://rekor.sigstore.dev"
        assert cfg.oidc_issuer == "https://oauth2.sigstore.dev/auth"
        assert cfg.key_ref == ""
        assert cfg.enabled is False

    def test_custom_values(self) -> None:
        cfg = GitsignConfig(
            fulcio_url="https://custom.fulcio",
            rekor_url="https://custom.rekor",
            oidc_issuer="https://custom.issuer",
            key_ref="my-key",
            enabled=True,
        )
        assert cfg.fulcio_url == "https://custom.fulcio"
        assert cfg.rekor_url == "https://custom.rekor"
        assert cfg.oidc_issuer == "https://custom.issuer"
        assert cfg.key_ref == "my-key"
        assert cfg.enabled is True


class TestScopedPath:
    def test_valid_project_id(self) -> None:
        assert _scoped_path("gludd") == "projects/gludd/gitsign/config"

    def test_project_id_with_hyphens_and_underscores(self) -> None:
        assert _scoped_path("my-project_v2") == "projects/my-project_v2/gitsign/config"

    def test_project_id_with_numbers(self) -> None:
        assert _scoped_path("project123") == "projects/project123/gitsign/config"

    def test_invalid_project_id_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid project_id"):
            _scoped_path("bad project")

    def test_invalid_project_id_special_chars(self) -> None:
        with pytest.raises(ValueError, match="invalid project_id"):
            _scoped_path("proj/ect")

    def test_invalid_project_id_empty(self) -> None:
        with pytest.raises(ValueError, match="invalid project_id"):
            _scoped_path("")

    def test_invalid_project_id_dots(self) -> None:
        with pytest.raises(ValueError, match="invalid project_id"):
            _scoped_path("my.project")


class FakeSecretsManager:
    def __init__(self) -> None:
        self._store: dict[str, dict[str, object]] = {}

    def write_secret(self, path: str, data: dict[str, object]) -> None:
        self._store[path] = dict(data)

    def read_secret(self, path: str) -> dict[str, object] | None:
        return self._store.get(path)


class TestWriteGitsignConfig:
    def test_default_write(self) -> None:
        mgr = FakeSecretsManager()
        write_gitsign_config(mgr, "gludd")
        data = mgr.read_secret("projects/gludd/gitsign/config")
        assert data is not None
        assert data["fulcio_url"] == "https://fulcio.sigstore.dev"
        assert data["enabled"] is True

    def test_custom_write(self) -> None:
        mgr = FakeSecretsManager()
        write_gitsign_config(
            mgr, "proj", fulcio_url="https://f", rekor_url="https://r",
            oidc_issuer="https://o", key_ref="k", enabled=False,
        )
        data = mgr.read_secret("projects/proj/gitsign/config")
        assert data is not None
        assert data["fulcio_url"] == "https://f"
        assert data["rekor_url"] == "https://r"
        assert data["oidc_issuer"] == "https://o"
        assert data["key_ref"] == "k"
        assert data["enabled"] is False


class TestReadGitsignConfig:
    def test_read_existing(self) -> None:
        mgr = FakeSecretsManager()
        write_gitsign_config(mgr, "gludd", enabled=True, key_ref="sig")
        cfg = read_gitsign_config(mgr, "gludd")
        assert cfg is not None
        assert cfg.enabled is True
        assert cfg.key_ref == "sig"

    def test_read_nonexistent_returns_none(self) -> None:
        mgr = FakeSecretsManager()
        cfg = read_gitsign_config(mgr, "no-project")
        assert cfg is None

    def test_read_defaults_missing_fields(self) -> None:
        mgr = FakeSecretsManager()
        mgr.write_secret("projects/p/gitsign/config", {"enabled": True})
        cfg = read_gitsign_config(mgr, "p")
        assert cfg is not None
        assert cfg.fulcio_url == "https://fulcio.sigstore.dev"
        assert cfg.key_ref == ""
        assert cfg.enabled is True

    def test_write_then_read_roundtrip(self) -> None:
        mgr = FakeSecretsManager()
        write_gitsign_config(
            mgr, "roundtrip",
            fulcio_url="https://f.example", rekor_url="https://r.example",
            oidc_issuer="https://o.example", key_ref="round-key", enabled=True,
        )
        cfg = read_gitsign_config(mgr, "roundtrip")
        assert cfg is not None
        assert cfg.fulcio_url == "https://f.example"
        assert cfg.rekor_url == "https://r.example"
        assert cfg.oidc_issuer == "https://o.example"
        assert cfg.key_ref == "round-key"
        assert cfg.enabled is True
