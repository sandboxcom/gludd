"""FileStore — virtual filesystem-based artifact and binary storage using PyFilesystem2."""

from __future__ import annotations

import os
from typing import Any

from fs.osfs import OSFS
from fs.path import dirname, join


class FileStore:
    """Virtual filesystem store for artifacts, binaries, configs, and cache.

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
        self._fs = OSFS(root_path, create=True)

        if overlay_path is False:
            self._overlay_path = None
            self._overlay_fs = None
        else:
            if overlay_path is None:
                overlay_path = os.path.expanduser("~/.config/gludd/fs")
            self._overlay_path: str | None = overlay_path
            self._overlay_fs: OSFS | None = None
            if os.path.isdir(overlay_path):
                self._overlay_fs = OSFS(overlay_path)

    def _resolve_path(self, path: str) -> tuple[OSFS, str]:
        if self._overlay_fs is not None and self._overlay_fs.exists(path):
            return self._overlay_fs, path
        return self._fs, path

    @property
    def root_path(self) -> str:
        return self._root_path

    def write_text(self, path: str, content: str) -> None:
        self._fs.makedirs(dirname(path), recreate=True)
        self._fs.writetext(path, content)

    def read_text(self, path: str) -> str:
        fs, resolved = self._resolve_path(path)
        return fs.readtext(resolved)

    def read_bytes(self, path: str) -> bytes:
        fs, resolved = self._resolve_path(path)
        return fs.readbytes(resolved)

    def write_bytes(self, path: str, data: bytes) -> None:
        self._fs.makedirs(dirname(path), recreate=True)
        self._fs.writebytes(path, data)

    def exists(self, path: str) -> bool:
        if self._overlay_fs is not None and self._overlay_fs.exists(path):
            return True
        return self._fs.exists(path)

    def is_dir(self, path: str) -> bool:
        if self._overlay_fs is not None and self._overlay_fs.exists(path):
            return self._overlay_fs.isdir(path)
        return self._fs.isdir(path)

    def list_dir(self, path: str = "/") -> list[dict[str, Any]]:
        seen: set[str] = set()
        entries: list[dict[str, Any]] = []

        def _add_from(fs: Any, base_path: str) -> None:
            if not fs.isdir(base_path):
                return
            for entry in fs.scandir(base_path):
                if entry.name in seen:
                    continue
                seen.add(entry.name)
                full_path = join(path, entry.name).lstrip("/")
                info = fs.getinfo(full_path, namespaces=["details"])
                entries.append({
                    "name": entry.name,
                    "path": full_path,
                    "is_dir": entry.is_dir,
                    "size": info.size,
                    "modified": info.modified.isoformat() if info.modified else None,
                })

        if self._overlay_fs is not None:
            _add_from(self._overlay_fs, path)
        _add_from(self._fs, path)

        entries.sort(key=lambda e: (not e["is_dir"], e["name"]))
        return entries

    def tree(self, path: str = "/") -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for step in self._fs.walk(path):
            for file in step.files:
                full = join(step.path, file.name).lstrip("/")
                entries.append({"path": full, "is_dir": False, "name": file.name})
            for subdir in step.dirs:
                full = join(step.path, subdir.name).lstrip("/")
                entries.append({"path": full, "is_dir": True, "name": subdir.name})
        return entries

    def makedirs(self, path: str) -> None:
        self._fs.makedirs(path, recreate=True)

    def remove(self, path: str) -> None:
        if not self._fs.exists(path):
            raise FileNotFoundError(f"Path not found: {path}")
        if self._fs.isdir(path):
            self._fs.removetree(path)
        else:
            self._fs.remove(path)

    def get_info(self, path: str) -> dict[str, Any]:
        info = self._fs.getinfo(path, namespaces=["details"])
        return {
            "name": info.name,
            "path": path.lstrip("/"),
            "is_dir": info.is_dir,
            "size": info.size,
            "modified": info.modified.isoformat() if info.modified else None,
        }

    def copy(self, src: str, dst: str) -> None:
        self._fs.makedirs(dirname(dst), recreate=True)
        self._fs.copy(src, dst)

    def move(self, src: str, dst: str) -> None:
        self._fs.makedirs(dirname(dst), recreate=True)
        self._fs.move(src, dst)

    def close(self) -> None:
        self._fs.close()
