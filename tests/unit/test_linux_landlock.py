"""Unit tests for the Landlock (Linux LSM) sandbox backend.

These tests lift coverage on ``src/general_ludd/security/sandboxes/linux_landlock.py``
from ~24% by exercising:

  * The import-guard path in ``LandlockBackend.available()`` — pylandlock missing
    or unusable must report ``available() == False`` rather than silently no-op.
  * The ruleset construction in ``apply()`` (lines 89-200 of the source) via a
    synthetic ``landlock`` module installed into ``sys.modules`` — this lets the
    real construction code run on every CI platform, not just Linux.
  * The apply / verify / release lifecycle: file-cap rules, net-cap rules, the
    ``PR_SET_NO_NEW_PRIVS`` prctl, and the final ``rs.apply()`` call.
  * The failure paths: zero ABI, pylandlock ImportError, prctl failure, per-rule
    exceptions, hosts-without-ports warning, kernel-abi-too-old warning.
  * Two real-Linux tests (skipif-gated) that exercise the actual pylandlock
    binding when it is installed.

The trust anchors are:
  * ``test_apply_returns_applied_false_when_pylandlock_missing`` — fail-open contract.
  * ``test_apply_builds_ruleset_with_file_capabilities`` — the ruleset construction
    actually runs end-to-end against a mock landlock and produces ``applied=True``.
  * ``test_verify_returns_warn_finding_when_not_applied`` — applying without
    verifying is theater; verify must surface non-ok findings on a failed handle.
"""

from __future__ import annotations

import enum
import os
import sys
from typing import Any, ClassVar
from unittest import mock

import pytest

from general_ludd.security.permissions import Capability, PermissionSpec
from general_ludd.security.sandboxes import SandboxHandle, SandboxTarget

# ---------------------------------------------------------------------------
# Synthetic landlock module
# ---------------------------------------------------------------------------
#
# The source uses ``importlib.import_module("landlock")`` at runtime. Installing
# a fake module under ``sys.modules["landlock"]`` is therefore sufficient to
# exercise the real construction code on every CI platform — the import machinery
# consults ``sys.modules`` first and never touches the disk. IntFlag is used so
# the ``|``-combinations in ``_file_access_flags`` work natively.


class _FakeAccessFS(enum.IntFlag):
    EXECUTE = 1
    WRITE_FILE = 2
    READ_FILE = 4
    READ_DIR = 8
    MAKE_REG = 16
    REMOVE_FILE = 32
    MAKE_DIR = 64


class _FakeAccessNet(enum.IntFlag):
    BIND_TCP = 1
    CONNECT_TCP = 2


class _FakeRuleset:
    """Minimal stand-in for ``pylandlock.Ruleset``.

    Records every ``allow``/``allow_net`` call so tests can assert on the
    rules that were added. The constructor accepts both the old (no-arg) and
    new (handled_access_fs/hANDLED_ACCESS_NET) signatures used by the source.
    """

    instances: ClassVar[list[_FakeRuleset]] = []

    def __init__(
        self,
        handled_access_fs: Any = None,
        handled_access_net: Any = None,
    ) -> None:
        self.handled_access_fs = handled_access_fs
        self.handled_access_net = handled_access_net
        self.abi: int = 6  # default to ABI v6 (TCP net support)
        self.allowed_paths: list[tuple[str, Any]] = []
        self.allowed_nets: list[dict[str, Any]] = []
        self.applied: bool = False
        self.ruleset_fd: int = 42
        _FakeRuleset.instances.append(self)

    def allow(self, path: str, access: Any) -> None:
        self.allowed_paths.append((path, access))

    def allow_net(self, **kwargs: Any) -> None:
        self.allowed_nets.append(dict(kwargs))

    def apply(self) -> None:
        self.applied = True


def _install_fake_landlock(abi: int = 6) -> dict[str, Any]:
    """Install (or refresh) the fake ``landlock`` module in ``sys.modules``.

    Returns the module dict so a test can poke at it (e.g. raise from
    ``Ruleset`` to simulate a runtime error).
    """
    _FakeRuleset.instances.clear()
    module = mock.MagicMock()
    # The source uses ``landlock.AccessFs`` (lowercase 's') and ``AccessNet``
    # — matching the real pylandlock API. IntFlag is used so the ``|``-combinations
    # in ``_file_access_flags`` and ``apply()`` work natively.
    module.AccessFs = _FakeAccessFS
    module.AccessNet = _FakeAccessNet
    module.Ruleset = _FakeRuleset
    # The default Ruleset() returns an instance whose .abi is read by available().
    # _FakeRuleset.__init__ sets abi=6 by default; available() inspects rs.abi.
    sys.modules["landlock"] = module
    return {"module": module, "abi": abi}


