#!/usr/bin/env python3
"""Acquire the exact project-local Pants launcher with a pinned SHA-256."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import stat
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "pants_launcher_assets.json"
DEFAULT_DESTINATION = ROOT / ".pants.d" / "bin" / "pants"
SUPPORTED_ASSETS = frozenset(
    {"macos-aarch64", "linux-aarch64", "linux-x86_64"}
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_MAX_LAUNCHER_BYTES = 64 * 1024 * 1024
_MAX_SIDECAR_BYTES = 4096
Downloader = Callable[[str], bytes]


@dataclass(frozen=True, slots=True)
class LauncherAsset:
    """One immutable launcher release asset and its reviewed digest."""

    version: str
    filename: str
    sha256: str

    @property
    def download_url(self) -> str:
        """Return the immutable upstream release asset URL."""
        return (
            "https://github.com/pantsbuild/scie-pants/releases/download/"
            f"v{self.version}/{self.filename}"
        )

    @property
    def checksum_url(self) -> str:
        """Return upstream's checksum sidecar URL for review/refresh only."""
        return f"{self.download_url}.sha256"


def asset_key(*, system: str, machine: str) -> str:
    """Normalize the host tuple to one supported scie-pants asset key."""
    normalized_system = system.strip().lower()
    normalized_machine = machine.strip().lower()
    if normalized_machine == "amd64":
        normalized_machine = "x86_64"
    candidates = {
        ("darwin", "arm64"): "macos-aarch64",
        ("darwin", "aarch64"): "macos-aarch64",
        ("linux", "aarch64"): "linux-aarch64",
        ("linux", "arm64"): "linux-aarch64",
        ("linux", "x86_64"): "linux-x86_64",
    }
    try:
        return candidates[(normalized_system, normalized_machine)]
    except KeyError as exc:
        raise ValueError(
            f"unsupported Pants launcher platform: {system}/{machine}"
        ) from exc


def parse_checksum_sidecar(payload: bytes, *, filename: str) -> str:
    """Parse one exact upstream SHA-256 sidecar without accepting aliases."""
    if not payload or len(payload) > _MAX_SIDECAR_BYTES:
        raise ValueError("checksum sidecar is empty or exceeds the size bound")
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("checksum sidecar is not ASCII") from exc
    lines = [line for line in text.splitlines() if line]
    if len(lines) != 1:
        raise ValueError("checksum sidecar must contain exactly one entry")
    match = re.fullmatch(r"([0-9a-f]{64}) [ *](\S+)", lines[0])
    if match is None or match.group(2) != filename:
        raise ValueError("checksum sidecar does not name the exact asset")
    return match.group(1)


