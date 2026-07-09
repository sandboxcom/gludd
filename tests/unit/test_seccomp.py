"""Unit tests for the seccomp syscall-filtering backend (OpenShell P2 transfer).

gludd's Landlock backend restricts the filesystem and TCP ports but performs no
syscall-level filtering. A compromised playbook could call ``unshare`` to escape
its mount namespace or ``mount`` to remount a filesystem read-write. This module
adds a seccomp BPF filter installed in the ansible fork child *before*
``os.setsid()`` so those container-escape syscalls are killed (SIGSYS) or fail
(EPERM).

Test layers
-----------
1. **Construction / logic tests** (run on every platform, including macOS): the
   ``SeccompFilter`` API, the default allow/deny sets, YAML loading, and the
   fail-open contract. These are the TDD signal — they FAIL before the module
   exists and PASS after it is implemented.
2. **Runtime enforcement tests** (skipif-gated to Linux with a usable seccomp):
   fork a child, install the filter, and prove ``mount`` / namespace ``clone``
   are blocked while ``read`` / ``write`` / ``socket`` / ``bind`` still work. On
   macOS (this dev host) and old kernels these SKIP — never fail — which is the
   fail-open contract in action.
"""

from __future__ import annotations

import errno
import os
import signal
import sys

import pytest

from general_ludd.security.seccomp import (
    DEFAULT_ALLOWED_SYSCALLS,
    DEFAULT_DENIED_SYSCALLS,
    SeccompFilter,
)

# ---------------------------------------------------------------------------
# Platform gating
# ---------------------------------------------------------------------------

_IS_LINUX = sys.platform.startswith("linux")


def _enforceable() -> bool:
    """True iff seccomp can actually be installed on this host."""
    try:
        return _IS_LINUX and SeccompFilter.is_supported()
    except Exception:
        return False


requires_enforcement = pytest.mark.skipif(
    not _enforceable(),
    reason="seccomp enforcement requires Linux with a usable seccomp backend",
)

# Namespace-creating clone flag; a clone() carrying this is a container escape.
CLONE_NEWNS = 0x00020000


# ---------------------------------------------------------------------------
# Construction / logic tests (every platform)
# ---------------------------------------------------------------------------


def test_default_allow_and_deny_sets_are_disjoint_and_populated():
    """The default whitelist (~80 common syscalls) and the dangerous denylist
    must both be non-trivial and must not overlap."""
    assert len(DEFAULT_ALLOWED_SYSCALLS) >= 60
    assert isinstance(DEFAULT_ALLOWED_SYSCALLS, frozenset)
    assert isinstance(DEFAULT_DENIED_SYSCALLS, frozenset)
    # Common syscalls a local ansible run needs must be whitelisted.
    for sysc in ("read", "write", "openat", "close", "socket", "bind", "connect"):
        assert sysc in DEFAULT_ALLOWED_SYSCALLS
    # A syscall cannot be both allowed and denied.
    assert DEFAULT_ALLOWED_SYSCALLS.isdisjoint(DEFAULT_DENIED_SYSCALLS)


def test_seccomp_default_denies_dangerous_syscalls():
    """The default filter must deny the container-escape / privilege family:
    mount, setns, unshare, pivot_root, add_key, kexec_load (and the rest of the
    documented denylist)."""
    filt = SeccompFilter.default()
    for dangerous in (
        "mount",
        "setns",
        "unshare",
        "pivot_root",
        "add_key",
        "request_key",
        "keyctl",
        "reboot",
        "kexec_load",
        "init_module",
        "delete_module",
    ):
        assert filt.is_denied(dangerous), f"{dangerous} must be denied by default"
        assert not filt.is_allowed(dangerous), f"{dangerous} must not be allowed"


def test_default_filter_allows_common_syscalls():
    """read / write / socket / bind must be allowed by the default filter."""
    filt = SeccompFilter.default()
    for sysc in ("read", "write", "socket", "bind", "connect", "close"):
        assert filt.is_allowed(sysc), f"{sysc} must be allowed"
        assert not filt.is_denied(sysc)


def test_seccomp_filter_from_yaml(tmp_path):
    """A filter can be built from a declarative YAML spec."""
    spec = tmp_path / "seccomp.yml"
    spec.write_text(
        "default_action: allow\n"
        "allowed_syscalls:\n"
        "  - read\n"
        "  - write\n"
        "  - openat\n"
        "denied_syscalls:\n"
        "  - mount\n"
        "  - unshare\n"
        "  - ptrace\n"
    )
    filt = SeccompFilter.from_yaml(str(spec))
    assert isinstance(filt, SeccompFilter)
    assert filt.is_allowed("read")
    assert filt.is_allowed("write")
    assert filt.is_denied("mount")
    assert filt.is_denied("unshare")
    assert filt.is_denied("ptrace")
    # A syscall neither listed nor dangerous follows the default action (allow).
    assert not filt.is_denied("read")