@pytest.fixture(autouse=True)
def _restore_landlock_module():
    """Snapshot sys.modules so a test that installs a fake landlock cannot leak
    the fake into sibling tests (and cannot leak *removal* of the real one)."""
    snapshot = dict(sys.modules)
    yield
    # Restore: drop any new keys, reinstate any removed ones.
    for key in list(sys.modules.keys()):
        if key not in snapshot:
            del sys.modules[key]
    sys.modules.update(snapshot)


@pytest.fixture()
def _fake_landlock():
    """Install the fake landlock module for the duration of one test."""
    _install_fake_landlock()
    yield


@pytest.fixture()
def sample_spec() -> PermissionSpec:
    return PermissionSpec(
        agent_type="agent-42",
        capabilities=[
            Capability(
                resource="file:repo",
                actions=["read", "write"],
                constraints={"path_prefix": "/tmp/gludd/"},
            ),
            Capability(
                resource="net:egress",
                actions=["connect"],
                constraints={
                    "allowed_hosts": ["api.example.com"],
                    "allowed_ports": [443],
                },
            ),
        ],
        denied=[],
    )


@pytest.fixture()
def sample_target() -> SandboxTarget:
    return SandboxTarget(pid=99999, directory="/tmp/gludd/agent-42")


@pytest.fixture()
def _prctl_success():
    """Patch ``ctypes.CDLL`` so prctl returns 0 (success). The source imports
    ctypes inside ``apply()``, so we patch the symbol at the module root."""
    fake_libc = mock.MagicMock()
    fake_libc.prctl.return_value = 0
    fake_cdll = mock.MagicMock(return_value=fake_libc)
    with mock.patch("ctypes.CDLL", fake_cdll):
        yield fake_libc


# ---------------------------------------------------------------------------
# Import-guard / availability
# ---------------------------------------------------------------------------


def test_available_returns_false_when_landlock_not_importable():
    """available() must report False (not raise) when pylandlock is absent.

    This is the import-guard path: a missing optional dep must NOT silently
    no-op into ``True``; the daemon relies on this signal to skip the backend.
    """
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    sys.modules.pop("landlock", None)
    with mock.patch("importlib.import_module", side_effect=ImportError("no pylandlock")):
        assert LandlockBackend.available() is False


def test_available_returns_false_when_ruleset_constructor_raises():
    """If ``landlock.Ruleset()`` itself raises, available() catches it -> False."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    broken = mock.MagicMock()
    broken.Ruleset.side_effect = RuntimeError("kernel headers mismatch")
    sys.modules["landlock"] = broken
    assert LandlockBackend.available() is False


def test_available_returns_false_when_abi_is_zero(_fake_landlock):
    """abi == 0 means Landlock is compiled into the kernel but disabled."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    # Patch the Ruleset class so its instances report abi=0.
    landlock = sys.modules["landlock"]

    class _DisabledRuleset(_FakeRuleset):
        def __init__(self, *a, **kw) -> None:
            super().__init__(*a, **kw)
            self.abi = 0

    landlock.Ruleset = _DisabledRuleset
    assert LandlockBackend.available() is False


def test_available_returns_true_when_landlock_present_and_abi_supported(_fake_landlock):
    """Happy path: pylandlock importable + abi > 0 -> True."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    assert LandlockBackend.available() is True


# ---------------------------------------------------------------------------
# Helpers: _file_access_flags, _kernel_supports_net
# ---------------------------------------------------------------------------


def test_file_access_flags_returns_read_and_write_with_exec(_fake_landlock):
    """``_file_access_flags`` must return (read, write|exec) using the landlock
    module's AccessFS enum — this is lines 87-100 of the source."""
    from general_ludd.security.sandboxes.linux_landlock import _file_access_flags

    landlock = sys.modules["landlock"]
    read_flags, write_flags = _file_access_flags(landlock)
    # Read side must include READ_FILE + READ_DIR.
    assert _FakeAccessFS.READ_FILE in read_flags
    assert _FakeAccessFS.READ_DIR in read_flags
    # Write side must include WRITE_FILE + MAKE_REG + REMOVE_FILE + MAKE_DIR + EXECUTE.
    assert _FakeAccessFS.WRITE_FILE in write_flags
    assert _FakeAccessFS.MAKE_REG in write_flags
    assert _FakeAccessFS.REMOVE_FILE in write_flags
    assert _FakeAccessFS.MAKE_DIR in write_flags
    assert _FakeAccessFS.EXECUTE in write_flags