def _download(url: str, *, maximum_bytes: int) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        raise ValueError("Pants launcher acquisition requires the exact GitHub HTTPS host")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "gludd-pants-launcher-bootstrap/1"},
    )
    chunks: list[bytes] = []
    total = 0
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"Pants launcher download returned HTTP {response.status}")
        while True:
            chunk = response.read(min(1024 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            print(f"PANTS-LAUNCHER-DOWNLOAD bytes={total} url={url}", flush=True)
            if total > maximum_bytes:
                raise RuntimeError("Pants launcher download exceeds the acquisition bound")
    if total == 0:
        raise RuntimeError("Pants launcher download is empty")
    return b"".join(chunks)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def acquire_launcher(
    *,
    destination: Path,
    asset: LauncherAsset,
    validate_only: bool,
    downloader: Downloader | None = None,
) -> str:
    """Validate/reuse/install one exact launcher via an atomic replacement."""
    if not _VERSION.fullmatch(asset.version):
        raise ValueError("Pants launcher version is not an exact stable version")
    if not _SHA256.fullmatch(asset.sha256):
        raise ValueError("Pants launcher digest is malformed")
    if asset.filename != f"scie-pants-{asset_key_from_filename(asset.filename)}":
        raise ValueError("Pants launcher filename is unsupported")
    if destination.is_symlink():
        raise ValueError("Pants launcher destination must not be a symlink")
    if destination.is_file() and _digest(destination) == asset.sha256:
        if validate_only:
            print(
                f"PANTS-LAUNCHER-VALID exact=true destination={destination}",
                flush=True,
            )
            return "validated"
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR)
        print(f"PANTS-LAUNCHER-REUSE destination={destination}", flush=True)
        return "reused"
    if validate_only:
        print(
            f"PANTS-LAUNCHER-PLAN url={asset.download_url} sha256={asset.sha256} "
            f"destination={destination}",
            flush=True,
        )
        return "planned"
    fetch = downloader or (
        lambda url: _download(url, maximum_bytes=_MAX_LAUNCHER_BYTES)
    )
    payload = fetch(asset.download_url)
    if not payload or len(payload) > _MAX_LAUNCHER_BYTES:
        raise ValueError("Pants launcher payload is empty or exceeds the size bound")
    actual = hashlib.sha256(payload).hexdigest()
    if actual != asset.sha256:
        raise ValueError(
            f"Pants launcher digest mismatch: expected {asset.sha256}, got {actual}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o700)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(
        f"PANTS-LAUNCHER-INSTALLED destination={destination} sha256={actual}",
        flush=True,
    )
    return "installed"


def asset_key_from_filename(filename: str) -> str:
    """Extract and validate one exact supported filename suffix."""
    prefix = "scie-pants-"
    if not filename.startswith(prefix):
        raise ValueError("Pants launcher filename is unsupported")
    key = filename.removeprefix(prefix)
    if key not in SUPPORTED_ASSETS:
        raise ValueError("Pants launcher filename is unsupported")
    return key


def load_asset(config_path: Path, *, requested_asset: str) -> LauncherAsset:
    """Load one launcher asset from the exact reviewed configuration."""
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Pants launcher configuration is unreadable") from exc
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "launcher_version",
        "assets",
    }:
        raise ValueError("Pants launcher configuration has an unexpected schema")
    version = raw["launcher_version"]
    assets = raw["assets"]
    if raw["schema_version"] != 1 or not isinstance(version, str):
        raise ValueError("Pants launcher configuration version is invalid")
    if not isinstance(assets, dict) or set(assets) != SUPPORTED_ASSETS:
        raise ValueError("Pants launcher configuration asset set is incomplete")
    key = (
        asset_key(system=platform.system(), machine=platform.machine())
        if requested_asset == "auto"
        else requested_asset
    )
    if key not in SUPPORTED_ASSETS:
        raise ValueError("requested Pants launcher asset is unsupported")
    entry = assets[key]
    if not isinstance(entry, Mapping) or set(entry) != {"filename", "sha256"}:
        raise ValueError("Pants launcher asset entry is malformed")
    filename = entry["filename"]
    digest = entry["sha256"]
    if not isinstance(filename, str) or not isinstance(digest, str):
        raise ValueError("Pants launcher asset values are malformed")
    asset = LauncherAsset(version=version, filename=filename, sha256=digest)
    if filename != f"scie-pants-{key}":
        raise ValueError("Pants launcher asset filename does not match its key")
    return asset


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--asset", default="auto")
    parser.add_argument("--validate-only", type=int, choices=(0, 1), default=1)
    parser.add_argument("--metadata-only", type=int, choices=(0, 1), default=0)
    return parser


def main() -> int:
    """Validate metadata or acquire the exact project-local launcher."""
    args = _parser().parse_args()
    try:
        asset = load_asset(args.config, requested_asset=args.asset)
        if args.metadata_only:
            payload = _download(asset.checksum_url, maximum_bytes=_MAX_SIDECAR_BYTES)
            upstream = parse_checksum_sidecar(payload, filename=asset.filename)
            print(
                json.dumps(
                    {
                        "asset": asset.filename,
                        "configured_sha256": asset.sha256,
                        "matches_configuration": upstream == asset.sha256,
                        "upstream_sha256": upstream,
                        "version": asset.version,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            return 0 if upstream == asset.sha256 else 1
        acquire_launcher(
            destination=args.destination,
            asset=asset,
            validate_only=bool(args.validate_only),
        )
        return 0
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(
            f"PANTS-LAUNCHER-FAIL error={type(exc).__name__}:{exc}",
            flush=True,
        )
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
