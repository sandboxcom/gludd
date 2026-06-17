"""Unit tests for CLI URL/path validation helpers (batch-5 security fixes).

Covers:
  - _validate_daemon_url  (MED: scheme + creds + host checks)
  - _validate_webhook_url (HIGH: hooks-register SSRF guard)
  - _validate_daemon_path (LOW: workspace_path confinement, via existing helper)
  - Integration: main() calls _validate_daemon_url before dispatch
  - Integration: _cmd_hooks_register calls _validate_webhook_url before POST
  - Integration: _cmd_project_add calls _validate_daemon_path + expands workspace_path
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.cli import (
    _validate_daemon_url,
    _validate_webhook_url,
)

# ---------------------------------------------------------------------------
# _validate_daemon_url
# ---------------------------------------------------------------------------


class TestValidateDaemonUrl:
    def test_valid_http_localhost(self):
        """Default URL must pass without raising SystemExit."""
        _validate_daemon_url("http://localhost:8000")  # no exception

    def test_valid_http_ip(self):
        _validate_daemon_url("http://192.168.1.10:8000")

    def test_valid_https(self):
        _validate_daemon_url("https://my-daemon.example.com:8443")

    def test_rejects_ftp_scheme(self):
        with pytest.raises(SystemExit):
            _validate_daemon_url("ftp://example.com")

    def test_rejects_file_scheme(self):
        with pytest.raises(SystemExit):
            _validate_daemon_url("file:///etc/passwd")

    def test_rejects_empty_string(self):
        with pytest.raises(SystemExit):
            _validate_daemon_url("")

    def test_rejects_embedded_credentials(self):
        with pytest.raises(SystemExit):
            _validate_daemon_url("http://user:pass@example.com:8000")  # pragma: allowlist secret

    def test_rejects_user_only_credentials(self):
        with pytest.raises(SystemExit):
            _validate_daemon_url("http://user@example.com:8000")

    def test_rejects_no_host(self):
        with pytest.raises(SystemExit):
            _validate_daemon_url("http://")

    def test_error_message_mentions_scheme(self, capsys):
        with pytest.raises(SystemExit):
            _validate_daemon_url("ftp://bad.example.com")
        captured = capsys.readouterr()
        assert "http" in captured.err.lower() or "scheme" in captured.err.lower()

    def test_error_message_mentions_credentials(self, capsys):
        with pytest.raises(SystemExit):
            _validate_daemon_url("http://user:pass@example.com")  # pragma: allowlist secret
        captured = capsys.readouterr()
        assert "credential" in captured.err.lower() or "user" in captured.err.lower()


# ---------------------------------------------------------------------------
# _validate_webhook_url
# ---------------------------------------------------------------------------


class TestValidateWebhookUrl:
    # --- valid cases --------------------------------------------------------

    def test_valid_https_public(self):
        """Public https webhook must pass."""
        _validate_webhook_url("https://hooks.example.com/receiver")

    def test_valid_http_public(self):
        """Plain-http public webhook must pass (some receivers use http)."""
        _validate_webhook_url("http://hooks.example.com/receiver")

    # --- scheme rejection ---------------------------------------------------

    def test_rejects_ftp(self):
        with pytest.raises(SystemExit):
            _validate_webhook_url("ftp://hooks.example.com/recv")

    def test_rejects_file(self):
        with pytest.raises(SystemExit):
            _validate_webhook_url("file:///etc/passwd")

    def test_rejects_no_scheme(self):
        with pytest.raises(SystemExit):
            _validate_webhook_url("example.com/path")

    # --- loopback / RFC-1918 rejection --------------------------------------

    def test_rejects_localhost(self):
        with pytest.raises(SystemExit):
            _validate_webhook_url("http://localhost/hook")

    def test_rejects_127_0_0_1(self):
        with pytest.raises(SystemExit):
            _validate_webhook_url("http://127.0.0.1:9000/hook")

    def test_rejects_rfc1918_192_168(self):
        with pytest.raises(SystemExit):
            _validate_webhook_url("http://192.168.1.1/hook")

    def test_rejects_rfc1918_10_x(self):
        with pytest.raises(SystemExit):
            _validate_webhook_url("https://10.0.0.1/hook")

    def test_rejects_rfc1918_172_16(self):
        with pytest.raises(SystemExit):
            _validate_webhook_url("https://172.16.0.1/hook")

    def test_rejects_link_local(self):
        with pytest.raises(SystemExit):
            _validate_webhook_url("http://169.254.1.1/hook")

    def test_rejects_aws_metadata(self):
        with pytest.raises(SystemExit):
            _validate_webhook_url("http://169.254.169.254/latest/meta-data/")

    def test_rejects_ipv6_loopback(self):
        with pytest.raises(SystemExit):
            _validate_webhook_url("http://[::1]/hook")

    # --- empty / malformed --------------------------------------------------

    def test_rejects_empty(self):
        with pytest.raises(SystemExit):
            _validate_webhook_url("")

    def test_rejects_no_host(self):
        with pytest.raises(SystemExit):
            _validate_webhook_url("https://")

    # --- error message quality ----------------------------------------------

    def test_error_mentions_loopback_or_private(self, capsys):
        with pytest.raises(SystemExit):
            _validate_webhook_url("http://localhost/hook")
        captured = capsys.readouterr()
        assert any(
            kw in captured.err.lower()
            for kw in ("loopback", "private", "metadata", "webhook")
        )


# ---------------------------------------------------------------------------
# _validate_daemon_path (existing helper) — unit tests
# ---------------------------------------------------------------------------


class TestValidateDaemonPath:
    """Tests for the existing _validate_daemon_path(value, *, name) helper.

    It raises ValueError (not SystemExit) and forbids NUL bytes,
    newlines, and shell metacharacters.
    """

    def test_valid_absolute_path_accepted(self):
        from general_ludd.cli import _validate_daemon_path

        result = _validate_daemon_path("/home/user/projects", name="workspace-path")
        assert result == "/home/user/projects"

    def test_empty_string_accepted(self):
        from general_ludd.cli import _validate_daemon_path

        result = _validate_daemon_path("", name="workspace-path")
        assert result == ""

    def test_nul_byte_raises(self):
        from general_ludd.cli import _validate_daemon_path

        with pytest.raises(ValueError, match="NUL"):
            _validate_daemon_path("/some/path\x00evil", name="workspace-path")

    def test_newline_raises(self):
        from general_ludd.cli import _validate_daemon_path

        with pytest.raises(ValueError, match="newline"):
            _validate_daemon_path("/some/path\nevil", name="workspace-path")

    def test_semicolon_raises(self):
        from general_ludd.cli import _validate_daemon_path

        with pytest.raises(ValueError, match="metacharacter"):
            _validate_daemon_path("/some/path;rm -rf /", name="workspace-path")

    def test_pipe_raises(self):
        from general_ludd.cli import _validate_daemon_path

        with pytest.raises(ValueError, match="metacharacter"):
            _validate_daemon_path("/some/path|cat /etc/passwd", name="workspace-path")


# ---------------------------------------------------------------------------
# Integration: main() validates --daemon-url before dispatch
# ---------------------------------------------------------------------------


class TestMainValidatesDaemonUrl:
    def test_invalid_scheme_exits_before_dispatch(self, capsys):
        """main() must reject ftp:// before calling any _cmd_* function."""
        with (
            patch("sys.argv", ["gludd", "health", "--daemon-url", "ftp://bad.example.com"]),
            patch("general_ludd.cli._cmd_health") as mock_cmd,
        ):
            with pytest.raises(SystemExit):
                from general_ludd.cli import main

                main()
            mock_cmd.assert_not_called()

    def test_credentials_in_url_exits_before_dispatch(self):
        with (
            patch("sys.argv", ["gludd", "list", "--daemon-url", "http://u:p@example.com"]),  # pragma: allowlist secret
            patch("general_ludd.cli._cmd_list"),
            pytest.raises(SystemExit),
        ):
            from general_ludd.cli import main

            main()

    def test_valid_url_reaches_dispatch(self):
        with patch("sys.argv", ["gludd", "version"]):
            # version has no --daemon-url; main() must not crash
            from general_ludd.cli import main

            main()  # no exception


