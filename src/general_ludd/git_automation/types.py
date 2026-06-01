"""Data classes for git automation results."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class InitResult:
    path: str
    created: bool
    message: str = ""


@dataclass
class WorktreeResult:
    path: str
    branch: str
    success: bool
    message: str = ""


@dataclass
class WorktreeInfo:
    path: str
    branch: str
    is_main: bool = False
    commit: str = ""


@dataclass
class MergeResult:
    success: bool
    strategy: str = "ff"
    message: str = ""
    conflicts: list[str] = field(default_factory=list)


@dataclass
class PushResult:
    success: bool
    remote: str = "origin"
    branch: str = ""
    message: str = ""
