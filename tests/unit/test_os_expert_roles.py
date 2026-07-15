"""Unit tests for os_expert collection role backends.

Tests the Python gatherer/auditor scripts shipped in each role's files/
directory. The scripts are self-contained (stdlib only) so they can be
copied to target hosts by ansible. Tests load them via importlib from the
file path to avoid polluting the src/ import path.

Covers:
  - android_diagnose: files/android_gather.py (logcat/dumpsys/getprop/pm list parsing)
  - android_security: files/android_security_audit.py (sepolicy/permissions/keystore/dm-verity)
  - ios_diagnose: files/ios_gather.py (ideviceinfo/idevicesyslog/idevicediagnostics/oslog)
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# ── helpers ──────────────────────────────────────────────────────────────

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


# ── android_gather.py (android_diagnose role backend) ────────────────────

@pytest.fixture(scope="module")
def android_gather():
    return _load_module("android_diagnose/files/android_gather.py", "android_gather")


def test_android_gather_has_gather_functions(android_gather):
    for fn in ("parse_logcat", "parse_getprop", "parse_pm_list", "gather"):
        assert hasattr(android_gather, fn), f"android_gather missing {fn}"


def test_android_parse_logcat_threadtime(android_gather):
    sample = (
        "06-15 10:30:45.123  1234  5678 I ActivityManager: Start proc\n"
        "06-15 10:30:46.000  2000  2000 E AndroidRuntime: FATAL EXCEPTION\n"
    )
    entries = android_gather.parse_logcat(sample)
    assert len(entries) == 2
    assert entries[0]["level"] == "I"
    assert entries[0]["tag"] == "ActivityManager"
    assert entries[0]["pid"] == 1234
    assert entries[1]["level"] == "E"


def test_android_parse_logcat_empty(android_gather):
    assert android_gather.parse_logcat("") == []


def test_android_parse_logcat_malformed_skipped(android_gather):
    sample = "garbage line\n06-15 10:30:45.123  1234  5678 I Tag: msg\n"
    entries = android_gather.parse_logcat(sample)
    assert len(entries) == 1


def test_android_parse_getprop(android_gather):
    sample = (
        "[ro.build.version.sdk]: [34]\n"
        "[ro.product.model]: [Pixel 7]\n"
        "[ro.debuggable]: [1]\n"
    )
    props = android_gather.parse_getprop(sample)
    assert props["ro.build.version.sdk"] == "34"
    assert props["ro.product.model"] == "Pixel 7"
    assert props["ro.debuggable"] == "1"


def test_android_parse_getprop_empty(android_gather):
    assert android_gather.parse_getprop("") == {}


def test_android_parse_getprop_ignores_malformed(android_gather):
    sample = "not a prop line\n[ro.ok]: [yes]\n"
    props = android_gather.parse_getprop(sample)
    assert props == {"ro.ok": "yes"}


def test_android_parse_pm_list(android_gather):
    sample = (
        "package:com.example.app\n"
        "package:com.test.foo\n"
    )
    pkgs = android_gather.parse_pm_list(sample)
    assert pkgs == ["com.example.app", "com.test.foo"]


def test_android_parse_pm_list_empty(android_gather):
    assert android_gather.parse_pm_list("") == []


# ── android_security_audit.py (android_security role backend) ────────────

@pytest.fixture(scope="module")
def android_security_audit():
    return _load_module(
        "android_security/files/android_security_audit.py",
        "android_security_audit",
    )


def test_android_security_has_audit_functions(android_security_audit):
    for fn in (
        "parse_sepolicy",
        "parse_dumpsys_package_permissions",
        "parse_keystore_status",
        "parse_verity_status",
        "audit",
    ):
        assert hasattr(android_security_audit, fn), (
            f"android_security_audit missing {fn}"
        )


def test_android_parse_sepolicy(android_security_audit):
    sample = (
        "uid=system() tcontext=u:r:system:s0 tclass=service\n"
        "allow system system_service:service_manager add;\n"
    )
    rules = android_security_audit.parse_sepolicy(sample)
    assert len(rules) >= 1
    assert "allow" in rules[0]["raw"].lower()


def test_android_parse_sepolicy_empty(android_security_audit):
    assert android_security_audit.parse_sepolicy("") == []


def test_android_parse_dumpsys_package_permissions(android_security_audit):
    sample = (
        "Package [com.example.app]\n"
        "  requested permissions:\n"
        "    android.permission.INTERNET\n"
        "    android.permission.CAMERA\n"
        "  install permissions:\n"
        "    android.permission.INTERNET: granted=true\n"
    )
    result = android_security_audit.parse_dumpsys_package_permissions(sample)
    assert "com.example.app" in result
    perms = result["com.example.app"]
    assert "android.permission.CAMERA" in perms["requested"]
    assert "android.permission.INTERNET" in perms["granted"]


def test_android_parse_dumpsys_package_permissions_empty(android_security_audit):
    assert android_security_audit.parse_dumpsys_package_permissions("") == {}


def test_android_parse_keystore_status(android_security_audit):
    sample = (
        "KeystoreService: State: RUNNING\n"
        "KeystoreService: Auth bound: true\n"
    )
    status = android_security_audit.parse_keystore_status(sample)
    assert status["state"] == "RUNNING"
    assert status["auth_bound"] is True


def test_android_parse_keystore_status_empty(android_security_audit):
    status = android_security_audit.parse_keystore_status("")
    assert status == {}


def test_android_parse_verity_status(android_security_audit):
    sample_verity = (
        "[foopercent][1b3d6402a8ad4f10d5af6c7ac9c262f02c2c9f7c4f2f5e1d]\n"
        "Verity mode: ENFORCING\n"
    )
    status = android_security_audit.parse_verity_status(sample_verity)
    assert status["mode"] == "ENFORCING"


def test_android_parse_verity_status_empty(android_security_audit):
    assert android_security_audit.parse_verity_status("") == {}


# ── ios_gather.py (ios_diagnose role backend) ────────────────────────────

@pytest.fixture(scope="module")
def ios_gather():
    return _load_module("ios_diagnose/files/ios_gather.py", "ios_gather")


def test_ios_gather_has_gather_functions(ios_gather):
    for fn in (
        "parse_ideviceinfo",
        "parse_idevicesyslog",
        "parse_idevicediagnostics",
        "gather",
    ):
        assert hasattr(ios_gather, fn), f"ios_gather missing {fn}"


def test_ios_parse_ideviceinfo(ios_gather):
    sample = (
        "ProductType: iPhone15,2\n"
        "ProductName: iPhone OS\n"
        "ProductVersion: 17.0\n"
        "SerialNumber: F2LX1234ABC\n"
    )
    info = ios_gather.parse_ideviceinfo(sample)
    assert info["ProductType"] == "iPhone15,2"
    assert info["ProductVersion"] == "17.0"
    assert info["SerialNumber"] == "F2LX1234ABC"


def test_ios_parse_ideviceinfo_empty(ios_gather):
    assert ios_gather.parse_ideviceinfo("") == {}


def test_ios_parse_idevicesyslog(ios_gather):
    sample = (
        "Jul 15 10:30:45 iPhone SpringBoard[123]: foobar message here\n"
        "Jul 15 10:30:46 iPhone mediaserverd[456]: another entry\n"
    )
    entries = ios_gather.parse_idevicesyslog(sample)
    assert len(entries) == 2
    assert entries[0]["process"] == "SpringBoard"
    assert "foobar" in entries[0]["message"]


def test_ios_parse_idevicesyslog_empty(ios_gather):
    assert ios_gather.parse_idevicesyslog("") == []


def test_ios_parse_idevicediagnostics(ios_gather):
    sample = (
        "DiagnosticsType: All\n"
        "GasGauge: {\n"
        "  CycleCount: 42\n"
        "}\n"
    )
    result = ios_gather.parse_idevicediagnostics(sample)
    assert "diagnostics" in result
    assert result["diagnostics_type"] == "All"


def test_ios_parse_idevicediagnostics_empty(ios_gather):
    result = ios_gather.parse_idevicediagnostics("")
    assert result == {}
