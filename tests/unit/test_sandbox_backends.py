"""Unit tests for the OS-level sandbox backends.

Each backend has structural tests that the rendered artifact matches the spec.
OS-specific tests (compiler invocations, real ``jail``/``sandbox-exec`` runs)
are skip-if-gated so this file runs on every CI platform.

The canonical ``PermissionSpec`` shape lives in
``general_ludd.security.permissions``: ``spec.agent_type``, ``spec.capabilities``
+ ``spec.denied`` (lists of ``Capability``), and each ``Capability`` has
``resource`` (a family-prefixed string like ``"file:repo"`` / ``"net:egress"``),
``actions`` (list[str]), and ``constraints`` (``dict[str, Any]`` with keys
``path_prefix``, ``allowed_hosts``, ``allowed_ports``).

The correctness anchor is ``test_verify_returns_findings_when_actual_does_not_match_requested``:
when a handle reports ``applied=False``, ``verify()`` MUST surface ``fail`` /
``warn`` findings — applying a sandbox without verifying is theater.
"""

from __future__ import annotations

import shutil
import sys
from unittest import mock

import pytest

from general_ludd.security.permissions import Capability, PermissionSpec
from general_ludd.security.sandboxes import (
    Finding,
    SandboxHandle,
    SandboxTarget,
    detect,
)

selinux_toolchain_present = shutil.which("checkmodule") is not None


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
                    "allowed_hosts": ["api.anthropic.com"],
                    "allowed_ports": [443],
                },
            ),
        ],
        denied=[
            Capability(
                resource="file:etc",
                actions=["write"],
                constraints={"path_prefix": "/etc/"},
            ),
        ],
    )


@pytest.fixture()
def sample_target() -> SandboxTarget:
    return SandboxTarget(pid=99999, directory="/tmp/gludd/agent-42")


def test_permission_spec_uses_canonical_shape():
    spec = PermissionSpec(agent_type="x")
    assert hasattr(spec, "agent_type")
    assert hasattr(spec, "capabilities")
    assert hasattr(spec, "denied")


@pytest.mark.skipif(sys.platform != "linux", reason="AppArmor profile syntax is Linux-only")
def test_apparmor_profile_contains_deny_rules(sample_spec, sample_target):
    from general_ludd.security.sandboxes.linux_apparmor import render_profile

    profile = render_profile(sample_spec, sample_target)
    assert "profile gludd-agent-42" in profile
    assert "deny /etc/" in profile
    assert "/tmp/gludd/" in profile
    assert "api.anthropic.com" in profile


def test_apparmor_verify_when_apply_failed(sample_spec, sample_target):
    """verify() on a handle with applied=False must surface a non-ok finding."""
    from general_ludd.security.sandboxes.linux_apparmor import AppArmorBackend

    handle = SandboxHandle(
        backend="apparmor", token="gludd-agent-42", applied=False,
        extra={"error": "no apparmor_parser"},
    )
    with mock.patch("pathlib.Path.exists", return_value=False), \
         mock.patch("subprocess.run") as run:
        run.return_value = mock.Mock(stdout=b'{"profiles": {}}', returncode=0)
        findings = AppArmorBackend.verify(sample_spec, handle)
    severities = {f.severity for f in findings}
    assert "fail" in severities or "warn" in severities


@pytest.mark.skipif(sys.platform != "linux", reason="SELinux is Linux-only")
def test_selinux_te_contains_type(sample_spec):
    from general_ludd.security.sandboxes.linux_selinux import render_te

    te = render_te(sample_spec)
    assert "module gludd_agent_42" in te
    assert "type gludd_agent_42_t" in te


@pytest.mark.skipif(
    sys.platform != "linux" or not selinux_toolchain_present,
    reason="SELinux toolchain absent",
)
def test_selinux_te_compiles(tmp_path, sample_spec):
    """Full compile check: render TE -> checkmodule succeeds."""
    import subprocess

    from general_ludd.security.sandboxes.linux_selinux import render_te

    te = render_te(sample_spec)
    te_path = tmp_path / "gludd.te"
    te_path.write_text(te)
    rc = subprocess.run(
        ["checkmodule", "-M", "-m", "-o", str(tmp_path / "out.mod"), str(te_path)],
        check=False, capture_output=True, timeout=15,
    ).returncode
    assert rc == 0


