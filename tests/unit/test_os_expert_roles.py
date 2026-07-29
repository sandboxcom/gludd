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
  - linux_automation: files/linux_automation_audit.py (systemd/cron/logrotate/unattended)
  - windows_automation: files/windows_automation_audit.py (psremoting/dsc/schtasks/software)
  - macos_automation: files/macos_automation_audit.py (launchd/homebrew/defaults/swupdate/profiles)
  - macos_security: files/macos_security_audit.py (csrutil/spctl/xprotect/tccutil/plist)
  - linux_kernel: files/linux_kernel_audit.py (lsmod/modinfo/cgroups/namespaces/ebpf)
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

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


def test_macos_run_spools_command_output_and_caps_decoded_payload(
    macos_gather, monkeypatch
):
    captured = {}

    def fake_run(args, **kwargs):
        captured.update(kwargs)
        kwargs["stdout"].write(b"x" * 64)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(macos_gather.subprocess, "run", fake_run)

    result = macos_gather._run(["log", "show"], max_output_bytes=16)

    assert result == "x" * 16
    assert "capture_output" not in captured
    assert "text" not in captured
    assert captured["stderr"] is macos_gather.subprocess.DEVNULL


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


# ── linux_automation_audit.py (linux_automation role backend) ─────────────

@pytest.fixture(scope="module")
def linux_automation_audit():
    return _load_module(
        "linux_automation/files/linux_automation_audit.py",
        "linux_automation_audit",
    )


def test_linux_automation_has_audit_functions(linux_automation_audit):
    for fn in (
        "parse_systemctl_list_timers",
        "parse_crontab",
        "parse_logrotate_config",
        "parse_unattended_config",
        "audit",
    ):
        assert hasattr(linux_automation_audit, fn), (
            f"linux_automation_audit missing {fn}"
        )


def test_linux_automation_parse_systemctl_list_timers(linux_automation_audit):
    sample = (
        "NEXT     LEFT  LAST     PASSED  UNIT             ACTIVATES\n"
        "Wed 1min Wed   Wed      3min    apt-daily.timer  apt-daily.service\n"
        "Thu 13h  Wed   Wed      10h     logrotate.timer  logrotate.service\n"
        "\n"
        "2 timers listed.\n"
    )
    result = linux_automation_audit.parse_systemctl_list_timers(sample)
    assert len(result) == 2
    assert result[0]["unit"] == "apt-daily.timer"
    assert result[1]["activates"] == "logrotate.service"


def test_linux_automation_parse_systemctl_list_timers_empty(linux_automation_audit):
    assert linux_automation_audit.parse_systemctl_list_timers("") == []


def test_linux_automation_parse_crontab(linux_automation_audit):
    sample = (
        "# /etc/crontab\n"
        "SHELL=/bin/bash\n"
        "PATH=/usr/local/sbin:/usr/local/bin\n"
        "*/5 * * * * root /usr/lib/sysstat/sa1 1 1\n"
        "0 6 * * * root test -x /usr/sbin/anacron || run-parts --report /etc/cron.daily\n"
    )
    result = linux_automation_audit.parse_crontab(sample)
    assert len(result) == 2
    assert result[0]["minute"] == "*/5"
    assert result[0]["command"] == "root /usr/lib/sysstat/sa1 1 1"
    assert result[1]["hour"] == "6"


def test_linux_automation_parse_crontab_empty(linux_automation_audit):
    assert linux_automation_audit.parse_crontab("") == []


def test_linux_automation_parse_crontab_skips_comments(linux_automation_audit):
    sample = "# this is a comment\n# another\n"
    assert linux_automation_audit.parse_crontab(sample) == []


