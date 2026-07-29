"""P5 TDD tests — real Firecracker REST API.

P5 advances the Firecracker backend from a P1 stub to a real microVM boot:
- ``FirecrackerUnixHTTPConnection`` — HTTP-over-UNIX-socket adapter for the
  Firecracker REST API (subclasses ``http.client.HTTPConnection`` and overrides
  ``connect()`` to use ``socket.AF_UNIX``).
- ``_firecracker_put`` — issues a PUT to the API socket and parses the JSON
  response; raises on non-2xx.
- ``_wait_for_socket`` — polls for socket existence + connectivity until a
  deadline; returns True once connectable, False on timeout.
- ``FirecrackerBackend.apply`` spawns ``firecracker --api-sock=<path>``,
  waits for the socket, then issues PUTs to ``/machine-config``,
  ``/boot-source``, ``/drives/rootfs``, ``/vsock``, ``/actions`` (with
  ``action_type=InstanceStart``). The handle carries ``pid``, ``popen``,
  ``api_sock``, ``sandbox_id``, ``vsock_uds``.
- ``FirecrackerBackend.verify`` checks popen liveness AND API socket
  existence (ok finding when both alive; warn when process alive but socket
  missing; fail when process dead).
- ``FirecrackerBackend.release`` issues ``InstanceSendCtrlAltDel`` via the
  API first, then terminates/kills the popen, then unlinks the socket file.

Tests are written FIRST and FAIL until the implementation lands. Mocks
substitute for the real firecracker binary, the UNIX socket, and the HTTP
responses — none of /dev/kvm, firecracker, or a real rootfs image is required.
"""

from __future__ import annotations

import http.client
import socket
import subprocess
import threading
import time
from unittest import mock

import pytest

from general_ludd.security.permissions import PermissionSpec
from general_ludd.security.sandboxes import (
    SandboxHandle,
    SandboxTarget,
)


@pytest.fixture()
def sample_spec():
    return PermissionSpec(agent_type="test-agent")


@pytest.fixture()
def sample_target():
    return SandboxTarget(pid=99999)


_REAL_POPEN = subprocess.Popen


def _fake_popen(pid: int = 4242, returncode: int | None = None) -> mock.MagicMock:
    """Mock Popen with a configurable poll result."""
    p = mock.MagicMock(spec=_REAL_POPEN)
    p.pid = pid
    p.poll.return_value = returncode
    p.returncode = returncode
    return p


def _serve_http_response(
    server: socket.socket,
    response_bytes: bytes,
    *,
    ready: threading.Event | None = None,
    requests: list[bytes] | None = None,
    errors: list[OSError] | None = None,
) -> None:
    """Accept one connection, drain the FULL request, then send the response.

    Parses ``Content-Length`` from the request headers and reads the body to
    completion before responding — otherwise the client's ``sendall`` of the
    body can race with the server's ``close`` and produce ``BrokenPipeError``.
    """
    if ready is not None:
        ready.set()
    try:
        conn, _ = server.accept()
    except OSError as exc:
        if errors is not None:
            errors.append(exc)
        return
    try:
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                conn.sendall(response_bytes)
                return
            buf += chunk
        head, _, body_start = buf.partition(b"\r\n\r\n")
        content_length = 0
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                content_length = int(line.split(b":", 1)[1].strip())
                break
        body_buf = body_start
        while len(body_buf) < content_length:
            chunk = conn.recv(4096)
            if not chunk:
                break
            body_buf += chunk
        if requests is not None:
            requests.append(head + b"\r\n\r\n" + body_buf)
        conn.sendall(response_bytes)
    except OSError as exc:
        if errors is not None:
            errors.append(exc)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# FirecrackerUnixHTTPConnection — HTTP-over-UNIX-socket adapter
# ---------------------------------------------------------------------------


def test_unix_http_connection_class_exists():
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerUnixHTTPConnection,
    )

    assert issubclass(FirecrackerUnixHTTPConnection, http.client.HTTPConnection)


