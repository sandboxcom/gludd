"""FileStore — virtual filesystem-based artifact and binary storage using PyFilesystem2."""

from __future__ import annotations

import os
from typing import Any

from fs.osfs import OSFS
from fs.path import dirname, join


class FileStore:
    """Virtual filesystem store for artifacts, binaries, configs, and cache."""

    def __init__(self, root_path: str | None = None) -> None:
        if root_path is None:
            root_path = os.path.expanduser("~/.local/share/general-ludd/filestore")
        self._root_path = root_path
        self._fs = OSFS(root_path, create=True)

    @property
    def root_path(self) -> str:
        return self._root_path

    def write_text(self, path: str, content: str) -> None:
        self._fs.makedirs(dirname(path), recreate=True)
        self._fs.writetext(path, content)

    def read_text(self, path: str) -> str:
        return self._fs.readtext(path)

    def write_bytes(self, path: str, data: bytes) -> None:
        self._fs.makedirs(dirname(path), recreate=True)
        self._fs.writebytes(path, data)

    def read_bytes(self, path: str) -> bytes:
        return self._fs.readbytes(path)

    def list_dir(self, path: str = "/") -> list[dict[str, Any]]:
        if not self._fs.isdir(path):
            return []
        entries: list[dict[str, Any]] = []
        for entry in self._fs.scandir(path):
            full_path = join(path, entry.name).lstrip("/")
            info = self._fs.getinfo(full_path, namespaces=["details"])
            entries.append({
                "name": entry.name,
                "path": full_path,
                "is_dir": entry.is_dir,
                "size": info.size,
                "modified": info.modified.isoformat() if info.modified else None,
            })
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

    def exists(self, path: str) -> bool:
        return self._fs.exists(path)

    def is_dir(self, path: str) -> bool:
        return self._fs.isdir(path)

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