def test_linux_automation_parse_logrotate_config(linux_automation_audit):
    sample = (
        "/var/log/syslog {\n"
        "    rotate 7\n"
        "    daily\n"
        "    missingok\n"
        "    notifempty\n"
        "    compress\n"
        "}\n"
        "\n"
        "/var/log/nginx/*.log {\n"
        "    weekly\n"
        "    rotate 4\n"
        "}\n"
    )
    result = linux_automation_audit.parse_logrotate_config(sample)
    assert len(result) == 2
    assert "/var/log/syslog" in result[0]["paths"]
    assert result[0]["directives"]["rotate"] == "7"
    assert result[1]["directives"]["weekly"] == "true"


def test_linux_automation_parse_logrotate_config_empty(linux_automation_audit):
    assert linux_automation_audit.parse_logrotate_config("") == []


def test_linux_automation_parse_unattended_config_apt(linux_automation_audit):
    sample = (
        'APT::Periodic::Update-Package-Lists "1";\n'
        'APT::Periodic::Unattended-Upgrade "1";\n'
        'APT::Periodic::AutocleanInterval "7";\n'
    )
    result = linux_automation_audit.parse_unattended_config(sample)
    assert result["format"] == "apt"
    assert result["settings"]["APT::Periodic::Update-Package-Lists"] == "1"
    assert result["settings"]["APT::Periodic::Unattended-Upgrade"] == "1"


def test_linux_automation_parse_unattended_config_ini(linux_automation_audit):
    sample = (
        "[commands]\n"
        "upgrade_type = default\n"
        "apply_updates = yes\n"
    )
    result = linux_automation_audit.parse_unattended_config(sample)
    assert result["format"] == "ini"
    assert "commands.upgrade_type" in result["settings"]


def test_linux_automation_parse_unattended_config_empty(linux_automation_audit):
    result = linux_automation_audit.parse_unattended_config("")
    assert result["format"] == "unknown"
    assert result["settings"] == {}


# ── windows_automation_audit.py (windows_automation role backend) ─────────

@pytest.fixture(scope="module")
def windows_automation_audit():
    return _load_module(
        "windows_automation/files/windows_automation_audit.py",
        "windows_automation_audit",
    )


def test_windows_automation_has_audit_functions(windows_automation_audit):
    for fn in (
        "parse_wsman_test",
        "parse_winrm_service",
        "parse_dsc_status",
        "parse_dsc_test",
        "parse_scheduled_tasks",
        "parse_schtasks_raw",
        "parse_installed_software",
        "parse_unattend_detection",
        "audit",
    ):
        assert hasattr(windows_automation_audit, fn), (
            f"windows_automation_audit missing {fn}"
        )


def test_windows_parse_wsman_test(windows_automation_audit):
    sample = json.dumps({
        "ProductVersion": "2.0",
        "ConfigVersion": "2.0",
        "ProductVendor": "Microsoft Corporation",
    })
    result = windows_automation_audit.parse_wsman_test(sample)
    assert result["product_version"] == "2.0"
    assert result["config_version"] == "2.0"


def test_windows_parse_wsman_test_empty(windows_automation_audit):
    result = windows_automation_audit.parse_wsman_test("")
    assert result["product_version"] == ""


def test_windows_parse_winrm_service(windows_automation_audit):
    sample = json.dumps([{
        "Name": "WinRM",
        "Status": "Running",
        "StartType": "Automatic",
    }])
    result = windows_automation_audit.parse_winrm_service(sample)
    assert result["name"] == "WinRM"
    assert result["status"] == "Running"


def test_windows_parse_winrm_service_empty(windows_automation_audit):
    result = windows_automation_audit.parse_winrm_service("")
    assert result["name"] == ""


def test_windows_parse_dsc_status(windows_automation_audit):
    sample = json.dumps([{
        "Status": "Success",
        "StartDate": "2026-07-15T10:00:00",
        "Type": "Consistency",
        "Mode": "ApplyAndAutocorrect",
        "ResourcesInDesiredState": [{"r1": True}],
        "ResourcesNotInDesiredState": [],
    }])
    result = windows_automation_audit.parse_dsc_status(sample)
    assert len(result) == 1
    assert result[0]["status"] == "Success"
    assert result[0]["number_of_resources"] == 1