def test_kernel_supports_net_boundary():
    """Net rules require ABI >= 6 (kernel 6.7+). Below that -> False."""
    from general_ludd.security.sandboxes.linux_landlock import (
        ABI_NET_TCP,
        _kernel_supports_net,
    )

    assert ABI_NET_TCP == 6
    assert _kernel_supports_net(0) is False
    assert _kernel_supports_net(5) is False
    assert _kernel_supports_net(6) is True
    assert _kernel_supports_net(7) is True
    assert _kernel_supports_net(10) is True


def test_backend_kernel_supports_net_delegates_to_module_function():
    """The @staticmethod on the class must delegate to the module-level helper
    (kept for back-compat per source comment line 132-133)."""
    from general_ludd.security.sandboxes.linux_landlock import (
        LandlockBackend,
        _kernel_supports_net,
    )

    assert LandlockBackend._kernel_supports_net(6) is _kernel_supports_net(6)


# ---------------------------------------------------------------------------
# apply() — failure paths (fail-open contract)
# ---------------------------------------------------------------------------


def test_apply_returns_applied_false_when_pylandlock_missing(
    sample_spec,
    sample_target,
):
    """If the landlock module cannot be imported, apply() must return a
    SandboxHandle with applied=False — never raise. This is the contract that
    keeps the daemon alive when sandboxing is unavailable."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    sys.modules.pop("landlock", None)
    with mock.patch("importlib.import_module", side_effect=ImportError("no pylandlock")):
        handle = LandlockBackend.apply(sample_spec, sample_target)
    assert isinstance(handle, SandboxHandle)
    assert handle.applied is False
    assert handle.backend == "landlock"
    assert "pylandlock import failed" in handle.extra["reason"]


def test_apply_returns_applied_false_when_abi_zero(
    _fake_landlock,
    sample_spec,
    sample_target,
    _prctl_success,
):
    """abi == 0 means Landlock is disabled at the kernel — apply must fail-open."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    landlock = sys.modules["landlock"]

    class _DisabledRuleset(_FakeRuleset):
        def __init__(self, *a, **kw) -> None:
            super().__init__(*a, **kw)
            self.abi = 0

    landlock.Ruleset = _DisabledRuleset
    handle = LandlockBackend.apply(sample_spec, sample_target)
    assert handle.applied is False
    assert "abi=0" in handle.extra["reason"]


def test_apply_returns_applied_false_when_prctl_fails(
    _fake_landlock,
    sample_spec,
    sample_target,
):
    """If prctl(PR_SET_NO_NEW_PRIVS) returns nonzero errno, apply must fail-open.

    This is the guardrail that prevents a setuid binary from later escalating
    out of the sandbox — without it, the sandbox is bypassable and we refuse
    to claim ``applied=True``.
    """
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    fake_libc = mock.MagicMock()
    fake_libc.prctl.return_value = -1
    with mock.patch("ctypes.CDLL", return_value=fake_libc), mock.patch("ctypes.get_errno", return_value=1):
        handle = LandlockBackend.apply(sample_spec, sample_target)
    assert handle.applied is False
    assert "PR_SET_NO_NEW_PRIVS" in handle.extra["reason"]
    # The fake ruleset must NOT have had .apply() called.
    assert all(not rs.applied for rs in _FakeRuleset.instances)


