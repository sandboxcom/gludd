"""OS detection and diagnostics.

Identifies the running platform and provides diagnostic capabilities
for gathering system information. Covers macOS, Linux, Windows, Android,
iOS, and BSD detection via platform-specific markers.
"""

from __future__ import annotations

import platform as _platform
from enum import Enum
from typing import TypedDict


class OSFamily(Enum):
    LINUX = "linux"
    MACOS = "macos"
    WINDOWS = "windows"
    ANDROID = "android"
    IOS = "ios"
    BSD = "bsd"
    UNKNOWN = "unknown"


class OSInfo(TypedDict):
    platform: str
    name: str
    version: str
    arch: str
    kernel_version: str
    detection_method: str


def detect_os_family(name: str | None = None) -> OSFamily:
    name = _platform.system().lower() if name is None else name.lower()

    if name in ("linux",):
        return OSFamily.LINUX
    if name in ("darwin", "macos", "mac os x"):
        return OSFamily.MACOS
    if name in ("windows", "win32", "win64"):
        return OSFamily.WINDOWS
    if name in ("android",):
        return OSFamily.ANDROID
    if name in ("ios",):
        return OSFamily.IOS
    if any(bsd_name in name for bsd_name in ("freebsd", "openbsd", "netbsd", "dragonfly")):
        return OSFamily.BSD
    return OSFamily.UNKNOWN


_CURRENT_OS = detect_os_family()


OS_DETECTION_TABLE: list[OSInfo] = [
    {
        "platform": "linux",
        "name": "Linux",
        "version": "",
        "arch": _platform.machine(),
        "kernel_version": _platform.release(),
        "detection_method": "/etc/os-release; uname -a; /proc/version",
    },
    {
        "platform": "macos",
        "name": "macOS",
        "version": "",
        "arch": _platform.machine(),
        "kernel_version": _platform.release(),
        "detection_method": "sw_vers; system_profiler SPSoftwareDataType; uname -a",
    },
    {
        "platform": "windows",
        "name": "Windows",
        "version": "",
        "arch": _platform.machine(),
        "kernel_version": _platform.release(),
        "detection_method": "systeminfo; Get-WmiObject Win32_OperatingSystem; ver",
    },
    {
        "platform": "android",
        "name": "Android",
        "version": "",
        "arch": _platform.machine(),
        "kernel_version": _platform.release(),
        "detection_method": "getprop ro.build.version.release; uname -a; dumpsys",
    },
    {
        "platform": "ios",
        "name": "iOS",
        "version": "",
        "arch": _platform.machine(),
        "kernel_version": _platform.release(),
        "detection_method": "ideviceinfo; uname -a; sysctl",
    },
    {
        "platform": "freebsd",
        "name": "FreeBSD",
        "version": "",
        "arch": _platform.machine(),
        "kernel_version": _platform.release(),
        "detection_method": "freebsd-version; uname -a; sysctl",
    },
    {
        "platform": "openbsd",
        "name": "OpenBSD",
        "version": "",
        "arch": _platform.machine(),
        "kernel_version": _platform.release(),
        "detection_method": "uname -a; sysctl; dmesg",
    },
    {
        "platform": "netbsd",
        "name": "NetBSD",
        "version": "",
        "arch": _platform.machine(),
        "kernel_version": _platform.release(),
        "detection_method": "uname -a; sysctl; dmesg",
    },
]