def test_windows_parse_dsc_status_empty(windows_automation_audit):
    assert windows_automation_audit.parse_dsc_status("") == []


def test_windows_parse_dsc_test(windows_automation_audit):
    sample = json.dumps({"InDesiredState": True})
    result = windows_automation_audit.parse_dsc_test(sample)
    assert result["in_desired_state"] is True


def test_windows_parse_dsc_test_false(windows_automation_audit):
    sample = json.dumps({"InDesiredState": False})
    result = windows_automation_audit.parse_dsc_test(sample)
    assert result["in_desired_state"] is False


def test_windows_parse_scheduled_tasks(windows_automation_audit):
    sample = json.dumps([
        {"TaskName": "GoogleUpdateTaskMachineCore", "State": "Ready", "TaskPath": "\\"},
        {"TaskName": "OneDriveStandaloneUpdateTask", "State": "Disabled", "TaskPath": "\\"},
    ])
    result = windows_automation_audit.parse_scheduled_tasks(sample)
    assert len(result) == 2
    assert result[0]["task_name"] == "GoogleUpdateTaskMachineCore"
    assert result[1]["state"] == "Disabled"


def test_windows_parse_scheduled_tasks_empty(windows_automation_audit):
    assert windows_automation_audit.parse_scheduled_tasks("") == []


def test_windows_parse_schtasks_raw(windows_automation_audit):
    sample = (
        "\n"
        "HostName:                                      DESKTOP-ABC123\n"
        "TaskName:                                      \\GoogleUpdateTaskMachineCore\n"
        "Next Run Time:                                 7/15/2026 12:00:00 PM\n"
        "Status:                                        Ready\n"
        "\n"
        "HostName:                                      DESKTOP-ABC123\n"
        "TaskName:                                      \\BackupTask\n"
        "Status:                                        Disabled\n"
    )
    result = windows_automation_audit.parse_schtasks_raw(sample)
    assert len(result) == 2
    assert result[0]["task_name"] == "\\GoogleUpdateTaskMachineCore"
    assert result[1]["task_name"] == "\\BackupTask"


def test_windows_parse_schtasks_raw_empty(windows_automation_audit):
    assert windows_automation_audit.parse_schtasks_raw("") == []


def test_windows_parse_installed_software(windows_automation_audit):
    sample = json.dumps([
        {"DisplayName": "Google Chrome", "DisplayVersion": "126.0", "Publisher": "Google"},
        {"DisplayName": "7-Zip 23.01", "DisplayVersion": "23.01", "Publisher": "Igor Pavlov"},
        {"DisplayName": "", "DisplayVersion": "1.0"},
    ])
    result = windows_automation_audit.parse_installed_software(sample)
    assert len(result) == 2
    assert result[0]["name"] == "Google Chrome"
    assert result[1]["version"] == "23.01"


def test_windows_parse_installed_software_empty(windows_automation_audit):
    assert windows_automation_audit.parse_installed_software("") == []


def test_windows_parse_unattend_detection(windows_automation_audit):
    sample = json.dumps([
        {"Path": "C:\\Windows\\System32\\sysprep\\unattend.xml", "Exists": True},
        {"Path": "C:\\Windows\\Panther\\unattend.xml", "Exists": False},
    ])
    result = windows_automation_audit.parse_unattend_detection(sample)
    assert len(result) == 2
    assert result[0]["exists"] is True
    assert result[1]["exists"] is False


# ── macos_automation_audit.py (macos_automation role backend) ─────────────

@pytest.fixture(scope="module")
def macos_automation_audit():
    return _load_module(
        "macos_automation/files/macos_automation_audit.py",
        "macos_automation_audit",
    )


