"""Binary bootstrapper — downloads and manages OpenBao/OpenTofu binaries with bundled fallback."""

from __future__ import annotations

import logging
import os
import platform
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

OPENBAO_VERSION = "2.2.0"
OPENTOFU_VERSION = "1.9.0"
OPENBAO_BASE_URL = f"https://github.com/openbao/openbao/releases/download/v{OPENBAO_VERSION}"
OPENTOFU_BASE_URL = f"https://github.com/opentofu/opentofu/releases/download/v{OPENTOFU_VERSION}"


class BinaryBootstrapper:
    """Downloads and manages platform-specific binaries. Bundled binaries take priority over downloads."""

    KNOWN_VERSIONS: dict[str, str]

    def __init__(self, store: Any = None, bundled_binaries_dir: str | None = None) -> None:
        from general_ludd.filestore.store import FileStore

        self.KNOWN_VERSIONS = {"openbao": OPENBAO_VERSION, "opentofu": OPENTOFU_VERSION}
        self._store = store or FileStore()
        self._store.makedirs("binaries")
        self._bundled_dir = bundled_binaries_dir

    def detect_binary(self, name: str) -> bool:
        return shutil.which(name) is not None

    def get_platform_info(self) -> dict[str, str]:
        system = platform.system().lower()
        machine = platform.machine().lower()
        if machine in ("x86_64", "amd64"):
            arch = "amd64"
        elif machine in ("aarch64", "arm64"):
            arch = "arm64"
        else:
            arch = machine
        return {"os": system, "arch": arch}

    def store_binary(self, name: str, data: bytes) -> None:
        path = f"binaries/{name}"
        self._store.write_bytes(path, data)
        logger.info("Stored binary %s (%d bytes)", name, len(data))

    def get_known_versions(self) -> dict[str, str]:
        return dict(self.KNOWN_VERSIONS)

    def list_binaries(self) -> list[dict[str, Any]]:
        entries = self._store.list_dir("binaries")
        for e in entries:
            e["binary_name"] = e["name"]
            e["version"] = self.KNOWN_VERSIONS.get(e["name"], "unknown")
        return entries

    def list_binaries_with_versions(self) -> list[dict[str, Any]]:
        return self.list_binaries()

    def get_binary_path(self, name: str) -> str | None:
        path = f"binaries/{name}"
        if self._store.exists(path):
            return str(Path(self._store.root_path) / path)
        return None

    def get_bundled_binary_path(self, name: str) -> str | None:
        if self._bundled_dir:
            bp = Path(self._bundled_dir) / name
            if bp.is_file():
                return str(bp)
        dist_bundled = self._find_dist_bundled_dir()
        if dist_bundled:
            bp = Path(dist_bundled) / name
            if bp.is_file():
                return str(bp)
        return None

    def _has_bundled(self, name: str) -> bool:
        return self.get_bundled_binary_path(name) is not None

    @staticmethod
    def _find_dist_bundled_dir() -> str | None:
        candidates = [
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "dist", "binaries"),
            os.path.join(os.getcwd(), "dist", "binaries"),
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "dist", "binaries"),
        ]
        for c in candidates:
            if os.path.isdir(c):
                return c
        return None

    def sync_bundled_to_filestore(self) -> list[str]:
        synced = []
        for name in self.KNOWN_VERSIONS:
            bundled = self.get_bundled_binary_path(name)
            if bundled and not self._store.exists(f"binaries/{name}"):
                try:
                    data = Path(bundled).read_bytes()
                    self.store_binary(name, data)
                    synced.append(name)
                except Exception as exc:
                    logger.warning("Failed to sync bundled binary %s: %s", name, exc)
        return synced

    def get_download_url(self, name: str) -> str:
        info = self.get_platform_info()
        os_name = info["os"]
        arch = info["arch"]
        if name == "openbao":
            version = OPENBAO_VERSION
            base = OPENBAO_BASE_URL
            release_name = "bao"
        else:
            version = OPENTOFU_VERSION
            base = OPENTOFU_BASE_URL
            release_name = "tofu"
        ext = ".zip" if os_name == "darwin" else ".tar.gz"
        filename = f"{release_name}_{version}_{os_name}_{arch}{ext}"
        return f"{base}/{filename}"

    async def download(self, name: str) -> bool:
        import httpx

        bundled = self.get_bundled_binary_path(name)
        if bundled:
            try:
                data = Path(bundled).read_bytes()
                self.store_binary(name, data)
                logger.info("Used bundled binary %s from %s", name, bundled)
                return True
            except Exception as exc:
                logger.warning("Bundled binary %s read failed: %s", name, exc)

        url = self.get_download_url(name)
        try:
            async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    self.store_binary(name, resp.content)
                    logger.info("Downloaded %s v%s from %s", name, self.KNOWN_VERSIONS.get(name, "?"), url)
                    return True
                else:
                    logger.warning("%s download failed: HTTP %d", name, resp.status_code)
                    return False
        except Exception as exc:
            logger.warning("%s download error: %s", name, exc)
            return False

    async def download_openbao(self) -> bool:
        return await self.download("openbao")

    def check_openbao_in_store(self) -> bool:
        return self._store.exists("binaries/openbao") or self._has_bundled("openbao")

    async def download_all(self) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for name in self.KNOWN_VERSIONS:
            results[name] = await self.download(name)
        return results
