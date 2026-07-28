"""Unit tests for FirecrackerBackend — P5 real-firecracker VM sandbox backend.

Covers: FirecrackerBackend (available, apply, verify, release),
FirecrackerUnixHTTPConnection, _wait_for_socket, _firecracker_put,
_spawn_firecracker.
"""

from __future__ import annotations

import http.client
import os
import socket
import subprocess
import time
from pathlib import Path
from unittest import mock

import pytest

from general_ludd.security.permissions import PermissionSpec
from general_ludd.security.sandboxes import (
    Finding,
    SandboxHandle,
    SandboxTarget,
)
from general_ludd.security.sandboxes.vm.firecracker_backend import (
    FirecrackerBackend,
    FirecrackerUnixHTTPConnection,
    _firecracker_put,
    _spawn_firecracker,
    _wait_for_socket,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_spec() -> PermissionSpec:
    return PermissionSpec(agent_type="test-agent")


@pytest.fixture()
def sample_target() -> SandboxTarget:
    return SandboxTarget(pid=99999)


@pytest.fixture()
def temp_socket_path(tmp_path: Path) -> str:
    return str(tmp_path / "test.sock")


# ---------------------------------------------------------------------------
# FirecrackerUnixHTTPConnection
# ---------------------------------------------------------------------------


class TestFirecrackerUnixHTTPConnection:
    def test_init_stores_sock_path(self, temp_socket_path: str) -> None:
        conn = FirecrackerUnixHTTPConnection(temp_socket_path)
        assert conn._sock_path == temp_socket_path
        assert conn.timeout == 5.0

    def test_init_custom_timeout(self, temp_socket_path: str) -> None:
        conn = FirecrackerUnixHTTPConnection(temp_socket_path, timeout=2.0)
        assert conn._sock_path == temp_socket_path
        assert conn.timeout == 2.0

    def test_connect_creates_unix_socket_and_connects(
        self,
        temp_socket_path: str,
    ) -> None:
        conn = FirecrackerUnixHTTPConnection(temp_socket_path)
        with mock.patch("socket.socket") as mock_socket_cls:
            mock_sock = mock_socket_cls.return_value
            conn.connect()
            mock_socket_cls.assert_called_once_with(
                socket.AF_UNIX,
                socket.SOCK_STREAM,
            )
            mock_sock.settimeout.assert_called_once_with(conn.timeout)
            mock_sock.connect.assert_called_once_with(temp_socket_path)

    def test_is_http_connection_subclass(self) -> None:
        conn = FirecrackerUnixHTTPConnection("/tmp/any")
        assert isinstance(conn, http.client.HTTPConnection)


# ---------------------------------------------------------------------------
# _wait_for_socket
# ---------------------------------------------------------------------------


class TestWaitForSocket:
    def test_returns_true_when_socket_connectable(
        self,
        temp_socket_path: str,
    ) -> None:
        with mock.patch("os.path.exists", return_value=True), mock.patch("socket.socket") as mock_socket_cls:
            result = _wait_for_socket(temp_socket_path, timeout=1.0)
        assert result is True
        mock_socket_cls.assert_called_once_with(
            socket.AF_UNIX,
            socket.SOCK_STREAM,
        )

    def test_returns_false_when_timeout_elapses(
        self,
        temp_socket_path: str,
    ) -> None:
        with (
            mock.patch("os.path.exists", return_value=False),
            mock.patch("time.sleep", return_value=None),
            mock.patch("time.monotonic", side_effect=[0.0, 10.0]),
        ):
            result = _wait_for_socket(temp_socket_path, timeout=1.0)
        assert result is False

    def test_polls_until_socket_appears(
        self,
        temp_socket_path: str,
    ) -> None:
        exists_calls = [False, False, True]

        def fake_exists(_: str) -> bool:
            return exists_calls.pop(0) if exists_calls else True

        connect_success = [False, True]
        fake_socks: list[mock.MagicMock] = []

        def fake_socket(family: int, sock_type: int) -> mock.MagicMock:
            s = mock.MagicMock()
            if len(fake_socks) < len(connect_success):
                can_connect = connect_success.pop(0)
            else:
                can_connect = True
            if not can_connect:
                s.connect.side_effect = OSError()
            fake_socks.append(s)
            return s

        with (
            mock.patch("os.path.exists", side_effect=fake_exists),
            mock.patch("socket.socket", side_effect=fake_socket),
            mock.patch("time.sleep", return_value=None),
            mock.patch("time.monotonic", return_value=0.0),
        ):
            result = _wait_for_socket(temp_socket_path, timeout=5.0)
        assert result is True

    def test_connects_on_second_poll_after_oserror(
        self,
        temp_socket_path: str,
    ) -> None:
        connect_errors = [OSError(), None]
        sock_calls: list[mock.MagicMock] = []

        def fake_socket(family: int, sock_type: int) -> mock.MagicMock:
            s = mock.MagicMock()
            err = connect_errors.pop(0) if connect_errors else None
            if err is not None:
                s.connect.side_effect = err
                s.close.side_effect = OSError()
            else:
                s.connect.return_value = None
            sock_calls.append(s)
            return s

        with (
            mock.patch("os.path.exists", return_value=True),
            mock.patch("socket.socket", side_effect=fake_socket),
            mock.patch("time.sleep", return_value=None),
            mock.patch("time.monotonic", return_value=0.0),
        ):
            result = _wait_for_socket(temp_socket_path, timeout=5.0)
        assert result is True
        assert len(sock_calls) == 2
        sock_calls[1].connect.assert_called_once_with(temp_socket_path)


# ---------------------------------------------------------------------------
# _firecracker_put
# ---------------------------------------------------------------------------


class TestFirecrackerPut:
    def test_successful_put_with_json_response(
        self,
        temp_socket_path: str,
    ) -> None:
        with mock.patch(
            "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerUnixHTTPConnection",
        ) as mock_conn_cls:
            mock_conn = mock_conn_cls.return_value
            mock_conn.getresponse.return_value.status = 200
            mock_conn.getresponse.return_value.read.return_value = b'{"key": "value"}'
            result = _firecracker_put(temp_socket_path, "/machine-config", {"vcpu_count": 1})
        assert result == {"key": "value"}
        mock_conn.request.assert_called_once_with(
            "PUT",
            "/machine-config",
            body=mock.ANY,
            headers={"Content-Type": "application/json"},
        )
        mock_conn.close.assert_called_once()

    def test_204_no_content_returns_empty_dict(
        self,
        temp_socket_path: str,
    ) -> None:
        with mock.patch(
            "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerUnixHTTPConnection",
        ) as mock_conn_cls:
            mock_conn = mock_conn_cls.return_value
            mock_conn.getresponse.return_value.status = 204
            mock_conn.getresponse.return_value.read.return_value = b""
            result = _firecracker_put(temp_socket_path, "/actions", {"action_type": "InstanceStart"})
        assert result == {}

    def test_non_2xx_status_raises_runtime_error(
        self,
        temp_socket_path: str,
    ) -> None:
        with mock.patch(
            "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerUnixHTTPConnection",
        ) as mock_conn_cls:
            mock_conn = mock_conn_cls.return_value
            mock_conn.getresponse.return_value.status = 400
            mock_conn.getresponse.return_value.read.return_value = b"Bad Request"
            with pytest.raises(RuntimeError, match=r"Firecracker PUT .* -> HTTP 400"):
                _firecracker_put(temp_socket_path, "/bad-path", {})

    def test_unix_socket_connection_error_propagates(
        self,
        temp_socket_path: str,
    ) -> None:
        with mock.patch(
            "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerUnixHTTPConnection",
        ) as mock_conn_cls:
            mock_conn_cls.side_effect = OSError("Connection refused")
            with pytest.raises(OSError, match="Connection refused"):
                _firecracker_put(temp_socket_path, "/machine-config", {})

    def test_close_called_even_on_request_error(
        self,
        temp_socket_path: str,
    ) -> None:
        with mock.patch(
            "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerUnixHTTPConnection",
        ) as mock_conn_cls:
            mock_conn = mock_conn_cls.return_value
            mock_conn.request.side_effect = ConnectionError("broken pipe")
            with pytest.raises(ConnectionError):
                _firecracker_put(temp_socket_path, "/test", {})
            mock_conn.close.assert_called_once()

    def test_invalid_json_response_returns_empty_dict(
        self,
        temp_socket_path: str,
    ) -> None:
        with mock.patch(
            "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerUnixHTTPConnection",
        ) as mock_conn_cls:
            mock_conn = mock_conn_cls.return_value
            mock_conn.getresponse.return_value.status = 200
            mock_conn.getresponse.return_value.read.return_value = b"not json at all"
            result = _firecracker_put(temp_socket_path, "/test", {})
        assert result == {}

    def test_json_array_response_returns_empty_dict(
        self,
        temp_socket_path: str,
    ) -> None:
        with mock.patch(
            "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerUnixHTTPConnection",
        ) as mock_conn_cls:
            mock_conn = mock_conn_cls.return_value
            mock_conn.getresponse.return_value.status = 200
            mock_conn.getresponse.return_value.read.return_value = b"[1, 2, 3]"
            result = _firecracker_put(temp_socket_path, "/test", {})
        assert result == {}

    def test_utf8_decode_error_on_error_response_uses_replacement(
        self,
        temp_socket_path: str,
    ) -> None:
        with mock.patch(
            "general_ludd.security.sandboxes.vm.firecracker_backend.FirecrackerUnixHTTPConnection",
        ) as mock_conn_cls:
            mock_conn = mock_conn_cls.return_value
            mock_conn.getresponse.return_value.status = 500
            mock_conn.getresponse.return_value.read.return_value = b"\xff\xfe\xfd"
            with pytest.raises(RuntimeError, match=r"Firecracker PUT .* -> HTTP 500"):
                _firecracker_put(temp_socket_path, "/test", {})


# ---------------------------------------------------------------------------
# _spawn_firecracker (internal)
# ---------------------------------------------------------------------------


class TestSpawnFirecracker:
    def test_successful_spawn_and_rest_boot(
        self,
        sample_spec: PermissionSpec,
        sample_target: SandboxTarget,
    ) -> None:
        fake_popen = mock.MagicMock(spec=subprocess.Popen)
        fake_popen.pid = 99999
        fake_popen.poll.return_value = None

        with (
            mock.patch("subprocess.Popen", return_value=fake_popen),
            mock.patch(
                "general_ludd.security.sandboxes.vm.firecracker_backend._wait_for_socket",
                return_value=True,
            ),
            mock.patch(
                "general_ludd.security.sandboxes.vm.firecracker_backend._firecracker_put",
                return_value={},
            ) as mock_put,
        ):
            handle = _spawn_firecracker(sample_spec, sample_target)

        assert handle.backend == "firecracker"
        assert handle.applied is True
        assert handle.extra.get("pid") == 99999
        assert handle.extra.get("popen") is fake_popen
        assert "sandbox_id" in handle.extra
        assert "api_sock" in handle.extra
        assert "vsock_uds" in handle.extra
        assert "started_at" in handle.extra
        assert mock_put.call_count == 5

    def test_popen_oserror_returns_fail_open_handle(
        self,
        sample_spec: PermissionSpec,
        sample_target: SandboxTarget,
    ) -> None:
        with mock.patch(
            "subprocess.Popen",
            side_effect=OSError("firecracker not found"),
        ):
            handle = _spawn_firecracker(sample_spec, sample_target)

        assert handle.backend == "firecracker"
        assert handle.applied is False
        assert "firecracker spawn failed" in str(handle.extra.get("reason"))
        assert "firecracker not found" in str(handle.extra.get("reason"))

    def test_socket_timeout_returns_fail_open_handle(
        self,
        sample_spec: PermissionSpec,
        sample_target: SandboxTarget,
    ) -> None:
        fake_popen = mock.MagicMock(spec=subprocess.Popen)

        with (
            mock.patch("subprocess.Popen", return_value=fake_popen),
            mock.patch(
                "general_ludd.security.sandboxes.vm.firecracker_backend._wait_for_socket",
                return_value=False,
            ),
        ):
            handle = _spawn_firecracker(sample_spec, sample_target)

        assert handle.applied is False
        assert "API socket" in str(handle.extra.get("reason"))
        fake_popen.kill.assert_called_once()

    def test_rest_configuration_failure_terminates_and_returns_fail_open(
        self,
        sample_spec: PermissionSpec,
        sample_target: SandboxTarget,
    ) -> None:
        fake_popen = mock.MagicMock(spec=subprocess.Popen)
        fake_popen.pid = 11111

        with (
            mock.patch("subprocess.Popen", return_value=fake_popen),
            mock.patch(
                "general_ludd.security.sandboxes.vm.firecracker_backend._wait_for_socket",
                return_value=True,
            ),
            mock.patch(
                "general_ludd.security.sandboxes.vm.firecracker_backend._firecracker_put",
                side_effect=RuntimeError("REST config error"),
            ),
        ):
            handle = _spawn_firecracker(sample_spec, sample_target)

        assert handle.applied is False
        assert "REST configuration failed" in str(handle.extra.get("reason"))
        fake_popen.terminate.assert_called_once()
        fake_popen.wait.assert_called_once()

    def test_rest_failure_terminate_grace_expired_kills(
        self,
        sample_spec: PermissionSpec,
        sample_target: SandboxTarget,
    ) -> None:
        fake_popen = mock.MagicMock(spec=subprocess.Popen)
        fake_popen.wait.side_effect = subprocess.TimeoutExpired(cmd="firecracker", timeout=2.0)

        with (
            mock.patch("subprocess.Popen", return_value=fake_popen),
            mock.patch(
                "general_ludd.security.sandboxes.vm.firecracker_backend._wait_for_socket",
                return_value=True,
            ),
            mock.patch(
                "general_ludd.security.sandboxes.vm.firecracker_backend._firecracker_put",
                side_effect=RuntimeError("REST config error"),
            ),
        ):
            handle = _spawn_firecracker(sample_spec, sample_target)

        assert handle.applied is False
        fake_popen.kill.assert_called()

    def test_spawn_argv_includes_api_sock_and_no_file(
        self,
        sample_spec: PermissionSpec,
        sample_target: SandboxTarget,
    ) -> None:
        fake_popen = mock.MagicMock(spec=subprocess.Popen)
        fake_popen.pid = 77777
        mock_popen = mock.MagicMock(return_value=fake_popen)

        with (
            mock.patch("subprocess.Popen", mock_popen),
            mock.patch(
                "general_ludd.security.sandboxes.vm.firecracker_backend._wait_for_socket",
                return_value=True,
            ),
            mock.patch(
                "general_ludd.security.sandboxes.vm.firecracker_backend._firecracker_put",
                return_value={},
            ),
        ):
            _spawn_firecracker(sample_spec, sample_target)

        call_args: list[str] = mock_popen.call_args[0][0]  # type: ignore[index]
        assert call_args[0] == "firecracker"
        assert any(arg.startswith("--api-sock=") for arg in call_args)
        assert "--no-file" in call_args
        assert "--level=Info" in call_args


# ---------------------------------------------------------------------------
# FirecrackerBackend.available
# ---------------------------------------------------------------------------


class TestFirecrackerBackendAvailable:
    def test_available_when_kvm_and_binary_present(self) -> None:
        with (
            mock.patch("os.path.exists", return_value=True),
            mock.patch("os.access", return_value=True),
            mock.patch("shutil.which", return_value="/usr/bin/firecracker"),
        ):
            assert FirecrackerBackend.available() is True

    def test_unavailable_when_kvm_missing(self) -> None:
        with (
            mock.patch("os.path.exists", return_value=False),
            mock.patch("shutil.which", return_value="/usr/bin/firecracker"),
        ):
            assert FirecrackerBackend.available() is False

    def test_unavailable_when_kvm_not_accessible(self) -> None:
        with (
            mock.patch("os.path.exists", return_value=True),
            mock.patch("os.access", return_value=False),
            mock.patch("shutil.which", return_value="/usr/bin/firecracker"),
        ):
            assert FirecrackerBackend.available() is False

    def test_unavailable_when_firecracker_binary_missing(self) -> None:
        with (
            mock.patch("os.path.exists", return_value=True),
            mock.patch("os.access", return_value=True),
            mock.patch("shutil.which", return_value=None),
        ):
            assert FirecrackerBackend.available() is False

    def test_unavailable_when_both_missing(self) -> None:
        with mock.patch("os.path.exists", return_value=False), mock.patch("shutil.which", return_value=None):
            assert FirecrackerBackend.available() is False


# ---------------------------------------------------------------------------
# FirecrackerBackend.apply
# ---------------------------------------------------------------------------


class TestFirecrackerBackendApply:
    def test_returns_fail_open_when_backend_not_available(
        self,
        sample_spec: PermissionSpec,
        sample_target: SandboxTarget,
    ) -> None:
        with mock.patch.object(FirecrackerBackend, "available", return_value=False):
            handle = FirecrackerBackend.apply(sample_spec, sample_target)
        assert handle.backend == "firecracker"
        assert handle.applied is False
        assert "reason" in handle.extra
        assert "UNSANDBOXED" not in str(handle.extra.get("reason"))

    def test_delegates_to_spawn_when_available(
        self,
        sample_spec: PermissionSpec,
        sample_target: SandboxTarget,
    ) -> None:
        fake_popen = mock.MagicMock(spec=subprocess.Popen)
        fake_popen.pid = 12345
        fake_popen.poll.return_value = None

        with (
            mock.patch.object(FirecrackerBackend, "available", return_value=True),
            mock.patch("subprocess.Popen", return_value=fake_popen),
            mock.patch(
                "general_ludd.security.sandboxes.vm.firecracker_backend._wait_for_socket",
                return_value=True,
            ),
            mock.patch(
                "general_ludd.security.sandboxes.vm.firecracker_backend._firecracker_put",
                return_value={},
            ),
        ):
            handle = FirecrackerBackend.apply(sample_spec, sample_target)
        assert handle.applied is True
        assert handle.token == "gludd-test-agent"

    def test_token_set_on_fail_open_handle(
        self,
        sample_spec: PermissionSpec,
        sample_target: SandboxTarget,
    ) -> None:
        with mock.patch.object(FirecrackerBackend, "available", return_value=False):
            handle = FirecrackerBackend.apply(sample_spec, sample_target)
        assert handle.token == "gludd-test-agent"


# ---------------------------------------------------------------------------
# FirecrackerBackend.verify
# ---------------------------------------------------------------------------


class TestFirecrackerBackendVerify:
    def test_returns_fail_when_not_applied(
        self,
        sample_spec: PermissionSpec,
    ) -> None:
        handle = SandboxHandle(
            backend="firecracker",
            token="gludd-test",
            applied=False,
            extra={"reason": "no /dev/kvm"},
        )
        findings = FirecrackerBackend.verify(sample_spec, handle)
        assert any(f.severity == "fail" for f in findings)
        assert any("not applied" in f.message for f in findings)

    def test_returns_fail_for_legacy_stub_handle(
        self,
        sample_spec: PermissionSpec,
    ) -> None:
        handle = SandboxHandle(
            backend="firecracker",
            token="gludd-test",
            applied=True,
            extra={"stub": True},
        )
        findings = FirecrackerBackend.verify(sample_spec, handle)
        assert any(f.severity == "fail" for f in findings)
        assert any("no live popen" in f.message for f in findings)

    def test_returns_fail_for_handle_with_none_popen(
        self,
        sample_spec: PermissionSpec,
    ) -> None:
        handle = SandboxHandle(
            backend="firecracker",
            token="gludd-test",
            applied=True,
            extra={"popen": None},
        )
        findings = FirecrackerBackend.verify(sample_spec, handle)
        assert any(f.severity == "fail" for f in findings)

    def test_returns_warn_when_poll_raises(
        self,
        sample_spec: PermissionSpec,
    ) -> None:
        fake_popen = mock.MagicMock(spec=subprocess.Popen)
        fake_popen.poll.side_effect = RuntimeError("poll exploded")

        handle = SandboxHandle(
            backend="firecracker",
            token="gludd-test",
            applied=True,
            extra={"popen": fake_popen, "pid": 100, "sandbox_id": "s-1"},
        )
        findings = FirecrackerBackend.verify(sample_spec, handle)
        assert any(f.severity == "warn" for f in findings)
        assert any("poll raised" in f.message for f in findings)

    def test_returns_fail_when_process_died(
        self,
        sample_spec: PermissionSpec,
    ) -> None:
        fake_popen = mock.MagicMock(spec=subprocess.Popen)
        fake_popen.poll.return_value = 1

        handle = SandboxHandle(
            backend="firecracker",
            token="gludd-test",
            applied=True,
            extra={
                "popen": fake_popen,
                "pid": 100,
                "sandbox_id": "dead-sbox",
            },
        )
        findings = FirecrackerBackend.verify(sample_spec, handle)
        assert any(f.severity == "fail" for f in findings)
        assert any("dead" in f.message for f in findings)

    def test_returns_ok_when_process_alive_and_socket_exists(
        self,
        sample_spec: PermissionSpec,
    ) -> None:
        fake_popen = mock.MagicMock(spec=subprocess.Popen)
        fake_popen.poll.return_value = None

        with mock.patch("os.path.exists", return_value=True):
            handle = SandboxHandle(
                backend="firecracker",
                token="gludd-test",
                applied=True,
                extra={
                    "popen": fake_popen,
                    "pid": 42,
                    "sandbox_id": "alive-sbox",
                    "api_sock": "/tmp/alive.sock",
                },
            )
            findings = FirecrackerBackend.verify(sample_spec, handle)
        assert any(f.severity == "ok" for f in findings)
        assert any("alive" in f.message for f in findings)

    def test_returns_warn_when_process_alive_but_socket_missing(
        self,
        sample_spec: PermissionSpec,
    ) -> None:
        fake_popen = mock.MagicMock(spec=subprocess.Popen)
        fake_popen.poll.return_value = None

        with mock.patch("os.path.exists", return_value=False):
            handle = SandboxHandle(
                backend="firecracker",
                token="gludd-test",
                applied=True,
                extra={
                    "popen": fake_popen,
                    "pid": 42,
                    "sandbox_id": "no-sock-sbox",
                    "api_sock": "/tmp/missing.sock",
                },
            )
            findings = FirecrackerBackend.verify(sample_spec, handle)
        assert any(f.severity == "warn" for f in findings)
        assert any("socket" in f.message for f in findings)

    def test_handle_with_string_popen_returns_fail(
        self,
        sample_spec: PermissionSpec,
    ) -> None:
        handle = SandboxHandle(
            backend="firecracker",
            token="gludd-test",
            applied=True,
            extra={"popen": "not-a-Popen-object"},
        )
        findings = FirecrackerBackend.verify(sample_spec, handle)
        assert any(f.severity == "fail" for f in findings)

    def test_empty_extra_returns_fail(
        self,
        sample_spec: PermissionSpec,
    ) -> None:
        handle = SandboxHandle(
            backend="firecracker",
            token="gludd-test",
            applied=True,
        )
        findings = FirecrackerBackend.verify(sample_spec, handle)
        assert any(f.severity == "fail" for f in findings)


# ---------------------------------------------------------------------------
# FirecrackerBackend.release
# ---------------------------------------------------------------------------


class TestFirecrackerBackendRelease:
    def test_noop_when_no_popen(
        self,
        sample_spec: PermissionSpec,
    ) -> None:
        handle = SandboxHandle(
            backend="firecracker",
            token="gludd-test",
            applied=True,
            extra={"stub": True},
        )
        FirecrackerBackend.release(handle)

    def test_noop_when_popen_is_not_popen_instance(
        self,
        sample_spec: PermissionSpec,
    ) -> None:
        handle = SandboxHandle(
            backend="firecracker",
            token="gludd-test",
            applied=True,
            extra={"popen": "garbage"},
        )
        FirecrackerBackend.release(handle)

    def test_graceful_shutdown_via_ctrl_alt_del(
        self,
        sample_spec: PermissionSpec,
    ) -> None:
        fake_popen = mock.MagicMock(spec=subprocess.Popen)
        fake_popen.poll.return_value = None

        with (
            mock.patch("os.path.exists", return_value=True),
            mock.patch(
                "general_ludd.security.sandboxes.vm.firecracker_backend._firecracker_put",
                return_value={},
            ) as mock_put,
        ):
            handle = SandboxHandle(
                backend="firecracker",
                token="gludd-test",
                applied=True,
                extra={
                    "popen": fake_popen,
                    "pid": 42,
                    "api_sock": "/tmp/test.sock",
                },
            )
            FirecrackerBackend.release(handle)

        mock_put.assert_called_once_with(
            "/tmp/test.sock",
            "/actions",
            {"action_type": "InstanceSendCtrlAltDel"},
        )
        assert fake_popen.wait.called

    def test_ctrl_alt_del_raises_then_terminate(
        self,
        sample_spec: PermissionSpec,
    ) -> None:
        fake_popen = mock.MagicMock(spec=subprocess.Popen)
        fake_popen.poll.return_value = None

        with (
            mock.patch("os.path.exists", return_value=True),
            mock.patch(
                "general_ludd.security.sandboxes.vm.firecracker_backend._firecracker_put",
                side_effect=RuntimeError("API socket gone"),
            ),
        ):
            handle = SandboxHandle(
                backend="firecracker",
                token="gludd-test",
                applied=True,
                extra={
                    "popen": fake_popen,
                    "pid": 42,
                    "api_sock": "/tmp/test.sock",
                },
            )
            FirecrackerBackend.release(handle)

        fake_popen.terminate.assert_called_once()

    def test_already_dead_process_is_noop(
        self,
        sample_spec: PermissionSpec,
    ) -> None:
        fake_popen = mock.MagicMock(spec=subprocess.Popen)
        fake_popen.poll.return_value = 0

        with mock.patch("os.path.exists", return_value=False):
            handle = SandboxHandle(
                backend="firecracker",
                token="gludd-test",
                applied=True,
                extra={
                    "popen": fake_popen,
                    "pid": 42,
                    "api_sock": "/tmp/test.sock",
                },
            )
            FirecrackerBackend.release(handle)

        fake_popen.terminate.assert_not_called()
        fake_popen.kill.assert_not_called()

    def test_terminate_timeout_kills(
        self,
        sample_spec: PermissionSpec,
    ) -> None:
        fake_popen = mock.MagicMock(spec=subprocess.Popen)
        fake_popen.poll.return_value = None
        fake_popen.wait.side_effect = subprocess.TimeoutExpired(
            cmd="firecracker",
            timeout=2.0,
        )

        with mock.patch("os.path.exists", return_value=False):
            handle = SandboxHandle(
                backend="firecracker",
                token="gludd-test",
                applied=True,
                extra={
                    "popen": fake_popen,
                    "pid": 42,
                },
            )
            FirecrackerBackend.release(handle)

        fake_popen.kill.assert_called_once()

    def test_kill_itself_raises_is_handled(
        self,
        sample_spec: PermissionSpec,
    ) -> None:
        fake_popen = mock.MagicMock(spec=subprocess.Popen)
        fake_popen.poll.return_value = None
        fake_popen.wait.side_effect = subprocess.TimeoutExpired(
            cmd="firecracker",
            timeout=2.0,
        )
        fake_popen.kill.side_effect = ProcessLookupError("no such process")

        with mock.patch("os.path.exists", return_value=False):
            handle = SandboxHandle(
                backend="firecracker",
                token="gludd-test",
                applied=True,
                extra={
                    "popen": fake_popen,
                    "pid": 42,
                },
            )
            FirecrackerBackend.release(handle)

        fake_popen.kill.assert_called_once()

    def test_terminate_itself_raises_is_handled(
        self,
        sample_spec: PermissionSpec,
    ) -> None:
        fake_popen = mock.MagicMock(spec=subprocess.Popen)
        fake_popen.poll.return_value = None
        fake_popen.terminate.side_effect = ProcessLookupError("already gone")

        with mock.patch("os.path.exists", return_value=False):
            handle = SandboxHandle(
                backend="firecracker",
                token="gludd-test",
                applied=True,
                extra={
                    "popen": fake_popen,
                    "pid": 42,
                },
            )
            FirecrackerBackend.release(handle)

        fake_popen.terminate.assert_called_once()

    def test_unlinks_api_socket_after_shutdown(
        self,
        sample_spec: PermissionSpec,
    ) -> None:
        fake_popen = mock.MagicMock(spec=subprocess.Popen)
        fake_popen.poll.return_value = 0

        with mock.patch("os.path.exists", return_value=False), mock.patch("os.unlink") as mock_unlink:
            handle = SandboxHandle(
                backend="firecracker",
                token="gludd-test",
                applied=True,
                extra={
                    "popen": fake_popen,
                    "pid": 42,
                    "api_sock": "/tmp/test.sock",
                },
            )
            FirecrackerBackend.release(handle)

        mock_unlink.assert_called_once_with("/tmp/test.sock")

    def test_unlink_oserror_is_suppressed(
        self,
        sample_spec: PermissionSpec,
    ) -> None:
        fake_popen = mock.MagicMock(spec=subprocess.Popen)
        fake_popen.poll.return_value = 0

        with mock.patch(
            "os.unlink",
            side_effect=OSError("file not found"),
        ):
            handle = SandboxHandle(
                backend="firecracker",
                token="gludd-test",
                applied=True,
                extra={
                    "popen": fake_popen,
                    "pid": 42,
                    "api_sock": "/tmp/test.sock",
                },
            )
            FirecrackerBackend.release(handle)


# ---------------------------------------------------------------------------
# FirecrackerBackend protocol shape
# ---------------------------------------------------------------------------


class TestFirecrackerBackendProtocol:
    def test_name_is_firecracker(self) -> None:
        assert FirecrackerBackend.name == "firecracker"

    def test_has_all_backend_methods(self) -> None:
        for attr in ("available", "apply", "verify", "release"):
            assert hasattr(FirecrackerBackend, attr), f"FirecrackerBackend missing {attr}"

    def test_all_methods_are_static(self) -> None:
        for attr in ("available", "apply", "verify", "release"):
            raw = FirecrackerBackend.__dict__[attr]
            assert isinstance(raw, staticmethod), f"FirecrackerBackend.{attr} must be staticmethod, got {type(raw)}"
