"""FileStore — confined artifact and binary storage on the local filesystem."""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class FileStore:
    """Local filesystem store for artifacts, binaries, configs, and cache.

    Supports a config overlay: files in ~/.config/gludd/fs/ take precedence
    over files in the main store (~/.local/share/general-ludd/filestore/).
    Writes always go to the main store.
    """

    def __init__(
        self,
        root_path: str | None = None,
        overlay_path: str | None = None,
    ) -> None:
        if root_path is None:
            root_path = os.path.expanduser("~/.local/share/general-ludd/filestore")
        self._root_path = root_path
        self._fs = Path(root_path).expanduser().resolve()
        self._fs.mkdir(parents=True, exist_ok=True)

        if overlay_path is None:
            self._overlay_path = os.path.expanduser("~/.config/gludd/fs")
        else:
            self._overlay_path = overlay_path
        overlay = Path(self._overlay_path).expanduser()
        self._overlay_fs: Path | None = overlay.resolve() if overlay.is_dir() else None

    @staticmethod
    def _is_binary_path(path: str) -> bool:
        # Binaries/ must NEVER resolve through the overlay (an attacker-controlled
        # overlay must not be able to shadow a trusted executable).
        norm = path.lstrip("/")
        return norm == "binaries" or norm.startswith("binaries/")

    @staticmethod
    def _confine(root: Path, path: str) -> Path:
        """Resolve *path* and reject any result outside *root*.

        ``Path.resolve(strict=False)`` follows every existing symlink while also
        supporting new write targets.  Checking the resolved result prevents
        traversal and symlink-based reads, writes, moves, and deletions outside
        the store.
        """
        root_real = root.resolve()
        target = (root_real / path.lstrip("/")).resolve(strict=False)
        try:
            target.relative_to(root_real)
        except ValueError as exc:
            raise PermissionError(f"Path escapes store root: {path}") from exc
        return target

    def _overlay_owns(self, path: str) -> bool:
        """Whether the overlay should serve this path.

        Writes/removes go to the main store, so the overlay only serves a path
        when the main store does not have it. Binaries are never served from the
        overlay.
        """
        if self._overlay_fs is None or self._is_binary_path(path):
            return False
        if self._confine(self._fs, path).exists():
            return False
        return self._confine(self._overlay_fs, path).exists()

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
        return self._root_path

    def write_text(self, path: str, content: str) -> None:
        target = self._confine(self._fs, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def read_text(self, path: str) -> str:
        root, resolved = self._resolve_path(path)
        return self._confine(root, resolved).read_text(encoding="utf-8")

    def read_bytes(self, path: str) -> bytes:
        root, resolved = self._resolve_path(path)
        return self._confine(root, resolved).read_bytes()

    def write_bytes(self, path: str, data: bytes) -> None:
        target = self._confine(self._fs, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def exists(self, path: str) -> bool:
        if self._overlay_owns(path):
            return True
        return self._confine(self._fs, path).exists()

    def is_dir(self, path: str) -> bool:
        root, resolved = self._resolve_path(path)
        return self._confine(root, resolved).is_dir()

    @staticmethod
    def _entry_info(root: Path, entry: Path) -> dict[str, Any]:
        relative = entry.relative_to(root).as_posix()
        confined = FileStore._confine(root, relative)
        stat = confined.stat()
        return {
            "name": entry.name,
            "path": relative,
            "is_dir": confined.is_dir(),
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
        }

    def list_dir(self, path: str = "/") -> list[dict[str, Any]]:
        seen: set[str] = set()
        entries: list[dict[str, Any]] = []

        def _add_from(root: Path, base_path: str) -> None:
            directory = self._confine(root, base_path)
            if not directory.is_dir():
                return
            for entry in directory.iterdir():
                if entry.name in seen:
                    continue
                seen.add(entry.name)
                entries.append(self._entry_info(root, entry))

        if self._overlay_fs is not None:
            _add_from(self._overlay_fs, path)
        _add_from(self._fs, path)

        entries.sort(key=lambda entry: (not entry["is_dir"], entry["name"]))
        return entries

    def tree(self, path: str = "/") -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        base = self._confine(self._fs, path)
        if not base.is_dir():
            return entries

        for current, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames.sort()
            filenames.sort()
            current_path = Path(current)
            for name, is_dir in [
                *((dirname, True) for dirname in dirnames),
                *((filename, False) for filename in filenames),
            ]:
                entry = current_path / name
                relative = entry.relative_to(self._fs).as_posix()
                self._confine(self._fs, relative)
                entries.append({"path": relative, "is_dir": is_dir, "name": name})
        return entries

    def makedirs(self, path: str) -> None:
        self._confine(self._fs, path).mkdir(parents=True, exist_ok=True)

    def remove(self, path: str) -> None:
        if self._overlay_owns(path):
            assert self._overlay_fs is not None
            root = self._overlay_fs
        elif self._confine(self._fs, path).exists():
            root = self._fs
        else:
            raise FileNotFoundError(f"Path not found: {path}")

        target = self._confine(root, path)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

    def get_info(self, path: str) -> dict[str, Any]:
        target = self._confine(self._fs, path)
        stat = target.stat()
        return {
            "name": target.name,
            "path": path.lstrip("/"),
            "is_dir": target.is_dir(),
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
        }

    def copy(self, src: str, dst: str) -> None:
        source = self._confine(self._fs, src)
        destination = self._confine(self._fs, dst)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    def move(self, src: str, dst: str) -> None:
        source = self._confine(self._fs, src)
        destination = self._confine(self._fs, dst)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(source, destination)

    def close(self) -> None:
        """Retained for API compatibility; local files are opened per operation."""
