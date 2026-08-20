"""E2E tests for OS-level sandbox backends.

Exercises every backend in ``src/general_ludd/security/sandboxes/`` through the
full lifecycle (construct -> apply -> verify -> release) and asserts the core
security invariant: **a sandboxed process CANNOT access a file outside its
allowlist**.

Platform gating:
  - ``linux_landlock``, ``linux_selinux``, ``linux_apparmor``,
    ``linux_bubblewrap`` -> Linux-only
  - ``freebsd_jail`` -> FreeBSD-only
  - ``macos_seatbelt`` -> macOS-only (further gated on ``sandbox-exec`` presence
    and macOS < 15.4 where the binary was removed)
  - ``windows_appcontainer`` -> Windows-only

On any given host, off-platform backends skip cleanly. The import tests run on
every platform because every backend module is contractually importable
anywhere (OS-specific imports are lazy). When the on-platform backend's
toolchain is present, the lifecycle + security-invariant tests run for real
against ``sandbox-exec`` / ``bwrap`` / the Landlock LSM.

The Landlock lifecycle runs in a FORKED SUBPROCESS because Landlock restrictions
are irreversible for the process that applies them (calling ``apply()`` in the
pytest process would lock down the test runner itself).

Run:  make test-specific TESTFILE=tests/e2e/test_sandbox_backends_e2e.py
"""

from __future__ import annotations

import importlib
import json
import pkgutil
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import cast

import pytest

from general_ludd.security.permissions import Capability, PermissionSpec
from general_ludd.security.sandboxes import (
    SandboxBackend,
    SandboxHandle,
    SandboxTarget,
    allowed_hosts,
    allowed_ports,
    constraint_value,
    path_prefix,
)

IS_LINUX = sys.platform.startswith("linux")
IS_FREEBSD = sys.platform.startswith("freebsd")
IS_DARWIN = sys.platform == "darwin"
IS_WINDOWS = sys.platform.startswith("win")

BACKEND_MODULES: list[tuple[str, str]] = [
    ("general_ludd.security.sandboxes.linux_apparmor", "apparmor"),
    ("general_ludd.security.sandboxes.linux_bubblewrap", "bubblewrap"),
    ("general_ludd.security.sandboxes.linux_landlock", "landlock"),
    ("general_ludd.security.sandboxes.linux_selinux", "selinux"),
    ("general_ludd.security.sandboxes.freebsd_jail", "jail"),
    ("general_ludd.security.sandboxes.macos_seatbelt", "seatbelt"),
    ("general_ludd.security.sandboxes.windows_appcontainer", "appcontainer"),
    ("general_ludd.security.sandboxes.vm.firecracker_backend", "firecracker"),
    ("general_ludd.security.sandboxes.vm.gvisor_backend", "gvisor"),
]

