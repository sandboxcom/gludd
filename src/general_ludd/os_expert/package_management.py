"""Package management interfaces per platform.

Covers apt/rpm/pacman, brew, winget/choco, apk, IPA/dylib —
install, query, update, and audit operations for each package manager.
"""

from __future__ import annotations

from typing import TypedDict


class PackageManager(TypedDict):
    platform: str
    name: str
    format: str
    install_command: str
    query_command: str
    update_command: str
    audit_command: str


PACKAGE_MANAGERS: list[PackageManager] = []