def test_unix_http_connection_connect_uses_af_unix(tmp_path):
    """connect() must create an AF_UNIX SOCK_STREAM and connect to the path."""
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerUnixHTTPConnection,
    )

    sock_path = str(tmp_path / "fc.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    server.listen(1)
    try:
        conn = FirecrackerUnixHTTPConnection(sock_path, timeout=2.0)
        conn.connect()
        try:
            assert conn.sock is not None
            assert conn.sock.family == socket.AF_UNIX
        finally:
            conn.close()
    finally:
        server.close()


def test_unix_http_connection_round_trip_put(tmp_path):
    """A real PUT over the UNIX socket reaches the server with the body."""
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerUnixHTTPConnection,
    )

    sock_path = str(tmp_path / "fc-rt.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    server.listen(1)
    received: list[bytes] = []

    response = b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n"

    def serve_capture() -> None:
        try:
            conn, _ = server.accept()
        except OSError:
            return
        try:
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            head, _, body_start = buf.partition(b"\r\n\r\n")
            content_length = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    content_length = int(line.split(b":", 1)[1].strip())
                    break
            body_buf = body_start
            while len(body_buf) < content_length:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                body_buf += chunk
            received.append(buf + body_buf)
            conn.sendall(response)
        finally:
            conn.close()

    t = threading.Thread(target=serve_capture, daemon=True)
    t.start()
    try:
        conn = FirecrackerUnixHTTPConnection(sock_path, timeout=2.0)
        conn.request("PUT", "/machine-config", body=b'{"vcpu_count":1}')
        resp = conn.getresponse()
        assert resp.status == 204
        t.join(timeout=2.0)
        assert received, "server did not receive any bytes"
        assert b"PUT /machine-config" in received[0]
        assert b'"vcpu_count":1' in received[0]
        conn.close()
    finally:
        server.close()


# ---------------------------------------------------------------------------
# _firecracker_put — REST helper
# ---------------------------------------------------------------------------


def test_firecracker_put_returns_empty_dict_on_204(tmp_path):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        _firecracker_put,
    )

    sock_path = str(tmp_path / "fc-put.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    server.listen(1)

    response = b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n"
    t = threading.Thread(
        target=_serve_http_response, args=(server, response), daemon=True,
    )
    t.start()
    try:
        result = _firecracker_put(sock_path, "/machine-config", {"vcpu_count": 1})
        assert result == {}
    finally:
        server.close()
        t.join(timeout=2.0)


def test_firecracker_put_parses_json_body(tmp_path):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        _firecracker_put,
    )

    sock_path = str(tmp_path / "fc-json.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    server.listen(1)

    body = b'{"state":"Running"}'
    response = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )
    t = threading.Thread(
        target=_serve_http_response, args=(server, response), daemon=True,
    )
    t.start()
    try:
        result = _firecracker_put(sock_path, "/machine-config", {"vcpu_count": 1})
        assert result == {"state": "Running"}
    finally:
        server.close()
        t.join(timeout=2.0)


def test_firecracker_put_raises_on_non_2xx(tmp_path):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        _firecracker_put,
    )

    sock_path = str(tmp_path / "fc-err.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    server.listen(1)
    body = b'{"fault_message":"bad config"}'
    response = (
        b"HTTP/1.1 400 Bad Request\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )
    t = threading.Thread(
        target=_serve_http_response, args=(server, response), daemon=True,
    )
    t.start()
    try:
        with pytest.raises(RuntimeError, match="400"):
            _firecracker_put(sock_path, "/machine-config", {"vcpu_count": -1})
    finally:
        server.close()
        t.join(timeout=2.0)


def test_firecracker_put_raises_on_missing_socket(tmp_path):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        _firecracker_put,
    )

    with pytest.raises(OSError):
        _firecracker_put(str(tmp_path / "nope.sock"), "/actions", {})


# ---------------------------------------------------------------------------
# _wait_for_socket — socket readiness poller
# ---------------------------------------------------------------------------


def test_wait_for_socket_returns_true_when_connectable(tmp_path):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        _wait_for_socket,
    )

    sock_path = str(tmp_path / "ready.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    server.listen(1)
    try:
        assert _wait_for_socket(sock_path, timeout=2.0) is True
    finally:
        server.close()


def test_wait_for_socket_returns_false_on_timeout(tmp_path):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        _wait_for_socket,
    )

    start = time.monotonic()
    result = _wait_for_socket(str(tmp_path / "never.sock"), timeout=0.3)
    elapsed = time.monotonic() - start
    assert result is False
    assert elapsed >= 0.25