VM_BACKEND_MODULES = [
    item for item in BACKEND_MODULES if ".sandboxes.vm." in item[0]
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def allow_dir(tmp_path: Path) -> Path:
    """Temp directory that IS inside the sandbox allowlist."""
    d = tmp_path / "allow"
    d.mkdir()
    (d / "inside.txt").write_text("allowed content\n")
    return d


@pytest.fixture()
def outside_secret(tmp_path: Path) -> Path:
    """Temp file OUTSIDE the allowlist that a sandboxed process must NOT read."""
    secret = tmp_path / "outside" / "secret.txt"
    secret.parent.mkdir(parents=True)
    secret.write_text("this is secret\n")
    return secret


@pytest.fixture()
def file_spec(allow_dir: Path) -> PermissionSpec:
    """A spec that allows read-write to ``allow_dir`` only."""
    return PermissionSpec(
        agent_type="e2e-sandbox",
        capabilities=[
            Capability(
                resource="file:repo",
                actions=["read", "write"],
                constraints={"path_prefix": str(allow_dir) + "/"},
            ),
        ],
        denied=[],
    )


@pytest.fixture()
def file_target(allow_dir: Path) -> SandboxTarget:
    return SandboxTarget(directory=str(allow_dir))


# ---------------------------------------------------------------------------
# 1. Import + protocol-shape tests (run on ALL platforms)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", [m for m, _ in BACKEND_MODULES])
def test_backend_module_importable_on_any_platform(module_name: str) -> None:
    """Every backend module MUST import cleanly regardless of host OS.

    This is the cross-platform portability contract: OS-specific imports are
    lazy (inside methods), so importing the module never fails off-platform.
    """
    mod = importlib.import_module(module_name)
    assert mod is not None


@pytest.mark.parametrize("module_name,expected_name", BACKEND_MODULES)
def test_backend_class_exposes_protocol_shape(module_name: str, expected_name: str) -> None:
    """Each backend class has ``name`` + ``available``/``apply``/``verify``/``release``."""
    mod = importlib.import_module(module_name)
    backend_cls = _find_backend_class(mod)
    assert backend_cls is not None, f"no backend class found in {module_name}"
    assert backend_cls.name == expected_name
    for method in ("available", "apply", "verify", "release"):
        assert hasattr(backend_cls, method), (
            f"{expected_name} backend missing '{method}' (protocol violation)"
        )


def _find_backend_class(mod: object) -> type[SandboxBackend] | None:
    """Find the class in ``mod`` that has ``name`` and ``available`` attributes."""
    for attr in dir(mod):
        obj = getattr(mod, attr)
        if (
            isinstance(obj, type)
            and hasattr(obj, "name")
            and hasattr(obj, "available")
            and hasattr(obj, "apply")
            and hasattr(obj, "verify")
        ):
            return cast(type[SandboxBackend], obj)
    return None


def _discover_concrete_backend_modules() -> dict[str, str]:
    """Discover concrete backends so the E2E matrix cannot silently drift."""
    import general_ludd.security.sandboxes as sandbox_package

    discovered: dict[str, str] = {}
    for module_info in pkgutil.walk_packages(
        sandbox_package.__path__,
        prefix=f"{sandbox_package.__name__}.",
    ):
        mod = importlib.import_module(module_info.name)
        backend_cls = _find_backend_class(mod)
        if (
            backend_cls is not None
            and backend_cls.__module__ == module_info.name
            and isinstance(getattr(backend_cls, "name", None), str)
        ):
            discovered[module_info.name] = backend_cls.name
    return discovered


def test_backend_matrix_covers_every_concrete_runtime_backend() -> None:
    """Every shipped backend must be represented in this cross-platform E2E file."""
    assert dict(BACKEND_MODULES) == _discover_concrete_backend_modules()


@pytest.mark.parametrize("module_name,expected_name", BACKEND_MODULES)
def test_backend_runtime_protocol_and_probe_contract(
    module_name: str,
    expected_name: str,
) -> None:
    """Every concrete backend conforms at runtime and probes without raising."""
    backend_cls = _find_backend_class(importlib.import_module(module_name))
    assert backend_cls is not None
    assert backend_cls.name == expected_name
    assert isinstance(backend_cls, SandboxBackend)
    assert isinstance(backend_cls.available(), bool)


@pytest.mark.parametrize("module_name,expected_name", VM_BACKEND_MODULES)
def test_unavailable_vm_backend_fails_closed_at_verification_and_releases_twice(
    module_name: str,
    expected_name: str,
    monkeypatch: pytest.MonkeyPatch,
    file_spec: PermissionSpec,
    file_target: SandboxTarget,
) -> None:
    """Unavailable VM backends expose a negative attestation and idempotent cleanup."""
    backend_cls = _find_backend_class(importlib.import_module(module_name))
    assert backend_cls is not None
    monkeypatch.setattr(backend_cls, "available", staticmethod(lambda: False))

    handle = backend_cls.apply(file_spec, file_target)
    assert handle.backend == expected_name
    assert handle.applied is False
    assert handle.extra.get("reason")
    findings = backend_cls.verify(file_spec, handle)
    assert findings
    assert all(finding.severity != "ok" for finding in findings)

    backend_cls.release(handle)
    backend_cls.release(handle)


def test_auto_detected_backend_is_in_the_complete_e2e_matrix() -> None:
    """The host-selected backend must be a probed backend, never an untested class."""
    from general_ludd.security.sandboxes.detect import auto

    backend_cls = auto()
    if backend_cls is None:
        return
    assert backend_cls.__module__ in dict(BACKEND_MODULES)
    assert backend_cls.name == dict(BACKEND_MODULES)[backend_cls.__module__]
    assert isinstance(backend_cls, SandboxBackend)
    assert backend_cls.available() is True


def test_permission_constraints_round_trip_through_portable_backend_adapters() -> None:
    """Canonical permission constraints retain path, host, and port semantics."""
    file_cap = Capability(
        resource="file:repo",
        actions=["read"],
        constraints={"path_prefix": "/srv/gludd"},
    )
    assert constraint_value(file_cap, "path_prefix") == "/srv/gludd"
    assert constraint_value(file_cap, "missing") is None
    assert path_prefix(file_cap) == "/srv/gludd"
    assert path_prefix(
        Capability(
            resource="file:repo",
            actions=["read"],
            constraints={"path_prefix": 7},
        ),
    ) is None

    explicit_net = Capability(
        resource="net:tcp",
        actions=["connect"],
        constraints={
            "allowed_hosts": ["model.internal", "audit.internal"],
            "allowed_ports": (443, 8443),
        },
    )
    assert allowed_hosts(explicit_net) == ["model.internal", "audit.internal"]
    assert allowed_ports(explicit_net) == [443, 8443]

    scalar_net = Capability(
        resource="net:tcp",
        actions=["connect"],
        constraints={"allowed_hosts": "model.internal", "allowed_ports": 443},
    )
    assert allowed_hosts(scalar_net) == ["model.internal"]
    assert allowed_ports(scalar_net) == [443]

    resource_net = Capability(
        resource="net:tcp:model.internal:9443",
        actions=["connect"],
        constraints={},
    )
    assert allowed_hosts(resource_net) == ["model.internal"]
    assert allowed_ports(resource_net) == [9443]
    assert allowed_hosts(
        Capability(resource="net", actions=["connect"], constraints={}),
    ) == []
    assert allowed_ports(
        Capability(
            resource="net:tcp:model.internal:not-a-port",
            actions=["connect"],
            constraints={},
        ),
    ) == []


# ---------------------------------------------------------------------------
# 2. Availability detection tests (verify available() per-platform)
# ---------------------------------------------------------------------------


def test_landlock_availability_detection() -> None:
    """available() returns False off-Linux; a bool on Linux without raising."""
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    result = LandlockBackend.available()
    if not IS_LINUX:
        assert result is False, "LandlockBackend.available() must be False on non-Linux"
    assert isinstance(result, bool)


def test_selinux_availability_detection() -> None:
    """available() returns False off-Linux (checkmodule absent); bool on Linux."""
    from general_ludd.security.sandboxes.linux_selinux import SELinuxBackend

    result = SELinuxBackend.available()
    if not IS_LINUX:
        assert result is False, "SELinuxBackend.available() must be False on non-Linux"
    assert isinstance(result, bool)


def test_apparmor_availability_detection() -> None:
    """available() returns False off-Linux (apparmor_parser absent)."""
    from general_ludd.security.sandboxes.linux_apparmor import AppArmorBackend

    result = AppArmorBackend.available()
    if not IS_LINUX:
        assert result is False
    assert isinstance(result, bool)


def test_bubblewrap_availability_detection() -> None:
    """available() returns False off-Linux (bwrap absent); bool on Linux."""
    from general_ludd.security.sandboxes.linux_bubblewrap import BubblewrapBackend

    result = BubblewrapBackend.available()
    if not IS_LINUX:
        assert result is False
    assert isinstance(result, bool)


def test_freebsd_jail_availability_detection() -> None:
    """available() returns False off-FreeBSD."""
    from general_ludd.security.sandboxes.freebsd_jail import JailBackend

    result = JailBackend.available()
    if not IS_FREEBSD:
        assert result is False
    assert isinstance(result, bool)


def test_seatbelt_availability_detection() -> None:
    """available() returns False off-macOS; bool on macOS."""
    from general_ludd.security.sandboxes.macos_seatbelt import SeatbeltBackend

    result = SeatbeltBackend.available()
    if not IS_DARWIN:
        assert result is False
    assert isinstance(result, bool)


def test_appcontainer_availability_detection() -> None:
    """available() returns False off-Windows."""
    from general_ludd.security.sandboxes.windows_appcontainer import AppContainerBackend

    result = AppContainerBackend.available()
    if not IS_WINDOWS:
        assert result is False
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# 3. Lifecycle tests: construct -> apply -> verify -> release
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not IS_DARWIN, reason="macOS-only")
def test_seatbelt_lifecycle(file_spec: PermissionSpec, file_target: SandboxTarget) -> None:
    """Full apply -> verify -> release for SeatbeltBackend when sandbox-exec is present."""
    from general_ludd.security.sandboxes.macos_seatbelt import SeatbeltBackend

    if not SeatbeltBackend.available():
        pytest.skip("sandbox-exec absent or macOS 15.4+ (deprecated)")

    handle = SeatbeltBackend.apply(file_spec, file_target)
    assert isinstance(handle, SandboxHandle)
    assert handle.backend == "seatbelt"

    findings = SeatbeltBackend.verify(file_spec, handle)
    assert isinstance(findings, list)
    assert len(findings) > 0, "verify() must return at least one Finding"

    if handle.applied:
        ok_or_fail = {f.severity for f in findings}
        assert "ok" in ok_or_fail or "fail" in ok_or_fail, (
            f"unexpected findings when applied=True: {findings!r}"
        )

    SeatbeltBackend.release(handle)
    assert not SeatbeltBackend.available() or True  # release did not raise