def test_macos_automation_has_audit_functions(macos_automation_audit):
    for fn in (
        "parse_launchctl_list",
        "parse_brew_list",
        "parse_brew_outdated",
        "parse_brew_taps",
        "parse_brew_casks",
        "parse_defaults",
        "parse_softwareupdate_list",
        "parse_softwareupdate_history",
        "parse_profiles_list",
        "parse_profiles_status",
        "audit",
    ):
        assert hasattr(macos_automation_audit, fn), (
            f"macos_automation_audit missing {fn}"
        )


def test_macos_automation_parse_launchctl_list(macos_automation_audit):
    sample = (
        "PID\tStatus\tLabel\n"
        "123\t0\tcom.apple.launchd\n"
        "456\t-2\tcom.example.app\n"
        "-\t0\tcom.apple.Dock.agent\n"
    )
    result = macos_automation_audit.parse_launchctl_list(sample)
    assert len(result) == 3
    assert result[0]["pid"] == 123
    assert result[0]["running"] is True
    assert result[2]["pid"] == 0
    assert result[2]["running"] is False


def test_macos_automation_parse_launchctl_list_empty(macos_automation_audit):
    assert macos_automation_audit.parse_launchctl_list("") == []


def test_macos_automation_parse_brew_list(macos_automation_audit):
    sample = (
        "python@3.11 3.11.9\n"
        "node 22.3.0\n"
        "git 2.45.2\n"
    )
    result = macos_automation_audit.parse_brew_list(sample)
    assert len(result) == 3
    assert result[0]["name"] == "python@3.11"
    assert result[0]["version"] == "3.11.9"
    assert result[1]["version"] == "22.3.0"


def test_macos_automation_parse_brew_list_empty(macos_automation_audit):
    assert macos_automation_audit.parse_brew_list("") == []


def test_macos_automation_parse_brew_outdated(macos_automation_audit):
    sample = "python\nnode\ngit\n"
    result = macos_automation_audit.parse_brew_outdated(sample)
    assert len(result) == 3
    assert result[0]["name"] == "python"


def test_macos_automation_parse_brew_taps(macos_automation_audit):
    sample = "homebrew/core\nhomebrew/cask\nhashicorp/tap\n"
    result = macos_automation_audit.parse_brew_taps(sample)
    assert result == ["homebrew/core", "homebrew/cask", "hashicorp/tap"]


def test_macos_automation_parse_brew_casks(macos_automation_audit):
    sample = "google-chrome\nvisual-studio-code\n"
    result = macos_automation_audit.parse_brew_casks(sample)
    assert result == ["google-chrome", "visual-studio-code"]


def test_macos_automation_parse_defaults(macos_automation_audit):
    sample = (
        "{\n"
        '    AppleInterfaceStyle = Dark;\n'
        '    AppleMetricUnits = 1;\n'
        '    AppleMeasurementUnits = "Centimeters";\n'
        "}\n"
    )
    result = macos_automation_audit.parse_defaults(sample)
    assert result["keys"]["AppleInterfaceStyle"] == "Dark"
    assert result["keys"]["AppleMeasurementUnits"] == "Centimeters"


def test_macos_automation_parse_defaults_empty(macos_automation_audit):
    result = macos_automation_audit.parse_defaults("")
    assert result["keys"] == {}


def test_macos_automation_parse_softwareupdate_list(macos_automation_audit):
    sample = (
        "Software Update Tool\n"
        "\n"
        "Finding available software\n"
        "* Label: macOS Sonoma 14.5 Update-12345\n"
        "    Title: macOS Sonoma 14.5 Update, Version: 14.5, Size: 1234567890\n"
    )
    result = macos_automation_audit.parse_softwareupdate_list(sample)
    assert len(result) == 1
    assert result[0]["label"] == "macOS Sonoma 14.5 Update-12345"


def test_macos_automation_parse_softwareupdate_history(macos_automation_audit):
    sample = (
        "Display Name                                        Version    Date       \n"
        "-----------------------------------------------------------\n"
        "macOS Sonoma 14.4.1                                 14.4.1     2026-03-15\n"
        "Safari                                              17.4       2026-03-10\n"
    )
    result = macos_automation_audit.parse_softwareupdate_history(sample)
    assert len(result) >= 1


