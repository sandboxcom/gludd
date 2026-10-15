"""Bounded Git-selected leaf operations for the automatic disk-cleanup owner."""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import stat
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from scripts import staged_snapshot

if TYPE_CHECKING:
    import check_disk_usage
    import clean_ci_shard_scratch
else:
    check_disk_usage = importlib.import_module("scripts.check_disk_usage")
    clean_ci_shard_scratch = importlib.import_module("scripts.clean_ci_shard_scratch")

MAX_BYTECODE_FILES = 100_000
MAX_GIT_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_BYTECODE_SECONDS = 120.0
BYTECODE_BATCH_SIZE = 128
PRESERVED_COMPONENTS = frozenset({
    ".git", ".gludd", ".gate-logs", "ci-attempt-logs", ".venv", "node_modules",
    "preserved-evidence", "release-evidence", "gludd-release-evidence",
})
MAKE_GIT_ROUTING = frozenset({
    "GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CEILING_DIRECTORIES",
    "GIT_CONFIG", "GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS",
    "GIT_ICASE_PATHSPECS",
})
INVENTORY_PATHS = (
    ":(top,glob)**/__pycache__/*.py[co]",
    *(f":(top,glob,exclude)**/{name}/**" for name in sorted(PRESERVED_COMPONENTS)),
)
DIRECTORY_RELATIVE_SUPPORTED = (
    hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW")
    and {os.open, os.stat, os.unlink}.issubset(os.supports_dir_fd)
    and os.stat in os.supports_follow_symlinks
)


class BytecodeRefusal(RuntimeError):
    """Refuse further leaf operations when safety or capacity is uncertain."""


class BytecodeCapacity(BytecodeRefusal):
    """Defer remaining candidates while allowing the owner's next bounded pass."""


@dataclass
class Budget:
    """Share hard inventory/output/time bounds across one owner's worktree pass."""

    deadline: float = field(default_factory=lambda: time.monotonic() + MAX_BYTECODE_SECONDS)
    files: int = 0
    output_bytes: int = 0

    def charge(self, count: int) -> None:
        """Charge both Git pipes immediately, including commands that later fail."""
        self.output_bytes += count

    def check(self) -> None:
        """Refuse inherited Git routing and an exhausted cleanup deadline."""
        if time.monotonic() >= self.deadline:
            raise BytecodeCapacity("bytecode deadline exhausted; remaining work deferred")
        if self.output_bytes > MAX_GIT_OUTPUT_BYTES:
            raise BytecodeCapacity("bytecode Git output cap exhausted; remaining work deferred")
        if any(name in os.environ for name in MAKE_GIT_ROUTING) or any(
            name.startswith("GIT_CONFIG_") for name in os.environ
        ):
            raise BytecodeRefusal("inherited Git routing/configuration override")


@dataclass(frozen=True)
class Outcome:
    """Report actual partial progress without claiming deferred work completed."""

    removed_files: int = 0
    removed_bytes: int = 0
    skipped: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    blocked: bool = False


@dataclass(frozen=True)
class Candidate:
    """Pin the planned leaf and its real parent before any unlink."""

    relative: Path
    metadata: os.stat_result
    parent: os.stat_result


def _git(root: Path, arguments: Sequence[str], budget: Budget) -> bytes:
    budget.check()
    try:
        output = staged_snapshot._git(
            root, arguments, max_output_bytes=MAX_GIT_OUTPUT_BYTES - budget.output_bytes,
            timeout_seconds=budget.deadline - time.monotonic(),
            on_output_bytes=budget.charge,
        )
    except staged_snapshot.SnapshotLimitError as exc:
        raise BytecodeCapacity(f"{exc}; remaining work deferred") from exc
    if budget.output_bytes > MAX_GIT_OUTPUT_BYTES:
        raise BytecodeCapacity("bytecode Git output cap exceeded; remaining work deferred")
    budget.check()
    return output


