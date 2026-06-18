#!/usr/bin/env python3
"""verify_gate_status.py — HMAC-SHA256 verifier for .gate-status provenance lines.

Reads .gate-status (or a file given as argv[1]), recomputes the HMAC with the
key stored at ~/.config/gludd/gate.key, and asserts:

  1. SIG line present (not a hand-written / forged file).
  2. HMAC matches (not tampered / fabricated).
  3. TREE matches current git HEAD^{tree} (not replayed from a different commit).
  4. EPOCH within the freshness window (default 1800s; override via
     GLUDD_GATE_FRESHNESS_SECS env var).

Exit 0  — all checks pass (valid, fresh, matching-tree gate status).
Exit 1  — verification failed (reason printed to stderr).
Exit 2  — usage / IO error.

Key path: ~/.config/gludd/gate.key (created 0600 with 32 random bytes, base64).
  Threat-model note: this key lives on the LOCAL filesystem, so any process
  running as the same user can read it — it does NOT provide security against a
  compromised process on the same UID.  The goal is to make it EXPENSIVE for a
  sandboxed subagent that cannot reach ~/.config (e.g. a worktree jail without
  $HOME access) to forge a gate-status, and to provide an audit trail.
  Keychain upgrade: store the key in macOS Keychain (security add-generic-password)
  or 1Password CLI (op read) and replace _read_key() with a subprocess call.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants / configuration
# ---------------------------------------------------------------------------

DEFAULT_KEY_PATH = Path.home() / ".config" / "gludd" / "gate.key"
DEFAULT_FRESHNESS_SECS = 1800  # 30 minutes

# Public entry-point used by tests to call signing without shelling out.
SIGN_FIELDS_SEP = "|"


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------


def _key_path() -> Path:
    """Return the gate key path (env override supported for tests)."""
    return Path(os.environ.get("GLUDD_GATE_KEY_PATH", str(DEFAULT_KEY_PATH)))


def _read_key(key_path: Path | None = None) -> bytes:
    """Read the raw HMAC key bytes (base64-decoded) from the key file.

    If the key file is missing, create it with secure random bytes (0600).
    """
    p = key_path or _key_path()
    if not p.exists():
        _create_key(p)
    raw = p.read_text().strip()
    import base64
    return base64.b64decode(raw)


def _create_key(p: Path) -> None:
    """Create the key file with 32 cryptographically random bytes, base64-encoded."""
    import base64
    import secrets

    p.parent.mkdir(parents=True, exist_ok=True)
    key_bytes = secrets.token_bytes(32)
    key_b64 = base64.b64encode(key_bytes).decode()
    # Write with O_CREAT | O_EXCL to avoid races, then chmod 0600.
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, (key_b64 + "\n").encode())
    finally:
        os.close(fd)
    # Ensure mode is 0600 even if umask is permissive.
    p.chmod(0o600)


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def compute_sig(tree_sha: str, epoch: int | str, result: str, key: bytes | None = None) -> str:
    """Return the HMAC-SHA256 hex digest for the provenance tuple.

    Message format: "<tree_sha>|<epoch>|<result>"
    """
    k = key if key is not None else _read_key()
    message = f"{tree_sha}{SIGN_FIELDS_SEP}{epoch}{SIGN_FIELDS_SEP}{result}".encode()
    return hmac.new(k, message, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# git helpers
# ---------------------------------------------------------------------------


def _git_tree_sha(repo_root: str | None = None) -> str:
    """Return the current HEAD^{tree} SHA (detects wrong-tree replays)."""
    cwd = repo_root or os.getcwd()
    result = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git rev-parse HEAD^{{tree}} failed: {result.stderr.strip()}")
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_status_file(path: Path) -> dict[str, str]:
    """Parse key-value lines from a gate-status file.

    Lines without a space-delimited key are collected under '' (free-text).
    We extract lines of the form:
      TREE <sha>
      EPOCH <n>
      SIG <hex>
      RESULT <word>
    """
    fields: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            fields[parts[0]] = parts[1]
    return fields


# ---------------------------------------------------------------------------
# Verification entry point
# ---------------------------------------------------------------------------


def verify(
    status_file: Path,
    freshness_secs: int | None = None,
    repo_root: str | None = None,
    key: bytes | None = None,
) -> tuple[bool, str]:
    """Verify a .gate-status file.

    Returns (ok: bool, reason: str).  ok=True means all checks passed.
    """
    window = freshness_secs if freshness_secs is not None else int(
        os.environ.get("GLUDD_GATE_FRESHNESS_SECS", str(DEFAULT_FRESHNESS_SECS))
    )

    if not status_file.exists():
        return False, f"gate-status file not found: {status_file}"

    fields = _parse_status_file(status_file)

    # 1. SIG must be present.
    sig = fields.get("SIG")
    if not sig:
        return False, "forged or stale gate status: SIG line missing (hand-written file?)"

    # 2. Required provenance fields.
    tree_sha = fields.get("TREE")
    epoch_str = fields.get("EPOCH")
    result = fields.get("RESULT")
    if not tree_sha:
        return False, "forged or stale gate status: TREE line missing"
    if not epoch_str:
        return False, "forged or stale gate status: EPOCH line missing"
    if not result:
        return False, "forged or stale gate status: RESULT line missing"

    try:
        epoch = int(epoch_str)
    except ValueError:
        return False, f"forged or stale gate status: EPOCH is not an integer: {epoch_str!r}"

    # 3. Validate HMAC.
    expected_sig = compute_sig(tree_sha, epoch, result, key=key)
    if not hmac.compare_digest(sig, expected_sig):
        return False, "forged or stale gate status: HMAC mismatch (bad-HMAC = fabricated)"

    # 4. Tree SHA must match current HEAD^{tree}.
    try:
        current_tree = _git_tree_sha(repo_root)
    except RuntimeError as exc:
        return False, f"cannot determine current tree SHA: {exc}"

    if tree_sha != current_tree:
        return False, (
            f"forged or stale gate status: wrong-tree "
            f"(status tree={tree_sha[:12]}..., HEAD tree={current_tree[:12]}...)"
        )

    # 5. Freshness: EPOCH must be within the allowed window.
    import time
    now = int(time.time())
    age = now - epoch
    if age > window:
        return False, (
            f"forged or stale gate status: stale "
            f"(age={age}s > window={window}s — run 'make gate' again)"
        )
    if age < -60:
        # Timestamp in the far future — clock skew or manipulation.
        return False, (
            f"forged or stale gate status: EPOCH is in the future "
            f"(now={now}, epoch={epoch})"
        )

    return True, f"OK tree={tree_sha[:12]} epoch={epoch} age={age}s result={result}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    status_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".gate-status")

    try:
        ok, reason = verify(status_path)
    except Exception as exc:  # noqa: BLE001
        print(f"verify_gate_status: error: {exc}", file=sys.stderr)
        return 2

    if ok:
        print(f"verify_gate_status: {reason}")
        return 0
    else:
        print(f"verify_gate_status: REJECTED — {reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
