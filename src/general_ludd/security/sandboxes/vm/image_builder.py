"""Rootfs image builder for VM sandboxes.

Phase P1 stubs. P2 will build an Alpine Linux rootfs with gludd + deps
(CPython, ansible, git, native extensions), cache the image at
``~/.cache/gludd/sandbox/<hash>/rootfs.ext4``, and verify the image integrity
before booting a Firecracker microVM from it.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".cache" / "gludd" / "sandbox"


def build_rootfs(output_path: str | Path) -> Path:
    """Build a rootfs image at ``output_path``. Phase P1 stub.

    P2 will: download Alpine minirootfs, install Python + ansible + git +
    native extensions, create the ext4 image via ``mkfs.ext4``, and return
    the path.
    """
    dest = Path(output_path)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("build_rootfs stub — output %s (P2 will build Alpine rootfs)", dest)
    return dest


def verify_image(image_path: str | Path) -> bool:
    """Check that ``image_path`` is a valid, bootable rootfs. Phase P1 stub.

    P2 will: verify the ext4 magic number, mount the image loopback, check
    that /sbin/init + agent_executor exist, and unmount.
    """
    img = Path(image_path)
    if not img.exists():
        logger.warning("verify_image stub: %s does not exist (always False in P1)", img)
        return False
    logger.info("verify_image stub — %s exists (P2 will validate ext4 + contents)", img)
    return True


__all__ = ["CACHE_DIR", "build_rootfs", "verify_image"]
