from __future__ import annotations

import subprocess
from unittest import mock

from general_ludd.searx.install import (
    _expand_user,
    ensure_searx_initialized,
    ensure_searx_installed,
)


class TestEnsureSearxInstalled:
    def test_already_available(self) -> None:
        with mock.patch("importlib.util.find_spec", return_value=mock.MagicMock()):
            assert ensure_searx_installed() is True

    def test_not_available_installs_successfully(self) -> None:
        mock_run = mock.MagicMock()
        mock_run.return_value.returncode = 0
        with mock.patch(
            "importlib.util.find_spec",
            side_effect=[None, mock.MagicMock()],
        ), mock.patch("subprocess.run", mock_run):
            result = ensure_searx_installed()
        assert result is True
        mock_run.assert_called_once()

    def test_not_available_install_fails_nonzero(self) -> None:
        mock_run = mock.MagicMock()
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "install failed"
        with mock.patch(
            "importlib.util.find_spec", side_effect=[None, None]
        ), mock.patch("subprocess.run", mock_run):
            result = ensure_searx_installed()
        assert result is False

    def test_not_available_install_installs_but_not_importable(self) -> None:
        mock_run = mock.MagicMock()
        mock_run.return_value.returncode = 0
        with mock.patch(
            "importlib.util.find_spec", side_effect=[None, None]
        ), mock.patch("subprocess.run", mock_run):
            result = ensure_searx_installed()
        assert result is False

    def test_subprocess_oserror_returns_false(self) -> None:
        with mock.patch("importlib.util.find_spec", return_value=None), mock.patch(
            "subprocess.run", side_effect=OSError("uv not found")
        ):
            result = ensure_searx_installed()
        assert result is False

    def test_subprocess_timeout_returns_false(self) -> None:
        with mock.patch("importlib.util.find_spec", return_value=None), mock.patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="uv", timeout=300)
        ):
            result = ensure_searx_installed()
        assert result is False

    def test_import_error_is_treated_as_not_installed(self) -> None:
        mock_run = mock.MagicMock()
        mock_run.return_value.returncode = 0
        with mock.patch(
            "importlib.util.find_spec",
            side_effect=ImportError("no module named searxng"),
        ), mock.patch("subprocess.run", mock_run):
            result = ensure_searx_installed()
        mock_run.assert_called_once()
        assert result is False


class TestEnsureSearxInitialized:
    def test_creates_config_and_returns_true(self, tmp_path) -> None:
        config_path = tmp_path / "settings.yml"
        with mock.patch(
            "general_ludd.searx.install._expand_user", return_value=tmp_path
        ), mock.patch(
            "general_ludd.searx.config.SearXConfig.generate", return_value=str(config_path)
        ):
            config_path.touch()
            result = ensure_searx_initialized(base_dir=str(tmp_path))
        assert result is True

    def test_config_generate_failure_returns_false(self, tmp_path) -> None:
        with mock.patch(
            "general_ludd.searx.install._expand_user", return_value=tmp_path
        ), mock.patch(
            "general_ludd.searx.config.SearXConfig.generate",
            side_effect=Exception("boom"),
        ):
            result = ensure_searx_initialized(base_dir=str(tmp_path))
        assert result is False

    def test_config_not_written_returns_false(self, tmp_path) -> None:
        with mock.patch(
            "general_ludd.searx.install._expand_user", return_value=tmp_path
        ), mock.patch(
            "general_ludd.searx.config.SearXConfig.generate",
            return_value="/tmp/nonexistent/settings.yml",
        ):
            result = ensure_searx_initialized(base_dir=str(tmp_path))
        assert result is False


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
