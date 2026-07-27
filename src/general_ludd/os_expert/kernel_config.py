"""Kernel configuration analysis.

Per-platform kernel parameters, tunables, and feature detection paths.
Covers /proc/sys, sysctl, boot config, Windows registry, and mobile
kernel build configurations.
"""

from __future__ import annotations

from typing import TypedDict


class KernelParameter(TypedDict):
    platform: str
    name: str
    path: str
    expected_type: str
    default: str
    description: str


class KernelFeature(TypedDict):
    platform: str
    feature_name: str
    detection_path: str
    detection_command: str
    description: str


KERNEL_PARAMETERS: list[KernelParameter] = [
    {
        "platform": "linux",
        "name": "kernel.hostname",
        "path": "/proc/sys/kernel/hostname",
        "expected_type": "str",
        "default": "localhost",
        "description": "System hostname",
    },
    {
        "platform": "linux",
        "name": "kernel.osrelease",
        "path": "/proc/sys/kernel/osrelease",
        "expected_type": "str",
        "default": "",
        "description": "Kernel release version string",
    },
    {
        "platform": "linux",
        "name": "kernel.randomize_va_space",
        "path": "/proc/sys/kernel/randomize_va_space",
        "expected_type": "int",
        "default": "2",
        "description": "Address space layout randomization: 0=off, 1=partial, 2=full",
    },
    {
        "platform": "linux",
        "name": "kernel.kptr_restrict",
        "path": "/proc/sys/kernel/kptr_restrict",
        "expected_type": "int",
        "default": "1",
        "description": "Restrict kernel pointer exposure: 0=off, 1=restricted, 2=hidden",
    },
    {
        "platform": "linux",
        "name": "kernel.dmesg_restrict",
        "path": "/proc/sys/kernel/dmesg_restrict",
        "expected_type": "int",
        "default": "0",
        "description": "Restrict dmesg access to CAP_SYSLOG",
    },
    {
        "platform": "linux",
        "name": "net.ipv4.ip_forward",
        "path": "/proc/sys/net/ipv4/ip_forward",
        "expected_type": "int",
        "default": "0",
        "description": "Enable IPv4 packet forwarding",
    },
    {
        "platform": "linux",
        "name": "vm.swappiness",
        "path": "/proc/sys/vm/swappiness",
        "expected_type": "int",
        "default": "60",
        "description": "Tendency to swap: 0=avoid, 100=aggressive",
    },
    {
        "platform": "linux",
        "name": "vm.overcommit_memory",
        "path": "/proc/sys/vm/overcommit_memory",
        "expected_type": "int",
        "default": "0",
        "description": "Memory overcommit: 0=heuristic, 1=always, 2=ratio-check",
    },
    {
        "platform": "linux",
        "name": "fs.file-max",
        "path": "/proc/sys/fs/file-max",
        "expected_type": "int",
        "default": "9223372036854775807",
        "description": "Maximum number of open file handles",
    },
    {
        "platform": "linux",
        "name": "kernel.perf_event_paranoid",
        "path": "/proc/sys/kernel/perf_event_paranoid",
        "expected_type": "int",
        "default": "2",
        "description": "perf_event security: 0=no restrictions, 1=user+CAP_SYS_ADMIN, 2=CAP_SYS_ADMIN, 3=disabled",
    },
    {
        "platform": "linux",
        "name": "kernel.unprivileged_bpf_disabled",
        "path": "/proc/sys/kernel/unprivileged_bpf_disabled",
        "expected_type": "int",
        "default": "2",
        "description": "Disable unprivileged BPF: 0=allow, 1=disabled, 2=permanently disabled",
    },
    {
        "platform": "linux",
        "name": "kernel.yama.ptrace_scope",
        "path": "/proc/sys/kernel/yama/ptrace_scope",
        "expected_type": "int",
        "default": "1",
        "description": "ptrace scope: 0=same-uid, 1=restricted, 2=admin-only, 3=no-attach",
    },
    {
        "platform": "linux",
        "name": "net.core.somaxconn",
        "path": "/proc/sys/net/core/somaxconn",
        "expected_type": "int",
        "default": "4096",
        "description": "Maximum socket listen backlog",
    },
    {
        "platform": "macos",
        "name": "kern.maxfiles",
        "path": "sysctl kern.maxfiles",
        "expected_type": "int",
        "default": "12288",
        "description": "Maximum number of open files",
    },
    {
        "platform": "macos",
        "name": "kern.maxproc",
        "path": "sysctl kern.maxproc",
        "expected_type": "int",
        "default": "2048",
        "description": "Maximum number of processes",
    },
    {
        "platform": "macos",
        "name": "vm.swapusage",
        "path": "sysctl vm.swapusage",
        "expected_type": "str",
        "default": "",
        "description": "Swap usage statistics",
    },
    {
        "platform": "macos",
        "name": "net.inet.tcp.keepidle",
        "path": "sysctl net.inet.tcp.keepidle",
        "expected_type": "int",
        "default": "7200000",
        "description": "TCP keepalive idle time in ms",
    },
    {
        "platform": "windows",
        "name": "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Memory Management\\PagingFiles",
        "path": "reg query",
        "expected_type": "str",
        "default": "",
        "description": "Page file configuration",
    },
    {
        "platform": "windows",
        "name": "HKLM\\SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters\\MaxUserPort",
        "path": "reg query",
        "expected_type": "int",
        "default": "16384",
        "description": "Maximum user port number",
    },
    {
        "platform": "android",
        "name": "ro.build.version.sdk",
        "path": "getprop",
        "expected_type": "int",
        "default": "",
        "description": "Android SDK version",
    },
    {
        "platform": "android",
        "name": "ro.secure",
        "path": "getprop",
        "expected_type": "int",
        "default": "1",
        "description": "Root access disabled: 0=root, 1=secure",
    },
]