# ---------------------------------------------------------------------------
# Integration: _cmd_hooks_register validates --handler before POST
# ---------------------------------------------------------------------------


class TestHooksRegisterSsrf:
    def _make_args(self, handler: str) -> object:
        import argparse

        return argparse.Namespace(
            event="task.complete",
            handler=handler,
            daemon_url="http://localhost:8000",
        )

    def test_valid_handler_calls_httpx(self):
        from general_ludd.cli import _cmd_hooks_register

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"hook_id": "h-123"}
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            _cmd_hooks_register(self._make_args("https://hooks.example.com/recv"))
            mock_post.assert_called_once()

    def test_loopback_handler_blocked_before_post(self):
        from general_ludd.cli import _cmd_hooks_register

        with patch("httpx.post") as mock_post:
            with pytest.raises(SystemExit):
                _cmd_hooks_register(self._make_args("http://127.0.0.1:9999/hook"))
            mock_post.assert_not_called()

    def test_localhost_handler_blocked(self):
        from general_ludd.cli import _cmd_hooks_register

        with patch("httpx.post") as mock_post:
            with pytest.raises(SystemExit):
                _cmd_hooks_register(self._make_args("http://localhost/hook"))
            mock_post.assert_not_called()

    def test_metadata_handler_blocked(self):
        from general_ludd.cli import _cmd_hooks_register

        with patch("httpx.post") as mock_post:
            with pytest.raises(SystemExit):
                _cmd_hooks_register(self._make_args("http://169.254.169.254/"))
            mock_post.assert_not_called()

    def test_rfc1918_handler_blocked(self):
        from general_ludd.cli import _cmd_hooks_register

        with patch("httpx.post") as mock_post:
            with pytest.raises(SystemExit):
                _cmd_hooks_register(self._make_args("http://10.0.0.1/hook"))
            mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# Integration: _cmd_project_add normalises workspace_path
