"""Binary bootstrapper — downloads and manages OpenBao/OpenTofu binaries with bundled fallback."""

from __future__ import annotations

import hashlib
import io
import logging
import os
import platform
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any

from general_ludd.filestore.store import FileStore

logger = logging.getLogger(__name__)

OPENBAO_VERSION = "2.2.0"
OPENTOFU_VERSION = "1.9.0"
OPENBAO_BASE_URL = f"https://github.com/openbao/openbao/releases/download/v{OPENBAO_VERSION}"
OPENTOFU_BASE_URL = f"https://github.com/opentofu/opentofu/releases/download/v{OPENTOFU_VERSION}"

# Hard cap for downloaded archive size: 200 MiB
_MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024


class ChecksumMismatchError(ValueError):
    """Raised when a downloaded archive's SHA-256 digest does not match the expected value."""


class DownloadSizeExceededError(OSError):
    """Raised when a streamed download exceeds the configured size cap."""


def _safe_extract(archive_bytes: bytes, dest: Path, archive_name: str) -> None:
    """Extract a tar.gz or .zip archive, rejecting path-traversal and absolute members.

    On Python 3.12+ uses tarfile ``filter='data'`` for additional hardening.
    For older Pythons we manually validate each member name.
    """
    lower = archive_name.lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            for info in zf.infolist():
                member = info.filename
                if os.path.isabs(member) or ".." in Path(member).parts:
                    raise ValueError(
                        f"Rejecting unsafe zip member: {member!r}"
                    )
            zf.extractall(dest)
    elif lower.endswith(".tar.gz") or lower.endswith(".tgz"):
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
            if sys.version_info >= (3, 12):
                tf.extractall(dest, filter="data")
            else:
                for member in tf.getmembers():
                    mname = member.name
                    if os.path.isabs(mname) or ".." in Path(mname).parts:
                        raise ValueError(
                            f"Rejecting unsafe tar member: {mname!r}"
                        )
                tf.extractall(dest)
    else:
        raise ValueError(f"Unsupported archive format: {archive_name!r}")


class BinaryBootstrapper:
    """Downloads and manages platform-specific binaries. Bundled binaries take priority over downloads."""

    KNOWN_VERSIONS: dict[str, str]

    def __init__(self, store: Any = None, bundled_binaries_dir: str | None = None) -> None:
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

    def get_checksum_url(self, name: str) -> str | None:
        """Return the URL of the SHA256SUMS file that accompanies the release archive."""
        if name == "openbao":
            version = OPENBAO_VERSION
            base = OPENBAO_BASE_URL
            release_name = "bao"
        else:
            version = OPENTOFU_VERSION
            base = OPENTOFU_BASE_URL
            release_name = "tofu"
        # Conventional sibling file: e.g. bao_2.2.0_SHA256SUMS or tofu_1.9.0_SHA256SUMS
        checksum_filename = f"{release_name}_{version}_SHA256SUMS"
        return f"{base}/{checksum_filename}"

    @staticmethod
    def _parse_sha256sums(content: str, archive_filename: str) -> str | None:
        """Parse a SHA256SUMS file and return the digest for *archive_filename*, or None."""
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                digest, fname = parts
                # fname may be prefixed with '*' or './'
                fname = fname.lstrip("*").lstrip("./").strip()
                if fname == archive_filename or fname.endswith("/" + archive_filename):
                    return digest.lower()
        return None

    async def _stream_download(self, client: Any, url: str) -> bytes:
        """Stream *url* via *client*, aborting if the body exceeds ``_MAX_DOWNLOAD_BYTES``."""
        chunks: list[bytes] = []
        total = 0
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(chunk_size=65536):
                total += len(chunk)
                if total > _MAX_DOWNLOAD_BYTES:
                    raise DownloadSizeExceededError(
                        f"Download from {url!r} exceeds size cap "
                        f"({_MAX_DOWNLOAD_BYTES // (1024 * 1024)} MiB); aborting."
                    )
                chunks.append(chunk)
        return b"".join(chunks)

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
        if url is None:
            logger.info("No binary download available for %s on this platform", name)
            return False

        # Derive the archive filename from the URL for checksum lookup.
        archive_filename = url.rsplit("/", 1)[-1]
        checksum_url = self.get_checksum_url(name)

        try:
            async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                # --- Fetch the archive with a hard size cap ---
                try:
                    content = await self._stream_download(client, url)
                except httpx.HTTPStatusError as exc:
                    logger.warning("%s download failed: HTTP %d", name, exc.response.status_code)
                    return False
                except DownloadSizeExceededError as exc:
                    logger.warning("%s download aborted: %s", name, exc)
                    return False

                # --- Fetch and verify SHA256SUMS ---
                if checksum_url is not None:
                    try:
                        cs_resp = await client.get(checksum_url)
                        if cs_resp.status_code == 200:
                            expected_digest = self._parse_sha256sums(
                                cs_resp.text, archive_filename
                            )
                            if expected_digest is not None:
                                actual_digest = hashlib.sha256(content).hexdigest()
                                if actual_digest != expected_digest:
                                    raise ChecksumMismatchError(
                                        f"SHA-256 mismatch for {archive_filename}: "
                                        f"expected {expected_digest}, got {actual_digest}"
                                    )
                                logger.info(
                                    "Checksum verified for %s (%s)", archive_filename, actual_digest
                                )
                            else:
                                logger.warning(
                                    "Archive %r not found in SHA256SUMS; skipping checksum",
                                    archive_filename,
                                )
                        else:
                            logger.warning(
                                "Could not fetch SHA256SUMS (HTTP %d); skipping checksum",
                                cs_resp.status_code,
                            )
                    except ChecksumMismatchError:
                        raise
                    except Exception as exc:
                        logger.warning("Checksum fetch/parse failed: %s", exc)

                self.store_binary(name, content)
                logger.info(
                    "Downloaded %s v%s from %s", name, self.KNOWN_VERSIONS.get(name, "?"), url
                )
                return True
        except ChecksumMismatchError as exc:
            logger.error("%s", exc)
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