def test_wait_for_socket_returns_true_after_appearing(tmp_path):
    """Socket that appears mid-poll is detected."""
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        _wait_for_socket,
    )

    sock_path = str(tmp_path / "late.sock")
    server_holder: list[socket.socket | None] = [None]

    def create_late() -> None:
        time.sleep(0.25)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(sock_path)
        s.listen(1)
        server_holder[0] = s

    t = threading.Thread(target=create_late, daemon=True)
    t.start()
    try:
        assert _wait_for_socket(sock_path, timeout=2.0) is True
    finally:
        t.join(timeout=2.0)
        if server_holder[0] is not None:
            server_holder[0].close()


# ---------------------------------------------------------------------------
# _spawn_firecracker — full boot sequence
# ---------------------------------------------------------------------------


def test_spawn_firecracker_invokes_binary_with_api_sock(
    sample_spec, sample_target, tmp_path,
):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        _spawn_firecracker,
    )

    fake_popen = _fake_popen(pid=31337)
    str(tmp_path / "spawn.sock")

    captured_argv: list[list[str]] = []

    def fake_popen_factory(argv: list[str], **kwargs):
        captured_argv.append(list(argv))
        return fake_popen

    with mock.patch("subprocess.Popen", side_effect=fake_popen_factory), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._wait_for_socket",
             return_value=True,
         ), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._firecracker_put",
             return_value={},
         ), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._FC_API_SOCKET_DIR",
             str(tmp_path),
         ):
        handle = _spawn_firecracker(
            sample_spec, sample_target,
            kernel_path=str(tmp_path / "vmlinux"),
            rootfs_path=str(tmp_path / "rootfs.ext4"),
        )

    assert handle.applied is True
    assert handle.backend == "firecracker"
    assert handle.extra["pid"] == 31337
    assert handle.extra["popen"] is fake_popen
    assert "api_sock" in handle.extra
    assert str(handle.extra["api_sock"]).startswith(str(tmp_path))
    assert "sandbox_id" in handle.extra
    assert "vsock_uds" in handle.extra

    assert captured_argv, "Popen was never invoked"
    assert captured_argv[0][0] == "firecracker"
    api_sock_flag = next(
        (a for a in captured_argv[0] if a.startswith("--api-sock")), None,
    )
    assert api_sock_flag is not None, "no --api-sock flag in firecracker argv"


def test_spawn_firecracker_issues_rest_calls_in_correct_order(
    sample_spec, sample_target, tmp_path,
):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        _spawn_firecracker,
    )

    fake_popen = _fake_popen(pid=31338)
    call_paths: list[str] = []

    def recording_put(_sock: str, path: str, _body: dict) -> dict:
        call_paths.append(path)
        return {}

    with mock.patch("subprocess.Popen", return_value=fake_popen), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._wait_for_socket",
             return_value=True,
         ), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._firecracker_put",
             side_effect=recording_put,
         ), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._FC_API_SOCKET_DIR",
             str(tmp_path),
         ):
        handle = _spawn_firecracker(sample_spec, sample_target)

    assert handle.applied is True
    assert call_paths == [
        "/machine-config",
        "/boot-source",
        "/drives/rootfs",
        "/vsock",
        "/actions",
    ]


def test_spawn_firecracker_sends_instance_start_action(
    sample_spec, sample_target, tmp_path,
):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        _spawn_firecracker,
    )

    fake_popen = _fake_popen(pid=31339)
    actions_calls: list[dict] = []

    def recording_put(_sock: str, path: str, body: dict) -> dict:
        if path == "/actions":
            actions_calls.append(body)
        return {}

    with mock.patch("subprocess.Popen", return_value=fake_popen), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._wait_for_socket",
             return_value=True,
         ), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._firecracker_put",
             side_effect=recording_put,
         ), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._FC_API_SOCKET_DIR",
             str(tmp_path),
         ):
        _spawn_firecracker(sample_spec, sample_target)

    assert actions_calls == [{"action_type": "InstanceStart"}]


def test_spawn_firecracker_machine_config_body_has_vcpu_and_mem(
    sample_spec, sample_target, tmp_path,
):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        _spawn_firecracker,
    )

    fake_popen = _fake_popen(pid=31340)
    machine_calls: list[dict] = []

    def recording_put(_sock: str, path: str, body: dict) -> dict:
        if path == "/machine-config":
            machine_calls.append(body)
        return {}

    with mock.patch("subprocess.Popen", return_value=fake_popen), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._wait_for_socket",
             return_value=True,
         ), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._firecracker_put",
             side_effect=recording_put,
         ), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._FC_API_SOCKET_DIR",
             str(tmp_path),
         ):
        _spawn_firecracker(
            sample_spec, sample_target, vcpus=2, mem_mib=256,
        )

    assert machine_calls == [{"vcpu_count": 2, "mem_size_mib": 256}]