def _git_path(root: Path, name: str, budget: Budget) -> Path:
    raw = _git(root, ["rev-parse", "--path-format=absolute", "--git-path", name], budget)
    path = Path(raw.decode("utf-8").removesuffix("\n"))
    if not path.is_absolute() or "\x00" in str(path):
        raise BytecodeRefusal("ambiguous native Git metadata path")
    return path


def _absolute_git_path(root: Path, arguments: Sequence[str], budget: Budget) -> Path:
    path = Path(_git(root, arguments, budget).decode("utf-8").removesuffix("\n"))
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise BytecodeRefusal("ambiguous or linked native Git route")
    return path


def _stamp(path: Path) -> os.stat_result | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        raise BytecodeRefusal("nonregular index/ignore/configuration entry")
    return metadata


def _same(before: os.stat_result | None, after: os.stat_result | None) -> bool:
    if before is None or after is None:
        return before is after
    return clean_ci_shard_scratch._same_identity(before, after) and (
        before.st_uid, before.st_gid, before.st_nlink, before.st_ctime_ns
    ) == (after.st_uid, after.st_gid, after.st_nlink, after.st_ctime_ns)


def _unchanged(stamps: dict[Path, os.stat_result | None]) -> None:
    if any(not _same(before, _stamp(path)) for path, before in stamps.items()):
        raise BytecodeRefusal("index/ignore/configuration identity changed")


def _parent_identity(before: os.stat_result, after: os.stat_result) -> bool:
    return (before.st_dev, before.st_ino, before.st_mode, before.st_uid) == (
        after.st_dev, after.st_ino, after.st_mode, after.st_uid,
    )


@contextlib.contextmanager
def _open_parent(root: Path, relative: Path, root_metadata: os.stat_result) -> Iterator[int]:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with contextlib.ExitStack() as stack:
        descriptor = os.open(root, flags)
        stack.callback(os.close, descriptor)
        if not _parent_identity(root_metadata, os.fstat(descriptor)):
            raise BytecodeRefusal("worktree directory identity changed")
        for component in relative.parts:
            descriptor = os.open(component, flags, dir_fd=descriptor)
            stack.callback(os.close, descriptor)
            if os.fstat(descriptor).st_uid != os.getuid():
                raise BytecodeRefusal("bytecode parent owner changed")
        yield descriptor


def _metadata_paths(root: Path, budget: Budget) -> tuple[Path, ...]:
    index = _git_path(root, "index", budget)
    metadata = _stamp(index)
    if metadata is None or metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
        raise BytecodeRefusal("native worktree index unavailable or unowned")
    if index.with_name(index.name + ".lock").exists():
        raise BytecodeRefusal("native worktree index lock present")
    configured = _git(root, ["config", "--null", "--path", "--default", "", "--get", "core.excludesFile"], budget)
    if not configured.endswith(b"\0") or b"\0" in configured[:-1]:
        raise BytecodeRefusal("ambiguous native ignore configuration")
    configured_path = configured[:-1].decode("utf-8")
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    global_ignore = Path(configured_path).expanduser() if configured_path else xdg / "git" / "ignore"
    if not global_ignore.is_absolute():
        global_ignore = root / global_ignore
    return (
        index, _git_path(root, "HEAD", budget), _git_path(root, "info/exclude", budget),
        _git_path(root, "config", budget), _git_path(root, "config.worktree", budget),
        root / ".gitignore", global_ignore, Path.home() / ".gitconfig", xdg / "git" / "config",
    )


