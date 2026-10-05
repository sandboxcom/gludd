"""Local artifact and binary storage backed by the Python standard library."""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any


class FileStore:
    """Store artifacts, binaries, configs, and cache on the local filesystem.

    Files in ``~/.config/gludd/fs`` supplement the main store, while writes
    always target ``~/.local/share/general-ludd/filestore``.  Gludd's store is
    intentionally local-only, so using :mod:`pathlib` avoids the dormant
    PyFilesystem2 compatibility layer and its synthetic ``six.moves`` imports.
    """

    def __init__(
        self,
        root_path: str | None = None,
        overlay_path: str | None = None,
    ) -> None:
        """Initialize the local store and an existing optional overlay."""
        if root_path is None:
            root_path = os.path.expanduser("~/.local/share/general-ludd/filestore")
        self._root_path = root_path
        self._fs = Path(root_path).expanduser()
        self._fs.mkdir(parents=True, exist_ok=True)

        if overlay_path is None:
            self._overlay_path = os.path.expanduser("~/.config/gludd/fs")
        else:
            self._overlay_path = overlay_path
        overlay = Path(self._overlay_path).expanduser()
        self._overlay_fs: Path | None = overlay if overlay.is_dir() else None

    @staticmethod
    def _is_binary_path(path: str) -> bool:
        # Binaries must never resolve through an operator-writable overlay.
        norm = path.lstrip("/")
        return norm == "binaries" or norm.startswith("binaries/")

    @staticmethod
    def _relative(path: str) -> Path:
        """Return a backend-relative path, rejecting traversal and drives."""
        normalized = path.replace("\\", "/").lstrip("/")
        pure = PurePosixPath(normalized)
        if any(part == ".." for part in pure.parts):
            raise PermissionError(f"Path escapes store root: {path}")
        # A drive prefix is not special to PurePosixPath but is special when the
        # same value is materialized as a pathlib.Path on Windows.
        relative = Path(*pure.parts)
        if relative.drive or relative.is_absolute():
            raise PermissionError(f"Path escapes store root: {path}")
        return relative

    @staticmethod
    def _within(root: Path, target: Path) -> bool:
        root_real = root.resolve()
        try:
            target.resolve(strict=False).relative_to(root_real)
        except ValueError:
            return False
        return True

    def _path(self, root: Path, path: str) -> Path:
        target = root / self._relative(path)
        if not self._within(root, target):
            raise PermissionError(f"Path escapes store root: {path}")
        return target

    def _overlay_owns(self, path: str) -> bool:
        """Return whether the overlay serves a path absent from the main store."""
        if self._overlay_fs is None or self._is_binary_path(path):
            return False
        if self._path(self._fs, path).exists():
            return False
        return self._path(self._overlay_fs, path).exists()

    def _confine(self, fs: Path, path: str) -> None:
        """Fail closed if a path (including symlinks) leaves its backend root."""
        self._path(fs, path)

    def _resolve_path(self, path: str) -> tuple[Path, str]:
        if self._overlay_owns(path):
            assert self._overlay_fs is not None
            root = self._overlay_fs
        else:
            root = self._fs
        self._confine(root, path)
        return root, path

    @property
    def root_path(self) -> str:
        """Return the configured main-store path."""
        return self._root_path

    @staticmethod
    def _modified(path: Path) -> str:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()

    @staticmethod
    def _display_path(base: str, name: str) -> str:
        parent = PurePosixPath(base.lstrip("/"))
        displayed = (parent / name).as_posix()
        return displayed[2:] if displayed.startswith("./") else displayed

    def write_text(self, path: str, content: str) -> None:
        """Write UTF-8 text to the main store."""
        target = self._path(self._fs, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Re-resolve after creating parents so an escaping symlink cannot be
        # introduced through a previously missing component.
        target = self._path(self._fs, path)
        target.write_text(content, encoding="utf-8")

    def read_text(self, path: str) -> str:
        """Read UTF-8 text using main-store-first overlay resolution."""
        root, resolved = self._resolve_path(path)
        return self._path(root, resolved).read_text(encoding="utf-8")

    def read_bytes(self, path: str) -> bytes:
        """Read bytes using main-store-first overlay resolution."""
        root, resolved = self._resolve_path(path)
        return self._path(root, resolved).read_bytes()

    def write_bytes(self, path: str, data: bytes) -> None:
        """Write bytes to the main store."""
        target = self._path(self._fs, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target = self._path(self._fs, path)
        target.write_bytes(data)

    def exists(self, path: str) -> bool:
        """Return whether the resolved main or overlay path exists."""
        if self._overlay_owns(path):
            return True
        return self._path(self._fs, path).exists()

    def is_dir(self, path: str) -> bool:
        """Return whether the resolved path is a directory."""
        root, resolved = self._resolve_path(path)
        return self._path(root, resolved).is_dir()

    def list_dir(self, path: str = "/") -> list[dict[str, Any]]:
        """List merged main and overlay entries with main-store precedence."""
        seen: set[str] = set()
        entries: list[dict[str, Any]] = []

        def _add_from(root: Path) -> None:
            directory = self._path(root, path)
            if not directory.is_dir():
                return
            for entry in directory.iterdir():
                if entry.name in seen:
                    continue
                full_path = self._display_path(path, entry.name)
                confined = self._path(root, full_path)
                seen.add(entry.name)
                entries.append(
                    {
                        "name": entry.name,
                        "path": full_path,
                        "is_dir": confined.is_dir(),
                        "size": confined.stat().st_size,
                        "modified": self._modified(confined),
                    }
                )

        # Main-store entries win consistently with read resolution.
        _add_from(self._fs)
        if self._overlay_fs is not None:
            _add_from(self._overlay_fs)

        entries.sort(key=lambda entry: (not entry["is_dir"], entry["name"]))
        return entries

    def tree(self, path: str = "/") -> list[dict[str, Any]]:
        """Return the recursive main-store tree below ``path``."""
        base = self._path(self._fs, path)
        if not base.is_dir():
            return []
        entries: list[dict[str, Any]] = []
        for current, dir_names, file_names in os.walk(base, followlinks=False):
            current_path = Path(current)
            for name in file_names:
                target = current_path / name
                relative = target.relative_to(self._fs).as_posix()
                self._confine(self._fs, relative)
                entries.append({"path": relative, "is_dir": False, "name": name})
            for name in dir_names:
                target = current_path / name
                relative = target.relative_to(self._fs).as_posix()
                self._confine(self._fs, relative)
                entries.append({"path": relative, "is_dir": True, "name": name})
        return entries

    def makedirs(self, path: str) -> None:
        """Create a main-store directory and missing parents."""
        self._path(self._fs, path).mkdir(parents=True, exist_ok=True)

    def remove(self, path: str) -> None:
        """Remove the path from the backend that currently serves it."""
        root, resolved = self._resolve_path(path)
        target = self._path(root, resolved)
        if not target.exists() and not target.is_symlink():
            raise FileNotFoundError(f"Path not found: {path}")
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target)

    def get_info(self, path: str) -> dict[str, Any]:
        """Return metadata for a main-store path."""
        target = self._path(self._fs, path)
        return {
            "name": target.name,
            "path": path.lstrip("/"),
            "is_dir": target.is_dir(),
            "size": target.stat().st_size,
            "modified": self._modified(target),
        }

    def copy(self, src: str, dst: str) -> None:
        """Copy a file within the main store."""
        source = self._path(self._fs, src)
        destination = self._path(self._fs, dst)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination = self._path(self._fs, dst)
        shutil.copy2(source, destination)

    def move(self, src: str, dst: str) -> None:
        """Move a path within the main store."""
        source = self._path(self._fs, src)
        destination = self._path(self._fs, dst)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination = self._path(self._fs, dst)
        shutil.move(source, destination)

    def close(self) -> None:
        """Release store resources (the native backend holds none)."""