def test_apply_returns_applied_false_when_rs_apply_raises(
    _fake_landlock,
    sample_spec,
    sample_target,
    _prctl_success,
):
    """If rs.apply() (the landlock_restrict_self call) raises, fail-open."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    landlock = sys.modules["landlock"]

    class _BoomRuleset(_FakeRuleset):
        def apply(self) -> None:
            raise OSError("ENOMEM in landlock_restrict_self")

    landlock.Ruleset = _BoomRuleset
    handle = LandlockBackend.apply(sample_spec, sample_target)
    assert handle.applied is False


# ---------------------------------------------------------------------------
# apply() — ruleset construction (the core of the untested 89-200 region)
# ---------------------------------------------------------------------------


def test_apply_builds_ruleset_with_file_capabilities(
    _fake_landlock,
    sample_spec,
    sample_target,
    _prctl_success,
):
    """The happy path: file caps produce ``rs.allow(prefix, access)`` calls and
    the final handle reports applied=True with the recorded extras."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    # Strip the net cap so we exercise just the file path here.
    spec = PermissionSpec(
        agent_type="agent-42",
        capabilities=[
            Capability(
                resource="file:repo",
                actions=["read", "write"],
                constraints={"path_prefix": "/tmp/gludd/"},
            ),
        ],
        denied=[],
    )
    handle = LandlockBackend.apply(spec, sample_target)

    assert handle.applied is True
    assert handle.token == "gludd-agent-42"
    assert handle.extra["abi"] == 6
    assert "/tmp/gludd/" in handle.extra["allowed_paths"]
    assert handle.extra["allowed_ports"] == []
    assert handle.extra["ruleset_fd"] == 42
    assert handle.extra["irreversible"] is True

    # The fake ruleset must have been constructed with handled_access_fs
    # covering read|write|exec, and had .allow() called once for the file cap.
    assert len(_FakeRuleset.instances) >= 1
    rs = _FakeRuleset.instances[-1]
    assert rs.applied is True
    assert rs.handled_access_fs is not None
    assert len(rs.allowed_paths) == 1
    prefix, access = rs.allowed_paths[0]
    assert prefix == "/tmp/gludd/"
    # write cap -> write_flags (WRITE_FILE + MAKE_REG + REMOVE_FILE + MAKE_DIR + EXECUTE)
    assert _FakeAccessFS.WRITE_FILE in access
    assert _FakeAccessFS.EXECUTE in access


def test_apply_uses_read_flags_for_read_only_cap(
    _fake_landlock,
    sample_target,
    _prctl_success,
):
    """A read-only file cap must use the read flag set, NOT the write set."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    spec = PermissionSpec(
        agent_type="agent-42",
        capabilities=[
            Capability(
                resource="file:logs",
                actions=["read"],
                constraints={"path_prefix": "/var/log/gludd/"},
            ),
        ],
        denied=[],
    )
    handle = LandlockBackend.apply(spec, sample_target)
    assert handle.applied is True

    rs = _FakeRuleset.instances[-1]
    prefix, access = rs.allowed_paths[0]
    assert prefix == "/var/log/gludd/"
    assert _FakeAccessFS.READ_FILE in access
    assert _FakeAccessFS.READ_DIR in access
    # Must NOT include write flags.
    assert _FakeAccessFS.WRITE_FILE not in access


def test_apply_uses_execute_flags_for_execute_cap(
    _fake_landlock,
    sample_target,
    _prctl_success,
):
    """An execute cap must use read_flags | EXECUTE."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    spec = PermissionSpec(
        agent_type="agent-42",
        capabilities=[
            Capability(
                resource="file:bin",
                actions=["execute"],
                constraints={"path_prefix": "/usr/local/bin/"},
            ),
        ],
        denied=[],
    )
    handle = LandlockBackend.apply(spec, sample_target)
    assert handle.applied is True
    _, access = _FakeRuleset.instances[-1].allowed_paths[0]
    assert _FakeAccessFS.EXECUTE in access
    assert _FakeAccessFS.READ_FILE in access