def _registered_route(root: Path, repository: Path, budget: Budget) -> dict[Path, os.stat_result | None]:
    marker = root / ".git"
    stamps = {marker: _stamp(marker)}
    common = _absolute_git_path(repository, ["rev-parse", "--path-format=absolute", "--git-common-dir"], budget)
    candidate_common = _absolute_git_path(root, ["rev-parse", "--path-format=absolute", "--git-common-dir"], budget)
    directory = _absolute_git_path(root, ["rev-parse", "--absolute-git-dir"], budget)
    top = _absolute_git_path(root, ["rev-parse", "--show-toplevel"], budget)
    if candidate_common != common or directory.parent != common / "worktrees" or top != root:
        raise BytecodeRefusal("native Git route is not the registered repository worktree")
    for name in ("gitdir", "commondir"):
        path = directory / name
        metadata = _stamp(path)
        if metadata is None or metadata.st_uid != os.getuid() or metadata.st_size > 4096:
            raise BytecodeRefusal("unowned or oversized native Git backpointer")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            if not _same(metadata, os.fstat(descriptor)):
                raise BytecodeRefusal("native Git backpointer identity changed")
            raw = os.read(descriptor, 4097)
        finally:
            os.close(descriptor)
        if len(raw) > 4096:
            raise BytecodeRefusal("native Git backpointer output cap exceeded")
        target = Path(raw.decode("utf-8").removesuffix("\n"))
        expected = marker if name == "gitdir" else common
        if (directory / target).resolve(strict=True) != expected:
            raise BytecodeRefusal("native Git backpointer does not match the registered worktree")
        stamps[path] = metadata
    inventory = _git(repository, ["worktree", "list", "--porcelain", "-z"], budget)
    registered = check_disk_usage._parse_registered_worktrees(inventory, root.parent)
    if root not in registered or inventory.split(b"\0").count(b"worktree " + os.fsencode(root)) != 1:
        raise BytecodeRefusal("native Git registration is missing or ambiguous")
    _unchanged(stamps)
    return stamps


def _inventory(root: Path, paths: Sequence[str], budget: Budget) -> tuple[str, ...]:
    raw = _git(root, ["ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--", *paths], budget)
    return staged_snapshot._nul_paths(raw, source="ignored bytecode inventory")