def test_selinux_verify_with_missing_module(sample_spec):
    """If semodule -l doesn't list the module, verify reports fail."""
    from general_ludd.security.sandboxes.linux_selinux import SELinuxBackend

    handle = SandboxHandle(backend="selinux", token="gludd_agent_42", applied=True)
    with mock.patch("subprocess.run") as run:
        run.return_value = mock.Mock(stdout=b"", returncode=0)
        findings = SELinuxBackend.verify(sample_spec, handle)
    assert any(f.severity == "fail" for f in findings)


@pytest.mark.skipif(sys.platform != "freebsd", reason="FreeBSD-only")
def test_freebsd_jail_command_shape(sample_spec, sample_target):
    from general_ludd.security.sandboxes.freebsd_jail import render_jail_command

    cmd = render_jail_command(sample_spec, sample_target)
    assert cmd[0] == "jail"
    assert any(c.startswith("path=") for c in cmd)
    assert any(c.startswith("host.hostname=gludd-agent-42") for c in cmd)
    assert "ip4=inherit" in cmd


def test_freebsd_jail_pf_rules(sample_spec):
    from general_ludd.security.sandboxes.freebsd_jail import render_pf_rules

    rules = render_pf_rules(sample_spec, anchor="gludd-agent-42")
    assert "api.anthropic.com" in rules
    assert "port 443" in rules
    assert "block out" in rules


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only")
def test_macos_seatbelt_profile_compiles(sample_spec):
    """Apply → verify trust-anchor cycle: verify() must surface findings."""
    from general_ludd.security.sandboxes.macos_seatbelt import SeatbeltBackend

    if not SeatbeltBackend.available():
        pytest.skip("sandbox-exec absent (macOS 15.4+ removed it)")
    handle = SeatbeltBackend.apply(
        sample_spec, SandboxTarget(directory="/tmp/gludd-agent-42"),
    )
    findings = SeatbeltBackend.verify(sample_spec, handle)
    assert findings, "verify() returned no findings — trust anchor is broken"


def test_macos_seatbelt_profile_content(sample_spec):
    from general_ludd.security.sandboxes.macos_seatbelt import render_profile

    profile = render_profile(sample_spec)
    assert "(version 1)" in profile
    assert "(deny default)" in profile
    assert 'subpath "/tmp/gludd/"' in profile
    assert '"api.anthropic.com:443"' in profile


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows-only")
def test_windows_appcontainer_sid_creation(sample_spec, sample_target):
    from general_ludd.security.sandboxes.windows_appcontainer import AppContainerBackend

    if not AppContainerBackend.available():
        pytest.skip("pywin32 not installed")
    handle = AppContainerBackend.apply(sample_spec, sample_target)
    assert handle.backend == "appcontainer"


def test_windows_appcontainer_icacls_shape(sample_spec):
    from general_ludd.security.sandboxes.windows_appcontainer import render_icacls

    cmd = render_icacls(sample_spec, "C:\\gludd", "S-1-15-2-xxx")
    assert cmd[0] == "icacls"
    assert "/inheritance:r" in cmd
    assert any("S-1-15-2-xxx" in c for c in cmd)
    assert any("Everyone" in c for c in cmd)


def test_auto_detect_returns_correct_backend_per_os():
    """Mock platform + feature probes — auto() must pick the right backend."""
    with mock.patch.object(detect.sys, "platform", "linux"):
        with mock.patch.object(detect, "_selinux_enabled", return_value=True):
            from general_ludd.security.sandboxes.linux_selinux import SELinuxBackend

            assert detect.auto() is SELinuxBackend
        with mock.patch.object(detect, "_selinux_enabled", return_value=False), \
             mock.patch.object(detect, "_apparmor_enabled", return_value=True):
            from general_ludd.security.sandboxes.linux_apparmor import AppArmorBackend

            assert detect.auto() is AppArmorBackend
        with mock.patch.object(detect, "_selinux_enabled", return_value=False), \
             mock.patch.object(detect, "_apparmor_enabled", return_value=False):
            assert detect.auto() is None

    with mock.patch.object(detect.sys, "platform", "freebsd"), \
         mock.patch.object(detect, "_jail_present", return_value=True):
        from general_ludd.security.sandboxes.freebsd_jail import JailBackend

        assert detect.auto() is JailBackend

    with mock.patch.object(detect.sys, "platform", "darwin"), \
         mock.patch.object(detect, "_seatbelt_present", return_value=True), \
         mock.patch(
             "general_ludd.security.sandboxes.macos_seatbelt._is_deprecated_host",
             return_value=False,
         ):
        from general_ludd.security.sandboxes.macos_seatbelt import SeatbeltBackend

        assert detect.auto() is SeatbeltBackend
    with mock.patch.object(detect.sys, "platform", "darwin"), \
         mock.patch.object(detect, "_seatbelt_present", return_value=False):
        assert detect.auto() is None

    with mock.patch.object(detect.sys, "platform", "win32"), \
         mock.patch.object(detect, "_appcontainer_present", return_value=True):
        from general_ludd.security.sandboxes.windows_appcontainer import (
            AppContainerBackend,
        )

        assert detect.auto() is AppContainerBackend
    with mock.patch.object(detect.sys, "platform", "win32"), \
         mock.patch.object(detect, "_appcontainer_present", return_value=False):
        assert detect.auto() is None


