"""Per-run temp roots with ownership manifests and scoped cleanup (D-22).

Private mode-0700 per-run temp roots with ownership manifests, bounded size/age
and exact cleanup on exit/signals/crash via a scoped reaper.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import signal
import stat
import tempfile
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Globals — registered temp roots for signal-driven cleanup
# ---------------------------------------------------------------------------

_registry: dict[str, TempRoot] = {}
_signal_handlers_installed = False


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TempRootError(Exception):
    """Raised when temp root creation or cleanup fails a security invariant."""


# ---------------------------------------------------------------------------
# TempRoot
# ---------------------------------------------------------------------------


class TempRoot:
    """A private mode-0700 per-run temp root with ownership manifest."""

    def __init__(
        self,
        root: Path,
        manifest_path: Path,
        *,
        max_bytes: int = 100 * 1024 * 1024,
        max_age_seconds: float = 3600.0,
    ) -> None:
        self.root = root
        self.manifest_path = manifest_path
        self.max_bytes = max_bytes
        self.max_age_seconds = max_age_seconds

    # --- creation ---

    @classmethod
    def create(
        cls,
        *,
        prefix: str = "gludd-tmp-",
        parent: Path | None = None,
        max_bytes: int = 100 * 1024 * 1024,
        max_age_seconds: float = 3600.0,
    ) -> TempRoot:
        """Create a new temp root with ownership manifest.

        Args:
            prefix: Name prefix for the temp root directory.
            parent: Parent directory.  Defaults to an OS temp directory whose
                ownership has been verified.
            max_bytes: Maximum total size in bytes before check_bounds fails.
            max_age_seconds: Maximum age in seconds before expiry.

        Raises:
            TempRootError: if the parent is a symlink, not absolute, or not
                owned by the caller.
        """
        if parent is None:
            parent = Path(tempfile.gettempdir())
        if not parent.is_absolute():
            raise TempRootError("parent must be an absolute path")
        # Enforce no symlink hops in the parent chain BEFORE resolving.
        _check_no_symlinks(parent)
        parent = parent.resolve(strict=False)

        _validate_owner(str(parent))

        parent.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix=prefix, dir=str(parent)))
        _secure_dir(root)

        work = root / "work"
        work.mkdir(mode=0o700)

        manifest_data: dict[str, Any] = {
            "root": str(root),
            "owner_uid": os.getuid(),
            "created_at": time.time(),
            "max_bytes": max_bytes,
            "max_age_seconds": max_age_seconds,
        }
        manifest_path = root / ".temp-root-manifest.json"
        _secure_write_json(manifest_path, manifest_data)

        instance = cls(
            root=root,
            manifest_path=manifest_path,
            max_bytes=max_bytes,
            max_age_seconds=max_age_seconds,
        )
        cls.install_signal_handlers()
        return instance

    # --- bound checks ---

    def check_bounds(self) -> None:
        """Raise TempRootError if size or age bounds are exceeded."""
        self._check_manifest()
        self._check_size()
        self._check_age()

    def _check_manifest(self) -> None:
        if not self.manifest_path.exists():
            raise TempRootError("manifest is missing — cannot verify bounds")

    def _check_size(self) -> None:
        total = sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file())
        if total > self.max_bytes:
            raise TempRootError(f"temp root size {total} exceeded max {self.max_bytes}")

    def _check_age(self) -> None:
        try:
            manifest = json.loads(self.manifest_path.read_text())
            created = manifest.get("created_at", 0.0)
        except (json.JSONDecodeError, KeyError):
            return
        if time.time() - created > self.max_age_seconds:
            raise TempRootError("temp root has exceeded max age")

    # --- cleanup ---

    def cleanup(self) -> None:
        """Remove the temp root and its manifest.  Idempotent."""
        unregister_temp_root(self)
        _remove_tree(self.root)
        with contextlib.suppress(OSError):
            self.manifest_path.unlink(missing_ok=True)

    # --- signal handling ---

    @classmethod
    def install_signal_handlers(cls) -> None:
        global _signal_handlers_installed
        if _signal_handlers_installed:
            return
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                prev = signal.signal(sig, cls._signal_cleanup)
                if prev is signal.SIG_DFL or prev is signal.SIG_IGN:
                    pass
            except (OSError, ValueError):
                pass
        atexit.register(cls._atexit_cleanup)
        _signal_handlers_installed = True

    @staticmethod
    def _signal_cleanup(signum: int, frame: object) -> None:
        for root in list(_registry.values()):
            with contextlib.suppress(Exception):
                root.cleanup()
        _registry.clear()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    @staticmethod
    def _atexit_cleanup() -> None:
        for root in list(_registry.values()):
            with contextlib.suppress(Exception):
                root.cleanup()
        _registry.clear()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_temp_root(root: TempRoot) -> None:
    _registry[str(root.root)] = root


def unregister_temp_root(root: TempRoot) -> None:
    _registry.pop(str(root.root), None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_temp_root_expired(root: TempRoot, *, max_age_seconds: float | None = None) -> bool:
    threshold = max_age_seconds if max_age_seconds is not None else root.max_age_seconds
    age = compute_age_seconds(root.manifest_path)
    return age > threshold


def compute_age_seconds(manifest_path: Path) -> float:
    try:
        manifest = json.loads(manifest_path.read_text())
        created: float = float(manifest.get("created_at", 0.0))
    except (json.JSONDecodeError, OSError, KeyError):
        return float("inf")
    return time.time() - created


def cleanup_all_temp_roots(
    *,
    manifest_root: Path,
    max_age_seconds: float = 3600.0,
) -> list[str]:
    cleaned: list[str] = []
    for entry in manifest_root.iterdir():
        manifest = entry / ".temp-root-manifest.json"
        if manifest.exists():
            age = compute_age_seconds(manifest)
            if age > max_age_seconds:
                root_path = str(entry)
                _remove_tree(entry)
                with contextlib.suppress(OSError):
                    manifest.unlink(missing_ok=True)
                cleaned.append(root_path)
    return cleaned


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_no_symlinks(path: Path) -> None:
    for candidate in [path, *path.parents]:
        try:
            if candidate.is_symlink():
                raise TempRootError(f"parent path contains a symlink: {candidate}")
        except OSError:
            pass


def _validate_owner(path_str: str) -> None:
    path = Path(path_str)
    if not path.exists():
        return
    try:
        st = path.stat()
    except OSError:
        return
    uid = os.getuid()
    if st.st_uid != uid:
        raise TempRootError(f"parent directory not owned by caller (uid={uid}, owner={st.st_uid}): {path}")


def _secure_dir(path: Path) -> None:
    path.chmod(0o700)
    try:
        st = path.stat()
    except OSError as exc:
        raise TempRootError(f"cannot stat directory: {path}") from exc
    if stat.S_IMODE(st.st_mode) != 0o700:
        raise TempRootError(f"directory mode is not 0700: {path}")


def _secure_write_json(path: Path, data: object) -> None:
    parent = path.parent
    parent.chmod(0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    content = json.dumps(data)
    try:
        fd = os.open(str(path), flags, 0o600)
    except OSError as exc:
        raise TempRootError(f"cannot create manifest: {path}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise TempRootError(f"manifest path is not a regular file: {path}")
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if fd >= 0:
            os.close(fd)


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink():
        path.unlink()
        return
    for child in sorted(path.rglob("*"), reverse=True):
        try:
            if child.is_symlink():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
            else:
                child.unlink()
        except OSError:
            pass
    with contextlib.suppress(OSError):
        path.rmdir()


__all__ = [
    "TempRoot",
    "TempRootError",
    "cleanup_all_temp_roots",
    "compute_age_seconds",
    "is_temp_root_expired",
    "register_temp_root",
    "unregister_temp_root",
]
