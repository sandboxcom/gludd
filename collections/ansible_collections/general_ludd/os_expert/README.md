# general_ludd.os_expert Ansible Collection

Ansible collection for OS-level diagnostics, security auditing, and automation
across macOS, Linux, Windows, Android, and iOS.

## Roles

| Role | OS | Purpose |
|------|----|---------|
| `macos_diagnose` | macOS | Gather: unified log, spdisaster_report, launchctl, nvram, pmset |
| `macos_security` | macOS | Audit: csrutil status, spctl, xprotect, tccutil, plist policy |
| `linux_diagnose` | Linux | Gather: /proc/*, journalctl, sysfs, dmesg, lsmod, sysctl |
| `linux_kernel` | Linux | Manage: modprobe, eBPF, namespaces, cgroups, sysctl tunables |
| `windows_diagnose` | Win | Query: Get-WmiObject, Get-EventLog, reg query, sc query |
| `windows_security` | Win | Audit: Get-MpPreference, Get-FirewallRule, auditpol, secedit |
| `android_diagnose` | Android | adb: logcat, dumpsys, getprop, pm list |
| `android_security` | Android | sepolicy-inject, pm permission audit, keystore, dm-verity |
| `ios_diagnose` | iOS | ideviceinfo, idevicesyslog, idevicediagnostics, oslog |
| `ios_security` | iOS | AMFI/trustcache audit, sandbox profiles, code-sign check |
| `linux_automation` | Linux | systemd timers, unattended-upgrades, cron, logrotate |
| `windows_automation` | Win | PSRemoting, DSC apply, schtasks, unattended install |

## Dependencies

- `general_ludd.agent >= 0.1.0`
- Python: `adb-shell` (mobile), `ansible-core>=2.16.0`
- System: launchctl/mdfind (macOS), systemd/journalctl (Linux), powershell.exe (Win),
  adb (Android), libimobiledevice (iOS)