def test_macos_automation_parse_profiles_status(macos_automation_audit):
    sample = (
        "Enrollment via DEP: Yes\n"
        "Device Enrollment: Enrolled\n"
    )
    result = macos_automation_audit.parse_profiles_status(sample)
    assert "Enrolled" in result["enrollment_status"]


# ── macos_security_audit.py (macos_security role backend) ─────────────────

@pytest.fixture(scope="module")
def macos_security_audit():
    return _load_module(
        "macos_security/files/macos_security_audit.py",
        "macos_security_audit",
    )


def test_macos_security_has_audit_functions(macos_security_audit):
    for fn in (
        "parse_csrutil_status",
        "parse_spctl_status",
        "parse_xprotect",
        "parse_tccutil",
        "parse_plist_policy",
        "audit",
    ):
        assert hasattr(macos_security_audit, fn), (
            f"macos_security_audit missing {fn}"
        )


def test_macos_security_parse_csrutil_enabled(macos_security_audit):
    sample = (
        "System Integrity Protection status: enabled.\n"
        "\n"
        "Configuration: Apple Internal, Developer Tools\n"
    )
    result = macos_security_audit.parse_csrutil_status(sample)
    assert result["sip_enabled"] is True
    assert "Apple Internal" in result["config"]
    assert "Developer Tools" in result["config"]


def test_macos_security_parse_csrutil_disabled(macos_security_audit):
    sample = "System Integrity Protection status: disabled.\n"
    result = macos_security_audit.parse_csrutil_status(sample)
    assert result["sip_enabled"] is False


def test_macos_security_parse_csrutil_empty(macos_security_audit):
    result = macos_security_audit.parse_csrutil_status("")
    assert result["sip_enabled"] is False
    assert result["config"] == []


def test_macos_security_parse_spctl_enabled(macos_security_audit):
    sample = "assessments enabled\n"
    result = macos_security_audit.parse_spctl_status(sample)
    assert result["assessments_enabled"] is True
    assert result["gatekeeper_active"] is True


def test_macos_security_parse_spctl_disabled(macos_security_audit):
    sample = "assessments disabled\n"
    result = macos_security_audit.parse_spctl_status(sample)
    assert result["assessments_enabled"] is False


def test_macos_security_parse_xprotect(macos_security_audit):
    sample = (
        "{\n"
        "    version = 5253;\n"
        "    extension = XProtect;\n"
        "}\n"
    )
    result = macos_security_audit.parse_xprotect(sample)
    assert result["version"] == "5253"


def test_macos_security_parse_xprotect_empty(macos_security_audit):
    result = macos_security_audit.parse_xprotect("")
    assert result["version"] == ""


def test_macos_security_parse_tccutil(macos_security_audit):
    sample = (
        "=== tccutil list Camera ===\n"
        "com.apple.Safari\n"
        "com.apple.FaceTime\n"
        "\n"
        "=== tccutil list Accessibility ===\n"
        "com.example.app\n"
    )
    result = macos_security_audit.parse_tccutil(sample)
    assert "Camera" in result
    assert len(result["Camera"]) == 2
    assert result["Camera"][0]["client"] == "com.apple.Safari"
    assert "Accessibility" in result
    assert result["Accessibility"][0]["client"] == "com.example.app"


def test_macos_security_parse_tccutil_empty(macos_security_audit):
    result = macos_security_audit.parse_tccutil("")
    assert result == {}


def test_macos_security_parse_plist_policy(macos_security_audit):
    sample = (
        "=== com.example.mdm.plist ===\n"
        '    "AllowCamera" => 1\n'
        '    "DisableCloudSync" => 0\n'
        "=== com.example.security.plist ===\n"
        '    "FirewallEnabled" => 1\n'
    )
    result = macos_security_audit.parse_plist_policy(sample)
    assert "com.example.mdm.plist" in result["profiles"]
    assert "com.example.security.plist" in result["profiles"]