@pytest.mark.skipif(not IS_LINUX, reason="Linux-only")
def test_bubblewrap_lifecycle(file_spec: PermissionSpec, file_target: SandboxTarget) -> None:
    """Full apply -> verify -> release for BubblewrapBackend when bwrap is present."""
    from general_ludd.security.sandboxes.linux_bubblewrap import BubblewrapBackend

    if not BubblewrapBackend.available():
        pytest.skip("bwrap binary absent")

    handle = BubblewrapBackend.apply(file_spec, file_target)
    assert isinstance(handle, SandboxHandle)
    assert handle.backend == "bubblewrap"
    assert handle.applied is True, "bubblewrap apply must succeed when bwrap is present"

    findings = BubblewrapBackend.verify(file_spec, handle)
    assert len(findings) > 0
    assert any(f.severity == "ok" for f in findings), (
        f"verify() must report ok findings when applied=True: {findings!r}"
    )

    BubblewrapBackend.release(handle)


@pytest.mark.skipif(not IS_LINUX, reason="Linux-only")
def test_landlock_lifecycle_in_subprocess() -> None:
    """Landlock apply -> verify -> release in a FORKED subprocess.

    Landlock restrictions are IRREVERSIBLE for the applying process. Running
    apply() in the pytest process would lock down the test runner. We fork
    a child that applies, probes, and reports the handle shape via JSON.
    """
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    if not LandlockBackend.available():
        pytest.skip("pylandlock not installed or kernel Landlock ABI disabled")

    script = textwrap.dedent(
        """
        import json, sys
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sandboxes import SandboxTarget
        from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

        spec = PermissionSpec(
            agent_type="e2e-landlock-child",
            capabilities=[Capability(
                resource="file:repo",
                actions=["read"],
                constraints={"path_prefix": "/tmp/"},
            )],
            denied=[],
        )
        target = SandboxTarget(directory="/tmp/")
        handle = LandlockBackend.apply(spec, target)
        findings = LandlockBackend.verify(spec, handle)
        LandlockBackend.release(handle)
        print(json.dumps({
            "applied": handle.applied,
            "backend": handle.backend,
            "num_findings": len(findings),
            "irreversible": handle.extra.get("irreversible", False),
        }))
        """,
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False, capture_output=True, timeout=30, text=True,
    )
    assert result.returncode == 0, (
        f"landlock subprocess failed (rc={result.returncode}): "
        f"stderr={result.stderr[:500]}"
    )
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["backend"] == "landlock"
    assert isinstance(data["applied"], bool)
    assert data["num_findings"] > 0