def test_apply_adds_net_rules_when_kernel_supports_net(
    _fake_landlock,
    sample_target,
    _prctl_success,
):
    """abi >= 6 + net cap with ports -> rs.allow_net(port=..., access=CONNECT_TCP)."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    spec = PermissionSpec(
        agent_type="agent-42",
        capabilities=[
            Capability(
                resource="net:egress",
                actions=["connect"],
                constraints={"allowed_ports": [443, 8443]},
            ),
        ],
        denied=[],
    )
    handle = LandlockBackend.apply(spec, sample_target)
    assert handle.applied is True
    assert handle.extra["allowed_ports"] == [443, 8443]

    rs = _FakeRuleset.instances[-1]
    # Two allow_net calls, one per port.
    assert len(rs.allowed_nets) == 2
    ports_called = sorted(n["port"] for n in rs.allowed_nets)
    assert ports_called == [443, 8443]
    # Each must use CONNECT_TCP access.
    for call in rs.allowed_nets:
        assert call["access"] == _FakeAccessNet.CONNECT_TCP


def test_apply_warns_when_net_hosts_without_ports(
    _fake_landlock,
    sample_target,
    _prctl_success,
    caplog,
):
    """Landlock filters on ports, not DNS names. Hosts-without-ports must log a
    warning and skip the rule (caller must pair with seccomp / eBPF)."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    spec = PermissionSpec(
        agent_type="agent-42",
        capabilities=[
            Capability(
                resource="net:egress",
                actions=["connect"],
                constraints={"allowed_hosts": ["internal.svc"]},
            ),
        ],
        denied=[],
    )
    with caplog.at_level("WARNING"):
        handle = LandlockBackend.apply(spec, sample_target)

    assert handle.applied is True
    # Hostname-only cap is recorded as unhandled so verify() can surface it.
    assert handle.extra["unhandled_net_hosts"] == ["internal.svc"]
    rs = _FakeRuleset.instances[-1]
    assert rs.allowed_nets == []
    assert any("hostname" in r.getMessage().lower() for r in caplog.records)


def test_apply_warns_when_kernel_abi_below_net_support(
    sample_target,
    _prctl_success,
    caplog,
):
    """abi < 6 + net cap with ports -> warn that kernel cannot enforce net rules."""
    _install_fake_landlock(abi=5)
    landlock = sys.modules["landlock"]

    class _OldKernelRuleset(_FakeRuleset):
        def __init__(self, *a, **kw) -> None:
            super().__init__(*a, **kw)
            self.abi = 5

    landlock.Ruleset = _OldKernelRuleset

    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    spec = PermissionSpec(
        agent_type="agent-42",
        capabilities=[
            Capability(
                resource="net:egress",
                actions=["connect"],
                constraints={"allowed_ports": [443]},
            ),
        ],
        denied=[],
    )
    with caplog.at_level("WARNING"):
        handle = LandlockBackend.apply(spec, sample_target)
    assert handle.applied is True
    rs = _FakeRuleset.instances[-1]
    assert rs.allowed_nets == []
    assert any("does not support net rules" in r.getMessage() for r in caplog.records)


def test_apply_handles_file_rule_exception(
    _fake_landlock,
    sample_target,
    _prctl_success,
    caplog,
):
    """If rs.allow() raises for one path, the exception is logged and the rest
    of the ruleset is still built + applied."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    landlock = sys.modules["landlock"]

    class _PartialRuleset(_FakeRuleset):
        def allow(self, path: str, access: Any) -> None:
            if "badpath" in path:
                raise OSError("permission denied")
            super().allow(path, access)

    landlock.Ruleset = _PartialRuleset

    spec = PermissionSpec(
        agent_type="agent-42",
        capabilities=[
            Capability(
                resource="file:bad",
                actions=["read"],
                constraints={"path_prefix": "/badpath/"},
            ),
            Capability(
                resource="file:good",
                actions=["read"],
                constraints={"path_prefix": "/tmp/good/"},
            ),
        ],
        denied=[],
    )
    with caplog.at_level("WARNING"):
        handle = LandlockBackend.apply(spec, sample_target)

    assert handle.applied is True
    rs = _FakeRuleset.instances[-1]
    # The good path was added; the bad path was logged + skipped.
    prefixes = [p for p, _ in rs.allowed_paths]
    assert "/tmp/good/" in prefixes
    assert "/badpath/" not in prefixes
    assert handle.extra["allowed_paths"] == ["/tmp/good/"]
    assert any("could not allow" in r.getMessage() for r in caplog.records)


def test_apply_handles_net_rule_exception(
    _fake_landlock,
    sample_target,
    _prctl_success,
    caplog,
):
    """If rs.allow_net() raises for one port, log + skip; remaining ports still apply."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    landlock = sys.modules["landlock"]

    class _PartialNetRuleset(_FakeRuleset):
        def allow_net(self, **kwargs: Any) -> None:
            if kwargs.get("port") == 666:
                raise OSError("port in use")
            super().allow_net(**kwargs)

    landlock.Ruleset = _PartialNetRuleset

    spec = PermissionSpec(
        agent_type="agent-42",
        capabilities=[
            Capability(
                resource="net:egress",
                actions=["connect"],
                constraints={"allowed_ports": [80, 666, 443]},
            ),
        ],
        denied=[],
    )
    with caplog.at_level("WARNING"):
        handle = LandlockBackend.apply(spec, sample_target)

    assert handle.applied is True
    # The two good ports are recorded; 666 was skipped.
    assert sorted(handle.extra["allowed_ports"]) == [80, 443]
    assert any("could not allow port 666" in r.getMessage() for r in caplog.records)


