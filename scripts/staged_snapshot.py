#!/usr/bin/env python3
"""Read and materialize Git snapshots without consulting unstaged files."""

from __future__ import annotations

import math
import os
import selectors
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

GIT_TIMEOUT_SECONDS = 30
GITLINK_MODE = "160000"


class SnapshotError(RuntimeError):
    """Raised when Git cannot prove or materialize an exact snapshot."""


class SnapshotLimitError(SnapshotError):
    """Raised when an opt-in Git transport exhausts output or time capacity."""


def _bounded_git_result(
    command: list[str], environment: dict[str, str], input_data: bytes | None,
    max_output_bytes: int, timeout_seconds: float,
    on_output_bytes: Callable[[int], None] | None,
) -> subprocess.CompletedProcess[bytes]:
    """Bound both native Git pipes before accumulating output and always reap it."""
    deadline = time.monotonic() + timeout_seconds
    output = bytearray()
    errors = bytearray()
    pending = memoryview(input_data or b"")
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE if input_data is not None else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, shell=False,
    )
    try:
        assert process.stdout is not None and process.stderr is not None
        with selectors.DefaultSelector() as selector:
            for stream in (process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            if process.stdin is not None:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE)
            while selector.get_map() or process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SnapshotLimitError("Git command timed out at remaining deadline")
                for key, event in selector.select(timeout=min(remaining, 0.05)):
                    if event & selectors.EVENT_WRITE:
                        if pending:
                            try:
                                pending = pending[os.write(key.fd, pending[:4096]):]
                            except BrokenPipeError:
                                pending = pending[len(pending):]
                        else:
                            selector.unregister(key.fd)
                            assert process.stdin is not None
                            process.stdin.close()
                        continue
                    available = max_output_bytes - len(output) - len(errors)
                    chunk = os.read(key.fd, min(64 * 1024, available + 1))
                    if chunk and on_output_bytes is not None:
                        on_output_bytes(len(chunk))
                    if not chunk:
                        selector.unregister(key.fd)
                    elif len(chunk) > available:
                        raise SnapshotLimitError("Git combined stdout/stderr output cap exceeded")
                    elif key.fileobj is process.stdout:
                        output.extend(chunk)
                    else:
                        errors.extend(chunk)
            return subprocess.CompletedProcess(command, process.wait(), bytes(output), bytes(errors))
    finally:
        for closing_stream in (process.stdin, process.stdout, process.stderr):
            if closing_stream is not None:
                closing_stream.close()
        if process.poll() is None:
            process.kill()
        process.wait()


@dataclass(frozen=True)
class IndexBlob:
    """One stage-zero Git index entry and its exact blob bytes."""

    __slots__ = ("data", "mode")
    mode: str
    data: bytes

    def __getstate__(self) -> list[object]:
        return [self.mode, self.data]

    def __setstate__(self, state: Sequence[object]) -> None:
        object.__setattr__(self, "mode", state[0])
        object.__setattr__(self, "data", state[1])


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
    max_output_bytes: int | None = None,
    timeout_seconds: float | None = None,
    on_output_bytes: Callable[[int], None] | None = None,
    git_environment: dict[str, str] | None = None,
) -> bytes:
    timeout = GIT_TIMEOUT_SECONDS if timeout_seconds is None else min(timeout_seconds, GIT_TIMEOUT_SECONDS)
    if not math.isfinite(timeout) or timeout <= 0:
        raise SnapshotLimitError("Git command remaining deadline must be positive and finite")
    if max_output_bytes is not None and (type(max_output_bytes) is not int or max_output_bytes < 0):
        raise SnapshotLimitError("Git output cap must be a nonnegative integer")
    environment = os.environ.copy() if git_environment is None else git_environment.copy()
    if index_file is not None:
        environment["GIT_INDEX_FILE"] = str(index_file)
    command = ["git", "-C", str(root), *arguments]
    try:
        if max_output_bytes is None:
            result = subprocess.run(
                command, input=input_data, capture_output=True, check=False,
                env=environment, shell=False, timeout=timeout,
            )
            if on_output_bytes is not None:
                on_output_bytes(len(result.stdout) + len(result.stderr))
        else:
            result = _bounded_git_result(command, environment, input_data, max_output_bytes, timeout, on_output_bytes)
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


def _resolve_commit(root: Path, ref: str, *, git_environment: dict[str, str] | None = None) -> str:
    if not ref or "\x00" in ref:
        raise SnapshotError("Git ref must be a non-empty string without NUL bytes")
    output = _git(
        root,
        ["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        git_environment=git_environment,
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


def prepare_comparison_repository(root: Path, ref: str, destination: Path) -> None:
    """Give a materialized candidate an isolated native index at the actual base.

    Only private metadata is written. The source object store is an alternate,
    and hook-exported Git redirects cannot select the source checkout's index.
    """
    metadata = destination / ".git"
    if metadata.exists() or metadata.is_symlink():
        raise SnapshotError(f"comparison metadata already exists: {metadata}")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    commit = _resolve_commit(root, ref, git_environment=environment)
    objects = os.fsdecode(_git(
        root, ["rev-parse", "--path-format=absolute", "--git-path", "objects"],
        git_environment=environment,
    )).rstrip("\r\n")
    if not objects or "\n" in objects or "\r" in objects or not Path(objects).is_absolute():
        raise SnapshotError("source object store must be one absolute native path")
    destination.mkdir(parents=True, exist_ok=True)
    _git(destination, ["init", "--quiet", "--template=", "--initial-branch=gludd-duplicate-code"],
         git_environment=environment)
    (metadata / "objects/info/alternates").write_bytes(os.fsencode(objects) + b"\n")
    _git(destination, ["update-ref", "--no-deref", "HEAD", commit], git_environment=environment)
    _git(destination, ["read-tree", commit], git_environment=environment)