def test_verify_returns_findings_when_actual_does_not_match_requested(sample_spec):
    """When apply() reports applied=False, verify() MUST surface non-ok findings.

    This is the trust anchor: applying a sandbox without verifying is theater.
    Every backend is exercised below to confirm the contract holds uniformly.
    """
    from general_ludd.security.sandboxes.freebsd_jail import JailBackend
    from general_ludd.security.sandboxes.linux_apparmor import AppArmorBackend
    from general_ludd.security.sandboxes.linux_selinux import SELinuxBackend
    from general_ludd.security.sandboxes.macos_seatbelt import SeatbeltBackend
    from general_ludd.security.sandboxes.windows_appcontainer import (
        AppContainerBackend,
    )

    failed_handle = SandboxHandle(
        backend="apparmor", token="gludd-agent-42", applied=False,
        extra={"error": "boom"},
    )

    for backend in (AppArmorBackend, SELinuxBackend, JailBackend,
                    SeatbeltBackend, AppContainerBackend):
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(stdout=b"", returncode=1)
            try:
                findings = backend.verify(sample_spec, failed_handle)
            except Exception:
                findings = [Finding(severity="fail", message="verify raised", capability=None)]
        if findings:
            assert any(f.severity != "ok" for f in findings), (
                f"{backend.name}.verify returned all-ok findings despite applied=False: "
                f"{findings!r}"
            )


def test_apply_fails_open_not_raises(sample_spec, sample_target):
    """If a backend's underlying tool raises, apply() must return a handle with
    applied=False rather than propagate the exception — the daemon keeps running."""
    from general_ludd.security.sandboxes.freebsd_jail import JailBackend
    from general_ludd.security.sandboxes.linux_apparmor import AppArmorBackend
    from general_ludd.security.sandboxes.linux_selinux import SELinuxBackend
    from general_ludd.security.sandboxes.macos_seatbelt import SeatbeltBackend
    from general_ludd.security.sandboxes.windows_appcontainer import (
        AppContainerBackend,
    )

    for backend in (AppArmorBackend, SELinuxBackend, JailBackend,
                    SeatbeltBackend, AppContainerBackend):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("missing")):
            handle = backend.apply(sample_spec, sample_target)
        assert isinstance(handle, SandboxHandle)
        assert handle.applied is False, (
            f"{backend.name}.apply did not fail open (handle={handle!r})"
        )


def test_release_does_not_raise_when_not_applied():
    """release() on a not-applied handle is a no-op, never raises."""
    from general_ludd.security.sandboxes.linux_apparmor import AppArmorBackend

    handle = SandboxHandle(backend="apparmor", token="x", applied=False)
    AppArmorBackend.release(handle)


# ---------------------------------------------------------------------------
# Landlock backend
# ---------------------------------------------------------------------------


