"""Integration tests for general_ludd.os_expert collection (NF.6).

End-to-end coverage of OS Expert role backends: load each backend
script, invoke its public entrypoint (``gather`` / ``audit``), and
verify the produced artifact has the documented JSON shape with
correct field types. Each backend is self-contained (stdlib only) and
degrades gracefully when host commands are unavailable (subprocess
failures return empty strings), so the artifact structure is always
well-formed regardless of the host OS the tests run on.

Coverage spans 5 roles across all role categories:
    - linux_diagnose   (gather) — diagnostic collection
    - linux_kernel     (audit)  — kernel subsystem audit
    - macos_diagnose   (gather) — diagnostic collection
    - macos_security   (audit)  — security audit
    - android_diagnose (gather) — diagnostic collection

For each role the integration contract is:
    1. Module loads via importlib from the role's files/ directory.
    2. Direct invocation of ``gather()`` / ``audit()`` returns a dict.
    3. The dict has the documented top-level keys.
    4. Each top-level value has the documented nested shape and types.
    5. The CLI entrypoint (``python3 <script> --output <path>``) writes
       a valid JSON file with the same shape (round-trip).
    6. Cross-role: invoking multiple backends in sequence yields one
       valid artifact per role with no shared mutable state.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_ROLES = Path(__file__).resolve().parents[2] / (
    "collections/ansible_collections/general_ludd/os_expert/roles"
)


def _load_module(rel_path: str, mod_name: str):
    """Load a Python module from an arbitrary file path."""
    full = _ROLES / rel_path
    if not full.exists():
        pytest.skip(f"backend script not found: {full}")
    spec = importlib.util.spec_from_file_location(mod_name, full)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_cli(script_rel: str, output_path: Path) -> dict[str, Any]:
    """Invoke a backend script via its CLI entrypoint and parse its JSON output.

    Returns the parsed JSON dict. Skips the test if the script cannot run
    (e.g. missing on disk).
    """
    script = _ROLES / script_rel
    if not script.exists():
        pytest.skip(f"backend script not found: {script}")
    proc = subprocess.run(
        [sys.executable, str(script), "--output", str(output_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"{script.name} exited {proc.returncode}: stderr={proc.stderr!r}"
    )
    assert output_path.exists(), f"{script.name} wrote no output file"
    with open(output_path, encoding="utf-8") as f:
        return json.load(f)


# ── linux_diagnose: gather() ──────────────────────────────────────────────

class TestLinuxDiagnoseIntegration:
    """End-to-end: linux_diagnose role backend (gather)."""

    @pytest.fixture(scope="class")
    def backend(self):
        return _load_module(
            "linux_diagnose/files/linux_gather.py", "linux_gather_int"
        )

    def test_gather_returns_well_formed_dict(self, backend):
        result = backend.gather(proc=False, sysfs=False, gather_lsmod=False,
                                gather_sysctl=False)
        for key in ("cpuinfo", "meminfo", "version", "lsmod", "df",
                    "dmesg", "sysctl"):
            assert key in result, f"linux_gather missing top-level key: {key}"
        assert isinstance(result["cpuinfo"], dict)
        assert isinstance(result["meminfo"], dict)
        assert isinstance(result["lsmod"], list)
        assert isinstance(result["df"], list)
        assert isinstance(result["dmesg"], list)
        assert isinstance(result["sysctl"], dict)

    def test_gather_with_proc_reads_local_cpuinfo(self, backend):
        result = backend.gather(proc=True, sysfs=False, gather_lsmod=False,
                                gather_sysctl=False)
        assert isinstance(result["cpuinfo"], dict)
        assert "processor_count" in result["cpuinfo"]
        assert isinstance(result["cpuinfo"]["processor_count"], int)
        assert result["cpuinfo"]["processor_count"] >= 0

    def test_cli_entrypoint_writes_valid_json(self, tmp_path):
        out = tmp_path / "linux_diag.json"
        data = _run_cli("linux_diagnose/files/linux_gather.py", out)
        for key in ("cpuinfo", "meminfo", "version", "lsmod", "df",
                    "dmesg", "sysctl"):
            assert key in data, f"CLI output missing key: {key}"
        json.dumps(data)  # serializable round-trip


# ── linux_kernel: audit() ─────────────────────────────────────────────────

class TestLinuxKernelIntegration:
    """End-to-end: linux_kernel role backend (audit)."""

    @pytest.fixture(scope="class")
    def backend(self):
        return _load_module(
            "linux_kernel/files/linux_kernel_audit.py", "linux_kernel_int"
        )

    def test_audit_returns_well_formed_dict(self, backend):
        result = backend.audit(audit_modules=False, audit_sysctl=False,
                               audit_cgroups=False, audit_namespaces=False,
                               audit_ebpf=False)
        for key in ("modules", "sysctl", "cgroups", "namespaces", "ebpf"):
            assert key in result, f"linux_kernel missing top-level key: {key}"
        assert isinstance(result["modules"], list)
        assert isinstance(result["sysctl"], dict)
        assert isinstance(result["cgroups"], dict)
        assert isinstance(result["namespaces"], dict)
        assert isinstance(result["ebpf"], dict)
        for sub in ("controllers", "init_cgroup", "mounts"):
            assert sub in result["cgroups"]
        for sub in ("lsns", "init_ns"):
            assert sub in result["namespaces"]
        assert "programs" in result["ebpf"]

    def test_audit_with_sysctl_returns_dict_or_empty(self, backend):
        result = backend.audit(audit_modules=False, audit_sysctl=True,
                               audit_cgroups=False, audit_namespaces=False,
                               audit_ebpf=False)
        assert isinstance(result["sysctl"], dict)

    def test_cli_entrypoint_writes_valid_json(self, tmp_path):
        out = tmp_path / "linux_kernel.json"
        data = _run_cli("linux_kernel/files/linux_kernel_audit.py", out)
        for key in ("modules", "sysctl", "cgroups", "namespaces", "ebpf"):
            assert key in data
        assert isinstance(data["modules"], list)
        assert isinstance(data["cgroups"], dict)


# ── macos_diagnose: gather() ──────────────────────────────────────────────

class TestMacOSDiagnoseIntegration:
    """End-to-end: macos_diagnose role backend (gather)."""

    @pytest.fixture(scope="class")
    def backend(self):
        return _load_module(
            "macos_diagnose/files/macos_gather.py", "macos_gather_int"
        )

    def test_gather_returns_well_formed_dict(self, backend):
        result = backend.gather()
        for key in ("launchctl", "pmset", "system_profiler", "nvram",
                    "unified_log"):
            assert key in result, f"macos_gather missing top-level key: {key}"

    def test_gather_launchctl_is_list(self, backend):
        result = backend.gather()
        assert isinstance(result["launchctl"], list)

    def test_cli_entrypoint_writes_valid_json(self, tmp_path):
        out = tmp_path / "macos_diag.json"
        data = _run_cli("macos_diagnose/files/macos_gather.py", out)
        for key in ("launchctl", "pmset", "system_profiler", "nvram",
                    "unified_log"):
            assert key in data
        assert isinstance(data["launchctl"], list)


# ── macos_security: audit() ───────────────────────────────────────────────

class TestMacOSSecurityIntegration:
    """End-to-end: macos_security role backend (audit)."""

    @pytest.fixture(scope="class")
    def backend(self):
        return _load_module(
            "macos_security/files/macos_security_audit.py",
            "macos_security_int",
        )

    def test_audit_returns_well_formed_dict(self, backend):
        result = backend.audit()
        for key in ("csrutil", "spctl", "xprotect", "tccutil", "plist_policy"):
            assert key in result, f"macos_security missing top-level key: {key}"

    def test_audit_csrutil_has_enabled_flag(self, backend):
        result = backend.audit()
        assert "sip_enabled" in result["csrutil"]
        assert isinstance(result["csrutil"]["sip_enabled"], bool)

    def test_audit_spctl_has_assessments_flag(self, backend):
        result = backend.audit()
        assert "assessments_enabled" in result["spctl"]
        assert isinstance(result["spctl"]["assessments_enabled"], bool)

    def test_cli_entrypoint_writes_valid_json(self, tmp_path):
        out = tmp_path / "macos_security.json"
        data = _run_cli("macos_security/files/macos_security_audit.py", out)
        for key in ("csrutil", "spctl", "xprotect", "tccutil", "plist_policy"):
            assert key in data
        assert isinstance(data["csrutil"]["sip_enabled"], bool)


# ── android_diagnose: gather() ────────────────────────────────────────────

class TestAndroidDiagnoseIntegration:
    """End-to-end: android_diagnose role backend (gather).

    ADB is unavailable in CI, so the gatherer must still return a
    well-formed dict with the documented keys and empty/default values.
    """

    @pytest.fixture(scope="class")
    def backend(self):
        return _load_module(
            "android_diagnose/files/android_gather.py", "android_gather_int"
        )

    def test_gather_without_adb_returns_well_formed_dict(self, backend):
        result = backend.gather()
        for key in ("logcat", "getprop", "packages", "dumpsys", "serial"):
            assert key in result, f"android_gather missing top-level key: {key}"
        assert isinstance(result["logcat"], list)
        assert isinstance(result["getprop"], dict)
        assert isinstance(result["packages"], list)
        assert isinstance(result["dumpsys"], dict)
        assert isinstance(result["serial"], str)
        assert result["serial"] == "default"

    def test_gather_dumpsys_has_requested_service_keys(self, backend):
        result = backend.gather(dumpsys_services=["meminfo"])
        assert "meminfo" in result["dumpsys"]

    def test_cli_entrypoint_writes_valid_json(self, tmp_path):
        out = tmp_path / "android_diag.json"
        data = _run_cli("android_diagnose/files/android_gather.py", out)
        for key in ("logcat", "getprop", "packages", "dumpsys", "serial"):
            assert key in data
        assert data["serial"] == "default"


# ── cross-role: multiple backends in sequence ─────────────────────────────

class TestCrossRoleArtifactIndependence:
    """Invoking multiple role backends in sequence yields independent artifacts."""

    def test_five_role_artifacts_each_well_formed(self):
        specs = [
            ("linux_diagnose/files/linux_gather.py", "gather",
             ("cpuinfo", "meminfo", "version", "lsmod", "df", "dmesg", "sysctl")),
            ("linux_kernel/files/linux_kernel_audit.py", "audit",
             ("modules", "sysctl", "cgroups", "namespaces", "ebpf")),
            ("macos_diagnose/files/macos_gather.py", "gather",
             ("launchctl", "pmset", "system_profiler", "nvram", "unified_log")),
            ("macos_security/files/macos_security_audit.py", "audit",
             ("csrutil", "spctl", "xprotect", "tccutil", "plist_policy")),
            ("android_diagnose/files/android_gather.py", "gather",
             ("logcat", "getprop", "packages", "dumpsys", "serial")),
        ]
        artifacts: list[dict[str, Any]] = []
        for rel, entry, expected_keys in specs:
            mod = _load_module(rel, f"xrole_{entry}_{rel.replace('/', '_')}")
            fn = getattr(mod, entry)
            kwargs = (
                {"proc": False, "sysfs": False, "gather_lsmod": False,
                 "gather_sysctl": False}
                if entry == "gather" and "linux_gather" in rel
                else {}
            )
            result = fn(**kwargs)
            assert isinstance(result, dict), f"{rel}: {entry} returned non-dict"
            for key in expected_keys:
                assert key in result, f"{rel}: missing key {key}"
            artifacts.append(result)
        assert len(artifacts) == len(specs)
        assert len({id(a) for a in artifacts}) == len(artifacts), (
            "backends shared a mutable result object"
        )


# ── role task file loadability (all 14 roles) ─────────────────────────────

class TestAllRolesHaveTaskFiles:
    """All 14 os_expert roles ship a loadable tasks/main.yml."""

    EXPECTED_ROLES = (
        "android_diagnose", "android_security",
        "ios_diagnose", "ios_security",
        "linux_diagnose", "linux_security", "linux_automation", "linux_kernel",
        "macos_diagnose", "macos_security", "macos_automation",
        "windows_diagnose", "windows_security", "windows_automation",
    )

    def test_all_14_roles_present_with_task_files(self):
        missing = []
        for role in self.EXPECTED_ROLES:
            tasks_file = _ROLES / role / "tasks" / "main.yml"
            if not tasks_file.is_file():
                missing.append(role)
                continue
            content = tasks_file.read_text(encoding="utf-8")
            if len(content) < 20 or "name:" not in content:
                missing.append(f"{role} (malformed tasks/main.yml)")
        assert not missing, f"roles missing/invalid tasks/main.yml: {missing}"

    def test_all_diagnose_roles_ship_gather_backend(self):
        for role in ("android_diagnose", "ios_diagnose", "linux_diagnose",
                     "macos_diagnose", "windows_diagnose"):
            backends = list((_ROLES / role / "files").glob("*.py"))
            assert backends, f"{role} has no backend script in files/"

    def test_all_security_roles_ship_audit_backend(self):
        for role in ("android_security", "ios_security", "linux_security",
                     "macos_security", "windows_security"):
            backends = list((_ROLES / role / "files").glob("*.py"))
            assert backends, f"{role} has no backend script in files/"
