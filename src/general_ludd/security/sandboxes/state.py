"""Secure, project-namespaced host state for OS sandbox backends.

Sandbox runtime artifacts are security-sensitive: policy sources, runtime
roots, and control sockets must not share a predictable world-writable
directory.  This module provides one stdlib-only allocation and cleanup
contract for those host-side artifacts.  Guest ``/tmp`` filesystems are not
affected.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from general_ludd.config.project import find_project_root

STATE_DIR_ENV = "GLUDD_SANDBOX_STATE_DIR"
PROJECT_ROOT_ENV = "GLUDD_PROJECT_ROOT"
DEFAULT_STATE_PREFIX = "gludd-sandbox-state"
_COMPONENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")


class SandboxStateError(RuntimeError):
    """Raised when sandbox state cannot be allocated or cleaned safely."""


def _current_uid() -> int | None:
    getter = getattr(os, "getuid", None)
    return int(getter()) if getter is not None else None


def _existing_path_components(path: Path) -> list[Path]:
    current = Path(path.anchor)
    components: list[Path] = []
    for part in path.parts[1:]:
        current /= part
        try:
            current.lstat()
        except FileNotFoundError:
            continue
        components.append(current)
    return components


def _reject_symlink_components(path: Path) -> None:
    for component in _existing_path_components(path):
        if stat.S_ISLNK(component.lstat().st_mode):
            raise SandboxStateError(
                f"sandbox state path contains symlink component: {component}",
            )


def _assert_owner(path: Path) -> None:
    uid = _current_uid()
    info = path.stat(follow_symlinks=False)
    if uid is not None and info.st_uid != uid:
        raise SandboxStateError(
            f"sandbox state directory must be owned by uid {uid}: {path} "
            f"is owned by uid {info.st_uid}",
        )


def _secure_directory(path: Path) -> Path:
    """Create *path* and every missing component with mode ``0700``."""
    _reject_symlink_components(path)
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    if not cursor.is_dir():
        raise SandboxStateError(f"sandbox state parent is not a directory: {cursor}")

    for directory in reversed(missing):
        with contextlib.suppress(FileExistsError):
            directory.mkdir(mode=0o700)
        _reject_symlink_components(directory)
        if not directory.is_dir():
            raise SandboxStateError(
                f"sandbox state path is not a directory: {directory}",
            )
        _assert_owner(directory)
        directory.chmod(0o700)

    _reject_symlink_components(path)
    if not path.is_dir():
        raise SandboxStateError(f"sandbox state path is not a directory: {path}")
    _assert_owner(path)
    path.chmod(0o700)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise SandboxStateError(f"sandbox state directory is not mode 0700: {path}")
    return path


def _validate_secure_directory_if_exists(path: Path) -> None:
    _reject_symlink_components(path)
    try:
        info = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(info.st_mode):
        raise SandboxStateError(f"sandbox state path is not a directory: {path}")
    _assert_owner(path)
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise SandboxStateError(f"sandbox state directory is not mode 0700: {path}")


def _rollback_empty_directory(path: Path) -> None:
    """Remove a newly allocated empty directory without masking the cause."""
    try:
        _reject_symlink_components(path)
        _assert_owner(path)
        path.rmdir()
    except (FileNotFoundError, OSError, SandboxStateError):
        # A concurrent writer, replacement, or ownership change makes rollback
        # unsafe. Preserve that evidence and re-raise the original allocation
        # failure instead of deleting a path whose identity is now uncertain.
        return


def safe_state_component(value: str) -> str:
    """Return a deterministic safe component for an untrusted identifier."""
    if _COMPONENT_RE.fullmatch(value) and value not in {".", ".."}:
        return value
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")[:48]
    if not slug:
        slug = "item"
    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=6).hexdigest()
    return f"{slug}-{digest}"


def _validate_component(value: str) -> str:
    if value in {".", ".."} or not _COMPONENT_RE.fullmatch(value):
        raise SandboxStateError(f"unsafe sandbox state path component: {value!r}")
    return value


def _project_namespace(project_root: Path) -> str:
    slug = safe_state_component(project_root.name or "project")[:48]
    digest = hashlib.blake2b(
        os.fsencode(project_root),
        digest_size=8,
    ).hexdigest()
    return f"{slug}-{digest}"


def _configured_base(raw: str, *, env_name: str = STATE_DIR_ENV) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise SandboxStateError(f"{env_name} must be an absolute path")
    if ".." in candidate.parts:
        raise SandboxStateError(f"{env_name} must not contain '..'")
    _reject_symlink_components(candidate)
    return candidate


def _default_base(prefix: str = DEFAULT_STATE_PREFIX) -> Path:
    _validate_component(prefix)
    temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
    uid = _current_uid()
    identity = str(uid) if uid is not None else "user"
    return temp_root / f"{prefix}-{identity}"


def _resolve_project_root(project_root: str | Path | None) -> Path:
    selected: str | Path | None = project_root
    if selected is None:
        selected = os.environ.get(PROJECT_ROOT_ENV)
    if selected is None:
        selected = find_project_root() or Path.cwd()
    path = Path(selected).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        return path.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise SandboxStateError(f"project root is unavailable: {path}") from exc


def _reject_tree_symlinks(root: Path) -> None:
    _reject_symlink_components(root)
    if not root.exists():
        return
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in sorted((*dirnames, *filenames)):
            candidate = parent / name
            if candidate.is_symlink():
                raise SandboxStateError(
                    f"refusing to clean sandbox state symlink: {candidate}",
                )


@dataclass(frozen=True)
class SandboxState:
    """One canonical, owner-only sandbox state namespace for a project."""

    base_dir: Path
    project_root: Path
    project_dir: Path
    namespace: str

    @classmethod
    def discover(
        cls,
        *,
        project_root: str | Path | None = None,
        create: bool = True,
        state_dir_env: str = STATE_DIR_ENV,
        default_prefix: str = DEFAULT_STATE_PREFIX,
    ) -> SandboxState:
        """Resolve project state and atomically allocate it when requested."""
        root = _resolve_project_root(project_root)
        raw_base = os.environ.get(state_dir_env)
        base = (
            _configured_base(raw_base, env_name=state_dir_env)
            if raw_base
            else _default_base(default_prefix)
        )
        namespace = _project_namespace(root)
        project_dir = base / namespace
        if create:
            base_existed = base.exists()
            project_existed = project_dir.exists()
            try:
                _secure_directory(base)
                _secure_directory(project_dir)
            except Exception:
                if not project_existed:
                    _rollback_empty_directory(project_dir)
                if not base_existed:
                    _rollback_empty_directory(base)
                raise
        else:
            _validate_secure_directory_if_exists(base)
            _validate_secure_directory_if_exists(project_dir)
        return cls(
            base_dir=base,
            project_root=root,
            project_dir=project_dir,
            namespace=namespace,
        )

    def _assert_contained(self, candidate: Path) -> None:
        resolved_root = self.project_dir.resolve(strict=False)
        resolved = candidate.resolve(strict=False)
        if resolved == resolved_root or resolved.is_relative_to(resolved_root):
            return
        raise SandboxStateError(
            f"sandbox state path is outside project namespace: {candidate}",
        )

    def path(self, *components: str) -> Path:
        """Return a validated path contained by the project namespace."""
        if not components:
            return self.project_dir
        safe = tuple(_validate_component(value) for value in components)
        candidate = self.project_dir.joinpath(*safe)
        self._assert_contained(candidate)
        return candidate

    def directory(self, *components: str) -> Path:
        """Securely create and return a contained owner-only directory."""
        if not components:
            return _secure_directory(self.project_dir)
        candidate = self.path(*components)
        return _secure_directory(candidate)

    def temporary_directory(self, category: str, *, prefix: str = "job-") -> Path:
        """Allocate one owner-only temporary directory inside this namespace."""
        _validate_component(category)
        if not prefix or Path(prefix).name != prefix or ".." in prefix:
            raise SandboxStateError(
                f"unsafe sandbox state temporary-directory prefix: {prefix!r}",
            )
        parent = self.directory(category)
        allocated = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
        self._assert_contained(allocated)
        return _secure_directory(allocated)

    def cleanup_path(self, candidate: str | Path) -> bool:
        """Remove a contained path after rejecting symlink traversal."""
        target = Path(candidate)
        _reject_symlink_components(target)
        self._assert_contained(target)
        if target == self.project_dir:
            raise SandboxStateError(
                "cleanup_path cannot remove the project namespace; use cleanup_project",
            )
        if not target.exists():
            return False
        _reject_tree_symlinks(target)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return True

    def cleanup_backend(self, backend: str) -> bool:
        """Remove one backend's confined state tree if it exists."""
        return self.cleanup_path(self.path(backend))

    def cleanup_project(self) -> bool:
        """Remove the project namespace after validating its full tree."""
        _reject_symlink_components(self.project_dir)
        self._assert_contained(self.project_dir)
        if not self.project_dir.exists():
            return False
        _reject_tree_symlinks(self.project_dir)
        shutil.rmtree(self.project_dir)
        return True


__all__ = [
    "DEFAULT_STATE_PREFIX",
    "PROJECT_ROOT_ENV",
    "STATE_DIR_ENV",
    "SandboxState",
    "SandboxStateError",
    "safe_state_component",
]
