"""Firecracker microVM sandbox backend.

Phase P5: ``apply`` spawns a real ``firecracker --api-sock=<path>`` subprocess
and issues the full REST configuration sequence over a UNIX domain socket
(``PUT /machine-config``, ``PUT /boot-source``, ``PUT /drives/rootfs``,
``PUT /vsock``, ``PUT /actions`` with ``action_type=InstanceStart``). The
handle's ``extra`` carries ``popen``, ``pid``, ``sandbox_id``, ``api_sock``,
``vsock_uds``, and ``started_at`` so ``verify`` can poll process + socket
liveness and ``release`` can shut the microVM down via
``InstanceSendCtrlAltDel`` then terminate the popen.

When ``firecracker`` or ``/dev/kvm`` is absent the backend fails open exactly
as the original P1 stub did — the auto-detection chain still resolves and the
caller dispatches with a "no sandbox" warning.

Firecracker REST API (v1.x):
  * ``PUT /machine-config``  — ``{vcpu_count, mem_size_mib}``
  * ``PUT /boot-source``     — ``{kernel_image_path, boot_args}``
  * ``PUT /drives/<id>``     — ``{drive_id, path_on_host, is_root_device, is_read_only}``
  * ``PUT /vsock``           — ``{guest_cid, uds_path}``
  * ``PUT /actions``         — ``{action_type: "InstanceStart"|"InstanceSendCtrlAltDel"}``
"""

from __future__ import annotations

import contextlib
import http.client
import json
import logging
import os
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from general_ludd.security.permissions import PermissionSpec
from general_ludd.security.sandboxes import (
    Finding,
    SandboxHandle,
    SandboxTarget,
)
from general_ludd.security.sandboxes.state import SandboxState, safe_state_component

logger = logging.getLogger(__name__)


_FC_TERMINATE_GRACE_S = 2.0
# Test/operator injection seam retained for compatibility. Production state
# uses :class:`SandboxState`; no host control socket defaults to public /tmp.
_FC_API_SOCKET_DIR: str | None = None
_FC_DEFAULT_VCPUS = 1
_FC_DEFAULT_MEM_MIB = 128
_FC_DEFAULT_KERNEL_PATH = "/var/lib/gludd/vmlinux"
_FC_DEFAULT_ROOTFS_PATH = "/var/lib/gludd/rootfs.ext4"
_FC_DEFAULT_BOOT_ARGS = "console=ttyS0 reboot=k panic=1 pci=off"
_FC_DEFAULT_GUEST_CID = 3


def _socket_paths(
    sandbox_id: str,
    *,
    create: bool = False,
) -> tuple[str, str]:
    component = safe_state_component(sandbox_id)
    if _FC_API_SOCKET_DIR is not None:
        root = Path(_FC_API_SOCKET_DIR)
        if create:
            root.mkdir(parents=True, exist_ok=True)
        return (
            str(root / f"{component}.api.sock"),
            str(root / f"{component}.vsock"),
        )
    state = SandboxState.discover(create=create)
    root = (
        state.directory("firecracker", component)
        if create
        else state.path("firecracker", component)
    )
    return str(root / "api.sock"), str(root / "vsock")


def _cleanup_firecracker_state(handle: SandboxHandle) -> None:
    state = handle.extra.get("state")
    runtime_root = handle.extra.get("runtime_root")
    if isinstance(state, SandboxState) and isinstance(runtime_root, str):
        try:
            state.cleanup_path(runtime_root)
        except Exception as exc:
            logger.warning(
                "Firecracker state cleanup failed for %s: %s",
                handle.token,
                exc,
            )
        return
    for key in ("api_sock", "vsock_uds"):
        path = handle.extra.get(key)
        if isinstance(path, str):
            with contextlib.suppress(OSError):
                os.unlink(path)


# ---------------------------------------------------------------------------
# HTTP-over-UNIX-socket adapter
# ---------------------------------------------------------------------------


class FirecrackerUnixHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection variant that dials a UNIX domain socket instead of TCP.

    Firecracker exposes its REST API on a UNIX socket created by
    ``--api-sock=<path>``. We reuse the stdlib HTTP request/response machinery
    by overriding :meth:`connect` to build an ``AF_UNIX`` ``SOCK_STREAM`` and
    ``connect`` against the socket path.
    """

    def __init__(self, sock_path: str, timeout: float = 5.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self._sock_path = sock_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._sock_path)


# ---------------------------------------------------------------------------
# REST helper
# ---------------------------------------------------------------------------


def _firecracker_put(
    sock_path: str, path: str, body: dict[str, Any],
) -> dict[str, Any]:
    """Issue a ``PUT`` to the Firecracker API socket.

    Returns the parsed JSON response body, or ``{}`` for a ``204 No Content``
    reply. Raises :class:`RuntimeError` on any non-2xx status and propagates
    the underlying :class:`OSError` if the socket is unreachable.
    """
    conn = FirecrackerUnixHTTPConnection(sock_path)
    try:
        payload = json.dumps(body).encode("utf-8")
        conn.request(
            "PUT", path, body=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        data = resp.read()
        if resp.status >= 300:
            text = data.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Firecracker PUT {path} -> HTTP {resp.status}: {text}",
            )
        if not data:
            return {}
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Socket readiness poll
# ---------------------------------------------------------------------------


def _wait_for_socket(
    sock_path: str, timeout: float = 5.0, poll_interval: float = 0.05,
) -> bool:
    """Poll for ``sock_path`` to exist and be connectable.

    Returns ``True`` once a successful ``AF_UNIX`` ``connect()`` succeeds,
    or ``False`` if the deadline elapses first.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(sock_path):
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(0.5)
            try:
                probe.connect(sock_path)
                probe.close()
                return True
            except OSError:
                with contextlib.suppress(OSError):
                    probe.close()
        time.sleep(poll_interval)
    return False


# ---------------------------------------------------------------------------
# Spawn + boot a microVM
# ---------------------------------------------------------------------------