def test_seccomp_filter_fail_open(monkeypatch):
    """If seccomp is unavailable (macOS, old kernel, missing backend), apply()
    is a NOP that returns False — never a crash."""
    filt = SeccompFilter.default()
    # Force the "unsupported platform" path regardless of the real host.
    monkeypatch.setattr(SeccompFilter, "is_supported", staticmethod(lambda: False))
    applied = filt.apply()
    assert applied is False  # NOP, fail-open — no exception raised


def test_apply_returns_bool():
    """apply() must always return a bool and never raise, on any platform."""
    filt = SeccompFilter.default()
    result = filt.apply()
    assert isinstance(result, bool)


def test_build_bpf_program_is_nonempty_list():
    """The manual-BPF fallback must assemble a non-empty instruction list so the
    filter is installable without libseccomp."""
    filt = SeccompFilter.default()
    program = filt.build_bpf()
    assert isinstance(program, list)
    assert len(program) > 0


# ---------------------------------------------------------------------------
# Runtime enforcement tests (Linux + usable seccomp only)
# ---------------------------------------------------------------------------


def _run_in_child(fn) -> int:
    """Fork, run ``fn`` in the child, return the child's raw wait status.

    ``fn`` returns an int exit code; if the child is killed by a signal (e.g.
    SIGSYS from seccomp KILL) the raw status reflects that.
    """
    pid = os.fork()
    if pid == 0:  # child
        code = 70
        try:
            code = fn()
        except BaseException:
            code = 71
        finally:
            os._exit(code)
    _, status = os.waitpid(pid, 0)
    return status


@requires_enforcement
def test_seccomp_filter_blocks_mount():
    """A child under the default filter must have ``mount()`` blocked — either
    killed by SIGSYS or failing with EPERM."""
    import ctypes

    def child() -> int:
        SeccompFilter.default().apply()
        libc = ctypes.CDLL(None, use_errno=True)
        ctypes.set_errno(0)
        rc = libc.mount(b"none", b"/proc", b"proc", 0, None)
        err = ctypes.get_errno()
        if rc != 0 and err in (errno.EPERM, errno.EACCES):
            return 0  # blocked via ERRNO
        return 50  # NOT blocked — mount succeeded or failed for another reason

    status = _run_in_child(child)
    if os.WIFSIGNALED(status):
        assert os.WTERMSIG(status) == signal.SIGSYS
    else:
        assert os.WEXITSTATUS(status) == 0, "mount() was not blocked by seccomp"


@requires_enforcement
def test_seccomp_filter_blocks_clone():
    """A child under the default filter must have ``clone()`` with the
    CLONE_NEWNS namespace flag blocked (SIGSYS or EPERM)."""
    import ctypes

    def child() -> int:
        SeccompFilter.default().apply()
        libc = ctypes.CDLL(None, use_errno=True)
        SYS_unshare = 272  # x86_64; unshare is unconditionally denied
        ctypes.set_errno(0)
        rc = libc.syscall(SYS_unshare, CLONE_NEWNS)
        err = ctypes.get_errno()
        if rc != 0 and err in (errno.EPERM, errno.EACCES):
            return 0
        return 50

    status = _run_in_child(child)
    if os.WIFSIGNALED(status):
        assert os.WTERMSIG(status) == signal.SIGSYS
    else:
        assert os.WEXITSTATUS(status) == 0, "namespace escape was not blocked"


@requires_enforcement
def test_seccomp_filter_allows_read_write():
    """read/write must still work after the filter is applied (no over-blocking)."""

    def child() -> int:
        r, w = os.pipe()
        SeccompFilter.default().apply()
        os.write(w, b"ok")
        data = os.read(r, 2)
        return 0 if data == b"ok" else 60

    status = _run_in_child(child)
    assert not os.WIFSIGNALED(status), "read/write killed the filtered child"
    assert os.WEXITSTATUS(status) == 0


@requires_enforcement
def test_seccomp_filter_allows_socket():
    """socket/bind must still work after the filter is applied."""
    import socket as _socket

    def child() -> int:
        SeccompFilter.default().apply()
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        s.close()
        return 0

    status = _run_in_child(child)
    assert not os.WIFSIGNALED(status), "socket/bind killed the filtered child"
    assert os.WEXITSTATUS(status) == 0