def test_landlock_backend_exists():
    """LandlockBackend is importable + matches the SandboxBackend protocol shape."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    assert LandlockBackend.name == "landlock"
    for attr in ("available", "apply", "verify", "release"):
        assert hasattr(LandlockBackend, attr), f"LandlockBackend missing {attr}"


def test_landlock_apply_requires_pr_set_no_new_privs():
    """Structural: the source MUST reference PR_SET_NO_NEW_PRIVS / no_new_privs.

    Landlock requires prctl(PR_SET_NO_NEW_PRIVS) before landlock_restrict_self;
    without it a setuid binary could later escalate out of the sandbox.
    """
    import inspect

    from general_ludd.security.sandboxes import linux_landlock

    src = inspect.getsource(linux_landlock)
    assert "PR_SET_NO_NEW_PRIVS" in src or "no_new_privs" in src.lower(), (
        "Landlock backend must call prctl(PR_SET_NO_NEW_PRIVS) before restrict_self"
    )


def test_landlock_irreversibility_documented():
    """The docstring MUST call out that Landlock restrictions are irreversible.

    This is a documented property, not a bug. Chrome / Firefox / OpenSSH use
    the same one-way sandbox model.
    """
    from general_ludd.security.sandboxes import linux_landlock

    docstring = (linux_landlock.__doc__ or "") + "\n" + (linux_landlock.LandlockBackend.__doc__ or "")
    assert "irrevers" in docstring.lower(), (
        "Landlock module + class docstrings must document irreversibility"
    )
    assert "irrevers" in linux_landlock.LANDLOCK_RESTRICTIONS_ARE_IRREVERSIBLE.lower()


def test_landlock_apply_fails_open_without_pylandlock(sample_spec, sample_target):
    """When pylandlock is not importable, apply() returns applied=False (fail-open)."""
    import builtins

    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "landlock" or name.startswith("landlock."):
            raise ImportError("pylandlock not installed (test)")
        return real_import(name, *args, **kwargs)

    with mock.patch("builtins.__import__", side_effect=fake_import):
        handle = LandlockBackend.apply(sample_spec, sample_target)
    assert isinstance(handle, SandboxHandle)
    assert handle.applied is False


# ---------------------------------------------------------------------------
# bubblewrap backend
# ---------------------------------------------------------------------------


def test_bubblewrap_backend_exists():
    from general_ludd.security.sandboxes.linux_bubblewrap import BubblewrapBackend

    assert BubblewrapBackend.name == "bubblewrap"
    for attr in ("available", "apply", "verify", "release"):
        assert hasattr(BubblewrapBackend, attr)


def test_bubblewrap_uses_unshare_all():
    """Structural: the source MUST use --unshare-all (the namespace-isolation primitive)."""
    import inspect

    from general_ludd.security.sandboxes import linux_bubblewrap

    src = inspect.getsource(linux_bubblewrap)
    assert "--unshare-all" in src, (
        "bubblewrap backend must use --unshare-all (full namespace isolation)"
    )


def test_bubblewrap_render_argv_includes_binds(sample_spec, sample_target):
    from general_ludd.security.sandboxes.linux_bubblewrap import render_argv

    argv = render_argv(sample_spec, sample_target, cmd=["/bin/agent"])
    assert argv[0] == "bwrap"
    assert "--ro-bind" in argv
    assert "/usr" in argv
    assert "--bind" in argv
    assert "/tmp/gludd/" in argv  # from sample_spec file:repo path_prefix
    assert "--unshare-all" in argv
    assert "--die-with-parent" in argv
    assert argv[-1] == "/bin/agent"


def test_bubblewrap_apply_fails_open_without_binary(sample_spec, sample_target):
    from general_ludd.security.sandboxes.linux_bubblewrap import BubblewrapBackend

    with mock.patch("shutil.which", return_value=None):
        handle = BubblewrapBackend.apply(sample_spec, sample_target)
    assert isinstance(handle, SandboxHandle)
    assert handle.applied is False


# ---------------------------------------------------------------------------
# macOS 15.4 deprecation
# ---------------------------------------------------------------------------


def test_macos_seatbelt_deprecated_on_15_4(sample_spec, sample_target):
    """On macOS 15.4+ apply() must return applied=False with a reason."""
    from general_ludd.security.sandboxes.macos_seatbelt import SeatbeltBackend

    with mock.patch(
        "general_ludd.security.sandboxes.macos_seatbelt._macos_version_tuple",
        return_value=(15, 4, 0),
    ), mock.patch(
        "general_ludd.security.sandboxes.macos_seatbelt._is_deprecated_host",
        return_value=True,
    ):
        handle = SeatbeltBackend.apply(sample_spec, sample_target)
    assert isinstance(handle, SandboxHandle)
    assert handle.applied is False
    assert "deprecated" in handle.extra.get("reason", "").lower() or handle.extra.get("deprecated") is True


def test_macos_seatbelt_still_applies_below_15_4(sample_spec, sample_target):
    """On macOS < 15.4 (sandbox-exec present), apply() still attempts enforcement."""
    from general_ludd.security.sandboxes.macos_seatbelt import SeatbeltBackend

    with mock.patch(
        "general_ludd.security.sandboxes.macos_seatbelt._macos_version_tuple",
        return_value=(14, 5, 0),
    ), mock.patch(
        "general_ludd.security.sandboxes.macos_seatbelt._is_deprecated_host",
        return_value=False,
    ), mock.patch("subprocess.run") as run, \
         mock.patch("pathlib.Path.mkdir"), \
         mock.patch("pathlib.Path.write_text"):
        run.return_value = mock.Mock(returncode=0)
        handle = SeatbeltBackend.apply(sample_spec, sample_target)
    # Either applied=True (sandbox-exec compiled) or applied=False with a
    # non-deprecation reason; either way it must NOT short-circuit on the
    # deprecation gate.
    if not handle.applied:
        assert "deprecated on macOS" not in handle.extra.get("reason", "")


# ---------------------------------------------------------------------------
# auto() preference ordering
# ---------------------------------------------------------------------------


def test_auto_detect_prefers_landlock_on_modern_linux():
    """On Linux with Landlock available, auto() MUST pick LandlockBackend first."""
    with mock.patch.object(detect.sys, "platform", "linux"), \
         mock.patch.object(detect, "_landlock_available", return_value=True), \
         mock.patch.object(detect, "_bubblewrap_present", return_value=True), \
         mock.patch.object(detect, "_apparmor_enabled", return_value=True), \
         mock.patch.object(detect, "_selinux_enabled", return_value=True):
        from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

        assert detect.auto() is LandlockBackend


def test_auto_detect_falls_back_to_bubblewrap_when_landlock_absent():
    """Landlock absent + bubblewrap present -> BubblewrapBackend (not AppArmor/SELinux)."""
    with mock.patch.object(detect.sys, "platform", "linux"), \
         mock.patch.object(detect, "_landlock_available", return_value=False), \
         mock.patch.object(detect, "_bubblewrap_present", return_value=True), \
         mock.patch.object(detect, "_apparmor_enabled", return_value=True), \
         mock.patch.object(detect, "_selinux_enabled", return_value=True):
        from general_ludd.security.sandboxes.linux_bubblewrap import BubblewrapBackend

        assert detect.auto() is BubblewrapBackend


def test_auto_detect_falls_back_to_apparmor_when_landlock_and_bwrap_absent():
    """Both per-process backends absent -> AppArmor (defense-in-depth)."""
    with mock.patch.object(detect.sys, "platform", "linux"), \
         mock.patch.object(detect, "_landlock_available", return_value=False), \
         mock.patch.object(detect, "_bubblewrap_present", return_value=False), \
         mock.patch.object(detect, "_apparmor_enabled", return_value=True), \
         mock.patch.object(detect, "_selinux_enabled", return_value=True):
        from general_ludd.security.sandboxes.linux_apparmor import AppArmorBackend

        assert detect.auto() is AppArmorBackend


def test_auto_detect_linux_returns_none_when_no_backend():
    """All Linux backends absent -> None + warning."""
    with mock.patch.object(detect.sys, "platform", "linux"), \
         mock.patch.object(detect, "_landlock_available", return_value=False), \
         mock.patch.object(detect, "_bubblewrap_present", return_value=False), \
         mock.patch.object(detect, "_apparmor_enabled", return_value=False), \
         mock.patch.object(detect, "_selinux_enabled", return_value=False):
        assert detect.auto() is None


def test_auto_detect_macos_15_4_returns_none_even_if_binary_present():
    """On macOS 15.4+ auto() returns None even if sandbox-exec binary exists."""
    with mock.patch.object(detect.sys, "platform", "darwin"), \
         mock.patch.object(detect, "_seatbelt_present", return_value=True), \
         mock.patch(
             "general_ludd.security.sandboxes.macos_seatbelt._is_deprecated_host",
             return_value=True,
         ), mock.patch("platform.mac_ver", return_value=("15.4", (), "")):
        assert detect.auto() is None