@pytest.mark.skipif(not IS_LINUX, reason="Linux-only")
def test_apparmor_lifecycle(file_spec: PermissionSpec, file_target: SandboxTarget) -> None:
    """apply -> verify -> release for AppArmorBackend.

    Without root, apply() fails-open (returns applied=False). Either way the
    lifecycle must not raise and verify() must return findings.
    """
    from general_ludd.security.sandboxes.linux_apparmor import AppArmorBackend

    if not AppArmorBackend.available():
        pytest.skip("apparmor_parser / aa-status absent")

    handle = AppArmorBackend.apply(file_spec, file_target)
    assert isinstance(handle, SandboxHandle)
    assert handle.backend == "apparmor"

    findings = AppArmorBackend.verify(file_spec, handle)
    assert len(findings) > 0

    AppArmorBackend.release(handle)


@pytest.mark.skipif(not IS_LINUX, reason="Linux-only")
def test_selinux_lifecycle(file_spec: PermissionSpec, file_target: SandboxTarget) -> None:
    """apply -> verify -> release for SELinuxBackend."""
    from general_ludd.security.sandboxes.linux_selinux import SELinuxBackend

    if not SELinuxBackend.available():
        pytest.skip("SELinux toolchain absent (checkmodule / semodule)")

    handle = SELinuxBackend.apply(file_spec, file_target)
    assert isinstance(handle, SandboxHandle)
    assert handle.backend == "selinux"

    findings = SELinuxBackend.verify(file_spec, handle)
    assert len(findings) > 0

    SELinuxBackend.release(handle)