def test_spawn_firecracker_fails_open_when_socket_never_appears(
    sample_spec, sample_target, tmp_path,
):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        _spawn_firecracker,
    )

    fake_popen = _fake_popen(pid=31341)
    with mock.patch("subprocess.Popen", return_value=fake_popen), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._wait_for_socket",
             return_value=False,
         ), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._firecracker_put",
         ) as put_mock, \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._FC_API_SOCKET_DIR",
             str(tmp_path),
         ):
        handle = _spawn_firecracker(sample_spec, sample_target)

    assert handle.applied is False
    assert "reason" in handle.extra
    assert "socket" in handle.extra["reason"].lower()
    put_mock.assert_not_called()
    fake_popen.kill.assert_called_once()


def test_spawn_firecracker_fails_open_on_popen_oserror(
    sample_spec, sample_target, tmp_path,
):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        _spawn_firecracker,
    )

    with mock.patch(
        "subprocess.Popen", side_effect=OSError("firecracker: not executable"),
    ), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._FC_API_SOCKET_DIR",
             str(tmp_path),
         ):
        handle = _spawn_firecracker(sample_spec, sample_target)

    assert handle.applied is False
    assert "reason" in handle.extra
    assert "not executable" in handle.extra["reason"]


def test_spawn_firecracker_fails_open_and_kills_on_rest_error(
    sample_spec, sample_target, tmp_path,
):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        _spawn_firecracker,
    )

    fake_popen = _fake_popen(pid=31342)

    def failing_put(_sock: str, path: str, _body: dict) -> dict:
        if path == "/boot-source":
            raise RuntimeError("kernel not found")
        return {}

    with mock.patch("subprocess.Popen", return_value=fake_popen), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._wait_for_socket",
             return_value=True,
         ), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._firecracker_put",
             side_effect=failing_put,
         ), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._FC_API_SOCKET_DIR",
             str(tmp_path),
         ):
        handle = _spawn_firecracker(sample_spec, sample_target)

    assert handle.applied is False
    assert "reason" in handle.extra
    assert "kernel not found" in handle.extra["reason"]
    fake_popen.terminate.assert_called()


# ---------------------------------------------------------------------------
# FirecrackerBackend.apply — entry point
# ---------------------------------------------------------------------------