def test_macos_security_parse_plist_policy_empty(macos_security_audit):
    result = macos_security_audit.parse_plist_policy("")
    assert result["profiles"] == {}


# ── linux_kernel_audit.py (linux_kernel role backend) ─────────────────────

@pytest.fixture(scope="module")
def linux_kernel_audit():
    return _load_module(
        "linux_kernel/files/linux_kernel_audit.py",
        "linux_kernel_audit",
    )


def test_linux_kernel_has_audit_functions(linux_kernel_audit):
    for fn in (
        "parse_lsmod",
        "parse_modinfo",
        "parse_proc_cgroups",
        "parse_proc_pid_cgroup",
        "parse_lsns",
        "parse_proc_ns_listing",
        "parse_bpftool_prog",
        "parse_findmnt_cgroup",
        "parse_sysctl",
        "audit",
    ):
        assert hasattr(linux_kernel_audit, fn), (
            f"linux_kernel_audit missing {fn}"
        )


def test_linux_kernel_parse_lsmod(linux_kernel_audit):
    sample = (
        "Module                  Size  Used by\n"
        "nvidia              35293184  42\n"
        "snd_hda_intel         53248  3 snd_hda_codec,snd_hda_core\n"
        "xfs                  1634304  1\n"
    )
    result = linux_kernel_audit.parse_lsmod(sample)
    assert len(result) == 3
    assert result[0]["module"] == "nvidia"
    assert result[0]["size"] == 35293184
    assert result[1]["used_by"] == ["snd_hda_codec", "snd_hda_core"]


def test_linux_kernel_parse_lsmod_empty(linux_kernel_audit):
    assert linux_kernel_audit.parse_lsmod("") == []


def test_linux_kernel_parse_modinfo(linux_kernel_audit):
    sample = (
        "filename:       /lib/modules/6.5.0/kernel/drivers/gpu/nvidia.ko\n"
        "version:        550.54.14\n"
        "license:        NVIDIA\n"
        "description:    nvidia\n"
        "depends:        drm,drm_kms_helper\n"
        "retpoline:      Y\n"
        "name:           nvidia\n"
    )
    result = linux_kernel_audit.parse_modinfo(sample)
    assert result["filename"] == "/lib/modules/6.5.0/kernel/drivers/gpu/nvidia.ko"
    assert result["version"] == "550.54.14"
    assert result["license"] == "NVIDIA"
    assert "drm" in result["depends"]
    assert "retpoline" in result["properties"]


def test_linux_kernel_parse_modinfo_empty(linux_kernel_audit):
    result = linux_kernel_audit.parse_modinfo("")
    assert result["filename"] == ""
    assert result["depends"] == []


def test_linux_kernel_parse_proc_cgroups(linux_kernel_audit):
    sample = (
        "#subsys_name\thierarchy\tnum_cgroups\tenabled\n"
        "cpuset\t0\t1\t1\n"
        "cpu\t0\t1\t1\n"
        "cpuacct\t0\t1\t1\n"
        "memory\t0\t1\t1\n"
    )
    result = linux_kernel_audit.parse_proc_cgroups(sample)
    assert len(result) == 4
    assert result[0]["subsystem"] == "cpuset"
    assert result[3]["subsystem"] == "memory"


def test_linux_kernel_parse_proc_cgroups_empty(linux_kernel_audit):
    assert linux_kernel_audit.parse_proc_cgroups("") == []


def test_linux_kernel_parse_proc_pid_cgroup(linux_kernel_audit):
    sample = (
        "0::/init.scope\n"
        "1:memory:/user.slice\n"
        "0::/system.slice/sshd.service\n"
    )
    result = linux_kernel_audit.parse_proc_pid_cgroup(sample)
    assert len(result) == 3
    assert result[0]["hierarchy_id"] == "0"
    assert result[0]["controllers"] == ""
    assert result[1]["controllers"] == "memory"


