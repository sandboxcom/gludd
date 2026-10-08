#!/usr/bin/env python3
"""Read and materialize Git snapshots without consulting unstaged files."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

GIT_TIMEOUT_SECONDS = 30
GITLINK_MODE = "160000"


class SnapshotError(RuntimeError):
    """Raised when Git cannot prove or materialize an exact snapshot."""


@dataclass(frozen=True, slots=True)
class IndexBlob:
    """One stage-zero Git index entry and its exact blob bytes."""

    mode: str
    data: bytes


def _safe_path(raw: str, *, source: str) -> str:
    candidate = PurePosixPath(raw)
    if (
        not raw
        or candidate.is_absolute()
        or ".." in candidate.parts
        or str(candidate) != raw
    ):
        raise SnapshotError(f"{source} is not a normalized repository path: {raw!r}")
    return raw


def _git(
    root: Path,
    arguments: Sequence[str],
    *,
    input_data: bytes | None = None,
    index_file: Path | None = None,
) -> bytes:
    environment = os.environ.copy()
    if index_file is not None:
        environment["GIT_INDEX_FILE"] = str(index_file)
    command = ["git", "-C", str(root), *arguments]
    try:
        result = subprocess.run(
            command,
            input=input_data,
            capture_output=True,
            check=False,
            env=environment,
            shell=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise SnapshotError(
            f"Git command timed out after {GIT_TIMEOUT_SECONDS}s: {arguments[0]}"
        ) from exc
    except OSError as exc:
        raise SnapshotError(f"Git command failed to start: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()[:500]
        raise SnapshotError(
            f"Git {arguments[0]} exited {result.returncode}: {detail or 'no stderr'}"
        )
    return result.stdout


def _nul_paths(
    data: bytes,
    *,
    source: str,
    require_sorted: bool = False,
) -> tuple[str, ...]:
    if data and not data.endswith(b"\x00"):
        raise SnapshotError(f"{source} returned unterminated NUL-delimited output")
    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SnapshotError(f"{source} returned a non-UTF-8 path") from exc
    paths = tuple(
        _safe_path(item, source=source) for item in decoded.split("\x00") if item
    )
    if len(paths) != len(set(paths)):
        raise SnapshotError(f"{source} returned duplicate paths")
    if require_sorted and paths != tuple(sorted(paths)):
        raise SnapshotError(f"{source} is not deterministically sorted")
    return tuple(sorted(paths))


def _resolve_commit(root: Path, ref: str) -> str:
    if not ref or "\x00" in ref:
        raise SnapshotError("Git ref must be a non-empty string without NUL bytes")
    output = _git(
        root,
        ["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
    )
    try:
        resolved = output.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise SnapshotError(f"Git ref {ref!r} resolved to non-ASCII output") from exc
    if len(resolved) != 40 or any(character not in "0123456789abcdef" for character in resolved):
        raise SnapshotError(f"Git ref {ref!r} did not resolve to one full commit ID")
    return resolved


def discover_staged_paths(root: Path) -> tuple[str, ...]:
    """Return changed stage-zero paths from the index, excluding deletions."""

    output = _git(
        root,
        ["diff", "--cached", "--name-only", "--diff-filter=ACMRT", "-z", "--"],
    )
    return _nul_paths(output, source="Git staged-path inventory")


def discover_tracked_paths(root: Path) -> tuple[str, ...]:
    """Return Git's complete, sorted, NUL-delimited tracked inventory."""

    output = _git(root, ["ls-files", "-z"])
    return _nul_paths(
        output,
        source="Git tracked-path inventory",
        require_sorted=True,
    )


def discover_changed_paths(root: Path, base_ref: str, current_ref: str) -> tuple[str, ...]:
    """Return paths changed between a merge base and an exact current commit."""

    base = _resolve_commit(root, base_ref)
    current = _resolve_commit(root, current_ref)
    output = _git(
        root,
        ["diff", "--name-only", "--diff-filter=ACMRT", "-z", f"{base}...{current}", "--"],
    )
    return _nul_paths(output, source="Git committed-path inventory")


def read_index_blob(root: Path, relative_path: str) -> IndexBlob:
    """Read one path exactly as staged, including its stage-zero mode."""

    path = _safe_path(relative_path, source="Git index path")
    raw_entry = _git(root, ["ls-files", "--stage", "-z", "--", path])
    if not raw_entry or not raw_entry.endswith(b"\x00"):
        raise SnapshotError(f"Git index has no stage-zero entry for {path}")
    records = tuple(item for item in raw_entry[:-1].split(b"\x00") if item)
    if len(records) != 1:
        raise SnapshotError(f"Git index returned {len(records)} entries for {path}")
    try:
        metadata, actual_raw_path = records[0].split(b"\t", 1)
        mode_raw, object_id_raw, stage_raw = metadata.split(b" ", 2)
        actual_path = actual_raw_path.decode("utf-8")
        mode = mode_raw.decode("ascii")
        object_id = object_id_raw.decode("ascii")
        stage = stage_raw.decode("ascii")
    except (UnicodeDecodeError, ValueError) as exc:
        raise SnapshotError(f"Git index returned a malformed entry for {path}") from exc
    if actual_path != path or stage != "0":
        raise SnapshotError(f"Git index did not return one stage-zero entry for {path}")
    if mode == GITLINK_MODE:
        return IndexBlob(mode, b"")
    data = _git(root, ["cat-file", "blob", object_id])
    return IndexBlob(mode, data)


def read_ref_blob(root: Path, ref: str, relative_path: str) -> bytes:
    """Read one tracked blob from an exact commit reference."""

    commit = _resolve_commit(root, ref)
    path = _safe_path(relative_path, source="Git ref path")
    return _git(root, ["show", f"{commit}:{path}"])


def _index_paths(
    root: Path,
    prefixes: Sequence[str],
    *,
    index_file: Path | None,
) -> tuple[str, ...]:
    safe_prefixes = tuple(_safe_path(path, source="snapshot prefix") for path in prefixes)
    if not safe_prefixes:
        raise SnapshotError("snapshot needs at least one production path")
    output = _git(
        root,
        ["ls-files", "-z", "--", *safe_prefixes],
        index_file=index_file,
    )
    return _nul_paths(output, source="Git snapshot inventory")


def _checkout_index(
    root: Path,
    destination: Path,
    prefixes: Sequence[str],
    *,
    index_file: Path | None,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    paths = _index_paths(root, prefixes, index_file=index_file)
    if not paths:
        return
    payload = b"\x00".join(path.encode("utf-8") for path in paths) + b"\x00"
    _git(
        root,
        [
            "checkout-index",
            "--force",
            "--stdin",
            "-z",
            f"--prefix={destination.resolve()}{os.sep}",
        ],
        input_data=payload,
        index_file=index_file,
    )


def materialize_index(root: Path, destination: Path, prefixes: Sequence[str]) -> None:
    """Materialize selected production roots from the current Git index."""

    _checkout_index(root, destination, prefixes, index_file=None)


def materialize_ref(
    root: Path,
    ref: str,
    destination: Path,
    prefixes: Sequence[str],
) -> None:
    """Materialize selected production roots from one exact commit."""

    commit = _resolve_commit(root, ref)
    index_file = destination.parent / f".{destination.name}.git-index"
    if index_file.exists():
        raise SnapshotError(f"temporary Git index already exists: {index_file}")
    try:
        _git(root, ["read-tree", commit], index_file=index_file)
        _checkout_index(root, destination, prefixes, index_file=index_file)
    finally:
        index_file.unlink(missing_ok=True)
        index_file.with_name(f"{index_file.name}.lock").unlink(missing_ok=True)