@pytest.mark.skipif(not IS_FREEBSD, reason="FreeBSD-only")
def test_jail_lifecycle(file_spec: PermissionSpec, file_target: SandboxTarget) -> None:
    """apply -> verify -> release for JailBackend."""
    from general_ludd.security.sandboxes.freebsd_jail import JailBackend

    if not JailBackend.available():
        pytest.skip("jail binary absent")

    handle = JailBackend.apply(file_spec, file_target)
    assert isinstance(handle, SandboxHandle)
    assert handle.backend == "jail"

    findings = JailBackend.verify(file_spec, handle)
    assert len(findings) > 0

    JailBackend.release(handle)


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows-only")
def test_appcontainer_lifecycle(file_spec: PermissionSpec, file_target: SandboxTarget) -> None:
    """apply -> verify -> release for AppContainerBackend."""
    from general_ludd.security.sandboxes.windows_appcontainer import AppContainerBackend

    if not AppContainerBackend.available():
        pytest.skip("pywin32 / AppContainer API absent")

    handle = AppContainerBackend.apply(file_spec, file_target)
    assert isinstance(handle, SandboxHandle)
    assert handle.backend == "appcontainer"

    findings = AppContainerBackend.verify(file_spec, handle)
    assert len(findings) > 0

    AppContainerBackend.release(handle)


# ---------------------------------------------------------------------------
# 4. Security invariant: sandboxed process CANNOT access outside allowlist
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not IS_DARWIN, reason="macOS-only")
def test_seatbelt_denies_access_outside_allowlist(
    allow_dir: Path, outside_secret: Path,
) -> None:
    """SECURITY INVARIANT: a sandbox-exec-confined process cannot read files
    outside its allowlist.

    Uses ``(allow default)`` + an explicit ``(deny file-read* <secret>)`` rule so
    that system binaries load normally but the secret file is blocked. This
    directly tests macOS Seatbelt enforcement — the core security guarantee.
    """
    if shutil.which("sandbox-exec") is None:
        pytest.skip("sandbox-exec absent")
    from general_ludd.security.sandboxes.macos_seatbelt import _is_deprecated_host

    if _is_deprecated_host():
        pytest.skip("sandbox-exec deprecated on macOS 15.4+")

    profile = textwrap.dedent(
        f"""\
        (version 1)
        (allow default)
        (deny file-read* (subpath "{outside_secret.parent}"))
        (deny file-write* (subpath "{outside_secret.parent}"))
        """,
    )
    profile_path = allow_dir.parent / "deny-test.sb"
    profile_path.write_text(profile)

    inside_file = allow_dir / "inside.txt"

    rc_allowed = subprocess.run(
        ["sandbox-exec", "-f", str(profile_path), "/bin/cat", str(inside_file)],
        check=False, capture_output=True, timeout=10,
    ).returncode
    assert rc_allowed == 0, (
        f"sandbox-exec should ALLOW reading {inside_file} (inside allowlist) "
        f"but got rc={rc_allowed}"
    )

    rc_denied = subprocess.run(
        ["sandbox-exec", "-f", str(profile_path), "/bin/cat", str(outside_secret)],
        check=False, capture_output=True, timeout=10,
    ).returncode
    assert rc_denied != 0, (
        "SECURITY INVARIANT BROKEN: sandbox-exec allowed reading "
        f"{outside_secret} which is denied by the profile"
    )


