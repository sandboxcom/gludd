"""Shared owner-only, project-namespaced runtime state.

This module generalizes the mature sandbox-state allocator instead of creating
a second path-security implementation.  Callers get configurable roots,
symlink and ownership checks, deterministic project namespaces, and exact
scoped cleanup through :class:`SandboxState`.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from general_ludd.security.sandboxes.state import (
    SandboxState,
    SandboxStateError,
    _reject_symlink_components,
    _secure_directory,
)

STATE_DIR_ENV = "GLUDD_STATE_DIR"
DEFAULT_STATE_PREFIX = "gludd-state"

SecureState = SandboxState
SecureStateError = SandboxStateError


def _canonical_platform_temp_path(candidate: Path) -> Path:
    """Map only the OS-owned ``/tmp`` alias to its physical temp root."""
    platform_temp = Path(os.sep) / "tmp"
    if candidate != platform_temp and not candidate.is_relative_to(platform_temp):
        return candidate
    canonical_temp = platform_temp.resolve(strict=True)
    relative = candidate.relative_to(platform_temp)
    return canonical_temp.joinpath(*relative.parts)


def project_state(
    *,
    project_root: str | Path | None = None,
    create: bool = True,
) -> SecureState:
    """Return the secure general-purpose state namespace for one project."""
    return SandboxState.discover(
        project_root=project_root,
        create=create,
        state_dir_env=STATE_DIR_ENV,
        default_prefix=DEFAULT_STATE_PREFIX,
    )


def secure_directory(path: str | Path) -> Path:
    """Create or validate an explicit absolute owner-only directory."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise SecureStateError("secure state directory must be an absolute path")
    if ".." in candidate.parts:
        raise SecureStateError("secure state directory must not contain '..'")
    # macOS exposes the trusted system temp root through a platform symlink.
    # Canonicalize only that OS-owned alias for backwards compatibility; all
    # caller-created symlink components still fail closed in _secure_directory.
    canonical = _canonical_platform_temp_path(candidate)
    if canonical != candidate:
        _secure_directory(canonical)
        return candidate
    return _secure_directory(candidate)


def secure_write_text(
    path: str | Path,
    content: str,
    *,
    encoding: str = "utf-8",
) -> Path:
    """Write one state file without following symlinks and force mode ``0600``."""
    requested_target = Path(path)
    if not requested_target.is_absolute():
        raise SecureStateError("secure state file must use an absolute path")
    target = _canonical_platform_temp_path(requested_target)
    _secure_directory(target.parent)
    _reject_symlink_components(target)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags, 0o600)
    except OSError as exc:
        raise SecureStateError(f"unable to open secure state file: {target}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise SecureStateError(f"secure state path is not a regular file: {target}")
        uid_getter = getattr(os, "getuid", None)
        if uid_getter is not None and info.st_uid != uid_getter():
            raise SecureStateError(f"secure state file is not owned by caller: {target}")
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            fd = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if fd >= 0:
            os.close(fd)
    return requested_target


def trusted_owned_file(path: str | Path) -> bool:
    """Validate and harden a known legacy signal/state file without following it."""
    target = Path(path)
    try:
        before = target.lstat()
        if not stat.S_ISREG(before.st_mode):
            return False
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(target, flags)
    except OSError:
        return False
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return False
        if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
            return False
        uid_getter = getattr(os, "getuid", None)
        if uid_getter is not None and info.st_uid != uid_getter():
            return False
        fchmod = getattr(os, "fchmod", None)
        if fchmod is not None:
            fchmod(fd, 0o600)
        return True
    finally:
        os.close(fd)


__all__ = [
    "DEFAULT_STATE_PREFIX",
    "STATE_DIR_ENV",
    "SecureState",
    "SecureStateError",
    "project_state",
    "secure_directory",
    "secure_write_text",
    "trusted_owned_file",
]
