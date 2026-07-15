"""Unit tests for os_expert collection role backends.

Tests the Python gatherer/auditor scripts shipped in each role's files/
directory. The scripts are self-contained (stdlib only) so they can be
copied to target hosts by ansible. Tests load them via importlib from the
file path to avoid polluting the src/ import path.

Covers:
  - android_diagnose: files/android_gather.py (logcat/dumpsys/getprop/pm list parsing)
  - android_security: files/android_security_audit.py (sepolicy/permissions/keystore/dm-verity)
  - ios_diagnose: files/ios_gather.py (ideviceinfo/idevicesyslog/idevicediagnostics/oslog)
  - ios_security: files/ios_security_audit.py (AMFI/trustcache/sandbox/codesign)
  - linux_diagnose: files/linux_gather.py (proc/meminfo/cpuinfo/lsmod/df/dmesg/sysctl)
  - macos_diagnose: files/macos_gather.py (unified log/launchctl/pmset/nvram/profiler)
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


# ── ios_security_audit.py (ios_security role backend) ────────────────────

@pytest.fixture(scope="module")
def ios_security_audit():
    return _load_module(
        "ios_security/files/ios_security_audit.py",
        "ios_security_audit",
    )


def test_ios_security_has_audit_functions(ios_security_audit):
    for fn in (
        "parse_amfi_status",
        "parse_sandbox_profiles",
        "parse_codesign_status",
        "parse_trustcache_status",
        "audit",
    ):
        assert hasattr(ios_security_audit, fn), (
            f"ios_security_audit missing {fn}"
        )


def test_ios_parse_amfi_status(ios_security_audit):
    sample = (
        "EnforcementMode: true\n"
        "DeveloperMode: enabled\n"
        "TrustCacheLoaded: yes\n"
    )
    result = ios_security_audit.parse_amfi_status(sample)
    assert result["enforcing"] is True
    assert result["developer_mode"] is True
    assert result["properties"]["TrustCacheLoaded"] == "yes"


def test_ios_parse_amfi_status_empty(ios_security_audit):
    result = ios_security_audit.parse_amfi_status("")
    assert result["enforcing"] is False
    assert result["developer_mode"] is False
    assert result["properties"] == {}


def test_ios_parse_amfi_status_not_enforcing(ios_security_audit):
    sample = "EnforcementMode: false\nDeveloperMode: disabled\n"
    result = ios_security_audit.parse_amfi_status(sample)
    assert result["enforcing"] is False
    assert result["developer_mode"] is False


def test_ios_parse_sandbox_profiles(ios_security_audit):
    sample = (
        "Container: /private/var/mobile/Containers/Data/Application/abc123\n"
        "BundleID: com.example.app\n"
        "Container: /private/var/mobile/Containers/Data/Application/def456\n"
    )
    result = ios_security_audit.parse_sandbox_profiles(sample)
    assert result["container_count"] == 2
    assert len(result["profiles"]) == 3  # 2 Container + 1 BundleID


def test_ios_parse_sandbox_profiles_violations(ios_security_audit):
    sample = "denied: attempt to access sandbox\n"
    result = ios_security_audit.parse_sandbox_profiles(sample)
    assert result["violations_detected"] is True


def test_ios_parse_sandbox_profiles_empty(ios_security_audit):
    result = ios_security_audit.parse_sandbox_profiles("")
    assert result["container_count"] == 0
    assert result["violations_detected"] is False


def test_ios_parse_codesign_status(ios_security_audit):
    sample = (
        "CDHash: a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2\n"
        "TeamIdentifier: ABCDE12345\n"
        "Signature: valid\n"
        "Authority: iPhone Developer: Test (ABC123)\n"
    )
    result = ios_security_audit.parse_codesign_status(sample)
    assert result["cdhash"] == "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    assert result["team_id"] == "ABCDE12345"
    assert result["valid"] is True
    assert "Authority" in result["certificate_info"]


def test_ios_parse_codesign_status_empty(ios_security_audit):
    result = ios_security_audit.parse_codesign_status("")
    assert result["cdhash"] == ""
    assert result["valid"] is False


def test_ios_parse_trustcache_status(ios_security_audit):
    sample = "Trust cache loaded: yes\nentries: 42\nversion: 2.1\n"
    result = ios_security_audit.parse_trustcache_status(sample)
    assert result["loaded"] is True
    assert result["entries"] == 42
    assert result["version"] == "2.1"


def test_ios_parse_trustcache_status_empty(ios_security_audit):
    result = ios_security_audit.parse_trustcache_status("")
    assert result["loaded"] is False
    assert result["entries"] == 0


# ── linux_gather.py (linux_diagnose role backend) ───────────────────────

@pytest.fixture(scope="module")
def linux_gather():
    return _load_module("linux_diagnose/files/linux_gather.py", "linux_gather")


def test_linux_gather_has_functions(linux_gather):
    for fn in (
        "parse_proc_cpuinfo",
        "parse_proc_meminfo",
        "parse_proc_version",
        "parse_lsmod",
        "parse_df",
        "parse_sysctl",
        "parse_dmesg",
        "gather",
    ):
        assert hasattr(linux_gather, fn), f"linux_gather missing {fn}"


def test_linux_parse_proc_cpuinfo(linux_gather):
    sample = (
        "processor\t: 0\n"
        "vendor_id\t: GenuineIntel\n"
        "cpu family\t: 6\n"
        "model\t\t: 158\n"
        "model name\t: Intel(R) Core(TM) i7-9700K CPU @ 3.60GHz\n"
        "cpu cores\t: 8\n"
        "physical id\t: 0\n"
        "flags\t\t: fpu vme de pse tsc msr pae mce cx8\n"
        "\n"
        "processor\t: 1\n"
        "vendor_id\t: GenuineIntel\n"
        "model name\t: Intel(R) Core(TM) i7-9700K CPU @ 3.60GHz\n"
        "cpu cores\t: 8\n"
        "physical id\t: 0\n"
        "flags\t\t: fpu vme\n"
    )
    result = linux_gather.parse_proc_cpuinfo(sample)
    assert result["processor_count"] == 2
    assert "i7-9700K" in result["model_name"]
    assert result["cores_per_socket"] == 8
    assert len(result["flags"]) > 0


def test_linux_parse_proc_cpuinfo_empty(linux_gather):
    result = linux_gather.parse_proc_cpuinfo("")
    assert result["processor_count"] == 0
    assert result["model_name"] == ""


def test_linux_parse_proc_meminfo(linux_gather):
    sample = (
        "MemTotal:       16384000 kB\n"
        "MemFree:         8192000 kB\n"
        "MemAvailable:   12288000 kB\n"
        "Buffers:          512000 kB\n"
        "Cached:          2048000 kB\n"
    )
    result = linux_gather.parse_proc_meminfo(sample)
    assert result["MemTotal"] == 16384000
    assert result["MemFree"] == 8192000
    assert result["MemAvailable"] == 12288000


def test_linux_parse_proc_meminfo_empty(linux_gather):
    assert linux_gather.parse_proc_meminfo("") == {}


def test_linux_parse_proc_version(linux_gather):
    sample = "Linux version 6.5.0-14-generic (buildd@lcy02-amd64-001) (gcc 13.2.0) #14-Ubuntu SMP\n"
    result = linux_gather.parse_proc_version(sample)
    assert result["kernel_version"] == "6.5.0-14-generic"
    assert "gcc" in result["compiler"]


def test_linux_parse_proc_version_empty(linux_gather):
    result = linux_gather.parse_proc_version("")
    assert result["kernel_version"] == ""


def test_linux_parse_lsmod(linux_gather):
    sample = (
        "Module                  Size  Used by\n"
        "nvidia              35293184  42\n"
        "snd_hda_intel         53248  3\n"
        "xfs                  1634304  1\n"
    )
    result = linux_gather.parse_lsmod(sample)
    assert len(result) == 3
    assert result[0]["module"] == "nvidia"
    assert result[0]["size"] == 35293184
    assert result[1]["module"] == "snd_hda_intel"


def test_linux_parse_lsmod_empty(linux_gather):
    assert linux_gather.parse_lsmod("") == []


def test_linux_parse_df(linux_gather):
    sample = (
        "Filesystem      Size  Used Avail Use% Mounted on\n"
        "/dev/sda1       100G   45G   50G  48% /\n"
        "/dev/sda2       500G  200G  280G  42% /home\n"
        "tmpfs            16G   2G   14G  13% /tmp\n"
    )
    result = linux_gather.parse_df(sample)
    assert len(result) == 3
    assert result[0]["filesystem"] == "/dev/sda1"
    assert result[0]["size"] == "100G"
    assert result[0]["mount"] == "/"
    assert result[1]["mount"] == "/home"


def test_linux_parse_df_empty(linux_gather):
    assert linux_gather.parse_df("") == []


def test_linux_parse_sysctl(linux_gather):
    sample = (
        "kernel.osrelease = 6.5.0-14-generic\n"
        "kernel.hostname = myhost\n"
        "net.ipv4.ip_forward = 1\n"
        "vm.swappiness = 60\n"
    )
    result = linux_gather.parse_sysctl(sample)
    assert result["kernel.osrelease"] == "6.5.0-14-generic"
    assert result["net.ipv4.ip_forward"] == "1"
    assert result["vm.swappiness"] == "60"


def test_linux_parse_sysctl_empty(linux_gather):
    assert linux_gather.parse_sysctl("") == {}


def test_linux_parse_dmesg(linux_gather):
    sample = (
        "[    0.000000] Linux version 6.5.0\n"
        "[    0.001234] ACPI: Power Button [PWRF]\n"
        "[    1.234567] EXT4-fs: mounted filesystem\n"
    )
    result = linux_gather.parse_dmesg(sample)
    assert len(result) == 3
    assert result[0]["timestamp"] == "0.000000"
    assert result[1]["subsystem"] == "ACPI"
    assert "Power Button" in result[1]["message"]


def test_linux_parse_dmesg_empty(linux_gather):
    assert linux_gather.parse_dmesg("") == []


# ── macos_gather.py (macos_diagnose role backend) ───────────────────────

@pytest.fixture(scope="module")
def macos_gather():
    return _load_module("macos_diagnose/files/macos_gather.py", "macos_gather")


def test_macos_gather_has_functions(macos_gather):
    for fn in (
        "parse_unified_log",
        "parse_launchctl_list",
        "parse_pmset",
        "parse_system_profiler",
        "parse_nvram",
        "gather",
    ):
        assert hasattr(macos_gather, fn), f"macos_gather missing {fn}"


def test_macos_parse_unified_log(macos_gather):
    sample = (
        '{"timestamp":"2026-07-15 10:30:45.123","eventType":"logEvent",'
        '"processName":"kernel","categoryName":"default","message":"test msg"}\n'
        '{"timestamp":"2026-07-15 10:30:46.000","eventType":"logEvent",'
        '"processName":"launchd","categoryName":"system","message":"started"}\n'
    )
    result = macos_gather.parse_unified_log(sample)
    assert len(result) == 2
    assert result[0]["processName"] == "kernel"
    assert result[1]["processName"] == "launchd"


def test_macos_parse_unified_log_skips_invalid_json(macos_gather):
    sample = (
        'not json line\n'
        '{"timestamp":"2026-07-15","processName":"test","message":"ok"}\n'
        'also not json\n'
    )
    result = macos_gather.parse_unified_log(sample)
    assert len(result) == 1
    assert result[0]["processName"] == "test"


def test_macos_parse_unified_log_empty(macos_gather):
    assert macos_gather.parse_unified_log("") == []


def test_macos_parse_launchctl_list(macos_gather):
    sample = (
        "PID\tStatus\tLabel\n"
        "123\t0\tcom.apple.launchd\n"
        "456\t-2\tcom.example.app\n"
        "-\t0\tcom.apple.Dock.agent\n"
    )
    result = macos_gather.parse_launchctl_list(sample)
    assert len(result) == 3
    assert result[0]["pid"] == 123
    assert result[0]["label"] == "com.apple.launchd"
    assert result[2]["pid"] == 0
    assert result[2]["label"] == "com.apple.Dock.agent"


def test_macos_parse_launchctl_list_empty(macos_gather):
    assert macos_gather.parse_launchctl_list("") == []


def test_macos_parse_pmset(macos_gather):
    sample = (
        "Active Profiles:\n"
        "Battery Power\t-1*\n"
        "AC Power\t-1\n"
        "Currently in use:\n"
        "sleep\t10\n"
        "displaysleep\t5\n"
        "disksleep\t10\n"
        "\n"
        "Assertions:\n"
        "pid 123(foremand): PreventUserIdleSystemSleep\n"
        "pid 456(mds_stores): BackgroundTask\n"
    )
    result = macos_gather.parse_pmset(sample)
    assert "sleep" in result["settings"]
    assert result["settings"]["sleep"] == 10
    assert result["settings"]["displaysleep"] == 5
    assert len(result["assertions"]) >= 1


def test_macos_parse_pmset_empty(macos_gather):
    result = macos_gather.parse_pmset("")
    assert result["settings"] == {}
    assert result["assertions"] == []


def test_macos_parse_system_profiler(macos_gather):
    sample = (
        "Hardware:\n"
        "\n"
        "  Model Name: MacBook Pro\n"
        "  Model Identifier: MacBookPro18,1\n"
        "  Chip: Apple M1 Pro\n"
        "  Total Number of Cores: 10\n"
        "\n"
        "Software:\n"
        "\n"
        "  System Software Overview:\n"
        "\n"
        "  System Version: macOS 14.0\n"
        "  Kernel Version: Darwin 23.0.0\n"
    )
    result = macos_gather.parse_system_profiler(sample)
    assert result["Model Name"] == "MacBook Pro"
    assert result["Chip"] == "Apple M1 Pro"
    assert result["System Version"] == "macOS 14.0"


def test_macos_parse_system_profiler_empty(macos_gather):
    assert macos_gather.parse_system_profiler("") == {}


def test_macos_parse_nvram(macos_gather):
    sample = (
        "boot-args\tdebug=0x146\n"
        "csr-active-config\t\xef\xbf\xbd%\x00\x00\n"
        "prev-lang:kbd\t en-US:0\n"
    )
    result = macos_gather.parse_nvram(sample)
    assert result["boot-args"] == "debug=0x146"


def test_macos_parse_nvram_empty(macos_gather):
    assert macos_gather.parse_nvram("") == {}