def test_apply_fails_open_when_unavailable(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    with mock.patch.object(FirecrackerBackend, "available", return_value=False):
        handle = FirecrackerBackend.apply(sample_spec, sample_target)
    assert handle.applied is False
    assert handle.backend == "firecracker"
    assert "reason" in handle.extra


def test_apply_invokes_spawn_when_available(sample_spec, sample_target):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    sentinel = SandboxHandle(
        backend="firecracker", token="t", applied=True, extra={"pid": 1},
    )
    with mock.patch.object(FirecrackerBackend, "available", return_value=True), \
         mock.patch(
             "general_ludd.security.sandboxes.vm.firecracker_backend._spawn_firecracker",
             return_value=sentinel,
         ) as spawn_mock:
        handle = FirecrackerBackend.apply(sample_spec, sample_target)

    assert handle is sentinel
    spawn_mock.assert_called_once_with(sample_spec, sample_target)


# ---------------------------------------------------------------------------
# FirecrackerBackend.verify — process + socket liveness
# ---------------------------------------------------------------------------


def test_verify_reports_ok_when_process_and_socket_alive(
    sample_spec, tmp_path,
):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    sock_path = str(tmp_path / "live.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    server.listen(1)
    try:
        fake_popen = _fake_popen(pid=55501, returncode=None)
        handle = SandboxHandle(
            backend="firecracker", token="t", applied=True,
            extra={
                "pid": 55501, "popen": fake_popen, "sandbox_id": "sb-1",
                "api_sock": sock_path, "vsock_uds": "/tmp/x.vsock",
            },
        )
        findings = FirecrackerBackend.verify(sample_spec, handle)
        assert any(f.severity == "ok" for f in findings)
        assert not any(f.severity == "fail" for f in findings)
    finally:
        server.close()


def test_verify_reports_warn_when_process_alive_but_socket_missing(
    sample_spec, tmp_path,
):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    fake_popen = _fake_popen(pid=55502, returncode=None)
    handle = SandboxHandle(
        backend="firecracker", token="t", applied=True,
        extra={
            "pid": 55502, "popen": fake_popen, "sandbox_id": "sb-2",
            "api_sock": str(tmp_path / "missing.sock"), "vsock_uds": "/tmp/x.vsock",
        },
    )
    findings = FirecrackerBackend.verify(sample_spec, handle)
    assert any(f.severity == "warn" for f in findings)
    assert not any(f.severity == "fail" for f in findings)


def test_verify_reports_fail_when_process_dead(sample_spec, tmp_path):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    fake_popen = _fake_popen(pid=55503, returncode=137)
    handle = SandboxHandle(
        backend="firecracker", token="t", applied=True,
        extra={
            "pid": 55503, "popen": fake_popen, "sandbox_id": "sb-3",
            "api_sock": str(tmp_path / "dead.sock"), "vsock_uds": "/tmp/x.vsock",
        },
    )
    findings = FirecrackerBackend.verify(sample_spec, handle)
    assert any(f.severity == "fail" for f in findings)


def test_verify_reports_fail_when_popen_missing(sample_spec):
    """Legacy stub handle has no popen — verify must surface as fail."""
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    handle = SandboxHandle(
        backend="firecracker", token="t", applied=True, extra={"stub": True},
    )
    findings = FirecrackerBackend.verify(sample_spec, handle)
    assert any(f.severity == "fail" for f in findings)


def test_verify_reports_fail_when_not_applied(sample_spec):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    handle = SandboxHandle(
        backend="firecracker", token="t", applied=False,
        extra={"reason": "no /dev/kvm"},
    )
    findings = FirecrackerBackend.verify(sample_spec, handle)
    assert any(f.severity == "fail" for f in findings)


# ---------------------------------------------------------------------------
# FirecrackerBackend.release — graceful CtrlAltDel + forceful terminate
# ---------------------------------------------------------------------------


def test_release_issues_ctrl_alt_del_via_api(sample_spec, tmp_path):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    sock_path = str(tmp_path / "release.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    server.listen(1)
    ready = threading.Event()
    requests: list[bytes] = []
    errors: list[OSError] = []
    response = b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n"
    t = threading.Thread(
        target=_serve_http_response,
        args=(server, response),
        kwargs={"ready": ready, "requests": requests, "errors": errors},
        daemon=True,
    )
    t.start()
    try:
        assert ready.wait(timeout=2.0), "release API server did not become ready"
        fake_popen = _fake_popen(pid=44401, returncode=None)
        handle = SandboxHandle(
            backend="firecracker", token="t", applied=True,
            extra={
                "pid": 44401, "popen": fake_popen, "sandbox_id": "sb-r",
                "api_sock": sock_path, "vsock_uds": "/tmp/x.vsock",
            },
        )
        FirecrackerBackend.release(handle)
        t.join(timeout=2.0)
        assert not t.is_alive(), "release API server did not finish"
        assert not errors, f"release API server failed: {errors}"
        assert len(requests) == 1, "release did not issue CtrlAltDel PUT"
        assert requests[0].startswith(b"PUT /actions HTTP/1.1\r\n")
        assert b'"action_type": "InstanceSendCtrlAltDel"' in requests[0]
    finally:
        server.close()
        t.join(timeout=2.0)


def test_release_attempts_ctrl_alt_del_without_socket_existence_precheck(
    sample_spec,
):
    """Avoid a TOCTOU race between checking and connecting to the API socket."""
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    fake_popen = _fake_popen(pid=44405, returncode=None)
    handle = SandboxHandle(
        backend="firecracker", token="t", applied=True,
        extra={
            "pid": 44405, "popen": fake_popen, "sandbox_id": "sb-r5",
            "api_sock": "/tmp/gludd-firecracker-toctou.sock",
            "vsock_uds": "/tmp/x.vsock",
        },
    )
    with mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend.os.path.exists",
        return_value=False,
    ), mock.patch(
        "general_ludd.security.sandboxes.vm.firecracker_backend._firecracker_put",
        return_value={},
    ) as put_mock:
        FirecrackerBackend.release(handle)

    put_mock.assert_called_once_with(
        "/tmp/gludd-firecracker-toctou.sock",
        "/actions",
        {"action_type": "InstanceSendCtrlAltDel"},
    )


def test_release_terminates_popen_when_ctrlaltdel_unavailable(
    sample_spec, tmp_path,
):
    """When the API socket file is gone, release must fall back to terminate."""
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    fake_popen = _fake_popen(pid=44402, returncode=None)
    handle = SandboxHandle(
        backend="firecracker", token="t", applied=True,
        extra={
            "pid": 44402, "popen": fake_popen, "sandbox_id": "sb-r2",
            "api_sock": str(tmp_path / "gone.sock"), "vsock_uds": "/tmp/x.vsock",
        },
    )
    FirecrackerBackend.release(handle)
    fake_popen.terminate.assert_called_once()


def test_release_kills_after_terminate_timeout(sample_spec):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    fake_popen = mock.MagicMock(spec=subprocess.Popen)
    fake_popen.pid = 44403
    fake_popen.poll.return_value = None
    fake_popen.wait.side_effect = subprocess.TimeoutExpired(
        cmd="firecracker", timeout=2,
    )
    handle = SandboxHandle(
        backend="firecracker", token="t", applied=True,
        extra={
            "pid": 44403, "popen": fake_popen, "sandbox_id": "sb-stubborn",
            "api_sock": "/nonexistent.sock", "vsock_uds": "/tmp/x.vsock",
        },
    )
    FirecrackerBackend.release(handle)
    fake_popen.terminate.assert_called_once()
    fake_popen.kill.assert_called_once()


def test_release_idempotent_when_popen_already_dead(sample_spec, tmp_path):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    fake_popen = _fake_popen(pid=44404, returncode=0)
    handle = SandboxHandle(
        backend="firecracker", token="t", applied=True,
        extra={
            "pid": 44404, "popen": fake_popen, "sandbox_id": "sb-dead",
            "api_sock": str(tmp_path / "dead.sock"), "vsock_uds": "/tmp/x.vsock",
        },
    )
    FirecrackerBackend.release(handle)
    fake_popen.terminate.assert_not_called()


def test_release_safe_when_no_popen():
    """Legacy stub handle has no popen — release is a no-op, never raises."""
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    handle = SandboxHandle(
        backend="firecracker", token="t", applied=True, extra={"stub": True},
    )
    FirecrackerBackend.release(handle)


def test_release_unlinks_socket_file(sample_spec, tmp_path):
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )

    sock_path = tmp_path / "to-unlink.sock"
    sock_path.write_text("dummy")

    fake_popen = _fake_popen(pid=44405, returncode=0)
    handle = SandboxHandle(
        backend="firecracker", token="t", applied=True,
        extra={
            "pid": 44405, "popen": fake_popen, "sandbox_id": "sb-unlink",
            "api_sock": str(sock_path), "vsock_uds": "/tmp/x.vsock",
        },
    )
    FirecrackerBackend.release(handle)
    assert not sock_path.exists(), "release did not unlink the socket file"


# ---------------------------------------------------------------------------
# VMSandboxManager integration — real firecracker spawn mocked end-to-end
# ---------------------------------------------------------------------------


def test_manager_boot_with_real_firecracker_apply(
    sample_spec, sample_target, tmp_path,
):
    """End-to-end: manager.boot fires real apply when firecracker is available."""
    from general_ludd.security.sandboxes.vm.firecracker_backend import (
        FirecrackerBackend,
    )
    from general_ludd.security.sandboxes.vm.lifecycle import VMSandboxManager

    fake_popen = _fake_popen(pid=33301, returncode=None)
    real_handle = SandboxHandle(
        backend="firecracker", token="t", applied=True,
        extra={
            "pid": 33301, "popen": fake_popen, "sandbox_id": "sb-e2e",
            "api_sock": str(tmp_path / "e2e.sock"), "vsock_uds": "/tmp/x.vsock",
        },
    )
    with mock.patch.object(FirecrackerBackend, "available", return_value=True), \
         mock.patch.object(
             FirecrackerBackend, "apply", return_value=real_handle,
         ):
        mgr = VMSandboxManager()
        inst = mgr.boot("firecracker", sample_spec, sample_target)

    assert inst.state.value == "running"
    assert inst.handle is real_handle
    assert inst.metrics.boot_ms >= 0.0
