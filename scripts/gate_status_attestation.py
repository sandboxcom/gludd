#!/usr/bin/env python3
"""Sign and verify a final gate result against the exact repository state."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import secrets
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_VERSION = "1"
_PREFIX = "attestation-"
_FIELDS = (
    "attestation-version",
    "attestation-state",
    "attestation-epoch",
    "attestation-status-digest",
    "attestation-signature",
)
_REQUIRED_PHASES = ("lint", "typecheck", "collect", "test", "smoke")
_DEFAULT_FRESHNESS_SECONDS = 1800
_DEFAULT_KEY_PATH = Path.home() / ".config" / "gludd" / "gate-attestation.key"


@dataclass(frozen=True)
class VerificationResult:
    """Machine-readable gate verification outcome."""

    ok: bool
    reason: str
    age_seconds: int | None = None


def _run_git(repo_root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def _git_blob_id(data: bytes, object_format: str) -> str:
    digest = hashlib.new(object_format)
    digest.update(f"blob {len(data)}\0".encode())
    digest.update(data)
    return digest.hexdigest()


def _index_entries(repo_root: Path) -> dict[str, tuple[str, str]]:
    entries: dict[str, tuple[str, str]] = {}
    raw = _run_git(repo_root, "ls-files", "--stage", "-z")
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_id, stage = metadata.decode("ascii").split()
        if stage != "0":
            raise RuntimeError("cannot attest an index with unresolved merge entries")
        path = os.fsdecode(raw_path)
        entries[path] = (mode, object_id)
    return entries


def _state_digest(
    head: str,
    entries: dict[str, tuple[str, str]],
    *,
    object_format: str,
) -> str:
    digest = hashlib.sha256()
    digest.update(b"gludd-gate-state-v1\0")
    digest.update(head.encode("ascii"))
    digest.update(b"\0")
    digest.update(object_format.encode("ascii"))
    digest.update(b"\0")
    for path in sorted(entries, key=os.fsencode):
        mode, object_id = entries[path]
        digest.update(os.fsencode(path))
        digest.update(b"\0")
        digest.update(mode.encode("ascii"))
        digest.update(b"\0")
        digest.update(object_id.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def repository_state_id(repo_root: Path, *, source: str = "worktree") -> str:
    """Hash HEAD plus the worktree or index tree in one comparable format."""
    root = Path(
        os.fsdecode(_run_git(repo_root, "rev-parse", "--show-toplevel")).strip()
    )
    head = _run_git(root, "rev-parse", "HEAD").decode("ascii").strip()
    object_format = (
        _run_git(root, "rev-parse", "--show-object-format").decode("ascii").strip()
    )
    index = _index_entries(root)
    if source == "index":
        return _state_digest(head, index, object_format=object_format)
    if source != "worktree":
        raise ValueError(f"unknown repository state source: {source}")

    raw_paths = _run_git(
        root,
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
    )
    entries: dict[str, tuple[str, str]] = {}
    for raw_path in raw_paths.split(b"\0"):
        if not raw_path:
            continue
        path = os.fsdecode(raw_path)
        full_path = root / path
        if not full_path.exists() and not full_path.is_symlink():
            continue
        indexed_mode = index.get(path, ("", ""))[0]
        if full_path.is_symlink():
            data = os.fsencode(os.readlink(full_path))
            mode = "120000"
        elif full_path.is_file():
            data = full_path.read_bytes()
            if indexed_mode in {"100644", "100755"}:
                mode = indexed_mode
            else:
                mode = "100755" if full_path.stat().st_mode & stat.S_IXUSR else "100644"
        elif indexed_mode == "160000":
            data = b""
            mode = indexed_mode
            object_id = _run_git(full_path, "rev-parse", "HEAD").decode("ascii").strip()
            entries[path] = (mode, object_id)
            continue
        else:
            raise RuntimeError(f"unsupported repository entry: {path}")
        entries[path] = (mode, _git_blob_id(data, object_format))
    return _state_digest(head, entries, object_format=object_format)


def _split_status(content: str) -> tuple[str, dict[str, str], str | None]:
    body: list[str] = []
    fields: dict[str, str] = {}
    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped.startswith(_PREFIX):
            body.append(line)
            continue
        key, separator, value = stripped.partition(" ")
        if key not in _FIELDS or not separator or not value:
            return "", {}, f"malformed attestation field: {stripped!r}"
        if key in fields:
            return "", {}, f"duplicate attestation field: {key}"
        fields[key] = value
    return "".join(body), fields, None


def _passed_body_error(body: str) -> str | None:
    lines = body.splitlines()
    if lines.count("=== GATE: PASSED ===") != 1 or "=== GATE: FAILED ===" in lines:
        return "gate status is not a uniquely completed passed gate"
    for phase in _REQUIRED_PHASES:
        if not any(line == f"{phase} PASS" or line.startswith(f"{phase} PASS ") for line in lines):
            return f"required gate phase is not passed: {phase}"
    return None


def _signature_message(
    *,
    state_id: str,
    epoch: int,
    status_digest: str,
) -> bytes:
    return "\0".join((_VERSION, state_id, str(epoch), status_digest)).encode()


def sign_status(
    status_path: Path,
    *,
    state_id: str,
    key: bytes,
    now: int | None = None,
) -> None:
    """Atomically append a replaceable attestation to a completed passing gate."""
    body, _old_fields, parse_error = _split_status(status_path.read_text(encoding="utf-8"))
    if parse_error:
        raise ValueError(parse_error)
    passed_error = _passed_body_error(body)
    if passed_error:
        raise ValueError(passed_error)
    epoch = int(time.time()) if now is None else now
    status_digest = hashlib.sha256(body.encode()).hexdigest()
    signature = hmac.new(
        key,
        _signature_message(
            state_id=state_id,
            epoch=epoch,
            status_digest=status_digest,
        ),
        hashlib.sha256,
    ).hexdigest()
    suffix = (
        f"attestation-version {_VERSION}\n"
        f"attestation-state {state_id}\n"
        f"attestation-epoch {epoch}\n"
        f"attestation-status-digest {status_digest}\n"
        f"attestation-signature {signature}\n"
    )
    temporary = status_path.with_name(f"{status_path.name}.attest-{os.getpid()}")
    temporary.write_text(body + suffix, encoding="utf-8")
    os.replace(temporary, status_path)


def verify_status(
    status_path: Path,
    *,
    state_id: str,
    key: bytes,
    now: int | None = None,
    freshness_seconds: int = _DEFAULT_FRESHNESS_SECONDS,
) -> VerificationResult:
    """Fail closed on missing, stale, replayed, or modified gate evidence."""
    if not status_path.exists():
        return VerificationResult(False, f"gate status is missing: {status_path}")
    body, fields, parse_error = _split_status(status_path.read_text(encoding="utf-8"))
    if parse_error:
        return VerificationResult(False, parse_error)
    missing = [field for field in _FIELDS if field not in fields]
    if missing:
        return VerificationResult(False, f"gate attestation is missing {', '.join(missing)}")
    if fields["attestation-version"] != _VERSION:
        return VerificationResult(False, "unsupported gate attestation version")
    actual_status_digest = hashlib.sha256(body.encode()).hexdigest()
    if not hmac.compare_digest(
        fields["attestation-status-digest"], actual_status_digest
    ):
        return VerificationResult(False, "gate status digest mismatch")
    if not hmac.compare_digest(fields["attestation-state"], state_id):
        return VerificationResult(False, "gate repository state does not match")
    try:
        epoch = int(fields["attestation-epoch"])
    except ValueError:
        return VerificationResult(False, "gate attestation epoch is not an integer")
    expected_signature = hmac.new(
        key,
        _signature_message(
            state_id=state_id,
            epoch=epoch,
            status_digest=actual_status_digest,
        ),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(
        fields["attestation-signature"], expected_signature
    ):
        return VerificationResult(False, "gate attestation signature mismatch")
    passed_error = _passed_body_error(body)
    if passed_error:
        return VerificationResult(False, passed_error)
    current_time = int(time.time()) if now is None else now
    age = current_time - epoch
    if age < -60:
        return VerificationResult(False, "gate attestation timestamp is in the future", age)
    if age > freshness_seconds:
        return VerificationResult(False, "gate attestation is stale", age)
    return VerificationResult(True, "gate attestation is valid", age)


def _read_key(path: Path, *, create: bool) -> bytes:
    if create and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            try:
                os.write(descriptor, secrets.token_hex(32).encode() + b"\n")
            finally:
                os.close(descriptor)
    if not path.exists():
        raise FileNotFoundError(f"gate attestation key is missing: {path}")
    key = bytes.fromhex(path.read_text(encoding="ascii").strip())
    if len(key) != 32:
        raise ValueError("gate attestation key must contain 32 bytes")
    return key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("sign", "verify"))
    parser.add_argument("status", nargs="?", default=".gate-status")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument(
        "--key-path",
        default=os.environ.get("GLUDD_GATE_KEY_PATH", str(_DEFAULT_KEY_PATH)),
    )
    parser.add_argument(
        "--freshness-seconds",
        type=int,
        default=int(
            os.environ.get(
                "GLUDD_GATE_FRESHNESS_SECS",
                str(_DEFAULT_FRESHNESS_SECONDS),
            )
        ),
    )
    args = parser.parse_args(argv)
    try:
        root = Path(args.repo_root).resolve()
        status = Path(args.status)
        key = _read_key(Path(args.key_path), create=args.action == "sign")
        worktree_state = repository_state_id(root, source="worktree")
        if args.action == "sign":
            sign_status(status, state_id=worktree_state, key=key)
            print(f"gate attestation signed state={worktree_state[:12]}")
            return 0
        result = verify_status(
            status,
            state_id=worktree_state,
            key=key,
            freshness_seconds=args.freshness_seconds,
        )
        if not result.ok:
            print(f"gate attestation rejected: {result.reason}", file=sys.stderr)
            return 1
        index_state = repository_state_id(root, source="index")
        if not hmac.compare_digest(worktree_state, index_state):
            print(
                "gate attestation rejected: staged index does not match tested worktree",
                file=sys.stderr,
            )
            return 1
        print(
            f"gate attestation valid state={worktree_state[:12]} "
            f"age={result.age_seconds}s"
        )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"gate attestation error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