def test_apply_skips_file_cap_with_no_path_prefix(
    _fake_landlock,
    sample_target,
    _prctl_success,
):
    """A file cap without a path_prefix constraint is silently skipped."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    spec = PermissionSpec(
        agent_type="agent-42",
        capabilities=[
            Capability(
                resource="file:repo",
                actions=["read"],
                # No constraints at all
            ),
        ],
        denied=[],
    )
    handle = LandlockBackend.apply(spec, sample_target)
    assert handle.applied is True
    rs = _FakeRuleset.instances[-1]
    assert rs.allowed_paths == []
    assert handle.extra["allowed_paths"] == []


def test_apply_ruleset_name_includes_agent_type(
    _fake_landlock,
    sample_target,
    _prctl_success,
):
    """The token (ruleset name) embeds the agent_type for audit traceability."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    spec = PermissionSpec(agent_type="primary-agent", capabilities=[], denied=[])
    handle = LandlockBackend.apply(spec, sample_target)
    assert handle.token == "gludd-primary-agent"


# ---------------------------------------------------------------------------
# apply() — the PR_SET_NO_NEW_PRIVS call
# ---------------------------------------------------------------------------


def test_apply_invokes_prctl_with_no_new_privs_constant(
    _fake_landlock,
    sample_spec,
    sample_target,
    _prctl_success,
):
    """prctl MUST be called with PR_SET_NO_NEW_PRIVS (=38) + arg=1 before rs.apply()."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    LandlockBackend.apply(sample_spec, sample_target)
    _prctl_success.prctl.assert_called_once()
    args, _ = _prctl_success.prctl.call_args
    # PR_SET_NO_NEW_PRIVS = 38, arg2 = 1
    assert args[0] == 38
    assert args[1] == 1


# ---------------------------------------------------------------------------
# verify()
# ---------------------------------------------------------------------------


def test_verify_returns_warn_finding_when_not_applied(sample_spec):
    """The trust anchor: an applied=False handle must produce a non-ok finding.
    Applying a sandbox without verifying is theater."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    handle = SandboxHandle(
        backend="landlock",
        token="gludd-agent-42",
        applied=False,
        extra={"reason": "pylandlock import failed: boom"},
    )
    findings = LandlockBackend.verify(sample_spec, handle)
    assert findings, "verify() returned no findings on a failed handle"
    assert any(f.severity == "warn" for f in findings)
    # And it short-circuits — exactly one finding when applied=False.
    assert len(findings) == 1


def test_verify_returns_ok_finding_when_applied(sample_spec):
    """applied=True + no unhandled hosts -> single 'ok' finding noting irreversibility."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    handle = SandboxHandle(
        backend="landlock",
        token="gludd-agent-42",
        applied=True,
        extra={"abi": 6, "unhandled_net_hosts": []},
    )
    findings = LandlockBackend.verify(sample_spec, handle)
    assert len(findings) == 1
    assert findings[0].severity == "ok"
    assert "irrevers" in findings[0].message.lower()


def test_verify_warns_about_unhandled_net_hosts(sample_spec):
    """applied=True + unhandled_net_hosts -> ok finding + additional warn finding."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    handle = SandboxHandle(
        backend="landlock",
        token="gludd-agent-42",
        applied=True,
        extra={
            "abi": 6,
            "unhandled_net_hosts": ["internal.svc", "db.internal"],
        },
    )
    findings = LandlockBackend.verify(sample_spec, handle)
    severities = [f.severity for f in findings]
    assert "ok" in severities
    assert "warn" in severities
    warn = next(f for f in findings if f.severity == "warn")
    assert "internal.svc" in warn.message
    assert "seccomp" in warn.message.lower() or "ebpf" in warn.message.lower()


# ---------------------------------------------------------------------------
# release()
# ---------------------------------------------------------------------------