def _spawn_firecracker(
    spec: PermissionSpec,
    target: SandboxTarget,
    *,
    kernel_path: str = _FC_DEFAULT_KERNEL_PATH,
    rootfs_path: str = _FC_DEFAULT_ROOTFS_PATH,
    vcpus: int = _FC_DEFAULT_VCPUS,
    mem_mib: int = _FC_DEFAULT_MEM_MIB,
    guest_cid: int = _FC_DEFAULT_GUEST_CID,
) -> SandboxHandle:
    """Spawn ``firecracker --api-sock=<path>`` then issue REST configuration.

    Returns a handle whose ``extra`` carries ``popen``, ``pid``,
    ``sandbox_id``, ``api_sock``, ``vsock_uds``, and ``started_at``. On any
    failure (Popen OSError, API socket never appearing, REST error) returns a
    fail-open handle with the cause in ``extra['reason']``.
    """
    del target

    token = f"gludd-{spec.agent_type}"
    sandbox_id = f"gludd-fc-{uuid.uuid4().hex[:12]}"
    state: SandboxState | None = None
    runtime_root: str | None = None
    try:
        sock_path, vsock_uds = _socket_paths(sandbox_id, create=True)
        if _FC_API_SOCKET_DIR is None:
            state = SandboxState.discover()
            runtime_root = str(Path(sock_path).parent)
    except Exception as exc:
        logger.error(
            "FirecrackerBackend.apply: secure runtime-state allocation failed "
            "for %s — %s",
            token,
            exc,
        )
        return SandboxHandle(
            backend="firecracker",
            token=token,
            applied=False,
            extra={"reason": f"runtime-state allocation failed: {exc}"},
        )

    argv = [
        "firecracker",
        f"--api-sock={sock_path}",
        "--no-file",
        "--level=Info",
    ]

    try:
        popen = subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        logger.error(
            "FirecrackerBackend.apply: spawn failed for %s — %s",
            token, exc,
        )
        handle = SandboxHandle(
            backend="firecracker",
            token=token,
            applied=False,
            extra={
                "reason": f"firecracker spawn failed: {exc}",
                "api_sock": sock_path,
                "vsock_uds": vsock_uds,
                "runtime_root": runtime_root or "",
                "state": state,
            },
        )
        _cleanup_firecracker_state(handle)
        return handle

    if not _wait_for_socket(sock_path, timeout=5.0):
        logger.error(
            "FirecrackerBackend.apply: API socket %s never appeared for %s",
            sock_path, token,
        )
        with contextlib.suppress(Exception):
            popen.kill()
        handle = SandboxHandle(
            backend="firecracker",
            token=token,
            applied=False,
            extra={
                "reason": (
                    f"API socket {sock_path} never appeared "
                    f"(firecracker failed to boot?)"
                ),
                "api_sock": sock_path,
                "vsock_uds": vsock_uds,
                "runtime_root": runtime_root or "",
                "state": state,
            },
        )
        _cleanup_firecracker_state(handle)
        return handle

    try:
        _firecracker_put(
            sock_path, "/machine-config",
            {"vcpu_count": vcpus, "mem_size_mib": mem_mib},
        )
        _firecracker_put(
            sock_path, "/boot-source",
            {
                "kernel_image_path": kernel_path,
                "boot_args": _FC_DEFAULT_BOOT_ARGS,
            },
        )
        _firecracker_put(
            sock_path, "/drives/rootfs",
            {
                "drive_id": "rootfs",
                "path_on_host": rootfs_path,
                "is_root_device": True,
                "is_read_only": False,
            },
        )
        _firecracker_put(
            sock_path, "/vsock",
            {"guest_cid": guest_cid, "uds_path": vsock_uds},
        )
        _firecracker_put(
            sock_path, "/actions", {"action_type": "InstanceStart"},
        )
    except Exception as exc:
        logger.error(
            "FirecrackerBackend.apply: REST configuration failed for %s — %s",
            token, exc,
        )
        try:
            popen.terminate()
            popen.wait(timeout=_FC_TERMINATE_GRACE_S)
        except Exception:
            with contextlib.suppress(Exception):
                popen.kill()
        handle = SandboxHandle(
            backend="firecracker",
            token=token,
            applied=False,
            extra={
                "reason": f"REST configuration failed: {exc}",
                "api_sock": sock_path,
                "vsock_uds": vsock_uds,
                "runtime_root": runtime_root or "",
                "state": state,
            },
        )
        _cleanup_firecracker_state(handle)
        return handle

    logger.info(
        "FirecrackerBackend.apply booted sandbox=%s pid=%d api_sock=%s",
        sandbox_id, popen.pid, sock_path,
    )
    return SandboxHandle(
        backend="firecracker",
        token=token,
        applied=True,
        extra={
            "popen": popen,
            "pid": popen.pid,
            "sandbox_id": sandbox_id,
            "api_sock": sock_path,
            "vsock_uds": vsock_uds,
            "runtime_root": runtime_root or "",
            "state": state,
            "started_at": time.time(),
        },
    )


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class FirecrackerBackend:
    name = "firecracker"

    @staticmethod
    def available() -> bool:
        kvm_ok = (
            os.path.exists("/dev/kvm")
            and os.access("/dev/kvm", os.R_OK | os.W_OK)
        )
        fc_ok = shutil.which("firecracker") is not None
        if not kvm_ok:
            logger.debug("Firecracker unavailable: /dev/kvm absent or not readable")
        if not fc_ok:
            logger.debug("Firecracker unavailable: firecracker binary not on PATH")
        return kvm_ok and fc_ok

    @staticmethod
    def apply(spec: PermissionSpec, target: SandboxTarget) -> SandboxHandle:
        token = f"gludd-{spec.agent_type}"
        if not FirecrackerBackend.available():
            logger.warning(
                "Firecracker apply skipped: /dev/kvm or firecracker binary "
                "absent — UNSANDBOXED",
            )
            return SandboxHandle(
                backend="firecracker", token=token, applied=False,
                extra={"reason": "firecracker or /dev/kvm absent"},
            )
        handle = _spawn_firecracker(spec, target)
        handle.token = token
        return handle

    @staticmethod
    def verify(spec: PermissionSpec, handle: SandboxHandle) -> list[Finding]:
        del spec

        findings: list[Finding] = []
        if not handle.applied:
            findings.append(Finding(
                severity="fail",
                message=(
                    f"Firecracker handle not applied (reason="
                    f"{handle.extra.get('reason', 'unknown')})"
                ),
                capability=None,
            ))
            return findings

        popen = handle.extra.get("popen")
        if not isinstance(popen, subprocess.Popen):
            findings.append(Finding(
                severity="fail",
                message=(
                    "Firecracker handle has no live popen — sandbox process "
                    "tracking lost (legacy stub handle?)"
                ),
                capability=None,
            ))
            return findings

        try:
            returncode: int | None = popen.poll()
        except Exception as exc:
            findings.append(Finding(
                severity="warn",
                message=(
                    f"Firecracker popen poll raised {type(exc).__name__}: {exc}"
                ),
                capability=None,
            ))
            return findings

        pid = handle.extra.get("pid")
        sandbox_id = handle.extra.get("sandbox_id")
        if returncode is not None:
            findings.append(Finding(
                severity="fail",
                message=(
                    f"Firecracker sandbox {sandbox_id} dead "
                    f"(pid={pid}, returncode={returncode})"
                ),
                capability=None,
            ))
            return findings

        api_sock_raw = handle.extra.get("api_sock")
        api_sock = api_sock_raw if isinstance(api_sock_raw, str) else None
        if api_sock and os.path.exists(api_sock):
            findings.append(Finding(
                severity="ok",
                message=(
                    f"Firecracker sandbox {sandbox_id} alive "
                    f"(pid={pid}, api_sock={api_sock})"
                ),
                capability=None,
            ))
        else:
            findings.append(Finding(
                severity="warn",
                message=(
                    f"Firecracker sandbox {sandbox_id} process alive but API "
                    f"socket {api_sock} missing"
                ),
                capability=None,
            ))
        return findings

    @staticmethod
    def release(handle: SandboxHandle) -> None:
        popen = handle.extra.get("popen")
        if not isinstance(popen, subprocess.Popen):
            logger.debug(
                "FirecrackerBackend.release: no popen on handle %s — no-op",
                handle.token,
            )
            _cleanup_firecracker_state(handle)
            return

        api_sock_raw = handle.extra.get("api_sock")
        api_sock = api_sock_raw if isinstance(api_sock_raw, str) else None
        if api_sock:
            try:
                _firecracker_put(
                    api_sock, "/actions",
                    {"action_type": "InstanceSendCtrlAltDel"},
                )
                with contextlib.suppress(subprocess.TimeoutExpired):
                    popen.wait(timeout=_FC_TERMINATE_GRACE_S)
            except Exception as exc:
                logger.debug(
                    "FirecrackerBackend.release: CtrlAltDel failed (%s) — "
                    "terminating pid=%s",
                    exc,
                    handle.extra.get("pid"),
                )

        try:
            if popen.poll() is not None:
                logger.debug(
                    "FirecrackerBackend.release: pid=%s already dead — no-op",
                    handle.extra.get("pid"),
                )
            else:
                popen.terminate()
                try:
                    popen.wait(timeout=_FC_TERMINATE_GRACE_S)
                except subprocess.TimeoutExpired:
                    logger.warning(
                        "FirecrackerBackend.release: pid=%s did not exit in "
                        "%.1fs — killing",
                        handle.extra.get("pid"),
                        _FC_TERMINATE_GRACE_S,
                    )
                    try:
                        popen.kill()
                    except Exception as exc:
                        logger.warning(
                            "FirecrackerBackend.release: kill raised %s for "
                            "pid=%s",
                            type(exc).__name__,
                            handle.extra.get("pid"),
                        )
        except Exception as exc:
            logger.warning(
                "FirecrackerBackend.release: terminate path raised %s for "
                "pid=%s",
                type(exc).__name__,
                handle.extra.get("pid"),
            )

        _cleanup_firecracker_state(handle)


__all__ = [
    "FirecrackerBackend",
    "FirecrackerUnixHTTPConnection",
]
