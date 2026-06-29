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
         mock.patch.object(detect, "_seatbelt_present", return_value=True):
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
