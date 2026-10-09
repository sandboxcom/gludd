#!/usr/bin/env python3
"""Safely inventory or reclaim stale Gludd-owned resource namespaces.

Only canonical direct children of a ``gludd-resources`` root are eligible.
Every ambiguous ownership, process, lock, path, age, or worktree observation
fails closed.  The default CLI mode is a non-mutating inventory.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from scripts.collection_lock import LeaseInspection, inspect_lease
from scripts.resource_arbiter import project_namespace
from scripts.workstream_registry import default_registry_path

_NAMESPACE = re.compile(
    r"^(?P<base>[A-Za-z0-9_.-]+-[0-9a-f]{12})(?P<toolchain>-toolchain)?$"
)
_MARKER_TOKEN = re.compile(r"(?:^|[._-])(lease|lock|owner|pid)(?:$|[._-])")
_MAX_MARKER_BYTES = 4_096
_MAX_GRACE_SECONDS = 30 * 24 * 60 * 60
_MAX_OUTPUT_DECISIONS = 100


@dataclass(frozen=True)
class CleanupConfig:
    """Bounded operator inputs for one cleanup pass."""

    root: Path
    grace_seconds: float
    now_epoch: float
    validate_only: bool
    receipt_path: Path
    max_candidates: int
    max_entries_per_namespace: int
    heartbeat_seconds: float


@dataclass(frozen=True)
class ProcessIdentity:
    """Stable process identity used to distinguish liveness from PID reuse."""

    pid: int
    started_at: str


@dataclass(frozen=True)
class PathFingerprint:
    """Top-level and recursive identity captured during inventory."""

    path: Path
    device: int
    inode: int
    tree_digest: str
    newest_mtime: float
    size_bytes: int


@dataclass(frozen=True)
class NamespaceDecision:
    """One paired namespace/toolchain cleanup decision."""

    base_name: str
    paths: tuple[Path, ...]
    action: Literal["preserve", "reclaim"]
    reason: str
    fingerprints: tuple[PathFingerprint, ...]


@dataclass(frozen=True)
class CleanupPlan:
    """Immutable inventory used for explicit apply revalidation."""

    root: Path
    root_device: int
    root_inode: int
    decisions: tuple[NamespaceDecision, ...]


@dataclass(frozen=True)
class CleanupResult:
    """Paths deleted or refused after the second validation pass."""

    deleted: tuple[Path, ...]
    refused: tuple[Path, ...]


@dataclass(frozen=True)
class _Scan:
    fingerprint: PathFingerprint | None
    reason: str | None


def _normalize_start(value: str) -> str:
    return " ".join(value.split())


def _validate_config(config: CleanupConfig) -> None:
    if not 0 < config.grace_seconds <= _MAX_GRACE_SECONDS:
        raise ValueError("grace must be greater than zero and no more than 30 days")
    if not 1 <= config.max_candidates <= 1_000:
        raise ValueError("candidate limit must be between 1 and 1000")
    if not 1 <= config.max_entries_per_namespace <= 1_000_000:
        raise ValueError("entry limit must be between 1 and 1000000")
    if not 0.1 <= config.heartbeat_seconds <= 60:
        raise ValueError("heartbeat must be between 0.1 and 60 seconds")


def _validate_root(root: Path) -> tuple[Path, os.stat_result] | None:
    expanded = root.expanduser().absolute()
    if expanded.name != "gludd-resources":
        raise ValueError("resource root must be named gludd-resources")
    if expanded.is_symlink():
        raise ValueError("resource root must not be a symlink")
    if not expanded.exists():
        return None
    resolved = expanded.resolve(strict=True)
    if resolved != expanded:
        raise ValueError("resource root path must be exact and symlink-free")
    identity = resolved.lstat()
    if not stat.S_ISDIR(identity.st_mode) or identity.st_uid != os.getuid():
        raise ValueError("resource root must be an owned directory")
    return resolved, identity


def _namespace_base(name: str) -> str | None:
    match = _NAMESPACE.fullmatch(name)
    return match.group("base") if match else None


def _is_marker(path: Path) -> bool:
    lowered = path.name.lower()
    return bool(lowered.endswith((".lock", ".pid")) or _MARKER_TOKEN.search(lowered))


def _positive_pid(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1 < value <= 2**31 - 1 else None


def _marker_owner(path: Path) -> tuple[int, str | None] | None:
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    if len(payload) > _MAX_MARKER_BYTES:
        return None
    try:
        text = payload.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not text:
        return None
    if text.isascii() and text.isdecimal() and not text.startswith("0"):
        pid = _positive_pid(int(text))
        return (pid, None) if pid is not None else None
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        fields: dict[str, str] = {}
        for line in text.splitlines():
            key, separator, value = line.partition("=")
            if not separator or key in fields:
                return None
            fields[key] = value
        raw_pid = fields.get("pid", "")
        if not raw_pid.isascii() or not raw_pid.isdecimal() or raw_pid.startswith("0"):
            return None
        pid = _positive_pid(int(raw_pid))
        started = fields.get("pid_started_at") or fields.get("started_at")
        return (pid, started or None) if pid is not None else None
    if not isinstance(decoded, dict) or set(decoded).isdisjoint(
        {"pid", "owner_pid", "worker_pid"}
    ):
        return None
    json_pid = decoded.get("pid", decoded.get("owner_pid", decoded.get("worker_pid")))
    pid = _positive_pid(json_pid)
    if pid is None:
        return None
    raw_start = decoded.get("pid_started_at", decoded.get("started_at"))
    started = raw_start if isinstance(raw_start, str) and raw_start.strip() else None
    return pid, started


def _owner_reason(
    marker: Path,
    processes: Mapping[int, ProcessIdentity],
    lease: LeaseInspection | None,
) -> str | None:
    if lease is not None:
        if lease.state == "held":
            return "lease-held"
        if lease.state == "unavailable":
            return "lease-unavailable"
        owner = (
            (lease.record.pid, None)
            if lease.record is not None
            else _marker_owner(marker)
        )
        if owner is None:
            return None  # an available, empty kernel lock is proven inactive
    else:
        owner = _marker_owner(marker)
        if owner is None:
            return "ambiguous-lease-owner"
    pid, recorded_start = owner
    current = processes.get(pid)
    if current is None:
        return None
    if recorded_start is None:
        return "ambiguous-lease-owner"
    if _normalize_start(recorded_start) == _normalize_start(current.started_at):
        return "live-lease-owner"
    return None  # same numeric PID now belongs to a different process identity


def _scan_candidate(
    path: Path,
    config: CleanupConfig,
    processes: Mapping[int, ProcessIdentity],
) -> _Scan:
    try:
        top = path.lstat()
    except OSError:
        return _Scan(None, "candidate-unreadable")
    if stat.S_ISLNK(top.st_mode):
        return _Scan(None, "candidate-symlink")
    if not stat.S_ISDIR(top.st_mode) or top.st_uid != os.getuid():
        return _Scan(None, "candidate-not-owned-directory")

    digest = hashlib.sha256()
    newest = top.st_mtime
    size_bytes = top.st_size
    entries = 0
    pending = [path]
    while pending:
        directory = pending.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda child: child.name)
        except OSError:
            return _Scan(None, "candidate-unreadable")
        for child in children:
            entries += 1
            if entries > config.max_entries_per_namespace:
                return _Scan(None, "namespace-entry-limit")
            try:
                identity = child.lstat()
            except OSError:
                return _Scan(None, "candidate-unreadable")
            if stat.S_ISLNK(identity.st_mode):
                return _Scan(None, "descendant-symlink")
            if identity.st_uid != os.getuid():
                return _Scan(None, "foreign-owner")
            if stat.S_ISREG(identity.st_mode) and identity.st_nlink != 1:
                return _Scan(None, "descendant-hardlink")
            relative = child.relative_to(path).as_posix()
            digest.update(
                f"{relative}\0{identity.st_dev}\0{identity.st_ino}\0"
                f"{identity.st_mode}\0{identity.st_size}\0{identity.st_mtime_ns}\n".encode()
            )
            newest = max(newest, identity.st_mtime)
            size_bytes += identity.st_size
            if stat.S_ISDIR(identity.st_mode):
                pending.append(child)
            elif _is_marker(child):
                lease = inspect_lease(child) if child.name.lower().endswith(".lock") else None
                if reason := _owner_reason(child, processes, lease):
                    return _Scan(None, reason)
    return _Scan(
        PathFingerprint(path, top.st_dev, top.st_ino, digest.hexdigest(), newest, size_bytes),
        None,
    )


def protected_namespace_names(
    current_project_root: Path, worktrees: Sequence[Path]
) -> set[str]:
    """Return current plus registered worktree namespaces."""
    current = current_project_root.expanduser().resolve(strict=True)
    registered = tuple(root.expanduser().resolve(strict=False) for root in worktrees)
    return {project_namespace(root) for root in (current, *registered)}


def plan_cleanup(
    config: CleanupConfig,
    protected_namespaces: set[str],
    process_identities: Mapping[int, ProcessIdentity] | None,
) -> CleanupPlan:
    """Build a bounded, non-mutating cleanup plan."""
    _validate_config(config)
    validated = _validate_root(config.root)
    if validated is None:
        return CleanupPlan(config.root.expanduser().absolute(), 0, 0, ())
    root, root_identity = validated
    children = sorted(root.iterdir(), key=lambda child: child.name)
    if len(children) > config.max_candidates:
        raise ValueError("candidate limit exceeded")

    grouped: dict[str, list[Path]] = {}
    unrecognized: list[Path] = []
    for child in children:
        base = _namespace_base(child.name)
        if base is None:
            unrecognized.append(child)
        else:
            grouped.setdefault(base, []).append(child)

    decisions: list[NamespaceDecision] = []
    for child in unrecognized:
        decisions.append(
            NamespaceDecision(child.name, (child,), "preserve", "unrecognized-namespace", ())
        )
    for base, paths in grouped.items():
        ordered = tuple(sorted(paths, key=lambda path: path.name.endswith("-toolchain")))
        if base in protected_namespaces:
            decisions.append(
                NamespaceDecision(base, ordered, "preserve", "protected-worktree-namespace", ())
            )
            continue
        if process_identities is None:
            decisions.append(
                NamespaceDecision(base, ordered, "preserve", "process-census-unavailable", ())
            )
            continue
        fingerprints: list[PathFingerprint] = []
        refusal: str | None = None
        for path in ordered:
            scan = _scan_candidate(path, config, process_identities)
            if scan.reason is not None:
                refusal = scan.reason
                break
            if scan.fingerprint is None:  # defensive fail-closed invariant
                refusal = "candidate-unreadable"
                break
            fingerprints.append(scan.fingerprint)
        if refusal is not None:
            decisions.append(NamespaceDecision(base, ordered, "preserve", refusal, ()))
            continue
        newest = max(fingerprint.newest_mtime for fingerprint in fingerprints)
        if config.now_epoch - newest < config.grace_seconds:
            decisions.append(
                NamespaceDecision(base, ordered, "preserve", "within-grace-period", tuple(fingerprints))
            )
            continue
        decisions.append(
            NamespaceDecision(
                base,
                ordered,
                "reclaim",
                "stale-and-proven-inactive",
                tuple(fingerprints),
            )
        )
    decisions.sort(key=lambda decision: decision.base_name)
    return CleanupPlan(root, root_identity.st_dev, root_identity.st_ino, tuple(decisions))


def _append_receipt(path: Path, record: dict[str, object], root: Path) -> None:
    destination = path.expanduser().absolute()
    if destination == root or root in destination.parents:
        raise ValueError("receipt path must be outside the cleanup root")
    if destination.is_symlink():
        raise ValueError("receipt path must not be a symlink")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _delete_with_heartbeats(path: Path, heartbeat_seconds: float) -> None:
    if not shutil.rmtree.avoids_symlink_attacks:
        raise RuntimeError("platform lacks symlink-safe recursive deletion")
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="gludd-resource-cleanup") as pool:
        future = pool.submit(shutil.rmtree, path)
        while True:
            try:
                future.result(timeout=heartbeat_seconds)
                return
            except FutureTimeout:
                print(f"RESOURCE_CLEANUP_HEARTBEAT path={path} state=deleting", flush=True)


def apply_cleanup(
    plan: CleanupPlan,
    config: CleanupConfig,
    protected_namespaces: set[str],
    process_identities: Mapping[int, ProcessIdentity] | None,
) -> CleanupResult:
    """Apply only decisions that remain byte-for-byte and identity equivalent."""
    if config.validate_only:
        return CleanupResult((), ())
    current = plan_cleanup(config, protected_namespaces, process_identities)
    if (current.root_device, current.root_inode) != (plan.root_device, plan.root_inode):
        return CleanupResult((), tuple(path for item in plan.decisions for path in item.paths))
    current_by_base = {decision.base_name: decision for decision in current.decisions}
    deleted: list[Path] = []
    refused: list[Path] = []
    for decision in plan.decisions:
        if decision.action != "reclaim":
            continue
        refreshed = current_by_base.get(decision.base_name)
        if (
            refreshed is None
            or refreshed.action != "reclaim"
            or refreshed.paths != decision.paths
            or refreshed.fingerprints != decision.fingerprints
        ):
            refused.extend(decision.paths)
            continue
        for fingerprint in decision.fingerprints:
            try:
                identity = fingerprint.path.lstat()
            except OSError:
                refused.append(fingerprint.path)
                continue
            if (
                fingerprint.path.parent != plan.root
                or stat.S_ISLNK(identity.st_mode)
                or (identity.st_dev, identity.st_ino)
                != (fingerprint.device, fingerprint.inode)
            ):
                refused.append(fingerprint.path)
                continue
            print(f"RESOURCE_CLEANUP_DELETE_BEGIN path={fingerprint.path}", flush=True)
            _delete_with_heartbeats(fingerprint.path, config.heartbeat_seconds)
            _append_receipt(
                config.receipt_path,
                {
                    "deleted_at": time.time(),
                    "device": fingerprint.device,
                    "grace_seconds": config.grace_seconds,
                    "inode": fingerprint.inode,
                    "namespace": decision.base_name,
                    "outcome": "deleted",
                    "path": str(fingerprint.path),
                    "reason": decision.reason,
                    "size_bytes": fingerprint.size_bytes,
                    "tree_digest": fingerprint.tree_digest,
                },
                plan.root,
            )
            deleted.append(fingerprint.path)
            print(f"RESOURCE_CLEANUP_DELETE_END path={fingerprint.path}", flush=True)
    return CleanupResult(tuple(deleted), tuple(refused))


def _process_identities() -> dict[int, ProcessIdentity] | None:
    try:
        completed = subprocess.run(
            ["/bin/ps", "-axo", "pid=,lstart="],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    identities: dict[int, ProcessIdentity] = {}
    for line in completed.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or not fields[0].isdecimal():
            return None
        pid = int(fields[0])
        started = _normalize_start(fields[1])
        if not started or pid in identities:
            return None
        if pid > 1:
            identities[pid] = ProcessIdentity(pid, started)
    return identities or None


def _git_worktrees(project_root: Path) -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "-C", str(project_root), "worktree", "list", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    paths = [
        Path(line.removeprefix("worktree "))
        for line in completed.stdout.splitlines()
        if line.startswith("worktree ")
    ]
    if not paths:
        raise ValueError("git worktree inventory is empty")
    return tuple(path.expanduser().resolve(strict=False) for path in paths)


def _registered_worktrees(registry_path: Path) -> tuple[Path, ...]:
    if not registry_path.exists():
        return ()
    if registry_path.is_symlink() or not registry_path.is_file():
        raise ValueError("active-workstream registry is unsafe")
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("active-workstream registry is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("active-workstream registry schema is invalid")
    workstreams = payload.get("workstreams")
    if not isinstance(workstreams, dict):
        raise ValueError("active-workstream registry entries are invalid")
    paths: list[Path] = []
    for branch, entry in workstreams.items():
        if not isinstance(branch, str) or not isinstance(entry, dict):
            raise ValueError("active-workstream registry entry is ambiguous")
        if entry.get("status") != "active" or entry.get("branch") != branch:
            raise ValueError("active-workstream registry entry is ambiguous")
        raw_path = entry.get("worktree")
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("active-workstream registry worktree is invalid")
        paths.append(Path(raw_path).expanduser().resolve(strict=False))
    return tuple(paths)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--grace-seconds", type=float, required=True)
    parser.add_argument("--max-candidates", type=int, default=1_000)
    parser.add_argument("--max-entries", type=int, default=100_000)
    parser.add_argument("--heartbeat-seconds", type=float, default=5.0)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run a bounded inventory or explicit cleanup pass."""
    args = _parser().parse_args(argv)
    project_root = args.project_root.expanduser().resolve(strict=True)
    registry = (args.registry or default_registry_path(project_root)).expanduser()
    config = CleanupConfig(
        root=args.root,
        grace_seconds=args.grace_seconds,
        now_epoch=time.time(),
        validate_only=args.validate_only,
        receipt_path=args.receipt,
        max_candidates=args.max_candidates,
        max_entries_per_namespace=args.max_entries,
        heartbeat_seconds=args.heartbeat_seconds,
    )
    try:
        worktrees = (*_git_worktrees(project_root), *_registered_worktrees(registry))
        protected = protected_namespace_names(project_root, worktrees)
        processes = _process_identities()
        print(
            f"RESOURCE_CLEANUP_PHASE phase=inventory mode={'validate' if args.validate_only else 'apply'} "
            f"root={args.root} grace_seconds={args.grace_seconds:g}",
            flush=True,
        )
        plan = plan_cleanup(config, protected, processes)
        for decision in plan.decisions[:_MAX_OUTPUT_DECISIONS]:
            print(
                json.dumps(
                    {
                        "action": decision.action,
                        "namespace": decision.base_name,
                        "paths": [str(path) for path in decision.paths],
                        "reason": decision.reason,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        omitted = max(0, len(plan.decisions) - _MAX_OUTPUT_DECISIONS)
        if omitted:
            print(f"RESOURCE_CLEANUP_INVENTORY_OMITTED count={omitted}", flush=True)
        fresh_processes = _process_identities() if args.apply else processes
        result = apply_cleanup(plan, config, protected, fresh_processes)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"RESOURCE_CLEANUP_REFUSED reason={exc}", flush=True)
        return 2
    reclaimable = [item for item in plan.decisions if item.action == "reclaim"]
    reclaim_bytes = sum(
        fingerprint.size_bytes
        for item in reclaimable
        for fingerprint in item.fingerprints
    )
    print(
        f"RESOURCE_CLEANUP_SUMMARY candidates={len(plan.decisions)} "
        f"reclaimable={len(reclaimable)} reclaim_bytes={reclaim_bytes} "
        f"deleted={len(result.deleted)} refused={len(result.refused)} "
        f"mode={'validate' if args.validate_only else 'apply'}",
        flush=True,
    )
    return 1 if result.refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
