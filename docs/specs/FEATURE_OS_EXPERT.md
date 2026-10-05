# Feature: OS Expert Collection

**Status: IMPLEMENTED** | **Created: 2026-07-14** | **Target: v0.1.0-beta.2**

## 1. Overview

Ansible collection `general_ludd.os_expert` providing roles that diagnose, audit,
and automate OS-level subsystems. Leverages existing connectors in
`src/general_ludd/connectors/` (proc_sys, mac_unified_log, journald, windows_event_log,
auditd, osquery, dmesg, syslog_file) and adds new connectors for gaps.

**Coverage**: macOS, Linux, Windows, Android, iOS, DOS/PowerShell.

## 2. Roles (12, 2 per OS)

| Role | OS | Purpose |
|------|----|---------|
| `macos_diagnose` | macOS | gather: unified log, spdisaster_report, launchctl, nvram, pmset |
| `macos_security` | macOS | audit: csrutil status, spctl, xprotect, tccutil, plist policy |
| `linux_diagnose` | Linux | gather: /proc/*, journalctl, sysfs, dmesg, lsmod, sysctl |
| `linux_kernel` | Linux | manage: modprobe, eBPF, namespaces, cgroups, sysctl tunables |
| `windows_diagnose` | Win | query: Get-WmiObject, Get-EventLog, reg query, sc query |
| `windows_security` | Win | audit: Get-MpPreference, Get-FirewallRule, auditpol, secedit |
| `android_diagnose` | Android | adb: logcat, dumpsys, getprop, pm list |
| `android_security` | Android | sepolicy-inject, pm permission audit, keystore, dm-verity |
| `ios_diagnose` | iOS | ideviceinfo, idevicesyslog, idevicediagnostics, oslog |
| `ios_security` | iOS | AMFI/trustcache audit, sandbox profiles, code-sign check |
| `linux_automation` | Linux | systemd timers, unattended-upgrades, cron, logrotate |
| `windows_automation` | Win | PSRemoting, DSC apply, schtasks, unattended install |

## 3. Knowledge Modules

| Module | Content |
|--------|---------|
| `os_events.py` | Cross-OS event type map, log paths, event bus APIs per platform |
| `security_architectures.py` | SIP, SELinux, AppArmor, Defender, Gatekeeper, TrustZone |
| `system_buses.py` | dbus, COM, XPC, Binder, Mach ports — connect/query primitives |
| `package_management.py` | apt/rpm/pacman, brew, winget/choco, apk, IPA/dylib |
| `logging_systems.py` | os_log, journald, EventLog, logcat, syslog — query + stream APIs |

## 4. New Connectors

| Connector | OS | Purpose |
|-----------|----|---------|
| `macos_security.py` | macOS | csrutil, spctl, xprotect, tccutil wrappers |
| `linux_namespaces.py` | Linux | namespace/cgroup operations (unshare, nsenter-like) |
| `windows_wmi.py` | Win | WMI/CIM queries via Python |
| `windows_defender.py` | Win | Defender status, scan, exclusion management |
| `adb.py` | Android | ADB bridge for diagnostics |
| `libimobiledevice.py` | iOS | ideviceinfo/idevicesyslog/idevicediagnostics |

## 5. Implementation Plan

| Phase | Scope | Duration |
|-------|-------|----------|
| A | Foundation: galaxy.yml, skeleton, knowledge modules | Week 1 |
| B | macOS + Linux: 4 roles, 2 new connectors | Weeks 2-3 |
| C | Windows: 3 roles, 2 new connectors | Weeks 4-5 |
| D | Mobile: 4 roles, 2 new connectors | Weeks 6-7 |

## 6. Files

```text
collections/ansible_collections/general_ludd/os_expert/
├── galaxy.yml
├── roles/{macos_diagnose,macos_security,linux_diagnose,linux_kernel,
│    windows_diagnose,windows_security,android_diagnose,android_security,
│    ios_diagnose,ios_security,linux_automation,windows_automation}/tasks/main.yml
src/general_ludd/os_expert/
├── __init__.py, os_events.py, security_architectures.py, system_buses.py,
│   package_management.py, logging_systems.py
src/general_ludd/connectors/
├── macos_security.py, linux_namespaces.py, windows_wmi.py, windows_defender.py,
│   adb.py, libimobiledevice.py
tests/unit/
├── test_os_expert_knowledge.py, test_connector_macos_security.py, etc.
```

## 7. Dependencies

pip: `adb-shell` (mobile), `ansible-core>=2.16.0` (existing), `psutil>=6.0.0` (existing)
system: launchctl/mdfind (macOS), systemd/journalctl (Linux), powershell.exe (Win),
adb (Android), libimobiledevice (iOS), osquery (optional fallback)

## 8. Test Plan

- Unit: knowledge modules type maps, OS-specific exhaustiveness. Connector mocks.
- Integration: molecule test per role against local VM/container
- E2E: `make test-os-expert` — runs on available platforms, skips when OS deps missing
- Gate: new connectors >=85% coverage; roles lint with ansible-lint
