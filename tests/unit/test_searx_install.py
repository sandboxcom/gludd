"""Tests for safe discovery and initialization of upstream SearXNG."""

from __future__ import annotations

from unittest import mock

from general_ludd.searx.install import (
    _expand_user,
    ensure_searx_initialized,
    ensure_searx_installed,
)


class TestEnsureSearxInstalled:
    def test_official_import_surface_is_available(self) -> None:
        with mock.patch("importlib.util.find_spec", return_value=mock.MagicMock()) as find:
            assert ensure_searx_installed() is True
        find.assert_called_once_with("searx.webapp")

    def test_missing_package_fails_closed_without_runtime_install(self) -> None:
        with mock.patch("importlib.util.find_spec", return_value=None) as find:
            assert ensure_searx_installed() is False
        find.assert_called_once_with("searx.webapp")

    def test_import_error_is_treated_as_unavailable(self) -> None:
        with mock.patch(
            "importlib.util.find_spec",
            side_effect=ImportError("no module named searx"),
        ):
            assert ensure_searx_installed() is False

    def test_ambiguous_searxng_import_name_is_never_probed(self) -> None:
        observed: list[str] = []

        def find(name: str) -> None:
            observed.append(name)
            return None

        with mock.patch("importlib.util.find_spec", side_effect=find):
            ensure_searx_installed()
        assert observed == ["searx.webapp"]


class TestEnsureSearxInitialized:
    def test_creates_config_and_returns_true(self, tmp_path) -> None:
        config_path = tmp_path / "settings.yml"
        with mock.patch(
            "general_ludd.searx.config.SearXConfig.generate", return_value=str(config_path)
        ):
            config_path.touch()
            result = ensure_searx_initialized(base_dir=str(tmp_path))
        assert result is True

    def test_config_generate_failure_returns_false(self, tmp_path) -> None:
        with mock.patch(
            "general_ludd.searx.config.SearXConfig.generate",
            side_effect=OSError("boom"),
        ):
            result = ensure_searx_initialized(base_dir=str(tmp_path))
        assert result is False

    def test_config_not_written_returns_false(self, tmp_path) -> None:
        with mock.patch(
            "general_ludd.searx.config.SearXConfig.generate",
            return_value="/tmp/nonexistent/settings.yml",
        ):
            result = ensure_searx_initialized(base_dir=str(tmp_path))
        assert result is False

    def test_default_initialization_uses_namespace(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        assert ensure_searx_initialized(namespace="test-project") is True
        assert (tmp_path / ".gludd" / "searx" / "test-project" / "settings.yml").is_file()


class TestExpandUser:
    def test_expands_tilde(self) -> None:
        result = _expand_user("~/my-path")
        assert "~" not in str(result)
        assert str(result).endswith("my-path")

    def test_resolves_absolute(self) -> None:
        import pathlib

        expected = pathlib.Path("/tmp/foo/bar").resolve()
        result = _expand_user("/tmp/foo/bar")
        assert result == expected
