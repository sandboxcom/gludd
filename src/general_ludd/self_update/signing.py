"""Ed25519 signature verification for self-update content integrity.

An update applied with zero cryptographic signature verification is a downgrade
attack vector: anyone who can submit a /admin/self-update plan can swap the
update content for arbitrary code. This module provides a fail-closed Ed25519
verification path that the applier MUST consult before writing any change.

A missing public key or missing signature is treated as verification FAILURE
(fail-closed — no key → no apply). The operator MUST configure a public key,
pin it to a filesystem path or env var, and sign every update payload.
"""

from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def _public_key_bytes(raw: str) -> bytes:
    """Decode a public key from a hex or base64 string."""
    stripped = raw.strip()
    try:
        return bytes.fromhex(stripped)
    except ValueError:
        pass
    try:
        return base64.b64decode(stripped, validate=True)
    except binascii.Error:
        pass
    return base64.b64decode(stripped)


def verify_signature(content: str, signature: str, public_key: str) -> bool:
    """Verify an Ed25519 detached signature over ``content``.

    Args:
        content: The update payload exactly as it will be written.
        signature: Hex- or base64-encoded 64-byte Ed25519 signature.
        public_key: Hex- or base64-encoded 32-byte Ed25519 public key.

    Returns:
        ``True`` when the signature is valid; ``False`` on any failure
        (tampered content, wrong key, malformed inputs, missing inputs).

    Rules (fail-closed):
    * A missing or empty ``content``, ``signature``, or ``public_key`` → False.
    * An unparseable signature or public key → False.
    * A key that parses but is NOT 32 bytes → False.
    * A signature that parses but is NOT 64 bytes → False.
    """
    if not content or not signature or not public_key:
        return False
    try:
        sig_bytes = _public_key_bytes(signature)
        key_bytes = _public_key_bytes(public_key)
    except (ValueError, binascii.Error):
        return False
    if len(key_bytes) != 32 or len(sig_bytes) != 64:
        return False
    try:
        verify_key = Ed25519PublicKey.from_public_bytes(key_bytes)
        verify_key.verify(sig_bytes, content.encode("utf-8"))
        return True
    except (InvalidSignature, TypeError):
        return False


def _read_public_key_file(path: str) -> str:
    """Read one UTF-8 key file and release its descriptor before returning."""
    with open(path, encoding="utf-8") as key_file:
        return key_file.read().strip()


def load_public_key(key_path: str | None = None) -> str:
    """Load the Ed25519 public key from a file or environment variable.

    Resolution order:
    1. ``key_path`` if non-empty and the file exists.
    2. ``GLUDD_SELF_UPDATE_PUBLIC_KEY`` env var (inline hex/base64).
    3. ``GLUDD_SELF_UPDATE_PUBLIC_KEY_FILE`` env var → file.
    4. Returns empty string (fail-closed — no key → no verify pass).

    Returns the raw key string (hex or base64), or ``""`` when no key is found.
    """
    if key_path and os.path.isfile(key_path):
        return _read_public_key_file(key_path)

    inline = os.environ.get("GLUDD_SELF_UPDATE_PUBLIC_KEY", "")
    if inline:
        return inline.strip()

    file_path = os.environ.get("GLUDD_SELF_UPDATE_PUBLIC_KEY_FILE", "")
    if file_path and os.path.isfile(file_path):
        return _read_public_key_file(file_path)

    return ""
