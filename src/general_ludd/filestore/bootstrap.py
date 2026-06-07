"""Binary bootstrapper — downloads and manages podman, openbao binaries in the filestore."""

from __future__ import annotations

import logging
import platform
import shutil
from typing import Any

logger = logging.getLogger(__name__)

OPENBAO_VERSION = "2.2.0"
OPENBAO_BASE_URL = f"https://github.com/openbao/openbao/releases/download/v{OPENBAO_VERSION}"


class BinaryBootstrapper:
    """Downloads and manages platform-specific binaries in the filestore."""

    def __init__(self, store: Any = None) -> None:
        from general_ludd.filestore.store import FileStore

        self._store = store or FileStore()
        self._store.makedirs("binaries")

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

    def list_binaries(self) -> list[dict[str, Any]]:
        entries = self._store.list_dir("binaries")
        for e in entries:
            e["binary_name"] = e["name"]
        return entries

    def get_binary_path(self, name: str) -> str | None:
        path = f"binaries/{name}"
        if self._store.exists(path):
            from pathlib import Path

            return str(Path(self._store.root_path) / path)
        return None

    def _get_openbao_download_url(self) -> str:
        info = self.get_platform_info()
        os_name = info["os"]
        arch = info["arch"]
        if os_name == "darwin":
            os_name = "darwin"
        elif os_name == "linux":
            os_name = "linux"
        ext = ".zip" if os_name == "darwin" or "windows" in os_name else ".tar.gz"
        filename = f"openbao_{OPENBAO_VERSION}_{os_name}_{arch}{ext}"
        return f"{OPENBAO_BASE_URL}/{filename}"

    async def download_openbao(self) -> bool:
        import httpx

        url = self._get_openbao_download_url()
        try:
            async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    self.store_binary("openbao", resp.content)
                    logger.info("Downloaded OpenBao %s from %s", OPENBAO_VERSION, url)
                    return True
                else:
                    logger.warning("OpenBao download failed: HTTP %d", resp.status_code)
                    return False
        except Exception as exc:
            logger.warning("OpenBao download error: %s", exc)
            return False

    def check_openbao_in_store(self) -> bool:
        return self._store.exists("binaries/openbao")
