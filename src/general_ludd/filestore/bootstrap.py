"""Binary bootstrapper — downloads and manages OpenBao/OpenTofu binaries with bundled fallback."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import os
import platform
import shutil
import stat
import tarfile
from pathlib import Path
from typing import Any, ClassVar

from general_ludd.filestore.store import FileStore

logger = logging.getLogger(__name__)

# Hard ceiling on a downloaded artifact: `resp.content` buffers the whole body,
# so a huge or malicious response could exhaust memory. 512 MiB comfortably
# covers the largest pinned binary (openbao/osquery are tens of MB).
_MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024

# Pinned SHA-256 checksums of the *received artifact* — the raw downloaded bytes
# and the read bundled bytes (the ``.tar.gz`` for tarball-packaged binaries and
# the bare executable otherwise) — keyed by the same binary name as
# KNOWN_VERSIONS.
#
# This map is INTENTIONALLY EMPTY by default: the genuine upstream release
# digests are operator/config supplied (see BinaryBootstrapper._resolve_pins —
# the GLUDD_BINARY_SHA256 env var or the ``known_sha256`` constructor arg),
# because they are not vendored in-tree and must be pinned by whoever operates
# the deployment. A binary with NO pin is REFUSED (fail-closed): it is never
# stored nor made executable. This closes an RCE where a hijacked/MITM GitHub
# redirect (download() uses follow_redirects=True) could deliver arbitrary
# bytes that were then chmod +x'd and executed with no integrity check.
KNOWN_SHA256: dict[str, str] = {}

# Environment variable carrying operator-supplied pins as a JSON object mapping
# ``{"<binary-name>": "<hex-sha256>"}``. It is merged over the module default
# and then overridden by any explicit ``known_sha256`` constructor argument.
_SHA256_ENV_VAR = "GLUDD_BINARY_SHA256"

OPENBAO_VERSION = "2.2.0"
OPENTOFU_VERSION = "1.9.0"
# osquery 5.10.2 is the last release published as a plain GitHub release tarball
# under osquery/osquery (the project later moved most builds to .pkg/.deb/.rpm,
# but the .tar.gz assets remain attached to the 5.10.2 release).
OSQUERY_VERSION = "5.10.2"
# DeusData codebase-memory-mcp: structural code-intelligence MCP server shipped
# as a single static binary inside a per-platform .tar.gz GitHub release asset.
# The release tag carries a leading "v"; the in-archive binary is a bare
# executable named "codebase-memory-mcp" at the archive root.
CODEBASE_MEMORY_VERSION = "0.8.1"
OPENBAO_BASE_URL = f"https://github.com/openbao/openbao/releases/download/v{OPENBAO_VERSION}"
OPENTOFU_BASE_URL = f"https://github.com/opentofu/opentofu/releases/download/v{OPENTOFU_VERSION}"
OSQUERY_BASE_URL = f"https://github.com/osquery/osquery/releases/download/{OSQUERY_VERSION}"
CODEBASE_MEMORY_BASE_URL = (
    "https://github.com/DeusData/codebase-memory-mcp/releases/download/"
    f"v{CODEBASE_MEMORY_VERSION}"
)


class BinaryBootstrapper:
    """Downloads and manages platform-specific binaries. Bundled binaries take priority over downloads."""

    KNOWN_VERSIONS: dict[str, str]
    KNOWN_SHA256: dict[str, str]

    def __init__(
        self,
        store: Any = None,
        bundled_binaries_dir: str | None = None,
        known_sha256: dict[str, str] | None = None,
    ) -> None:
        self.KNOWN_VERSIONS = {
            "openbao": OPENBAO_VERSION,
            "opentofu": OPENTOFU_VERSION,
            "osquery": OSQUERY_VERSION,
            "codebase-memory-mcp": CODEBASE_MEMORY_VERSION,
        }
        # Pinned checksums for integrity verification (fail-closed if unpinned).
        self.KNOWN_SHA256 = self._resolve_pins(known_sha256)
        self._store = store or FileStore()
        self._store.makedirs("binaries")
        self._bundled_dir = bundled_binaries_dir

    @staticmethod
    def _resolve_pins(explicit: dict[str, str] | None) -> dict[str, str]:
        """Assemble the effective SHA-256 pin map.

        Precedence (lowest → highest): the module-level ``KNOWN_SHA256`` default,
        then the ``GLUDD_BINARY_SHA256`` env var (a JSON object), then any
        ``explicit`` constructor argument. All keys/values are coerced to
        strings; a malformed env var is ignored (logged) — it never silently
        disables verification, because an absent pin still fails closed.
        """
        pins: dict[str, str] = {str(k): str(v) for k, v in KNOWN_SHA256.items()}
        env_raw = os.environ.get(_SHA256_ENV_VAR)
        if env_raw:
            try:
                loaded = json.loads(env_raw)
            except (ValueError, TypeError) as exc:
                logger.warning("Ignoring malformed %s (not valid JSON): %s", _SHA256_ENV_VAR, exc)
            else:
                if isinstance(loaded, dict):
                    pins.update({str(k): str(v) for k, v in loaded.items()})
                else:
                    logger.warning("Ignoring %s: expected a JSON object", _SHA256_ENV_VAR)
        if explicit:
            pins.update({str(k): str(v) for k, v in explicit.items()})
        return pins

    def _verify_digest(self, name: str, data: bytes) -> bool:
        """Return True only if *data*'s SHA-256 matches the pinned checksum for
        *name*. Fail-closed: a missing pin OR a mismatch returns False and logs
        an error. The caller MUST NOT store or chmod-exec unverified bytes."""
        pinned = self.KNOWN_SHA256.get(name)
        if not pinned:
            logger.error(
                "Refusing binary %s: no pinned sha256 checksum configured "
                "(fail-closed). Set %s or pass known_sha256=. Not stored/executed.",
                name,
                _SHA256_ENV_VAR,
            )
            return False
        actual = hashlib.sha256(data).hexdigest()
        if not hmac.compare_digest(actual.lower(), str(pinned).strip().lower()):
            logger.error(
                "Refusing binary %s: sha256 mismatch (expected %s, got %s). "
                "Possible MITM/hijacked-redirect or tampered artifact; not stored/executed.",
                name,
                pinned,
                actual,
            )
            return False
        return True

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

        if name == "codebase-memory-mcp":
            return self._codebase_memory_download_url(os_name, info["arch"])

        if name == "openbao":
            # OpenBao release archives are `bao_<ver>_<Os>_<arch>.tar.gz`: CAPITALIZED
            # OS (Linux/Darwin), x86_64/arm64 arch (NOT amd64), and .tar.gz on EVERY
            # OS. The OpenTofu-style lowercase-os + amd64 + .zip name (below) 404s on
            # every platform — verified against the v2.2.0 release asset list.
            bao_arch = {"amd64": "x86_64", "arm64": "arm64"}.get(info["arch"], info["arch"])
            # OpenBao ships Linux/Darwin as .tar.gz but Windows as .zip.
            bao_ext = ".zip" if os_name == "windows" else ".tar.gz"
            filename = f"bao_{OPENBAO_VERSION}_{os_name.capitalize()}_{bao_arch}{bao_ext}"
            return f"{OPENBAO_BASE_URL}/{filename}"
        # OpenTofu: `tofu_<ver>_<os>_amd64.{tar.gz|zip}` — lowercase os, amd64, .zip on
        # darwin/windows. (arm64-native OpenTofu is a separate follow-up; the amd64
        # build runs under emulation on arm64 macOS.)
        ext = ".zip" if os_name in ("darwin", "windows") else ".tar.gz"
        filename = f"tofu_{OPENTOFU_VERSION}_{os_name}_amd64{ext}"
        return f"{OPENTOFU_BASE_URL}/{filename}"

    @staticmethod
    def _osquery_download_url(os_name: str, arch: str) -> str | None:
        """Build the osquery 5.10.2 GitHub release-asset URL.

        Verified directly against the real 5.10.2 release asset list
        (``GET /repos/osquery/osquery/releases/tags/5.10.2``), which does
        NOT match the naming this function previously assumed:

          - linux:  ``osquery-5.10.2_1.linux_x86_64.tar.gz`` and
                     ``osquery-5.10.2_1.linux_aarch64.tar.gz`` — note the
                     ``_1`` build-revision infix between the version and the
                     OS, and ``aarch64`` (not ``arm64``) for the ARM asset.
                     The previous code built ``osquery-5.10.2.linux_x86_64.
                     tar.gz`` (missing ``_1``, always 404) and treated linux
                     arm64 as unpublished (it *is* published, just under
                     ``aarch64``).
          - macOS:  only ONE tarball is published —
                     ``osquery-5.10.2_1.macos_x86_64.tar.gz`` — there is no
                     ``macos_arm64.tar.gz`` asset for this release (Apple
                     Silicon support for 5.10.2 ships only in the universal
                     ``.pkg``, which this tar-only bootstrapper does not
                     unpack). This is the exact 404 this bootstrapper was
                     hitting on Apple Silicon: it built
                     ``osquery-5.10.2.macos_arm64.tar.gz``, which does not
                     exist. This x86_64 tarball is used for BOTH amd64 and
                     arm64 Macs — on arm64 the extracted ``osqueryi`` runs
                     under Rosetta 2 (it is an Intel binary, not a universal
                     one), which the caller is responsible for having
                     available; there is no other tar.gz option for arm64.
          - windows: only ``.msi``/``.zip`` assets are published (no
                     ``.tar.gz``), so windows keeps returning ``None`` — this
                     bootstrapper only knows how to unpack ``.tar.gz`` via
                     :meth:`_extract_executable_member`.

        Returns ``None`` for platform/arch combos osquery does not publish a
        tarball for.
        """
        if os_name == "darwin":
            # Only one macOS tarball is published (x86_64); arm64 has no
            # native tar.gz for 5.10.2, so both arches resolve here (runs
            # under Rosetta 2 on Apple Silicon).
            return f"{OSQUERY_BASE_URL}/osquery-{OSQUERY_VERSION}_1.macos_x86_64.tar.gz"
        if os_name == "linux":
            # Map the normalized arch back to osquery's release-asset naming.
            osq_arch = {"amd64": "x86_64", "arm64": "aarch64"}.get(arch)
            if osq_arch is None:
                return None
            return f"{OSQUERY_BASE_URL}/osquery-{OSQUERY_VERSION}_1.linux_{osq_arch}.tar.gz"
        return None

    @staticmethod
    def _codebase_memory_download_url(os_name: str, arch: str) -> str | None:
        """Build the DeusData codebase-memory-mcp v0.8.1 release-asset URL.

        Assets are named ``codebase-memory-mcp-<os>-<arch>[-portable].tar.gz``
        where ``os`` is ``darwin`` or ``linux`` and ``arch`` is the normalized
        ``amd64``/``arm64``. Linux ships fully-static ``-portable`` builds
        (matching upstream ``install.sh``); darwin ships plain tarballs. Windows
        publishes a ``.zip`` (handled by the npx launch path, not this
        tar-only bootstrapper) and windows/arm64 is unpublished, so non-tarball
        and unmapped platform/arch combos return ``None``.
        """
        if arch not in ("amd64", "arm64"):
            return None
        if os_name == "darwin":
            asset = f"codebase-memory-mcp-darwin-{arch}.tar.gz"
        elif os_name == "linux":
            # Prefer the portable (fully-static) Linux build, as upstream does.
            asset = f"codebase-memory-mcp-linux-{arch}-portable.tar.gz"
        else:
            return None
        return f"{CODEBASE_MEMORY_BASE_URL}/{asset}"

    def _extract_executable_member(self, data: bytes, basename: str) -> bytes:
        """Extract the archive member whose basename is *basename* from a
        ``.tar.gz`` and return its raw bytes.

        Locates the first regular-file member matching *basename*, validating
        it against path-traversal (rejecting absolute paths and any
        ``..``-containing component) before reading. Extraction is in-memory via
        ``extractfile`` — nothing is written to disk during the scan.

        If ``data`` is not a valid gzip tarball (e.g. the caller passed a
        bundled plain executable by mistake) or no matching member exists, the
        original bytes are returned unchanged so the caller can still store them.
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
                    if os.path.basename(name) == basename and member.isfile():
                        f = tf.extractfile(member)
                        if f is not None:
                            return f.read()
        except Exception:
            # Not a gzip tarball or extraction failed — treat as plain binary.
            pass
        return data

    def _extract_osquery_executable(self, data: bytes) -> bytes:
        """Extract the ``osqueryi`` binary from a ``.tar.gz`` archive.

        The osquery release tarball contains ``<prefix>/bin/osqueryi`` (and
        other files); this returns that member's raw bytes (path-traversal
        validated, in-memory). See :meth:`_extract_executable_member`.
        """
        return self._extract_executable_member(data, "osqueryi")

    def _chmod_executable(self, path: str) -> None:
        """Add executable bits (u+x g+x o+x) to the file at *path*."""
        try:
            current = os.stat(path).st_mode
            os.chmod(path, current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except Exception as exc:  # pragma: no cover - filesystem edge-cases
            logger.warning("Failed to chmod %s: %s", path, exc)

    # Binaries shipped inside a .tar.gz release asset: map the stored binary
    # name to the bare executable's basename inside the archive. For these the
    # downloaded bytes are an archive, so we extract the executable member,
    # store it, and set executable bits.
    _TARBALL_BINARIES: ClassVar[dict[str, str]] = {
        "osquery": "osqueryi",
        "codebase-memory-mcp": "codebase-memory-mcp",
    }

    def _store_binary_and_chmod(self, name: str, data: bytes) -> None:
        """Store *data* under ``binaries/<name>``. For tarball-packaged binaries
        (osquery, codebase-memory-mcp), extract the executable from the archive
        and set executable bits on the stored result."""
        archived = self._TARBALL_BINARIES.get(name)
        if archived is not None:
            data = self._extract_executable_member(data, archived)
        self.store_binary(name, data)
        if archived is not None:
            stored_path = self.get_binary_path(name)
            if stored_path and os.path.isfile(stored_path):
                self._chmod_executable(stored_path)

    async def download(self, name: str) -> bool:
        import httpx

        bundled = self.get_bundled_binary_path(name)
        if bundled:
            try:
                data = Path(bundled).read_bytes()
            except Exception as exc:
                logger.warning("Bundled binary %s read failed: %s", name, exc)
            else:
                # Verify the bundled bytes against the pinned checksum BEFORE
                # storing/chmod-exec. A missing pin or mismatch fails closed:
                # refuse rather than fall through to an unverified download.
                if not self._verify_digest(name, data):
                    return False
                self._store_binary_and_chmod(name, data)
                logger.info("Used bundled binary %s from %s", name, bundled)
                return True

        url = self.get_download_url(name)
        if url is None:
            logger.info("No binary download available for %s on this platform", name)
            return False
        try:
            async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    # Refuse to STORE + chmod-exec an oversized artifact (by declared
                    # Content-Length or actual body length). httpx has already
                    # buffered resp.content, so this bounds what we PERSIST + execute,
                    # not peak download memory — true memory-bounding (client.stream +
                    # running cap) is a documented follow-up. Low severity: the
                    # download URLs are hardcoded, trusted GitHub releases.
                    clen = resp.headers.get("content-length")
                    if (
                        clen is not None
                        and clen.isdigit()
                        and int(clen) > _MAX_DOWNLOAD_BYTES
                    ) or len(resp.content) > _MAX_DOWNLOAD_BYTES:
                        logger.warning(
                            "%s download rejected: exceeds %d bytes",
                            name,
                            _MAX_DOWNLOAD_BYTES,
                        )
                        return False
                    # Integrity gate: verify the RECEIVED bytes against the
                    # pinned sha256 BEFORE storing + chmod-exec. follow_redirects
                    # is on, so a hijacked/MITM redirect could deliver arbitrary
                    # bytes; a missing pin or a mismatch fails closed (nothing is
                    # stored or made executable). This is the RCE guard.
                    if not self._verify_digest(name, resp.content):
                        return False
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
