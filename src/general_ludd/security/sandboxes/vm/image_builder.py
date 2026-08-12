"""Rootfs image builder for VM sandboxes.

Builds minimal Linux rootfs images for Firecracker microVMs (kernel + initrd/ext4)
and OCI-compatible bundles for gVisor.  Images are content-hash-cached under
``~/.cache/gludd/sandbox/<sha256>/`` so identical dependency sets are never
rebuilt.

Phase P2 — programmatic image creation without requiring ``mkfs.ext4`` or
``debootstrap`` on the host.  Real Alpine-based builds will be added when the CI
pipeline provides the base minirootfs tarball (P3).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import struct
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from filelock import FileLock

logger = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".cache" / "gludd" / "sandbox"

EXT4_MAGIC = b"\x53\xef"
EXT4_SUPERBLOCK_OFFSET = 1024
EXT4_MIN_SIZE = 64 * 1024 * 1024


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ImageManifest:
    """Declares what goes into a rootfs image.

    The manifest is serialised to JSON and hashed to produce the cache key.
    """

    name: str
    packages: tuple[str, ...] = ()
    architecture: str = "x86_64"
    kernel_version: str = "5.10"
    custom_files: tuple[tuple[str, bytes], ...] = ()
    extra: dict[str, object] = field(default_factory=dict, compare=False, hash=False)

    def content_hash(self) -> str:
        payload = json.dumps(
            {
                "name": self.name,
                "packages": sorted(self.packages),
                "architecture": self.architecture,
                "kernel_version": self.kernel_version,
                "custom_files": sorted(
                    (p, _sha256_hex(c)) for p, c in self.custom_files
                ),
                "extra": dict(sorted(self.extra.items())),
            },
            sort_keys=True,
        ).encode()
        return _sha256_hex(payload)

    def cache_path(self) -> Path:
        return CACHE_DIR / self.content_hash()


@dataclass(frozen=True)
class BuiltImage:
    path: Path
    manifest_hash: str
    image_type: str
    size_bytes: int
    files: tuple[str, ...]
    built_at: float = field(default_factory=time.time)


def _write_ext4_superblock(fd: int, block_count: int, inode_count: int) -> None:
    superblock = bytearray(1024)
    superblock[0x38:0x3C] = struct.pack("<I", block_count)
    superblock[0x28:0x2C] = struct.pack("<I", inode_count)
    superblock[0x40:0x44] = struct.pack("<I", 0)
    superblock[0x44:0x48] = struct.pack("<I", 0)
    superblock[0x18:0x1C] = struct.pack("<I", 0)
    superblock[0x3C:0x40] = struct.pack("<I", 0)
    superblock[0x14:0x18] = struct.pack("<I", 1)
    superblock[0x38:0x3A] = EXT4_MAGIC
    superblock[0x50:0x52] = struct.pack("<H", 1)
    superblock[0x54:0x58] = struct.pack("<I", 4)
    superblock[0x20:0x24] = struct.pack("<I", 4096)
    os.lseek(fd, EXT4_SUPERBLOCK_OFFSET, os.SEEK_SET)
    os.write(fd, bytes(superblock))


def _create_ext4_image(path: Path, size_bytes: int = EXT4_MIN_SIZE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    block_size = 4096
    block_count = size_bytes // block_size
    inode_count = block_count // 4
    with path.open("wb") as f:
        f.truncate(size_bytes)
        _write_ext4_superblock(f.fileno(), block_count, inode_count)
    return path


def _verify_ext4_magic(image_path: Path) -> bool:
    if not image_path.is_file():
        return False
    try:
        with image_path.open("rb") as f:
            f.seek(EXT4_SUPERBLOCK_OFFSET + 0x38)
            magic = f.read(2)
            return magic == EXT4_MAGIC
    except OSError:
        return False


def _create_oci_bundle(
    output_dir: Path, rootfs_files: dict[str, bytes]
) -> tuple[str, ...]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rootfs_dir = output_dir / "rootfs"
    rootfs_dir.mkdir(exist_ok=True)
    for rel_path, content in rootfs_files.items():
        target = rootfs_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o755 if "bin" in str(rel_path) else 0o644)
    config = {
        "ociVersion": "1.1.0",
        "process": {
            "terminal": False,
            "user": {"uid": 0, "gid": 0},
            "args": ["/usr/bin/agent_executor"],
            "env": [
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            ],
            "cwd": "/",
            "capabilities": {
                "bounding": [],
                "effective": [],
                "inheritable": [],
                "permitted": [],
                "ambient": [],
            },
            "noNewPrivileges": True,
        },
        "root": {"path": "rootfs", "readonly": True},
        "linux": {
            "namespaces": [
                {"type": "pid"},
                {"type": "network"},
                {"type": "ipc"},
                {"type": "uts"},
                {"type": "mount"},
            ],
        },
    }
    config_path = output_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2))
    return tuple(
        str(p)
        for p in sorted(
            output_dir / p
            for p in sorted(
                p.relative_to(output_dir) for p in output_dir.rglob("*") if p.is_file()
            )
        )
    )


def _verify_oci_bundle(bundle_dir: Path) -> bool:
    config = bundle_dir / "config.json"
    rootfs = bundle_dir / "rootfs"
    if not config.is_file():
        return False
    if not rootfs.is_dir():
        return False
    try:
        spec = json.loads(config.read_text())
        if spec.get("ociVersion") is None:
            return False
        if spec.get("process", {}).get("args") is None:
            return False
    except (json.JSONDecodeError, OSError):
        return False
    return True


def build_firecracker_image(
    manifest: ImageManifest,
    force_rebuild: bool = False,
) -> BuiltImage:
    cache_key = manifest.content_hash()
    cache_path = manifest.cache_path()

    if not force_rebuild and cache_path.exists() and (cache_path / "rootfs.ext4").exists():
        ext4_path = cache_path / "rootfs.ext4"
        size = ext4_path.stat().st_size
        logger.info(
            "Firecracker image cache hit — %s (%d bytes)", cache_key[:12], size,
        )
        return BuiltImage(
            path=cache_path,
            manifest_hash=cache_key,
            image_type="firecracker",
            size_bytes=size,
            files=("rootfs.ext4",),
        )

    cache_path.mkdir(parents=True, exist_ok=True)
    ext4_path = cache_path / "rootfs.ext4"
    _create_ext4_image(ext4_path)

    manifest_json = json.dumps(
        {
            "name": manifest.name,
            "packages": list(manifest.packages),
            "architecture": manifest.architecture,
            "kernel_version": manifest.kernel_version,
            "built_at": time.time(),
            "builder_version": "gludd-image-builder-P2",
        },
        indent=2,
    ).encode()
    (cache_path / "manifest.json").write_bytes(manifest_json)

    size = ext4_path.stat().st_size
    logger.info(
        "Built Firecracker image — %s (%d bytes, cache=%s)",
        manifest.name, size, cache_key[:12],
    )
    return BuiltImage(
        path=cache_path,
        manifest_hash=cache_key,
        image_type="firecracker",
        size_bytes=size,
        files=("rootfs.ext4", "manifest.json"),
    )


def build_gvisor_image(
    manifest: ImageManifest,
    force_rebuild: bool = False,
) -> BuiltImage:
    cache_key = manifest.content_hash()
    cache_path = manifest.cache_path()

    if (
        not force_rebuild
        and cache_path.exists()
        and (cache_path / "config.json").exists()
    ):
        bundle_files = tuple(
            str(p.relative_to(cache_path))
            for p in sorted(cache_path.rglob("*"))
            if p.is_file()
        )
        total_size = sum((cache_path / p).stat().st_size for p in bundle_files)
        logger.info(
            "gVisor OCI bundle cache hit — %s (%d bytes)", cache_key[:12], total_size,
        )
        return BuiltImage(
            path=cache_path,
            manifest_hash=cache_key,
            image_type="gvisor",
            size_bytes=total_size,
            files=bundle_files,
        )

    cache_path.mkdir(parents=True, exist_ok=True)
    rootfs_files: dict[str, bytes] = {
        "usr/bin/agent_executor": b"#!/usr/bin/env python3\n# agent_executor for gVisor",
        "etc/hostname": manifest.name.encode(),
        "etc/resolv.conf": b"nameserver 8.8.8.8\n",
        "tmp/.keep": b"",
    }
    for rel, data in manifest.custom_files:
        rootfs_files[rel] = data

    oci_files = _create_oci_bundle(cache_path, rootfs_files)

    manifest_json = json.dumps(
        {
            "name": manifest.name,
            "packages": list(manifest.packages),
            "architecture": manifest.architecture,
            "built_at": time.time(),
            "builder_version": "gludd-image-builder-P2",
        },
        indent=2,
    ).encode()
    (cache_path / "manifest.json").write_bytes(manifest_json)

    total_size = sum((cache_path / p).stat().st_size for p in oci_files)
    logger.info(
        "Built gVisor OCI bundle — %s (%d bytes, cache=%s)",
        manifest.name, total_size, cache_key[:12],
    )
    return BuiltImage(
        path=cache_path,
        manifest_hash=cache_key,
        image_type="gvisor",
        size_bytes=total_size,
        files=tuple(sorted(oci_files)),
    )


def build_rootfs(
    output_path: str | Path,
    image_type: str = "firecracker",
    manifest: ImageManifest | None = None,
    force_rebuild: bool = False,
) -> BuiltImage:
    dest = Path(output_path)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if manifest is None:
        manifest = ImageManifest(
            name="gludd-sandbox-default",
            packages=("python3", "ansible", "git"),
            architecture="x86_64",
        )

    if image_type == "firecracker":
        built = build_firecracker_image(manifest, force_rebuild=force_rebuild)
    elif image_type == "gvisor":
        built = build_gvisor_image(manifest, force_rebuild=force_rebuild)
    else:
        raise ValueError(
            f"Unknown image_type: {image_type} (expected firecracker or gvisor)"
        )

    if dest.resolve() != built.path.resolve():
        dest.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=f".{dest.name}.stage-", dir=dest.parent))
        try:
            shutil.copytree(built.path, stage, dirs_exist_ok=True, symlinks=True)
            displaced: Path | None = None
            publish_lock = FileLock(
                str(dest.with_name(f".{dest.name}.publish.lock")), timeout=30
            )
            with publish_lock:
                if dest.exists():
                    candidate = dest.with_name(
                        f".{dest.name}.old-{os.getpid()}-{time.time_ns()}"
                    )
                    try:
                        dest.replace(candidate)
                    except FileNotFoundError:
                        # An external cleanup may remove the shared destination
                        # between the existence check and the atomic rename.
                        pass
                    else:
                        displaced = candidate
                try:
                    stage.replace(dest)
                except BaseException:
                    if displaced is not None and displaced.exists() and not dest.exists():
                        displaced.replace(dest)
                    raise
            if displaced is not None:
                shutil.rmtree(displaced, ignore_errors=True)
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    return BuiltImage(
        path=dest,
        manifest_hash=built.manifest_hash,
        image_type=built.image_type,
        size_bytes=built.size_bytes,
        files=built.files,
        built_at=built.built_at,
    )


def verify_image(image_path: str | Path) -> bool:
    img = Path(image_path)
    if not img.exists():
        logger.warning("Image path does not exist: %s", img)
        return False
    if img.is_dir():
        return _verify_oci_bundle(img)
    return _verify_ext4_magic(img)


def list_cached_images() -> list[dict[str, Any]]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for subdir in sorted(CACHE_DIR.iterdir()):
        if not subdir.is_dir():
            continue
        manifest_file = subdir / "manifest.json"
        is_firecracker = (subdir / "rootfs.ext4").exists()
        is_gvisor = (subdir / "config.json").exists()
        if not manifest_file.exists():
            entries.append({
                "hash": subdir.name,
                "type": "unknown",
                "size_bytes": 0,
            })
            continue
        try:
            meta = json.loads(manifest_file.read_text())
        except (json.JSONDecodeError, OSError):
            meta = {}
        total = sum(
            (p).stat().st_size for p in subdir.rglob("*") if p.is_file()
        )
        entry_type = (
            "firecracker" if is_firecracker else "gvisor" if is_gvisor else "unknown"
        )
        entries.append({
            "hash": subdir.name,
            "type": entry_type,
            "name": meta.get("name", "unknown"),
            "size_bytes": total,
            "built_at": meta.get("built_at", 0),
        })
    return entries


def cleanup_cache(max_age_seconds: int = 86400) -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    removed = 0
    now = time.time()
    for subdir in sorted(CACHE_DIR.iterdir()):
        if not subdir.is_dir():
            continue
        manifest_file = subdir / "manifest.json"
        if manifest_file.exists():
            try:
                meta = json.loads(manifest_file.read_text())
                built_at = meta.get("built_at", 0)
                if now - built_at < max_age_seconds:
                    continue
            except (json.JSONDecodeError, OSError):
                pass
        shutil.rmtree(subdir, ignore_errors=True)
        removed += 1
        logger.info("Cleaned up cached image: %s", subdir.name)
    return removed


def image_exists(manifest: ImageManifest) -> bool:
    cache_path = manifest.cache_path()
    if not cache_path.exists():
        return False
    return bool(
        (cache_path / "rootfs.ext4").exists()
        or (cache_path / "config.json").exists()
    )


def get_image_path(manifest: ImageManifest) -> Path | None:
    cache_path = manifest.cache_path()
    if (cache_path / "rootfs.ext4").exists():
        return cache_path / "rootfs.ext4"
    if (cache_path / "config.json").exists():
        return cache_path
    return None


__all__ = [
    "CACHE_DIR",
    "BuiltImage",
    "ImageManifest",
    "build_firecracker_image",
    "build_gvisor_image",
    "build_rootfs",
    "cleanup_cache",
    "get_image_path",
    "image_exists",
    "list_cached_images",
    "verify_image",
]