def reclaim(
    root: Path,
    *,
    repository_root: Path | None,
    budget: Budget,
    revalidate: Callable[[], str | None],
    parent_is_safe: Callable[[Path, Path], bool],
    dry_run: bool,
) -> Outcome:
    """Unlink only native-Git-selected regular bytecode leaves in a proven idle peer."""
    if repository_root is None:
        return Outcome()
    removed_files = removed_bytes = 0
    skipped: list[str] = []
    unsafe = 0
    try:
        budget.check()
        refusal = revalidate()
        if refusal is not None:
            return Outcome(skipped=(f"{root}:bytecode {refusal}",), blocked=True)
        if not DIRECTORY_RELATIVE_SUPPORTED:
            raise BytecodeRefusal("safe directory-relative bytecode unlink unavailable")
        root_metadata = root.lstat()
        if not stat.S_ISDIR(root_metadata.st_mode) or root_metadata.st_uid != os.getuid():
            raise BytecodeRefusal("unowned or linked bytecode worktree root")
        route_stamps = _registered_route(root, repository_root, budget)
        metadata_paths = _metadata_paths(root, budget)
        global_stamps = {**route_stamps, **{path: _stamp(path) for path in metadata_paths}}
        stamps = global_stamps.copy()
        identity = _git(root, ["rev-parse", "HEAD"], budget)
        paths = _inventory(root, INVENTORY_PATHS, budget)
        if budget.files + len(paths) > MAX_BYTECODE_FILES:
            raise BytecodeCapacity("bytecode candidate cap exceeded; remaining work deferred")
        budget.files += len(paths)
        _unchanged(stamps)
        candidates: list[Candidate] = []
        for raw in paths:
            budget.check()
            relative = Path(raw)
            if (len(relative.parts) < 2 or len(relative.parts) > 32
                    or relative.parts[-2] != "__pycache__" or relative.suffix not in {".pyc", ".pyo"}
                    or PRESERVED_COMPONENTS.intersection(relative.parts)):
                raise BytecodeRefusal("unsafe or preserved bytecode inventory entry")
            if not parent_is_safe(root, relative.parent):
                unsafe += 1
                continue
            with _open_parent(root, relative.parent, root_metadata) as descriptor:
                metadata = os.stat(relative.name, dir_fd=descriptor, follow_symlinks=False)
                if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                        or metadata.st_nlink != 1):
                    unsafe += 1
                    continue
                candidates.append(Candidate(relative, metadata, os.fstat(descriptor)))
            for parent in (relative.parent, *relative.parent.parents):
                ignore = root / parent / ".gitignore"
                stamps.setdefault(ignore, _stamp(ignore))
        if unsafe:
            skipped.append(f"{root}:bytecode unsafe leaves preserved={unsafe}")
        eligible_bytes = sum(item.metadata.st_size for item in candidates)
        print(
            f"phase=cleanup action=bytecode status={'dry-run' if dry_run else 'planned'} "
            f"path={json.dumps(str(root))} eligible_files={len(candidates)} eligible_bytes={eligible_bytes}",
            flush=True,
        )
        if dry_run:
            _unchanged(stamps)
            budget.check()
            return Outcome(skipped=(
                *skipped, f"{root}:would remove bytecode files={len(candidates)} bytes={eligible_bytes}",
            ), blocked=bool(unsafe))
        for start in range(0, len(candidates), BYTECODE_BATCH_SIZE):
            budget.check()
            refusal = revalidate()
            if refusal is not None:
                raise BytecodeRefusal(refusal)
            _unchanged(stamps)
            if _git(root, ["rev-parse", "HEAD"], budget) != identity:
                raise BytecodeRefusal("bytecode checkout HEAD changed")
            if metadata_paths[0].with_name(metadata_paths[0].name + ".lock").exists():
                raise BytecodeRefusal("native worktree index lock appeared")
            batch = candidates[start:start + BYTECODE_BATCH_SIZE]
            selected = tuple(str(item.relative) for item in batch)
            if _inventory(root, [f":(top,literal){path}" for path in selected], budget) != tuple(sorted(selected)):
                raise BytecodeRefusal("bytecode index/ignore eligibility changed")
            for item in batch:
                budget.check()
                own_stamps = {
                    root / parent / ".gitignore": stamps[root / parent / ".gitignore"]
                    for parent in (item.relative.parent, *item.relative.parent.parents)
                }
                _unchanged({**global_stamps, **own_stamps})
                if not parent_is_safe(root, item.relative.parent):
                    raise BytecodeRefusal("bytecode parent identity changed")
                with _open_parent(root, item.relative.parent, root_metadata) as descriptor:
                    if not _parent_identity(item.parent, os.fstat(descriptor)):
                        raise BytecodeRefusal("bytecode parent identity changed")
                    metadata = os.stat(item.relative.name, dir_fd=descriptor, follow_symlinks=False)
                    if not _same(item.metadata, metadata):
                        raise BytecodeRefusal("bytecode leaf identity changed")
                    if not _parent_identity(os.fstat(descriptor), (root / item.relative.parent).lstat()):
                        raise BytecodeRefusal("bytecode parent path changed")
                    os.unlink(item.relative.name, dir_fd=descriptor)
                    removed_files += 1
                    removed_bytes += item.metadata.st_size
            if start % (BYTECODE_BATCH_SIZE * 8) == 0:
                print(
                    f"phase=cleanup action=bytecode status=progress path={json.dumps(str(root))} "
                    f"removed_files={removed_files} removed_bytes={removed_bytes}", flush=True,
                )
    except (BytecodeRefusal, staged_snapshot.SnapshotError, OSError, RuntimeError, ValueError) as exc:
        print(
            f"phase=cleanup action=bytecode status=deferred path={json.dumps(str(root))} "
            f"removed_files={removed_files} removed_bytes={removed_bytes} reason={json.dumps(str(exc))}",
            flush=True,
        )
        detail = f"{root}:bytecode {exc}"
        if isinstance(exc, BytecodeCapacity):
            return Outcome(removed_files, removed_bytes, (*skipped, detail), blocked=True)
        return Outcome(removed_files, removed_bytes, tuple(skipped), (detail,), True)
    print(
        f"phase=cleanup action=bytecode status=complete path={json.dumps(str(root))} "
        f"removed_files={removed_files} removed_bytes={removed_bytes}", flush=True,
    )
    return Outcome(removed_files, removed_bytes, tuple(skipped), blocked=bool(unsafe))