def test_linux_kernel_parse_lsns(linux_kernel_audit):
    sample = json.dumps({
        "namespaces": [
            {
                "type": "pid", "nstype": "pid", "path": "/proc/1/ns/pid",
                "nprocs": 5, "pid": 1, "command": "systemd", "uid": 0,
            },
            {
                "type": "net", "nstype": "net", "path": "/proc/1/ns/net",
                "nprocs": 1, "pid": 1, "command": "systemd", "uid": 0,
            },
        ]
    })
    result = linux_kernel_audit.parse_lsns(sample)
    assert len(result) == 2
    assert result[0]["ns_type"] == "pid"
    assert result[0]["nprocs"] == 5
    assert result[1]["ns_type"] == "net"


def test_linux_kernel_parse_lsns_empty(linux_kernel_audit):
    assert linux_kernel_audit.parse_lsns("") == []


def test_linux_kernel_parse_proc_ns_listing(linux_kernel_audit):
    sample = (
        "total 0\n"
        "dr-x--x--x 2 root root 0 Jul 15 10:00 .\n"
        "dr-xr-xr-x 9 root root 0 Jul 15 10:00 ..\n"
        "lrwxrwxrwx 1 root root 0 Jul 15 10:00 net -> net:[4026532000]\n"
        "lrwxrwxrwx 1 root root 0 Jul 15 10:00 pid -> pid:[4026532001]\n"
        "lrwxrwxrwx 1 root root 0 Jul 15 10:00 mnt -> mnt:[4026532002]\n"
    )
    result = linux_kernel_audit.parse_proc_ns_listing(sample)
    assert len(result) == 3
    assert result[0]["type"] == "net"
    assert "net:[4026532000]" in result[0]["inode"]


def test_linux_kernel_parse_bpftool_prog(linux_kernel_audit):
    sample = (
        "13: cgroup_sock  tag a1b2c3d4e5f6a1b2  gpl\n"
        "    loaded_at 2026-07-15T10:00:00-0000  uid 0\n"
        "    xlated 152B  jited 104B  mem 4096B\n"
        "    btf_id 42  pids systemd(1)\n"
        "\n"
        "27: kprobe        tag f1e2d3c4b5a6f7e8  gpl\n"
        "    loaded_at 2026-07-15T09:00:00-0000  uid 0\n"
        "    xlated 256B  jited 200B  mem 4096B\n"
    )
    result = linux_kernel_audit.parse_bpftool_prog(sample)
    assert len(result) == 2
    assert result[0]["id"] == 13
    assert result[0]["type"] == "cgroup_sock"
    assert result[0]["license"] == "GPL"
    assert result[0]["bytes_xlated"] == 152
    assert result[1]["type"] == "kprobe"


def test_linux_kernel_parse_bpftool_prog_empty(linux_kernel_audit):
    assert linux_kernel_audit.parse_bpftool_prog("") == []


def test_linux_kernel_parse_findmnt_cgroup(linux_kernel_audit):
    sample = (
        "TARGET          SOURCE                     FSTYPE  OPTIONS\n"
        "/sys/fs/cgroup  cgroup                     cgroup  rw,nosuid,nodev\n"
        "/sys/fs/cgroup  cgroup2                    cgroup2 rw,nosuid,nodev\n"
    )
    result = linux_kernel_audit.parse_findmnt_cgroup(sample)
    assert len(result) == 2
    assert result[0]["target"] == "/sys/fs/cgroup"
    assert "cgroup" in result[0]["fstype"]


def test_linux_kernel_parse_sysctl(linux_kernel_audit):
    sample = (
        "kernel.osrelease = 6.5.0-14-generic\n"
        "net.ipv4.ip_forward = 1\n"
        "vm.swappiness = 60\n"
    )
    result = linux_kernel_audit.parse_sysctl(sample)
    assert result["kernel.osrelease"] == "6.5.0-14-generic"
    assert result["net.ipv4.ip_forward"] == "1"