def test_release_closes_positive_ruleset_fd():
    """release() must os.close() a positive ruleset_fd (tidiness, not lifting
    restrictions — those are irreversible)."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    handle = SandboxHandle(
        backend="landlock",
        token="gludd-agent-42",
        applied=True,
        extra={"ruleset_fd": 99},
    )
    with mock.patch("general_ludd.security.sandboxes.linux_landlock.os.close") as close:
        LandlockBackend.release(handle)
    close.assert_called_once_with(99)


def test_release_does_not_close_when_fd_missing_or_negative():
    """release() must be a no-op when there is no ruleset_fd or it is <= 0."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    # Missing fd entirely.
    handle_no_fd = SandboxHandle(
        backend="landlock",
        token="x",
        applied=True,
        extra={},
    )
    # Negative fd.
    handle_neg_fd = SandboxHandle(
        backend="landlock",
        token="x",
        applied=True,
        extra={"ruleset_fd": -1},
    )
    with mock.patch("general_ludd.security.sandboxes.linux_landlock.os.close") as close:
        LandlockBackend.release(handle_no_fd)
        LandlockBackend.release(handle_neg_fd)
    close.assert_not_called()


def test_release_swallows_osclose_errors():
    """release() must never raise — an OSError from os.close is suppressed."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    handle = SandboxHandle(
        backend="landlock",
        token="x",
        applied=True,
        extra={"ruleset_fd": 5},
    )
    with mock.patch("general_ludd.security.sandboxes.linux_landlock.os.close", side_effect=OSError("bad fd")):
        # Must not raise.
        LandlockBackend.release(handle)


# ---------------------------------------------------------------------------
# Structural invariants
# ---------------------------------------------------------------------------


def test_backend_name_is_landlock():
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    assert LandlockBackend.name == "landlock"


def test_backend_satisfies_protocol():
    """LandlockBackend must expose available/apply/verify/release (the
    SandboxBackend Protocol)."""
    from general_ludd.security.sandboxes import SandboxBackend
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    for attr in ("available", "apply", "verify", "release"):
        assert callable(getattr(LandlockBackend, attr, None)), f"LandlockBackend missing Protocol method: {attr}"
    # The Protocol is @runtime_checkable; isinstance should pass.
    assert isinstance(LandlockBackend, SandboxBackend)


def test_irreversibility_sentinel_text():
    """The irreversibility sentinel is the verify() trust message; lock its wording."""
    from general_ludd.security.sandboxes.linux_landlock import (
        LANDLOCK_RESTRICTIONS_ARE_IRREVERSIBLE,
    )

    assert "IRREVERSIBLE" in LANDLOCK_RESTRICTIONS_ARE_IRREVERSIBLE
    assert "release()" in LANDLOCK_RESTRICTIONS_ARE_IRREVERSIBLE


# ---------------------------------------------------------------------------
# Real-Linux tests (skipif-gated — only run when pylandlock can actually import)
# ---------------------------------------------------------------------------


_LINUX = sys.platform == "linux"


@pytest.mark.skipif(not _LINUX, reason="Landlock is Linux-only")
def test_real_available_does_not_raise_on_linux():
    """On Linux the real ``available()`` must return a bool (True or False
    depending on whether pylandlock + a Landlock-capable kernel are present)
    and must never raise."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    # No mocks — exercise the real import path.
    result = LandlockBackend.available()
    assert isinstance(result, bool)


@pytest.mark.skipif(
    not _LINUX or os.environ.get("GLUDD_LANDLOCK_LIVE_TEST") != "1",
    reason=(
        "Real Landlock apply is IRREVERSIBLE per-process (restrict_self); it "
        "must never run on shared CI runners. Set GLUDD_LANDLOCK_LIVE_TEST=1 "
        "on a dedicated Linux host to exercise the real kernel path."
    ),
)
def test_real_apply_fails_open_if_pylandlock_missing_on_linux(
    sample_spec,
    sample_target,
):
    """If pylandlock is genuinely not installed on this Linux host, apply()
    must fail-open (applied=False) rather than raise. If pylandlock IS
    installed, this test still passes — the only assertion is the contract."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    # If pylandlock is actually missing, import_module will raise ImportError
    # and apply() must catch it. If it's present, apply() will proceed and
    # exercise the real kernel path (possibly returning applied=False if the
    # kernel ABI is 0 or prctl fails — either way the contract holds).
    sys.modules.pop("landlock", None)
    try:
        handle = LandlockBackend.apply(sample_spec, sample_target)
    except Exception as exc:
        pytest.fail(f"LandlockBackend.apply raised on real Linux: {exc!r}")
    assert isinstance(handle, SandboxHandle)
    assert isinstance(handle.applied, bool)