# ---------------------------------------------------------------------------


class TestProjectAddWorkspacePath:
    def _make_args(self, workspace_path: str) -> object:
        import argparse

        return argparse.Namespace(
            name="my-project",
            weight=30.0,
            description="",
            repo_url="",
            workspace_path=workspace_path,
            dispatch_mode="active",
            daemon_url="http://localhost:8000",
        )

    def test_tilde_path_is_expanded_in_post(self):
        import json

        from general_ludd.cli import _cmd_project_add

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "project_id": "p-1",
            "name": "my-project",
            "weight": 30.0,
        }
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            _cmd_project_add(self._make_args("~/repos/myrepo"))
            call_kwargs = mock_post.call_args
            body = json.loads(call_kwargs.kwargs.get("content", call_kwargs[1].get("content", b"")))
            assert "~" not in body["workspace_path"]
            assert os.path.isabs(body["workspace_path"])

    def test_empty_workspace_passes_through(self):
        from general_ludd.cli import _cmd_project_add

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "project_id": "p-2",
            "name": "my-project",
            "weight": 30.0,
        }
        with patch("httpx.post", return_value=mock_resp):
            # Should not raise
            _cmd_project_add(self._make_args(""))

    def test_nul_byte_in_workspace_is_rejected(self):
        from general_ludd.cli import _cmd_project_add

        with patch("httpx.post") as mock_post:
            with pytest.raises(SystemExit):
                _cmd_project_add(self._make_args("/some/path\x00evil"))
            mock_post.assert_not_called()
