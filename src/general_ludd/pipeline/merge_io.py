"""Bounded filesystem and Git plumbing for pipeline three-way merges."""

from __future__ import annotations

import logging
import os
import stat
import subprocess
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import PurePosixPath
from re import Pattern

logger = logging.getLogger(__name__)

GitResult = subprocess.CompletedProcess[bytes] | None
GitRunner = Callable[..., GitResult]
GitErrorLogger = Callable[[str, str, GitResult], None]


@dataclass(frozen=True)
class ContentRead:
    """Result of one identity-checked bounded file read."""

    status: str
    data: bytes | None = None


def read_bounded(path: str, *, max_file_bytes: int) -> ContentRead:
    """Read one regular file without following its final symlink."""
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return ContentRead("absent")
    except OSError:
        return ContentRead("error")
    if not stat.S_ISREG(before.st_mode):
        return ContentRead("error")
    if before.st_size > max_file_bytes:
        return ContentRead("too_large")
    try:
        with open(path, "rb") as file_handle:
            opened = os.fstat(file_handle.fileno())
            data = file_handle.read(max_file_bytes + 1)
        after = os.lstat(path)
    except OSError:
        return ContentRead("error")
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_opened = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_opened or identity_opened != identity_after:
        return ContentRead("changed")
    if len(data) > max_file_bytes:
        return ContentRead("too_large")
    return ContentRead("ok", data)


def bounded_diagnostic(value: bytes | str | None, *, max_bytes: int) -> str:
    """Decode a bounded, NUL-free diagnostic for safe logging."""
    if value is None:
        return ""
    raw = value.encode("utf-8", "replace") if isinstance(value, str) else value
    return raw[:max_bytes].decode("utf-8", "replace").replace("\x00", "?").strip()