KERNEL_FEATURES: list[KernelFeature] = [
    {
        "platform": "linux",
        "feature_name": "SELinux",
        "detection_path": "/sys/fs/selinux/enforce",
        "detection_command": "sestatus; getenforce",
        "description": "SELinux mandatory access control",
    },
    {
        "platform": "linux",
        "feature_name": "AppArmor",
        "detection_path": "/sys/module/apparmor/parameters/enabled",
        "detection_command": "aa-status",
        "description": "AppArmor mandatory access control",
    },
    {
        "platform": "linux",
        "feature_name": "eBPF",
        "detection_path": "/sys/kernel/btf/vmlinux",
        "detection_command": "bpftool feature probe",
        "description": "Extended Berkeley Packet Filter support",
    },
    {
        "platform": "linux",
        "feature_name": "cgroups_v2",
        "detection_path": "/sys/fs/cgroup/cgroup.controllers",
        "detection_command": "stat -fc %T /sys/fs/cgroup/",
        "description": "Control group v2 unified hierarchy",
    },
    {
        "platform": "linux",
        "feature_name": "namespaces",
        "detection_path": "/proc/self/ns",
        "detection_command": "lsns",
        "description": "Linux namespace support (mnt, pid, net, ipc, uts, user, cgroup)",
    },
    {
        "platform": "linux",
        "feature_name": "seccomp",
        "detection_path": "/proc/self/status",
        "detection_command": "grep Seccomp /proc/self/status",
        "description": "Secure computing mode filtering",
    },
    {
        "platform": "linux",
        "feature_name": "KASLR",
        "detection_path": "/proc/cmdline",
        "detection_command": "grep -q nokaslr /proc/cmdline",
        "description": "Kernel Address Space Layout Randomization",
    },
    {
        "platform": "linux",
        "feature_name": "kernel_lockdown",
        "detection_path": "/sys/kernel/security/lockdown",
        "detection_command": "cat /sys/kernel/security/lockdown",
        "description": "Kernel lockdown integrity mode",
    },
    {
        "platform": "linux",
        "feature_name": "Landlock",
        "detection_path": "/sys/kernel/security/landlock",
        "detection_command": "grep -q landlock /proc/filesystems",
        "description": "Landlock unprivileged access control",
    },
    {
        "platform": "linux",
        "feature_name": "KVM",
        "detection_path": "/dev/kvm",
        "detection_command": "kvm-ok; lsmod | grep kvm",
        "description": "Kernel-based Virtual Machine support",
    },
    {
        "platform": "macos",
        "feature_name": "SIP",
        "detection_path": "",
        "detection_command": "csrutil status",
        "description": "System Integrity Protection",
    },
    {
        "platform": "macos",
        "feature_name": "Gatekeeper",
        "detection_path": "",
        "detection_command": "spctl --status",
        "description": "Gatekeeper code-signing enforcement",
    },
    {
        "platform": "macos",
        "feature_name": "XProtect",
        "detection_path": "/Library/Apple/System/Library/CoreServices/XProtect.bundle",
        "detection_command": "defaults read /Library/Apple/System/Library/CoreServices/"
        "XProtect.bundle/Contents/version",
        "description": "XProtect malware detection",
    },
    {
        "platform": "macos",
        "feature_name": "Hypervisor.framework",
        "detection_path": "/System/Library/Frameworks/Hypervisor.framework",
        "detection_command": "sysctl kern.hv_support",
        "description": "Apple Hypervisor framework for virtualisation",
    },
    {
        "platform": "windows",
        "feature_name": "Hyper-V",
        "detection_path": "",
        "detection_command": "Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V",
        "description": "Microsoft Hyper-V hypervisor",
    },
    {
        "platform": "windows",
        "feature_name": "Secure Boot",
        "detection_path": "",
        "detection_command": "Confirm-SecureBootUEFI",
        "description": "UEFI Secure Boot",
    },
    {
        "platform": "windows",
        "feature_name": "Credential Guard",
        "detection_path": "",
        "detection_command": (
            "Get-CimInstance -ClassName Win32_DeviceGuard -Namespace root\\Microsoft\\Windows\\DeviceGuard"
        ),
        "description": "Virtualisation-Based Security Credential Guard",
    },
    {
        "platform": "android",
        "feature_name": "dm-verity",
        "detection_path": "/sys/module/dm_verity",
        "detection_command": "getprop ro.boot.veritymode",
        "description": "Device-mapper verity integrity checking",
    },
    {
        "platform": "android",
        "feature_name": "SELinux",
        "detection_path": "/sys/fs/selinux/enforce",
        "detection_command": "getenforce",
        "description": "SELinux on Android",
    },
    {
        "platform": "ios",
        "feature_name": "Kernel Integrity Protection",
        "detection_path": "",
        "detection_command": "sysctl kern.kern_integrity",
        "description": "iOS kernel code integrity verification",
    },
]