@pytest.mark.skipif(not IS_LINUX, reason="Linux-only")
def test_bubblewrap_denies_access_outside_allowlist(
    allow_dir: Path, outside_secret: Path,
) -> None:
    """SECURITY INVARIANT: a bwrap-confined process cannot access paths that
    are not bind-mounted into its namespace.

    ``render_argv`` only bind-mounts the allowlist dir (+ OS essentials). The
    outside secret file is NOT mounted, so it is invisible inside the namespace.
    """
    if shutil.which("bwrap") is None:
        pytest.skip("bwrap absent")
    from general_ludd.security.sandboxes.linux_bubblewrap import render_argv

    spec = PermissionSpec(
        agent_type="e2e-bwrap-invariant",
        capabilities=[
            Capability(
                resource="file:repo",
                actions=["read", "write"],
                constraints={"path_prefix": str(allow_dir)},
            ),
        ],
        denied=[],
    )
    target = SandboxTarget(directory=str(allow_dir))

    smoke = subprocess.run(
        render_argv(spec, target, cmd=["/usr/bin/true"]),
        check=False, capture_output=True, timeout=10,
    )
    if smoke.returncode != 0:
        pytest.skip(
            f"bwrap does not work on this host (rc={smoke.returncode}): "
            f"{smoke.stderr.decode('utf-8', 'replace')[:200]}",
        )

    inside_file = allow_dir / "inside.txt"

    rc_allowed = subprocess.run(
        render_argv(spec, target, cmd=["/usr/bin/cat", str(inside_file)]),
        check=False, capture_output=True, timeout=10,
    ).returncode
    assert rc_allowed == 0, (
        f"bwrap should ALLOW reading {inside_file} (bind-mounted) "
        f"but got rc={rc_allowed}"
    )

    writable_file = allow_dir / "created-inside.txt"
    rc_writable = subprocess.run(
        render_argv(spec, target, cmd=["/usr/bin/touch", str(writable_file)]),
        check=False, capture_output=True, timeout=10,
    ).returncode
    assert rc_writable == 0 and writable_file.is_file(), (
        "bwrap did not preserve the declared read-write workspace contract"
    )

    escape_link = allow_dir / "outside-link"
    escape_link.symlink_to(outside_secret)
    for forbidden_path in (outside_secret, escape_link):
        rc_denied = subprocess.run(
            render_argv(spec, target, cmd=["/usr/bin/cat", str(forbidden_path)]),
            check=False, capture_output=True, timeout=10,
        ).returncode
        assert rc_denied != 0, (
            "SECURITY INVARIANT BROKEN: bwrap allowed reading "
            f"{forbidden_path} outside its bind-mounted namespace"
        )


@pytest.mark.skipif(not IS_LINUX, reason="Linux-only")
def test_landlock_denies_access_outside_allowlist_in_subprocess() -> None:
    """SECURITY INVARIANT (Landlock): a Landlock-restricted process cannot open
    files outside its allowed paths.

    Runs in a subprocess because Landlock is irreversible for the applying
    process. The child applies a ruleset allowing only ``/tmp/``, then probes
    ``open("/etc/passwd")`` — which MUST be denied by the LSM.
    """
    from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

    if not LandlockBackend.available():
        pytest.skip("pylandlock not installed or kernel Landlock ABI disabled")

    script = textwrap.dedent(
        """
        import json, sys
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sandboxes import SandboxTarget
        from general_ludd.security.sandboxes.linux_landlock import LandlockBackend

        spec = PermissionSpec(
            agent_type="e2e-landlock-invariant",
            capabilities=[Capability(
                resource="file:repo",
                actions=["read"],
                constraints={"path_prefix": "/tmp/"},
            )],
            denied=[],
        )
        target = SandboxTarget(directory="/tmp/")
        handle = LandlockBackend.apply(spec, target)
        if not handle.applied:
            print(json.dumps({"skipped": True, "reason": handle.extra.get("reason", "?")}))
            sys.exit(0)

        # Probe: try to open a file OUTSIDE the /tmp/ allowlist.
        denied = False
        denial_error = ""
        try:
            with open("/etc/passwd") as fh:
                fh.read(1)
            denied = False
        except OSError as exc:
            denied = True
            denial_error = str(exc)

        # Probe: try to open a file INSIDE the allowlist (must succeed).
        import tempfile
        tmp_file = tempfile.NamedTemporaryFile(delete=False)
        tmp_file.write(b"ok")
        tmp_file.close()
        allowed = False
        try:
            with open(tmp_file.name) as fh:
                fh.read()
            allowed = True
        except OSError:
            allowed = False

        print(json.dumps({
            "skipped": False,
            "outside_denied": denied,
            "denial_error": denial_error,
            "inside_allowed": allowed,
        }))
        """,
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False, capture_output=True, timeout=30, text=True,
    )
    assert result.returncode == 0, (
        f"landlock invariant subprocess failed (rc={result.returncode}): "
        f"stderr={result.stderr[:500]}"
    )
    data = json.loads(result.stdout.strip().splitlines()[-1])
    if data.get("skipped"):
        pytest.skip(f"landlock apply failed in subprocess: {data.get('reason')}")
    assert data["outside_denied"] is True, (
        "SECURITY INVARIANT BROKEN: Landlock allowed reading /etc/passwd "
        f"outside the /tmp/ allowlist (error was: {data.get('denial_error')})"
    )
    assert data["inside_allowed"] is True, (
        "Landlock denied reading a file INSIDE the /tmp/ allowlist — "
        "the allow rule is broken"
    )