def run_git(
    repo_path: str,
    *args: str,
    timeout_seconds: int,
) -> GitResult:
    """Run one bounded Git plumbing command without a shell."""
    try:
        return subprocess.run(
            ["git", "-C", repo_path, *args],
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning(
            "pipeline merge: git %s failed: %s",
            args[0] if args else "command",
            type(exc).__name__,
        )
        return None


def log_git_error(
    operation: str,
    relpath: str,
    result: GitResult,
    *,
    max_diagnostic_bytes: int,
) -> None:
    """Log bounded Git failure context without repository content."""
    returncode = "exception" if result is None else str(result.returncode)
    diagnostic = (
        ""
        if result is None
        else bounded_diagnostic(result.stderr, max_bytes=max_diagnostic_bytes)
    )
    logger.warning(
        "pipeline merge: %s failed for %s (rc=%s, diagnostic=%s)",
        operation,
        relpath,
        returncode,
        diagnostic or "unavailable",
    )


def read_fork_point(
    repo_path: str,
    base_sha: str,
    relpath: str,
    *,
    full_object_id: Pattern[str],
    max_file_bytes: int,
    run_git_fn: GitRunner,
    log_git_error_fn: GitErrorLogger,
) -> ContentRead:
    """Read one bounded blob from the exact fork-point tree."""
    if full_object_id.fullmatch(base_sha) is None:
        return ContentRead("invalid_base")
    listed = run_git_fn(
        repo_path,
        "ls-tree",
        "-z",
        "--full-tree",
        base_sha,
        "--",
        f":(literal){relpath}",
    )
    if listed is None or listed.returncode != 0:
        log_git_error_fn("ls-tree", relpath, listed)
        return ContentRead("tool_error")
    if len(listed.stdout) > len(os.fsencode(relpath)) + 256:
        return ContentRead("tool_error")
    entries = [entry for entry in listed.stdout.split(b"\x00") if entry]
    if not entries:
        return ContentRead("absent")
    if len(entries) != 1:
        return ContentRead("tool_error")
    try:
        header, raw_path = entries[0].split(b"\t", 1)
        _mode, object_type, object_id = header.split(b" ", 2)
    except ValueError:
        return ContentRead("tool_error")
    if raw_path != os.fsencode(relpath) or object_type != b"blob":
        return ContentRead("tool_error")
    object_name = object_id.decode("ascii", "strict")
    sized = run_git_fn(repo_path, "cat-file", "-s", object_name)
    if sized is None or sized.returncode != 0:
        log_git_error_fn("cat-file-size", relpath, sized)
        return ContentRead("tool_error")
    try:
        size = int(sized.stdout.strip())
    except ValueError:
        return ContentRead("tool_error")
    if size < 0:
        return ContentRead("tool_error")
    if size > max_file_bytes:
        return ContentRead("too_large")
    content = run_git_fn(repo_path, "cat-file", "blob", object_name)
    if content is None or content.returncode != 0:
        log_git_error_fn("cat-file-blob", relpath, content)
        return ContentRead("tool_error")
    if len(content.stdout) != size or len(content.stdout) > max_file_bytes:
        return ContentRead("tool_error")
    return ContentRead("ok", content.stdout)


def safe_path(root: str, relpath: str) -> str | None:
    """Resolve a canonical repo-relative path without metadata/traversal escape."""
    if not relpath or "\x00" in relpath or "\\" in relpath:
        return None
    pure = PurePosixPath(relpath)
    if pure.is_absolute() or pure.parts[0] == ".git" or ".." in pure.parts:
        return None
    canonical = pure.as_posix()
    if canonical != relpath or canonical == ".":
        return None
    root_real = os.path.realpath(root)
    candidate = os.path.join(root_real, *pure.parts)
    parent_real = os.path.realpath(os.path.dirname(candidate))
    try:
        if os.path.commonpath((root_real, parent_real)) != root_real:
            return None
    except ValueError:
        return None
    return candidate


@dataclass(frozen=True)
class FileMerge:
    """Result of one bounded three-way file merge."""

    status: str
    data: bytes | None = None


def merge_file_with_git(
    repo_path: str,
    relpath: str,
    ours: bytes,
    base: bytes,
    theirs: bytes,
    temp_root: str,
    *,
    max_file_bytes: int,
    run_git_fn: GitRunner,
    log_git_error_fn: GitErrorLogger,
) -> FileMerge:
    """Run one sequential, non-mutating ``git merge-file --stdout`` call."""
    paths = [os.path.join(temp_root, name) for name in ("ours", "base", "theirs")]
    try:
        for path, content in zip(paths, (ours, base, theirs), strict=True):
            with open(path, "wb") as file_handle:
                file_handle.write(content)
        result = run_git_fn(
            repo_path,
            "merge-file",
            "--stdout",
            "--",
            paths[0],
            paths[1],
            paths[2],
        )
    except OSError:
        return FileMerge("tool_error")
    if result is None:
        return FileMerge("tool_error")
    if result.returncode == 0:
        if len(result.stdout) > max_file_bytes:
            return FileMerge("too_large")
        return FileMerge("ok", result.stdout)
    if 1 <= result.returncode <= 127:
        return FileMerge("conflict")
    log_git_error_fn("merge-file", relpath, result)
    return FileMerge("tool_error")


def atomic_replace(path: str, content: bytes) -> None:
    """Replace one admitted file from a same-directory temporary file."""
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".gludd-merge-", dir=parent)
    try:
        with os.fdopen(descriptor, "wb") as file_handle:
            file_handle.write(content)
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(temporary, path)
        temporary = ""
    finally:
        if temporary:
            with suppress(OSError):
                os.unlink(temporary)


def commit_with_rollback(
    merged: dict[str, bytes],
    originals: dict[str, bytes | None],
    *,
    atomic_replace_fn: Callable[[str, bytes], None],
) -> bool:
    """Apply admitted files and restore prior content on a write failure."""
    applied: list[str] = []
    try:
        for path, content in merged.items():
            atomic_replace_fn(path, content)
            applied.append(path)
    except OSError as exc:
        logger.warning("pipeline merge: repository write failed: %s", type(exc).__name__)
        for path in reversed(applied):
            original = originals[path]
            try:
                if original is None:
                    os.unlink(path)
                else:
                    atomic_replace_fn(path, original)
            except OSError as rollback_exc:
                logger.error(
                    "pipeline merge: rollback failed: %s",
                    type(rollback_exc).__name__,
                )
        return False
    return True
