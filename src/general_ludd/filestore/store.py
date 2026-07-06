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
        self._overlay_fs: OSFS | None = None
        if overlay_path is None:
            self._overlay_path = os.path.expanduser("~/.config/gludd/fs")
        else:
            self._overlay_path = overlay_path
        if self._overlay_path is not None and os.path.isdir(self._overlay_path):
            self._overlay_fs = OSFS(self._overlay_path)

    @staticmethod
    def _is_binary_path(path: str) -> bool:
        # Binaries/ must NEVER resolve through the overlay (an attacker-controlled
        # overlay must not be able to shadow a trusted executable).
        norm = path.lstrip("/")
        return norm == "binaries" or norm.startswith("binaries/")

    def _overlay_owns(self, path: str) -> bool:
        """Whether the overlay should serve this path.

        Findings 1/2: writes/removes go to the MAIN store, so the overlay must
        only serve a path when the main store does NOT have it. Otherwise an
        overlay file silently shadows a just-written value (a write must be
        visible to the subsequent read). Binaries are never served from the
        overlay (Finding 1, trust boundary).
        """
        if self._overlay_fs is None:
            return False
        if self._is_binary_path(path):
            return False
        if self._fs.exists(path):
            return False
        return bool(self._overlay_fs.exists(path))

    def _root_for(self, fs: OSFS) -> str:
        return self._overlay_path if fs is self._overlay_fs else self._root_path

    def _confine(self, fs: OSFS, path: str) -> None:
        """Finding 4: reject entries whose realpath escapes the backend root.

        OSFS follows symlinks, so a symlink planted in the store/overlay root
        can be followed out of the root (exfiltration / out-of-root clobber).
        After resolving, assert the realpath stays under the intended root.
        """
        root = self._root_for(fs)
        root_real = os.path.realpath(root)
        try:
            syspath = fs.getsyspath(path)
        except Exception:
            # No OS-level path for this entry (non-OSFS backend) → nothing to confine.
            return
        target_real = os.path.realpath(syspath)
        if target_real != root_real and not target_real.startswith(root_real + os.sep):
            raise PermissionError(f"Path escapes store root: {path}")

    def _resolve_path(self, path: str) -> tuple[OSFS, str]:
        # _overlay_owns() is True only when _overlay_fs is not None, so this
        # narrows to a concrete OSFS for both branches.
        if self._overlay_owns(path):
            assert self._overlay_fs is not None
            fs: OSFS = self._overlay_fs
        else:
            fs = self._fs
        self._confine(fs, path)
        return fs, path

    @property
    def root_path(self) -> str:
        return self._root_path

    def write_text(self, path: str, content: str) -> None:
        self._fs.makedirs(dirname(path), recreate=True)
        # Confine the FULL path, not just its dirname: a symlink planted at the
        # file target itself would otherwise be followed and let the write escape
        # the store root (the read path already confines the full path).
        self._confine(self._fs, path)
        self._fs.writetext(path, content)

    def read_text(self, path: str) -> str:
        fs, resolved = self._resolve_path(path)
        return fs.readtext(resolved)

    def read_bytes(self, path: str) -> bytes:
        fs, resolved = self._resolve_path(path)
        return fs.readbytes(resolved)

    def write_bytes(self, path: str, data: bytes) -> None:
        self._fs.makedirs(dirname(path), recreate=True)
        # Confine the FULL path (see write_text): a symlink at the file target
        # must not let the write escape the store root.
        self._confine(self._fs, path)
        self._fs.writebytes(path, data)

    def exists(self, path: str) -> bool:
        # Use the same resolution as reads/removes so that exists()==True implies
        # the resolving backend can serve/remove the path (Finding 3 consistency).
        if self._overlay_owns(path):
            return True
        return self._fs.exists(path)

    def is_dir(self, path: str) -> bool:
        if self._overlay_fs is not None and self._overlay_owns(path):
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
        # Finding 3: remove from whichever backend exists() resolves to, so a
        # path exists() reported True can always be removed (no FileNotFoundError
        # on an overlay-only entry, and an overlay-only "removed" path no longer
        # resolves afterwards).
        if self._overlay_owns(path):
            assert self._overlay_fs is not None
            target: OSFS = self._overlay_fs
        elif self._fs.exists(path):
            target = self._fs
        else:
            raise FileNotFoundError(f"Path not found: {path}")
        # Confine before deleting: a symlink in the store must not let a remove
        # escape and delete a file outside the store root.
        self._confine(target, path)
        if target.isdir(path):
            target.removetree(path)
        else:
            target.remove(path)

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
