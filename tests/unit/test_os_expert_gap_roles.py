"""Unit tests for os_expert role backends that were previously missing.

Covers:
  - linux_security: files/linux_security_audit.py (SELinux/AppArmor/firewall/auditd/PAM/kernel/ports)
  - windows_security: files/windows_security_audit.py (Defender/firewall/auditpol/secedit/hotfix)
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_ROLES = Path(__file__).resolve().parents[2] / (
    "collections/ansible_collections/general_ludd/os_expert/roles"
)


def _load_module(rel_path: str, mod_name: str):
    full = _ROLES / rel_path
    if not full.exists():
        pytest.skip(f"backend script not found: {full}")
    spec = importlib.util.spec_from_file_location(mod_name, full)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── linux_security_audit.py ──────────────────────────────────────────────

@pytest.fixture(scope="module")
def linux_security_audit():
    return _load_module("linux_security/files/linux_security_audit.py", "linux_security_audit")


def test_linux_security_has_audit_functions(linux_security_audit):
    for fn in (
        "parse_getenforce",
        "parse_sestatus",
        "parse_semanage_booleans",
        "parse_aa_status",
        "parse_iptables_rules",
        "parse_auditctl_rules",
        "parse_auditctl_status",
        "parse_pam_config",
        "parse_kernel_params",
        "parse_listening_ports",
        "assess_findings",
        "main",
    ):
        assert hasattr(linux_security_audit, fn), f"linux_security_audit missing {fn}"


# ── SELinux parsing ──────────────────────────────────────────────────────

def test_linux_security_parse_getenforce_enforcing(linux_security_audit):
    result = linux_security_audit.parse_getenforce("Enforcing")
    assert result["mode"] == "enforcing"


def test_linux_security_parse_getenforce_permissive(linux_security_audit):
    result = linux_security_audit.parse_getenforce("Permissive")
    assert result["mode"] == "permissive"


def test_linux_security_parse_getenforce_disabled(linux_security_audit):
    result = linux_security_audit.parse_getenforce("Disabled")
    assert result["mode"] == "disabled"


def test_linux_security_parse_getenforce_empty(linux_security_audit):
    result = linux_security_audit.parse_getenforce("")
    assert result["mode"] == "unknown"


def test_linux_security_parse_sestatus(linux_security_audit):
    sample = (
        "SELinux status:                 enabled\n"
        "SELinuxfs mount:                /sys/fs/selinux\n"
        "SELinux root directory:         /etc/selinux\n"
        "Loaded policy name:             targeted\n"
        "Current mode:                   enforcing\n"
        "Mode from config file:          enforcing\n"
        "Policy MLS status:              enabled\n"
        "Policy deny_unknown status:     allowed\n"
        "Memory protection checking:     actual (secure)\n"
        "Max kernel policy version:      33\n"
    )
    result = linux_security_audit.parse_sestatus(sample)
    assert result["status"] == "enabled"
    assert result["current_mode"] == "enforcing"
    assert result["config_mode"] == "enforcing"
    assert result["loaded_policy"] == "targeted"


def test_linux_security_parse_sestatus_empty(linux_security_audit):
    assert linux_security_audit.parse_sestatus("") == {}


def test_linux_security_parse_semanage_booleans(linux_security_audit):
    sample = (
        "SELinux boolean                State  Default Description\n"
        "httpd_enable_cgi                (on   ,   on)  Allow httpd cgi\n"
        "httpd_can_network_connect       (off  ,  off)  Allow httpd network\n"
        "ssh_sysadm_login                (on   ,  off)  Allow ssh login as sysadm\n"
    )
    result = linux_security_audit.parse_semanage_booleans(sample)
    assert len(result) == 3
    assert result[0]["name"] == "httpd_enable_cgi"
    assert result[0]["state"] is True
    assert result[0]["default"] is True
    assert result[1]["state"] is False
    assert result[2]["name"] == "ssh_sysadm_login"


def test_linux_security_parse_semanage_booleans_empty(linux_security_audit):
    assert linux_security_audit.parse_semanage_booleans("") == []


# ── AppArmor parsing ─────────────────────────────────────────────────────

def test_linux_security_parse_aa_status(linux_security_audit):
    sample = (
        "apparmor module is loaded.\n"
        "5 profiles are loaded.\n"
        "2 profiles are in enforce mode.\n"
        "   /usr/bin/man\n"
        "   /usr/sbin/cupsd\n"
        "3 profiles are in complain mode.\n"
        "   /usr/sbin/nmbd\n"
        "   /usr/sbin/sshd\n"
        "   /sbin/dhclient\n"
        "0 profiles are in kill mode.\n"
        "0 profiles are in unconfined mode.\n"
        "2 processes have profiles defined.\n"
    )
    result = linux_security_audit.parse_aa_status(sample)
    assert result["loaded"] is True
    assert result["profiles_loaded"] == 5
    assert result["profiles_enforce"] == 2
    assert result["profiles_complain"] == 3
    assert len(result["enforce_profiles"]) == 2
    assert "/usr/bin/man" in result["enforce_profiles"]
    assert len(result["complain_profiles"]) == 3


def test_linux_security_parse_aa_status_empty(linux_security_audit):
    assert linux_security_audit.parse_aa_status("") == {}


def test_linux_security_parse_aa_status_not_loaded(linux_security_audit):
    sample = "apparmor module is not loaded.\n"
    result = linux_security_audit.parse_aa_status(sample)
    assert result["loaded"] is False


# ── Firewall parsing ─────────────────────────────────────────────────────

def test_linux_security_parse_iptables_rules(linux_security_audit):
    sample = (
        "Chain INPUT (policy ACCEPT)\n"
        " pkts bytes target     prot opt in     out     source               destination\n"
        "  123  4567 ACCEPT     tcp  --  *      *       0.0.0.0/0            0.0.0.0/0            tcp dpt:22\n"
        "   89  3456 ACCEPT     tcp  --  *      *       0.0.0.0/0            0.0.0.0/0            tcp dpt:80\n"
        " 5000 23456 DROP       all  --  *      *       0.0.0.0/0            0.0.0.0/0\n"
    )
    result = linux_security_audit.parse_iptables_rules(sample, "filter", "INPUT")
    assert result["chain"] == "INPUT"
    assert result["table"] == "filter"
    assert result["policy"] == "ACCEPT"
    assert len(result["rules"]) == 3
    assert result["rules"][0]["target"] == "ACCEPT"
    assert result["rules"][0]["dport"] == "22"
    assert result["rules"][2]["target"] == "DROP"


def test_linux_security_parse_iptables_rules_empty(linux_security_audit):
    result = linux_security_audit.parse_iptables_rules("", "filter", "INPUT")
    assert result["rules"] == []


# ── auditd parsing ───────────────────────────────────────────────────────

def test_linux_security_parse_auditctl_rules(linux_security_audit):
    sample = (
        "-a always,exit -F arch=b64 -S execve -k exec\n"
        "-w /etc/passwd -p wa -k identity\n"
        "-w /etc/shadow -p wa -k identity\n"
        "-a always,exit -F arch=b64 -S open -F dir=/etc -k file-access\n"
    )
    result = linux_security_audit.parse_auditctl_rules(sample)
    assert len(result) == 4
    assert result[0]["type"] == "syscall"
    assert result[0]["syscall"] == "execve"
    assert result[0]["key"] == "exec"
    assert result[1]["type"] == "file_watch"
    assert result[1]["path"] == "/etc/passwd"
    assert result[1]["permissions"] == "wa"


def test_linux_security_parse_auditctl_rules_empty(linux_security_audit):
    assert linux_security_audit.parse_auditctl_rules("") == []


def test_linux_security_parse_auditctl_status(linux_security_audit):
    sample = (
        "enabled 1\n"
        "flag 1\n"
        "pid 1234\n"
        "rate_limit 0\n"
        "backlog_limit 8192\n"
        "lost 0\n"
        "backlog 0\n"
        "loginuid_immutable 0 unlocked\n"
        "failure 1\n"
    )
    result = linux_security_audit.parse_auditctl_status(sample)
    assert result["enabled"] is True
    assert result["failure_flag"] is True
    assert result["lost"] == 0
    assert result["backlog_limit"] == 8192


def test_linux_security_parse_auditctl_status_disabled(linux_security_audit):
    sample = "enabled 0\nflag 0\n"
    result = linux_security_audit.parse_auditctl_status(sample)
    assert result["enabled"] is False


# ── PAM parsing ──────────────────────────────────────────────────────────

def test_linux_security_parse_pam_config(linux_security_audit):
    sample = (
        "auth     required pam_unix.so nullok_secure\n"
        "auth     required pam_faillock.so preauth silent audit deny=5 unlock_time=900\n"
        "account  required pam_unix.so\n"
        "password required pam_pwquality.so retry=3 minlen=12 dcredit=-1 ucredit=-1\n"
        "password required pam_unix.so obscure sha512 remember=5\n"
        "session  required pam_unix.so\n"
    )
    result = linux_security_audit.parse_pam_config(sample, "common-auth")
    assert result["file"] == "common-auth"
    assert len(result["lines"]) == 6
    assert result["has_faillock"] is True
    assert result["has_pwquality"] is True
    assert result["faillock_deny"] == 5


def test_linux_security_parse_pam_config_minimal(linux_security_audit):
    sample = "auth required pam_unix.so\n"
    result = linux_security_audit.parse_pam_config(sample, "system-auth")
    assert result["has_faillock"] is False
    assert result["has_pwquality"] is False


def test_linux_security_parse_pam_config_empty(linux_security_audit):
    assert linux_security_audit.parse_pam_config("", "empty")["lines"] == []


# ── Kernel params parsing ────────────────────────────────────────────────

def test_linux_security_parse_kernel_params(linux_security_audit):
    sample = (
        "kptr_restrict: 2\n"
        "dmesg_restrict: 1\n"
        "yama/ptrace_scope: 1\n"
        "perf_event_paranoid: 2\n"
        "unprivileged_bpf_disabled: 1\n"
        "unprivileged_userns_clone: 0\n"
        "modules_disabled: 0\n"
        "randomize_va_space: 2\n"
    )
    result = linux_security_audit.parse_kernel_params(sample)
    assert result["kptr_restrict"] == 2
    assert result["dmesg_restrict"] == 1
    assert result["ptrace_scope"] == 1
    assert result["perf_event_paranoid"] == 2
    assert result["unprivileged_bpf_disabled"] == 1
    assert result["randomize_va_space"] == 2


def test_linux_security_parse_kernel_params_empty(linux_security_audit):
    assert linux_security_audit.parse_kernel_params("") == {}


def test_linux_security_parse_kernel_params_unknown_values(linux_security_audit):
    sample = "kptr_restrict: N/A\nrandomize_va_space: N/A\n"
    result = linux_security_audit.parse_kernel_params(sample)
    assert result == {}


# ── Listening ports parsing ──────────────────────────────────────────────

def test_linux_security_parse_listening_ports(linux_security_audit):
    sample = (
        "LISTEN    0       128        0.0.0.0:22             0.0.0.0:*\n"
        "LISTEN    0       128              *:443                   *:*\n"
        "LISTEN    0       128        127.0.0.1:5432           0.0.0.0:*\n"
        "LISTEN    0       128        0.0.0.0:8080            0.0.0.0:*\n"
    )
    result = linux_security_audit.parse_listening_ports(sample)
    assert len(result) == 4
    assert result[0]["port"] == 22
    assert result[0]["bind_address"] == "0.0.0.0"
    assert result[1]["port"] == 443
    assert result[2]["bind_address"] == "127.0.0.1"
    assert result[2]["port"] == 5432


def test_linux_security_parse_listening_ports_empty(linux_security_audit):
    assert linux_security_audit.parse_listening_ports("") == []


# ── Findings assessment ──────────────────────────────────────────────────

def test_linux_security_assess_findings_hardened(linux_security_audit):
    data = {
        "selinux": {"mode": "enforcing", "status": "enabled"},
        "apparmor": {"loaded": True, "profiles_loaded": 5},
        "auditd": {"enabled": True, "failure_flag": True},
        "kernel": {
            "kptr_restrict": 2,
            "dmesg_restrict": 1,
            "ptrace_scope": 1,
            "randomize_va_space": 2,
        },
        "pam": [{"has_faillock": True, "has_pwquality": True, "faillock_deny": 3}],
        "ports": [{"port": 22, "bind_address": "0.0.0.0"}],
        "iptables": [],
    }
    findings = linux_security_audit.assess_findings(data)
    assert isinstance(findings, list)
    # Should have few or no high-severity findings for a well-hardened system
    high = [f for f in findings if f.get("severity") == "high"]
    critical = [f for f in findings if f.get("severity") == "critical"]
    assert len(high) <= 2
    assert len(critical) == 0


def test_linux_security_assess_findings_insecure(linux_security_audit):
    data = {
        "selinux": {"mode": "disabled"},
        "apparmor": {"loaded": False},
        "auditd": {"enabled": False},
        "kernel": {
            "kptr_restrict": 0,
            "dmesg_restrict": 0,
            "ptrace_scope": 0,
            "randomize_va_space": 0,
        },
        "pam": [{"has_faillock": False, "has_pwquality": False}],
        "ports": [
            {"port": 22, "bind_address": "0.0.0.0"},
            {"port": 23, "bind_address": "0.0.0.0"},
        ],
        "iptables": [],
    }
    findings = linux_security_audit.assess_findings(data)
    assert len(findings) > 0
    high_critical = [f for f in findings if f.get("severity") in ("high", "critical")]
    assert len(high_critical) > 0


# ── windows_security_audit.py ────────────────────────────────────────────

@pytest.fixture(scope="module")
def windows_security_audit():
    return _load_module("windows_security/files/windows_security_audit.py", "windows_security_audit")


def test_windows_security_has_audit_functions(windows_security_audit):
    for fn in (
        "parse_defender_prefs",
        "parse_defender_status",
        "parse_defender_threats",
        "parse_firewall_rules",
        "parse_firewall_profiles",
        "parse_auditpol",
        "parse_secedit",
        "parse_hotfixes",
        "assess_findings",
        "main",
    ):
        assert hasattr(windows_security_audit, fn), f"windows_security_audit missing {fn}"


# ── Defender parsing ─────────────────────────────────────────────────────

def test_windows_security_parse_defender_prefs(windows_security_audit):
    sample = {
        "DisableRealtimeMonitoring": False,
        "DisableBehaviorMonitoring": False,
        "DisableBlockAtFirstSeen": False,
        "DisableIOAVProtection": False,
        "SubmitSamplesConsent": 1,
        "MAPSReporting": 2,
    }
    result = windows_security_audit.parse_defender_prefs(json.dumps(sample))
    assert result["realtime_protection_enabled"] is True
    assert result["behavior_monitoring_enabled"] is True
    assert result["cloud_protection_enabled"] is True


def test_windows_security_parse_defender_prefs_disabled(windows_security_audit):
    sample = {
        "DisableRealtimeMonitoring": True,
        "DisableBehaviorMonitoring": True,
    }
    result = windows_security_audit.parse_defender_prefs(json.dumps(sample))
    assert result["realtime_protection_enabled"] is False
    assert result["behavior_monitoring_enabled"] is False


def test_windows_security_parse_defender_prefs_empty(windows_security_audit):
    assert windows_security_audit.parse_defender_prefs("") == {}


def test_windows_security_parse_defender_status(windows_security_audit):
    sample = {
        "AntivirusEnabled": True,
        "AMServiceEnabled": True,
        "RealTimeProtectionEnabled": True,
        "AntispywareEnabled": True,
    }
    result = windows_security_audit.parse_defender_status(json.dumps(sample))
    assert result["av_enabled"] is True
    assert result["rtp_enabled"] is True
    assert result["am_enabled"] is True


def test_windows_security_parse_defender_status_disabled(windows_security_audit):
    sample = {"AntivirusEnabled": False, "RealTimeProtectionEnabled": False}
    result = windows_security_audit.parse_defender_status(json.dumps(sample))
    assert result["av_enabled"] is False


def test_windows_security_parse_defender_threats(windows_security_audit):
    sample = [
        {"ThreatName": "Trojan:Win32/FakeAV", "Severity": "Severe", "ActionTaken": "Quarantine"},
        {"ThreatName": "PUA:Win32/InstallCore", "Severity": "Low", "ActionTaken": "Allow"},
    ]
    result = windows_security_audit.parse_defender_threats(json.dumps(sample))
    assert len(result) == 2
    assert result[0]["threat"] == "Trojan:Win32/FakeAV"
    assert result[0]["severity"] == "Severe"
    assert result[0]["resolved"] is True


def test_windows_security_parse_defender_threats_unresolved(windows_security_audit):
    sample = [{"ThreatName": "Test", "ActionTaken": "NoAction"}]
    result = windows_security_audit.parse_defender_threats(json.dumps(sample))
    assert result[0]["resolved"] is False


def test_windows_security_parse_defender_threats_empty(windows_security_audit):
    assert windows_security_audit.parse_defender_threats("[]") == []


# ── Firewall parsing ─────────────────────────────────────────────────────

def test_windows_security_parse_firewall_rules(windows_security_audit):
    sample = [
        {"Name": "SSH", "Action": "Allow", "Direction": "Inbound", "Protocol": "TCP", "LocalPort": 22},
        {"Name": "BlockRDP", "Action": "Block", "Direction": "Inbound", "Protocol": "TCP", "LocalPort": 3389},
    ]
    result = windows_security_audit.parse_firewall_rules(json.dumps(sample))
    assert len(result) == 2
    assert result[0]["name"] == "SSH"
    assert result[0]["action"] == "Allow"
    assert result[1]["action"] == "Block"


def test_windows_security_parse_firewall_rules_empty(windows_security_audit):
    assert windows_security_audit.parse_firewall_rules("[]") == []


def test_windows_security_parse_firewall_profiles(windows_security_audit):
    sample = [
        {"Name": "Domain", "Enabled": True, "DefaultInboundAction": "Block"},
        {"Name": "Private", "Enabled": True, "DefaultInboundAction": "Block"},
        {"Name": "Public", "Enabled": True, "DefaultInboundAction": "Allow"},
    ]
    result = windows_security_audit.parse_firewall_profiles(json.dumps(sample))
    assert len(result) == 3
    assert result[0]["name"] == "Domain"
    assert result[0]["enabled"] is True
    assert result[0]["default_inbound"] == "Block"
    assert result[2]["default_inbound"] == "Allow"


# ── Audit policy parsing ─────────────────────────────────────────────────

def test_windows_security_parse_auditpol(windows_security_audit):
    sample = (
        "System audit policy\n"
        "Category/Subcategory                      Setting\n"
        "  Security System Extension              Success and Failure\n"
        "  System Integrity                       Success and Failure\n"
        "  Logon                                  Success and Failure\n"
        "  Account Logon                          No Auditing\n"
        "  Object Access                          No Auditing\n"
    )
    result = windows_security_audit.parse_auditpol(sample)
    assert len(result) == 5
    assert result[0]["category"] == "Security System Extension"
    assert result[0]["setting"] == "Success and Failure"
    assert result[3]["setting"] == "No Auditing"


def test_windows_security_parse_auditpol_empty(windows_security_audit):
    assert windows_security_audit.parse_auditpol("") == []


# ── Security config parsing ──────────────────────────────────────────────

def test_windows_security_parse_secedit(windows_security_audit):
    sample = (
        "[System Access]\n"
        "MinimumPasswordAge = 1\n"
        "MaximumPasswordAge = 42\n"
        "MinimumPasswordLength = 8\n"
        "PasswordComplexity = 1\n"
        "LockoutBadCount = 5\n"
        "ResetLockoutCount = 30\n"
        "LockoutDuration = 30\n"
    )
    result = windows_security_audit.parse_secedit(sample)
    assert result["MinimumPasswordLength"] == "8"
    assert result["PasswordComplexity"] == "1"
    assert result["LockoutBadCount"] == "5"
    assert result["MaximumPasswordAge"] == "42"


def test_windows_security_parse_secedit_empty(windows_security_audit):
    assert windows_security_audit.parse_secedit("") == {}


# ── Hotfix parsing ───────────────────────────────────────────────────────

def test_windows_security_parse_hotfixes(windows_security_audit):
    sample = [
        {"HotFixID": "KB5001234", "InstalledOn": "2026-07-14", "Description": "Security Update"},
        {"HotFixID": "KB5005678", "InstalledOn": "2026-07-10", "Description": "Update"},
    ]
    result = windows_security_audit.parse_hotfixes(json.dumps(sample))
    assert len(result) == 2
    assert result[0]["id"] == "KB5001234"
    assert result[0]["date"] == "2026-07-14"
    assert result[0]["description"] == "Security Update"


def test_windows_security_parse_hotfixes_empty(windows_security_audit):
    assert windows_security_audit.parse_hotfixes("[]") == []


# ── Findings assessment ──────────────────────────────────────────────────

def test_windows_security_assess_findings_secure(windows_security_audit):
    data = {
        "defender_prefs": {"realtime_protection_enabled": True, "behavior_monitoring_enabled": True},
        "defender_status": {"av_enabled": True, "rtp_enabled": True},
        "defender_threats": [],
        "firewall_profiles": [{"enabled": True, "default_inbound": "Block"}],
        "auditpol": [{"setting": "Success and Failure"} for _ in range(5)],
        "secedit": {"PasswordComplexity": "1", "MinimumPasswordLength": "12"},
        "hotfixes": [{"id": "KB5001234", "date": "2026-07-14"}],
    }
    findings = windows_security_audit.assess_findings(data)
    high_critical = [f for f in findings if f.get("severity") in ("high", "critical")]
    assert len(high_critical) == 0


def test_windows_security_assess_findings_insecure(windows_security_audit):
    data = {
        "defender_prefs": {"realtime_protection_enabled": False},
        "defender_status": {"av_enabled": False, "rtp_enabled": False},
        "defender_threats": [{"threat": "Bad", "resolved": False}],
        "firewall_profiles": [{"enabled": False, "default_inbound": "Allow"}],
        "auditpol": [{"setting": "No Auditing"}],
        "secedit": {"PasswordComplexity": "0", "MinimumPasswordLength": "4"},
        "hotfixes": [],
    }
    findings = windows_security_audit.assess_findings(data)
    high_critical = [f for f in findings if f.get("severity") in ("high", "critical")]
    assert len(high_critical) > 0
