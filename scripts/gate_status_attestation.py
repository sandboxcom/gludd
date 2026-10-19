#!/usr/bin/env python3
"""Sign and verify a final gate result against the exact repository state."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import secrets
import stat
import subprocess as subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

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
_BOOKKEEPING_PATHS = frozenset({"TASKS.md", "BUGS.md", "SESSION.md"})
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


def repository_state_id(
    repo_root: Path,
    *,
    source: str = "worktree",
    include_head: bool = True,
    exclude_bookkeeping: bool = False,
) -> str:
    """Hash repository content with HEAD and complete-content gate defaults.

    Non-authoritative execution admission may explicitly omit commit identity
    and the fixed bookkeeping files. Gate callers retain the strict defaults.
    """
    # Refresh all three metadata facts in one native read for this identity only.
    try:
        raw_root, raw_format, raw_head, tail = _run_git(
            repo_root, "rev-parse", "--show-toplevel", "--show-object-format", "HEAD",
        ).rsplit(b"\n", 3)
        root = Path(os.fsdecode(raw_root).strip())
        object_format = raw_format.decode("ascii").strip()
        head = raw_head.decode("ascii").strip()
        if (tail or not root.is_absolute() or object_format not in {"sha1", "sha256"}
                or len(head) != {"sha1": 40, "sha256": 64}[object_format]
                or any(character not in "0123456789abcdef" for character in head)):
            raise ValueError("invalid native metadata")
    except ValueError:
        raise RuntimeError("malformed Git repository identity") from None
    index = _index_entries(root)
    if not include_head:
        head = ""
    if exclude_bookkeeping:
        index = {path: entry for path, entry in index.items() if path not in _BOOKKEEPING_PATHS}
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
        if exclude_bookkeeping and path in _BOOKKEEPING_PATHS:
            continue
        full_path = root / path
        indexed_mode, indexed_object_id = index.get(path, ("", ""))
        if indexed_mode == "160000":
            submodule_marker = full_path / ".git"
            object_id = indexed_object_id
            if full_path.is_dir() and submodule_marker.exists():
                object_id = (
                    _run_git(full_path, "rev-parse", "HEAD").decode("ascii").strip()
                )
            entries[path] = (indexed_mode, object_id)
            continue
        if not full_path.exists() and not full_path.is_symlink():
            continue
        if full_path.is_symlink():
            data = os.fsencode(os.readlink(full_path))
            mode = "120000"
        elif full_path.is_file():
            data = full_path.read_bytes()
            if indexed_mode in {"100644", "100755"}:
                mode = indexed_mode
            else:
                mode = "100755" if full_path.stat().st_mode & stat.S_IXUSR else "100644"
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


def _terminal_body_error(body: str) -> str | None:
    """Require exactly one terminal marker without treating failure as green."""
    lines = body.splitlines()
    passed = lines.count("=== GATE: PASSED ===")
    failed = lines.count("=== GATE: FAILED ===")
    if passed + failed != 1:
        return "gate status is not uniquely terminal"
    if failed and not any(
        " FAIL" in line for line in lines if line != "=== GATE: FAILED ==="
    ):
        return "failed gate status has no failed phase"
    return None


def _signature_message(
    *,
    state_id: str,
    epoch: int,
    status_digest: str,
) -> bytes:
    return "\0".join((_VERSION, state_id, str(epoch), status_digest)).encode()


def _sign_status(
    status_path: Path,
    *,
    state_id: str,
    key: bytes,
    now: int | None = None,
    require_pass: bool,
) -> None:
    """Atomically append a replaceable attestation to validated terminal evidence."""
    body, _old_fields, parse_error = _split_status(status_path.read_text(encoding="utf-8"))
    if parse_error:
        raise ValueError(parse_error)
    body_error = _passed_body_error(body) if require_pass else _terminal_body_error(body)
    if body_error:
        raise ValueError(body_error)
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


class _StatusSigner(Protocol):
    def __call__(
        self,
        status_path: Path,
        *,
        state_id: str,
        key: bytes,
        now: int | None = None,
    ) -> None: ...


def _status_signer(*, require_pass: bool) -> _StatusSigner:
    """Bind one admission policy without duplicating the public call contract."""

    def signer(
        status_path: Path,
        *,
        state_id: str,
        key: bytes,
        now: int | None = None,
    ) -> None:
        _sign_status(
            status_path,
            state_id=state_id,
            key=key,
            now=now,
            require_pass=require_pass,
        )

    return signer


sign_status = _status_signer(require_pass=True)
sign_terminal_status = _status_signer(require_pass=False)


def _verify_status(
    status_path: Path,
    *,
    state_id: str,
    key: bytes,
    now: int | None = None,
    freshness_seconds: int = _DEFAULT_FRESHNESS_SECONDS,
    require_pass: bool,
) -> VerificationResult:
    """Authenticate one terminal status under the requested admission policy."""
    if not status_path.exists():
        return VerificationResult(False, f"gate status is missing: {status_path}")
    return _verify_status_content(
        status_path.read_text(encoding="utf-8"), state_id=state_id, key=key, now=now,
        freshness_seconds=freshness_seconds, require_pass=require_pass,
    )


def _verify_status_content(
    content: str, *, state_id: str, key: bytes, now: int | None,
    freshness_seconds: int, require_pass: bool,
) -> VerificationResult:
    """Apply the native signature policy to exactly one already captured status."""
    body, fields, parse_error = _split_status(content)
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
    body_error = _passed_body_error(body) if require_pass else _terminal_body_error(body)
    if body_error:
        return VerificationResult(False, body_error)
    current_time = int(time.time()) if now is None else now
    age = current_time - epoch
    if age < -60:
        return VerificationResult(False, "gate attestation timestamp is in the future", age)
    if age > freshness_seconds:
        return VerificationResult(False, "gate attestation is stale", age)
    return VerificationResult(True, "gate attestation is valid", age)


def verify_status(
    status_path: Path,
    *,
    state_id: str,
    key: bytes,
    now: int | None = None,
    freshness_seconds: int = _DEFAULT_FRESHNESS_SECONDS,
) -> VerificationResult:
    """Fail closed on missing, stale, replayed, modified, or failed evidence."""
    return _verify_status(
        status_path,
        state_id=state_id,
        key=key,
        now=now,
        freshness_seconds=freshness_seconds,
        require_pass=True,
    )


def verify_terminal_status(
    status_path: Path,
    *,
    state_id: str,
    key: bytes,
    now: int | None = None,
    freshness_seconds: int = _DEFAULT_FRESHNESS_SECONDS,
) -> VerificationResult:
    """Authenticate a unique final pass/fail status without admitting failure."""
    return _verify_status(
        status_path,
        state_id=state_id,
        key=key,
        now=now,
        freshness_seconds=freshness_seconds,
        require_pass=False,
    )



def _captured_resume_status(path: Path, *, directory_fd: int | None = None) -> bytes:
    """Read one owned regular status snapshot without following its final entry."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory_fd)
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_nlink != 1 or not 0 < before.st_size <= 128 * 1024):
            raise ValueError("resume status must be a bounded owned regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            encoded = stream.read(128 * 1024 + 1)
        fingerprint = (before.st_dev, before.st_ino, before.st_mode, before.st_uid,
                       before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_nlink)
        observations = (os.fstat(descriptor), os.stat(path, dir_fd=directory_fd, follow_symlinks=False))
        if len(encoded) != before.st_size or any(
            (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_size,
             value.st_mtime_ns, value.st_ctime_ns, value.st_nlink) != fingerprint
            for value in observations
        ):
            raise ValueError("resume status changed while capturing its bytes")
        return encoded
    finally:
        os.close(descriptor)


def archive_prior_status(status_path: Path, archive_root: Path) -> Path | None:
    """Retain exact prior bytes by content identity, never under a new candidate SHA."""
    try:
        encoded = _captured_resume_status(status_path)
    except FileNotFoundError:
        return None
    archive_root.mkdir(parents=True, exist_ok=True)
    directory = os.open(archive_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        name = f"gate-resume-prior-{hashlib.sha256(encoded).hexdigest()}.status"
        try:
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
        except FileExistsError:
            if _captured_resume_status(Path(name), directory_fd=directory) != encoded:
                raise ValueError("prior status archive is occupied by different bytes") from None
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        pinned = os.fstat(directory)
        current = archive_root.lstat()
        if (pinned.st_dev, pinned.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError("prior status archive directory identity changed")
        return archive_root / name
    finally:
        os.close(directory)


def verify_resume_status(
    status_path: Path, *, state_id: str, key: bytes, profile: str,
    max_files_per_batch: int, workers: int, now: int | None = None,
    freshness_seconds: int = _DEFAULT_FRESHNESS_SECONDS,
) -> VerificationResult:
    """Permit only fresh authenticated current-state policy/preflight reuse."""
    try:
        content = _captured_resume_status(status_path).decode("utf-8")
    except (OSError, ValueError) as exc:
        return VerificationResult(False, f"resume status unavailable: {exc}")
    result = _verify_status_content(
        content, state_id=state_id, key=key, now=now,
        freshness_seconds=freshness_seconds, require_pass=False,
    )
    if not result.ok:
        return result
    body, _fields, _error = _split_status(content)
    lines = body.splitlines()
    policy = f"gate-policy profile={profile} max_files_per_batch={max_files_per_batch} workers={workers}"
    if [line for line in lines if line.startswith("gate-policy ")] != [policy]:
        return VerificationResult(False, "resume gate policy is missing, duplicate or changed")
    preflights = [line for line in lines if line.startswith("preflights ")]
    if (len(preflights) != 1 or len(preflights[0].split()) != 3
            or preflights[0].split()[1] != "PASS"
            or not preflights[0].split()[2].isascii()
            or not preflights[0].split()[2].isdigit()):
        return VerificationResult(False, "resume preflights are not uniquely passed")
    if "=== GATE: FAILED ===" not in lines:
        return VerificationResult(False, "resume requires an authenticated failed gate")
    return VerificationResult(True, "authenticated current preflights and policy", result.age_seconds)


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
    """Sign or verify canonical status with strict default source identity."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("sign", "sign-terminal", "verify", "verify-terminal", "verify-resume", "archive-prior"),
    )
    parser.add_argument("status", nargs="?", default=".gate-status")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--profile", choices=("native", "release"), default="native")
    parser.add_argument("--max-files-per-batch", type=int, default=32)
    parser.add_argument("--workers", type=int, default=1)
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
        if args.action == "archive-prior":
            archived = archive_prior_status(status, root / ".gate-logs")
            if archived is not None:
                print(archived)
            return 0
        key = _read_key(
            Path(args.key_path),
            create=args.action in {"sign", "sign-terminal"},
        )
        worktree_state = repository_state_id(root, source="worktree")
        if args.action == "sign":
            sign_status(status, state_id=worktree_state, key=key)
            print(f"gate attestation signed state={worktree_state[:12]}")
            return 0
        if args.action == "sign-terminal":
            sign_terminal_status(status, state_id=worktree_state, key=key)
            print(f"terminal gate attestation signed state={worktree_state[:12]}")
            return 0
        if args.action == "verify-resume":
            result = verify_resume_status(
                status, state_id=worktree_state, key=key, profile=args.profile,
                max_files_per_batch=args.max_files_per_batch, workers=args.workers,
                freshness_seconds=args.freshness_seconds,
            )
        else:
            verifier = verify_terminal_status if args.action == "verify-terminal" else verify_status
            result = verifier(
                status, state_id=worktree_state, key=key,
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
