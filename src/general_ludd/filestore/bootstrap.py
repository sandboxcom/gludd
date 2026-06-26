"""Binary bootstrapper — downloads and manages OpenBao/OpenTofu binaries with bundled fallback."""

from __future__ import annotations

import io
import logging
import os
import platform
import shutil
import stat
import tarfile
from pathlib import Path
from typing import Any

from general_ludd.filestore.store import FileStore

logger = logging.getLogger(__name__)

OPENBAO_VERSION = "2.2.0"
OPENTOFU_VERSION = "1.9.0"
# osquery 5.10.2 is the last release published as a plain GitHub release tarball
# under osquery/osquery (the project later moved most builds to .pkg/.deb/.rpm,
# but the .tar.gz assets remain attached to the 5.10.2 release).
OSQUERY_VERSION = "5.10.2"
OPENBAO_BASE_URL = f"https://github.com/openbao/openbao/releases/download/v{OPENBAO_VERSION}"
OPENTOFU_BASE_URL = f"https://github.com/opentofu/opentofu/releases/download/v{OPENTOFU_VERSION}"
OSQUERY_BASE_URL = f"https://github.com/osquery/osquery/releases/download/{OSQUERY_VERSION}"


class BinaryBootstrapper:
    """Downloads and manages platform-specific binaries. Bundled binaries take priority over downloads."""

    KNOWN_VERSIONS: dict[str, str]

    def __init__(self, store: Any = None, bundled_binaries_dir: str | None = None) -> None:
        self.KNOWN_VERSIONS = {
            "openbao": OPENBAO_VERSION,
            "opentofu": OPENTOFU_VERSION,
            "osquery": OSQUERY_VERSION,
        }
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

    def is_platform_available(self, name: str) -> bool:
        return self.get_download_url(name) is not None

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

    def get_download_url(self, name: str) -> str | None:
        info = self.get_platform_info()
        os_name = info["os"]

        if name == "osquery":
            return self._osquery_download_url(os_name, info["arch"])

        ext = ".zip" if os_name in ("darwin", "windows") else ".tar.gz"
        if name == "openbao":
            version = OPENBAO_VERSION
            base = OPENBAO_BASE_URL
            release_name = "bao"
        else:
            version = OPENTOFU_VERSION
            base = OPENTOFU_BASE_URL
            release_name = "tofu"
        filename = f"{release_name}_{version}_{os_name}_amd64{ext}"
        return f"{base}/{filename}"

    @staticmethod
    def _osquery_download_url(os_name: str, arch: str) -> str | None:
        """Build the osquery 5.10.2 GitHub release-asset URL.

        Assets are named ``osquery-<ver>.<os>_<arch>.tar.gz`` where ``os`` is
        ``linux`` or ``macos`` and ``arch`` is osquery's own naming
        (``x86_64`` / ``arm64`` — NOT the ``amd64`` the bootstrapper normalizes
        to). Returns ``None`` for platform/arch combos osquery does not publish
        a tarball for (e.g. Windows ships an .msi, not a .tar.gz, and linux
        arm64 has no 5.10.2 tarball).
        """
        # Map the normalized arch back to osquery's release-asset naming.
        osq_arch = {"amd64": "x86_64", "arm64": "arm64"}.get(arch)
        if osq_arch is None:
            return None
        if os_name == "darwin":
            asset_os = "macos"
        elif os_name == "linux":
            asset_os = "linux"
            # osquery 5.10.2 publishes only an x86_64 linux tarball.
            if osq_arch != "x86_64":
                return None
        else:
            return None
        filename = f"osquery-{OSQUERY_VERSION}.{asset_os}_{osq_arch}.tar.gz"
        return f"{OSQUERY_BASE_URL}/{filename}"

    def _extract_osquery_executable(self, data: bytes) -> bytes:
        """Extract the ``osqueryi`` binary from a ``.tar.gz`` archive.

        The osquery release tarball contains ``<prefix>/bin/osqueryi`` (and
        other files). This method locates the first member whose basename is
        ``osqueryi``, validates it against path-traversal attacks, and returns
        its raw bytes.

        If ``data`` is not a valid gzip tarball (e.g. the caller passed a
        bundled plain executable by mistake), the original bytes are returned
        unchanged so the caller can still store them.
        """
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
                for member in tf.getmembers():
                    name = member.name
                    # Safety: reject absolute paths and any '..'-containing component.
                    if os.path.isabs(name):
                        continue
                    parts = name.replace("\\", "/").split("/")
                    if ".." in parts:
                        continue
                    if os.path.basename(name) == "osqueryi" and member.isfile():
                        f = tf.extractfile(member)
                        if f is not None:
                            return f.read()
        except Exception:
            # Not a gzip tarball or extraction failed — treat as plain binary.
            pass
        return data

    def _chmod_executable(self, path: str) -> None:
        """Add executable bits (u+x g+x o+x) to the file at *path*."""
        try:
            current = os.stat(path).st_mode
            os.chmod(path, current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except Exception as exc:  # pragma: no cover - filesystem edge-cases
            logger.warning("Failed to chmod %s: %s", path, exc)

    def _store_binary_and_chmod(self, name: str, data: bytes) -> None:
        """Store *data* under ``binaries/<name>`` and, for osquery, extract the
        executable from the tarball and set executable bits on the result."""
        if name == "osquery":
            data = self._extract_osquery_executable(data)
        self.store_binary(name, data)
        if name == "osquery":
            stored_path = self.get_binary_path(name)
            if stored_path and os.path.isfile(stored_path):
                self._chmod_executable(stored_path)

    async def download(self, name: str) -> bool:
        import httpx

        bundled = self.get_bundled_binary_path(name)
        if bundled:
            try:
                data = Path(bundled).read_bytes()
                self._store_binary_and_chmod(name, data)
                logger.info("Used bundled binary %s from %s", name, bundled)
                return True
            except Exception as exc:
                logger.warning("Bundled binary %s read failed: %s", name, exc)

        url = self.get_download_url(name)
        if url is None:
            logger.info("No binary download available for %s on this platform", name)
            return False
        try:
            async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    self._store_binary_and_chmod(name, resp.content)
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
