#!/usr/bin/env python3
"""Automatically reclaim proven-idle Gludd storage under disk pressure.

Generated caches are removed from an idle invoking worktree or from inactive
worktrees. This includes the invoking checkout's exact regenerable Terraform
provider cache while preserving state. A complete, clean checkout may also be
dematerialized after its exact branch and commit are proven durable. The shared
uv cache is pruned through uv only after ownership is idle.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import check_disk_usage
    import clean_ci_shard_scratch
    import prune_worktrees_safe

    workstream_registry: Any
else:
    check_disk_usage = importlib.import_module("scripts.check_disk_usage")
    clean_ci_shard_scratch = importlib.import_module("scripts.clean_ci_shard_scratch")
    prune_worktrees_safe = importlib.import_module("scripts.prune_worktrees_safe")
    workstream_registry = importlib.import_module("scripts.workstream_registry")

DISPOSABLE_CACHE_DIR_NAMES = (
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
)
TERRAFORM_PLUGIN_CACHE_PATH = Path("infra/terraform/.plugin-cache")
TERRAFORM_PLUGIN_CACHE_MARKER = ".gitkeep"
INVOKING_DISPOSABLE_CACHE_PATHS = (
    *(Path(name) for name in DISPOSABLE_CACHE_DIR_NAMES),
    TERRAFORM_PLUGIN_CACHE_PATH,
)
TOOL_ENVIRONMENT_DIR_NAMES = (
    ".venv",
)
GENERATED_CACHE_DIR_NAMES = (
    *DISPOSABLE_CACHE_DIR_NAMES,
    *TOOL_ENVIRONMENT_DIR_NAMES,
)
DEFAULT_TMP_WORKTREE_ROOT = Path("/tmp/gludd-worktrees")
SHARED_UV_CACHE_ROOT = Path("/tmp/gludd-uv-cache-public-v2")
OWNED_TERRAFORM_CACHE_ROOTS = (
    Path("/tmp/gludd-azure-containerapp-live-proof"),
    Path("/tmp/gludd-azure-containerapp-environments"),
)
AZURE_TERRAFORM_OWNERSHIP_MARKER = ".gludd-azure-containerapp-live-proof.json"
AZURE_TERRAFORM_OWNERSHIP_PROTOCOL = "gludd-azure-containerapp-live-proof-v1"
MAX_AZURE_TERRAFORM_MARKER_BYTES = 4096
MAX_OWNED_TERRAFORM_WORKSPACES = 16
WORKSTREAM_LEASE_SECONDS = 24 * 60 * 60
MIN_COMMIT_RECEIPT_GRACE_SECONDS = 30 * 60
DEFAULT_COMMIT_RECEIPT_GRACE_SECONDS = MIN_COMMIT_RECEIPT_GRACE_SECONDS
MAX_WORKTREE_MATERIALIZATIONS = 4
MAX_EVIDENCE_RELOCATIONS = 16
MAX_PREFLIGHT_CLEANUP_PASSES = 8
MAX_CLEANUP_DETAILS_PER_KIND = 20
REGENERABLE_IGNORED_DIR_NAMES = frozenset(
    {".hypothesis", "__pycache__", "node_modules", *GENERATED_CACHE_DIR_NAMES}
)
REGENERABLE_IGNORED_SUFFIXES = frozenset({".pyc", ".pyo"})
ARCHIVABLE_PRESERVED_DATA_NAMES = frozenset({".gate-logs", ".gludd"})

DiskInspectionError = check_disk_usage.DiskInspectionError
ProcessInspectionError = clean_ci_shard_scratch.ProcessInspectionError


@dataclass(frozen=True)
class DiskSnapshot:
    """Measured scratch and volume pressure at one instant."""

    scratch_mb: float
    disk_pct: float

    @property
    def is_high(self) -> bool:
        """Return whether either established project limit is exceeded."""
        return (
            self.scratch_mb > check_disk_usage.GLUDD_TMP_LIMIT_MB
            or self.disk_pct > check_disk_usage.DISK_USAGE_PCT_LIMIT
        )


@dataclass(frozen=True)
class CleanupResult:
    """Bounded cleanup evidence separated by action and safety refusal."""

    removed: tuple[str, ...]
    skipped: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class WorkstreamLease:
    """One explicit model-workstream ownership lease."""

    branch: str
    worktree: Path
    updated_epoch: int


@dataclass(frozen=True)
class IntegrationPoint:
    """A trusted integration commit and its committer timestamp."""

    revision: str
    committed_epoch: int


@dataclass(frozen=True)
class LifecycleDecision:
    """Whether a registered worktree has deterministic completion proof."""

    reclaimable: bool
    reason: str
    error: bool = False
    cache_only: bool = False


ActiveBranches = Callable[[], frozenset[str]]
ActiveProcessPids = Callable[[Path], list[int]]
OwnedProcessPids = Callable[[], frozenset[int]]
RefreshRecords = Callable[[], list[prune_worktrees_safe.WorktreeRecord]]
RemoveTree = Callable[[Path], None]
InspectUsage = Callable[[], DiskSnapshot]
Cleanup = Callable[[], CleanupResult]
ActiveWorkstreamLeases = Callable[[], Mapping[str, WorkstreamLease]]
LifecycleProof = Callable[
    [prune_worktrees_safe.WorktreeRecord, WorkstreamLease], LifecycleDecision
]
RemoveMaterialization = Callable[
    [prune_worktrees_safe.WorktreeRecord, bool], LifecycleDecision
]


def inspect_usage() -> DiskSnapshot:
    """Reuse the canonical scratch classifier and repository-volume probe."""
    scratch_mb, _classifications = check_disk_usage._gludd_tmp_inspection()
    return DiskSnapshot(
        scratch_mb=scratch_mb,
        disk_pct=check_disk_usage._disk_usage_pct(),
    )


def _registered_worktrees() -> list[prune_worktrees_safe.WorktreeRecord]:
    """Read Git's authoritative stable-porcelain worktree registry."""
    completed = prune_worktrees_safe._git("worktree", "list", "--porcelain")
    records = prune_worktrees_safe.parse_worktrees(completed.stdout)
    if not records:
        raise DiskInspectionError("git worktree registry returned no worktrees")
    return records


def _canonical_evidence_archive_root() -> Path:
    """Return the repository-owned archive outside scratch worktree roots."""
    completed = prune_worktrees_safe._git(
        "rev-parse", "--path-format=absolute", "--git-common-dir", check=False
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise DiskInspectionError("git common directory is unavailable")
    common_dir = Path(completed.stdout.strip()).resolve(strict=True)
    if not common_dir.is_dir():
        raise DiskInspectionError("git common directory is invalid")
    return common_dir / "gludd-release-evidence"


def _active_workstream_leases(registry: Any) -> dict[str, WorkstreamLease]:
    """Read and strictly validate active leases through the canonical registry."""
    payload = registry._read()
    workstreams = payload.get("workstreams")
    if not isinstance(workstreams, dict):
        raise ValueError("active-workstream registry has invalid workstreams")
    leases: dict[str, WorkstreamLease] = {}
    for branch, raw_entry in workstreams.items():
        if not isinstance(branch, str) or not isinstance(raw_entry, dict):
            raise ValueError("active-workstream registry has invalid entry")
        if raw_entry.get("status") != "active":
            continue
        raw_worktree = raw_entry.get("worktree")
        updated_epoch = raw_entry.get("updated_epoch")
        if (
            raw_entry.get("branch") != branch
            or not isinstance(raw_worktree, str)
            or not raw_worktree
            or not isinstance(updated_epoch, int)
            or isinstance(updated_epoch, bool)
            or updated_epoch < 0
        ):
            raise ValueError("active-workstream registry has invalid lease")
        worktree = Path(raw_worktree).expanduser()
        if not worktree.is_absolute():
            raise ValueError("active-workstream registry has relative worktree")
        leases[branch] = WorkstreamLease(
            branch=branch,
            worktree=worktree.resolve(),
            updated_epoch=updated_epoch,
        )
    return leases


def _integration_points(
    records: Sequence[prune_worktrees_safe.WorktreeRecord],
) -> tuple[IntegrationPoint, ...]:
    """Resolve main/development/master commits used for completion proof."""
    if not records:
        raise DiskInspectionError("cannot resolve integration points without worktrees")
    references = ["HEAD", "refs/heads/development", "refs/heads/master"]
    points: dict[str, IntegrationPoint] = {}
    for reference in references:
        command = (
            ("-C", str(records[0].path), "rev-parse", "HEAD")
            if reference == "HEAD"
            else ("rev-parse", "--verify", f"{reference}^{{commit}}")
        )
        resolved = prune_worktrees_safe._git(*command, check=False)
        if resolved.returncode != 0:
            continue
        revision = resolved.stdout.strip()
        if not revision:
            continue
        timestamp = prune_worktrees_safe._git(
            "show", "-s", "--format=%ct", revision, check=False
        )
        try:
            committed_epoch = int(timestamp.stdout.strip())
        except ValueError:
            continue
        if timestamp.returncode == 0 and committed_epoch >= 0:
            points[revision] = IntegrationPoint(revision, committed_epoch)
    if not points:
        raise DiskInspectionError("no trusted integration commit could be resolved")
    return tuple(sorted(points.values(), key=lambda point: point.revision))


def _completion_proof(
    record: prune_worktrees_safe.WorktreeRecord,
    lease: WorkstreamLease,
    *,
    integration_points: Sequence[IntegrationPoint],
    now_epoch: int,
    receipt_grace_seconds: int = DEFAULT_COMMIT_RECEIPT_GRACE_SECONDS,
    run_git: Callable[..., subprocess.CompletedProcess[str]] = (
        prune_worktrees_safe._git
    ),
) -> LifecycleDecision:
    """Prove registered work is completed or safely beyond its ownership lease."""
    if receipt_grace_seconds < MIN_COMMIT_RECEIPT_GRACE_SECONDS:
        return LifecycleDecision(False, "commit receipt grace below minimum", True)
    status = run_git(
        "-C",
        str(record.path),
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        check=False,
    )
    if status.returncode != 0:
        return LifecycleDecision(False, "worktree status inspection failed", True)
    if status.stdout:
        return LifecycleDecision(False, "dirty worktree")

    head_result = run_git(
        "-C", str(record.path), "rev-parse", "HEAD", check=False
    )
    if head_result.returncode != 0 or not head_result.stdout.strip():
        return LifecycleDecision(False, "worktree HEAD inspection failed", True)
    head = head_result.stdout.strip()
    head_time_result = run_git(
        "show", "-s", "--format=%ct", head, check=False
    )
    try:
        head_epoch = int(head_time_result.stdout.strip())
    except ValueError:
        return LifecycleDecision(False, "worktree commit-time inspection failed", True)
    if head_time_result.returncode != 0 or head_epoch < 0:
        return LifecycleDecision(False, "worktree commit-time inspection failed", True)

    integrated = False
    completion_after_registration = False
    for point in integration_points:
        cherry = run_git("cherry", point.revision, head, check=False)
        if cherry.returncode != 0:
            return LifecycleDecision(False, "integration inspection failed", True)
        lines = [line for line in cherry.stdout.splitlines() if line.strip()]
        if any(line.startswith("+") for line in lines):
            continue
        integrated = True
        branch_changes = bool(lines) or head_epoch >= lease.updated_epoch
        if branch_changes and point.committed_epoch >= lease.updated_epoch:
            completion_after_registration = True
            break

    if completion_after_registration:
        return LifecycleDecision(True, "completed after workstream registration")
    if now_epoch - lease.updated_epoch > WORKSTREAM_LEASE_SECONDS:
        return LifecycleDecision(True, "expired lease; clean committed worktree")
    receipt = run_git(
        "-C",
        str(record.path),
        "reflog",
        "show",
        "-n",
        "1",
        "--format=%H%x00%ct%x00%gs",
        "HEAD",
        check=False,
    )
    receipt_fields = receipt.stdout.rstrip("\n").split("\0")
    if receipt.returncode == 0 and len(receipt_fields) == 3:
        receipt_head, raw_receipt_epoch, receipt_action = receipt_fields
        try:
            receipt_epoch = int(raw_receipt_epoch)
        except ValueError:
            receipt_epoch = -1
        if (
            receipt_head == head
            and lease.updated_epoch <= receipt_epoch <= now_epoch + 60
            and receipt_action.startswith("commit")
        ):
            if now_epoch - receipt_epoch >= receipt_grace_seconds:
                return LifecycleDecision(
                    True,
                    "exact-head commit receipt beyond retirement grace",
                )
            return LifecycleDecision(
                True,
                "exact-head successful commit receipt",
                cache_only=True,
            )
    if not integrated:
        return LifecycleDecision(False, "unintegrated worktree")
    return LifecycleDecision(False, "fresh active lease")


def _remove_worktree_materialization(
    record: prune_worktrees_safe.WorktreeRecord,
    dry_run: bool,
    *,
    evidence_archive_root: Path | None = None,
    run_git: Callable[..., subprocess.CompletedProcess[str]] = (
        prune_worktrees_safe._git
    ),
) -> LifecycleDecision:
    """Remove one clean checkout while proving its branch and commit survive."""
    if record.branch is None:
        return LifecycleDecision(False, "detached worktree")
    status = run_git(
        "-C",
        str(record.path),
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        check=False,
    )
    if status.returncode != 0:
        return LifecycleDecision(False, "worktree status inspection failed", True)
    if status.stdout:
        return LifecycleDecision(False, "dirty worktree")
    ignored = run_git(
        "-C",
        str(record.path),
        "status",
        "--porcelain=v1",
        "-z",
        "--ignored=matching",
        "--untracked-files=all",
        check=False,
    )
    if ignored.returncode != 0:
        return LifecycleDecision(False, "ignored data inspection failed", True)
    raw_evidence_paths: set[Path] = set()
    for raw_entry in ignored.stdout.split("\0"):
        if not raw_entry:
            continue
        if not raw_entry.startswith("!! "):
            return LifecycleDecision(False, "ignored data inspection failed", True)
        relative = Path(raw_entry.removeprefix("!! ").rstrip("/"))
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            return LifecycleDecision(False, "ignored data inspection failed", True)
        if (
            not REGENERABLE_IGNORED_DIR_NAMES.intersection(relative.parts)
            and relative.suffix not in REGENERABLE_IGNORED_SUFFIXES
        ):
            if relative.parts[0] in ARCHIVABLE_PRESERVED_DATA_NAMES:
                raw_evidence_paths.add(relative)
                continue
            return LifecycleDecision(
                False,
                f"ignored worktree data is not regenerable ({relative})",
            )
    evidence_paths: list[Path] = []
    for candidate in sorted(raw_evidence_paths, key=lambda path: (len(path.parts), str(path))):
        if any(candidate == parent or candidate.is_relative_to(parent) for parent in evidence_paths):
            continue
        evidence_paths.append(candidate)
    head_result = run_git(
        "-C", str(record.path), "rev-parse", "HEAD", check=False
    )
    head = head_result.stdout.strip()
    if head_result.returncode != 0 or not head:
        return LifecycleDecision(False, "worktree HEAD inspection failed", True)
    branch_ref = f"refs/heads/{record.branch}^{{commit}}"
    ref_result = run_git("rev-parse", "--verify", branch_ref, check=False)
    if ref_result.returncode != 0 or ref_result.stdout.strip() != head:
        return LifecycleDecision(False, "branch ref does not preserve worktree HEAD")
    commit_result = run_git("cat-file", "-e", f"{head}^{{commit}}", check=False)
    if commit_result.returncode != 0:
        return LifecycleDecision(False, "worktree commit is unavailable", True)
    if dry_run:
        return LifecycleDecision(True, "would remove materialization")

    archived: list[tuple[Path, Path]] = []
    archive_dir: Path | None = None
    manifest: Path | None = None

    def restore_archived_evidence() -> bool:
        restored = True
        for source, destination in reversed(archived):
            try:
                os.replace(destination, source)
            except OSError:
                restored = False
        if manifest is not None:
            try:
                manifest.unlink(missing_ok=True)
            except OSError:
                restored = False
        if archive_dir is not None:
            for directory in sorted(
                (
                    path
                    for path in archive_dir.rglob("*")
                    if path.is_dir() and not path.is_symlink()
                ),
                key=lambda path: len(path.parts),
                reverse=True,
            ):
                try:
                    directory.rmdir()
                except OSError:
                    restored = False
            try:
                archive_dir.rmdir()
            except OSError:
                restored = False
        return restored

    root = evidence_archive_root or record.path.parent / ".gludd-release-evidence"
    branch_digest = hashlib.sha256(record.branch.encode("utf-8")).hexdigest()[:12]
    archive_dir = root / f"{branch_digest}-{head}"
    manifest = archive_dir / "manifest.json"
    if root.is_symlink() or archive_dir.exists() or archive_dir.is_symlink():
        return LifecycleDecision(False, "release evidence archive is unsafe", True)
    print(
        "phase=cleanup action=archive-evidence status=starting "
        f"path={json.dumps(str(record.path))} "
        f"archive={json.dumps(str(archive_dir))}",
        flush=True,
    )
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise OSError("unsafe evidence archive root")
        archive_dir.mkdir(mode=0o700)
        for evidence_path in evidence_paths:
            source = record.path / evidence_path
            destination = archive_dir / evidence_path
            if source.is_symlink() or not source.exists():
                raise OSError("release evidence changed")
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.replace(source, destination)
            archived.append((source, destination))
        with manifest.open("x", encoding="utf-8") as handle:
            json.dump(
                {
                    "branch": record.branch,
                    "head": head,
                    "original_worktree": str(record.path),
                    "preserved": [str(path) for path in evidence_paths],
                    "rehydrate": {
                        "branch": record.branch,
                        "path": str(record.path),
                    },
                },
                handle,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        restored = restore_archived_evidence()
        reason = (
            "release evidence archive failed"
            if restored
            else "release evidence preserved but archive rollback failed"
        )
        return LifecycleDecision(False, reason, True)

    revalidated_status = run_git(
        "-C",
        str(record.path),
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        check=False,
    )
    revalidated_head = run_git(
        "-C", str(record.path), "rev-parse", "HEAD", check=False
    )
    revalidated_ref = run_git("rev-parse", "--verify", branch_ref, check=False)
    revalidation_failure = ""
    if revalidated_status.returncode != 0:
        revalidation_failure = "status inspection failed"
    elif revalidated_status.stdout:
        revalidation_failure = "worktree status changed"
    elif revalidated_head.returncode != 0:
        revalidation_failure = "HEAD inspection failed"
    elif revalidated_head.stdout.strip() != head:
        revalidation_failure = "HEAD changed"
    elif revalidated_ref.returncode != 0:
        revalidation_failure = "branch inspection failed"
    elif revalidated_ref.stdout.strip() != head:
        revalidation_failure = "branch changed"
    if revalidation_failure:
        restored = restore_archived_evidence()
        reason = (
            f"worktree changed during evidence archival ({revalidation_failure})"
            if restored
            else "release evidence preserved but archive rollback failed"
        )
        return LifecycleDecision(False, reason, True)
    print(
        "phase=cleanup action=archive-evidence status=complete "
        f"archive={json.dumps(str(archive_dir))}",
        flush=True,
    )

    print(
        "phase=cleanup action=remove-worktree status=starting "
        f"path={json.dumps(str(record.path))} branch={json.dumps(record.branch)} "
        f"head={head}",
        flush=True,
    )
    removed = run_git("worktree", "remove", "--", str(record.path), check=False)
    if removed.returncode != 0:
        restored = restore_archived_evidence()
        reason = (
            "git worktree remove refused"
            if restored
            else "release evidence preserved but archive rollback failed"
        )
        return LifecycleDecision(False, reason, True)
    if record.path.exists():
        return LifecycleDecision(False, "worktree materialization still exists", True)
    preserved_ref = run_git("rev-parse", "--verify", branch_ref, check=False)
    preserved_commit = run_git("cat-file", "-e", f"{head}^{{commit}}", check=False)
    if (
        preserved_ref.returncode != 0
        or preserved_ref.stdout.strip() != head
        or preserved_commit.returncode != 0
    ):
        return LifecycleDecision(False, "branch or commit preservation failed", True)
    print(
        "phase=cleanup action=remove-worktree status=complete "
        f"path={json.dumps(str(record.path))} branch={json.dumps(record.branch)} "
        f"head={head}",
        flush=True,
    )
    return LifecycleDecision(True, "materialization removed")


def _remove_tree(path: Path) -> None:
    """Remove one verified generated directory without following symlinks."""
    shutil.rmtree(path)


def _validated_owned_terraform_workspace(
    workspace: Path,
    *,
    resolved_root: Path,
) -> Path | None:
    """Return one exact marker-owned Terraform workspace or fail closed."""
    try:
        workspace_metadata = workspace.lstat()
        resolved_workspace = workspace.resolve(strict=True)
        marker = workspace / AZURE_TERRAFORM_OWNERSHIP_MARKER
        marker_metadata = marker.lstat()
        marker_raw = marker.read_bytes()
        marker_payload = json.loads(marker_raw.decode("utf-8"))
    except (OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    digest = (
        marker_payload.get("operation_digest")
        if isinstance(marker_payload, dict)
        else None
    )
    return (
        resolved_workspace
        if (
            stat.S_ISDIR(workspace_metadata.st_mode)
            and not workspace.is_symlink()
            and resolved_workspace.parent == resolved_root
            and len(resolved_workspace.name) == 24
            and all(character in "0123456789abcdef" for character in resolved_workspace.name)
            and stat.S_ISREG(marker_metadata.st_mode)
            and stat.S_IMODE(marker_metadata.st_mode) == 0o600
            and 0 < len(marker_raw) <= MAX_AZURE_TERRAFORM_MARKER_BYTES
            and isinstance(marker_payload, dict)
            and set(marker_payload) == {"operation_digest", "protocol"}
            and marker_payload.get("protocol") == AZURE_TERRAFORM_OWNERSHIP_PROTOCOL
            and isinstance(digest, str)
            and len(digest) == 64
            and all(character in "0123456789abcdef" for character in digest)
            and digest.startswith(resolved_workspace.name)
        )
        else None
    )


def _validated_terraform_provider_cache(
    workspace: Path,
    *,
    resolved_workspace: Path,
) -> Path | None:
    """Return the exact non-symlink provider cache inside one workspace."""
    terraform_dir = workspace / ".terraform"
    providers = terraform_dir / "providers"
    try:
        terraform_metadata = terraform_dir.lstat()
        provider_metadata = providers.lstat()
        resolved_terraform = terraform_dir.resolve(strict=True)
        resolved_providers = providers.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if (
        not stat.S_ISDIR(terraform_metadata.st_mode)
        or terraform_dir.is_symlink()
        or resolved_terraform != resolved_workspace / ".terraform"
        or not stat.S_ISDIR(provider_metadata.st_mode)
        or providers.is_symlink()
        or resolved_providers != resolved_terraform / "providers"
    ):
        return None
    return providers


def clean_owned_terraform_provider_caches(
    *,
    roots: Sequence[Path] = OWNED_TERRAFORM_CACHE_ROOTS,
    active_process_pids: ActiveProcessPids = clean_ci_shard_scratch._active_process_pids,
    remove_tree: RemoveTree = _remove_tree,
    max_workspaces: int = MAX_OWNED_TERRAFORM_WORKSPACES,
    dry_run: bool = False,
) -> CleanupResult:
    """Remove only idle, marker-owned Azure Terraform provider binaries."""
    removed: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    if isinstance(max_workspaces, bool) or max_workspaces < 1:
        return CleanupResult((), (), ("terraform-provider-cache:invalid-limit",))
    for root in dict.fromkeys(roots):
        if not root.exists() and not root.is_symlink():
            skipped.append(f"{root}:terraform-root-absent")
            continue
        try:
            root_metadata = root.lstat()
            resolved_root = root.resolve(strict=True)
            workspaces = list(islice(root.iterdir(), max_workspaces + 1))
        except (OSError, RuntimeError):
            errors.append(f"{root}:terraform-root-inspection-failed")
            continue
        if (
            not stat.S_ISDIR(root_metadata.st_mode)
            or root.is_symlink()
            or len(workspaces) > max_workspaces
        ):
            errors.append(
                f"{root}:terraform-workspace-limit-exceeded"
                if len(workspaces) > max_workspaces
                else f"{root}:unsafe-terraform-root"
            )
            continue
        for workspace in sorted(workspaces, key=lambda path: path.name):
            resolved_workspace = _validated_owned_terraform_workspace(
                workspace, resolved_root=resolved_root
            )
            if resolved_workspace is None:
                errors.append(f"{workspace}:terraform-ownership-invalid")
                continue
            providers = workspace / ".terraform" / "providers"
            if not providers.exists() and not providers.is_symlink():
                skipped.append(f"{providers}:terraform-provider-cache-absent")
                continue
            validated_providers = _validated_terraform_provider_cache(
                workspace,
                resolved_workspace=resolved_workspace,
            )
            if validated_providers is None:
                errors.append(f"{providers}:unsafe-terraform-provider-cache")
                continue
            try:
                initial_pids = active_process_pids(resolved_workspace)
            except (OSError, ProcessInspectionError, RuntimeError):
                errors.append(f"{workspace}:process-inspection-failed")
                continue
            if initial_pids:
                skipped.append(
                    f"{workspace}:active-pids="
                    + ",".join(str(pid) for pid in initial_pids)
                )
                continue
            revalidated_workspace = _validated_owned_terraform_workspace(
                workspace, resolved_root=resolved_root
            )
            revalidated_providers = (
                _validated_terraform_provider_cache(
                    workspace,
                    resolved_workspace=revalidated_workspace,
                )
                if revalidated_workspace is not None
                else None
            )
            if (
                revalidated_workspace != resolved_workspace
                or revalidated_providers != validated_providers
            ):
                errors.append(f"{providers}:terraform-provider-cache-changed")
                continue
            try:
                final_pids = active_process_pids(resolved_workspace)
            except (OSError, ProcessInspectionError, RuntimeError):
                errors.append(f"{workspace}:process-revalidation-failed")
                continue
            if final_pids:
                skipped.append(
                    f"{workspace}:active-pids=" + ",".join(str(pid) for pid in final_pids)
                )
                continue
            final_workspace = _validated_owned_terraform_workspace(
                workspace, resolved_root=resolved_root
            )
            final_providers = (
                _validated_terraform_provider_cache(
                    workspace,
                    resolved_workspace=final_workspace,
                )
                if final_workspace is not None
                else None
            )
            if final_workspace != resolved_workspace or final_providers != providers:
                errors.append(f"{providers}:terraform-provider-cache-changed")
                continue
            if dry_run:
                skipped.append(f"{providers}:would remove terraform provider cache")
                continue
            print(
                "phase=cleanup action=terraform-provider-cache status=starting "
                f"path={json.dumps(str(providers))}",
                flush=True,
            )
            try:
                remove_tree(providers)
            except OSError:
                errors.append(f"{providers}:removal-failed")
                continue
            if providers.exists() or providers.is_symlink():
                errors.append(f"{providers}:removal-verification-failed")
                continue
            removed.append(str(providers))
            print(
                "phase=cleanup action=terraform-provider-cache status=complete "
                f"path={json.dumps(str(providers))}",
                flush=True,
            )
    return CleanupResult(
        removed=tuple(sorted(removed)),
        skipped=tuple(sorted(skipped)),
        errors=tuple(sorted(errors)),
    )


def relocate_preserved_evidence(
    *,
    leases: Mapping[str, WorkstreamLease],
    approved_roots: Sequence[Path],
    canonical_root: Path,
    max_archives: int = MAX_EVIDENCE_RELOCATIONS,
    replace: Callable[[Path, Path], None] = os.replace,
    dry_run: bool = False,
) -> CleanupResult:
    """Move exact manifest-verified evidence archives out of scratch storage."""
    removed: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    legacy_roots = {
        lease.worktree.parent / ".gludd-release-evidence"
        for lease in leases.values()
        if any(
            lease.worktree == root.resolve()
            or lease.worktree.is_relative_to(root.resolve())
            for root in approved_roots
        )
    }
    if not legacy_roots:
        return CleanupResult((), (), ())
    try:
        resolved_approved = tuple(root.resolve() for root in approved_roots)
        resolved_canonical = canonical_root.resolve()
    except (OSError, RuntimeError):
        return CleanupResult((), (), (f"{canonical_root}:evidence-root-invalid",))
    if canonical_root.is_symlink() or any(
        resolved_canonical == root or resolved_canonical.is_relative_to(root)
        for root in resolved_approved
    ):
        return CleanupResult((), (), (f"{canonical_root}:evidence-root-invalid",))

    attempts = 0
    for legacy_root in sorted(legacy_roots, key=str):
        if not legacy_root.exists() and not legacy_root.is_symlink():
            continue
        try:
            resolved_legacy = legacy_root.resolve(strict=True)
        except (OSError, RuntimeError):
            errors.append(f"{legacy_root}:legacy-evidence-root-invalid")
            continue
        if legacy_root.is_symlink() or not legacy_root.is_dir() or not any(
            resolved_legacy != root and resolved_legacy.is_relative_to(root)
            for root in resolved_approved
        ):
            errors.append(f"{legacy_root}:legacy-evidence-root-invalid")
            continue
        try:
            archives = sorted(legacy_root.iterdir(), key=lambda path: path.name)
        except OSError:
            errors.append(f"{legacy_root}:legacy-evidence-inspection-failed")
            continue
        for archive in archives:
            if attempts >= max_archives:
                skipped.append(f"{archive}:evidence relocation limit reached")
                continue
            attempts += 1
            manifest = archive / "manifest.json"
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                branch = payload["branch"]
                head = payload["head"]
                original = Path(payload["original_worktree"])
                preserved = payload["preserved"]
                lease = leases[branch]
                relative_paths = tuple(Path(item) for item in preserved)
                valid = (
                    archive.is_dir()
                    and not archive.is_symlink()
                    and manifest.is_file()
                    and not manifest.is_symlink()
                    and isinstance(branch, str)
                    and isinstance(head, str)
                    and 40 <= len(head) <= 64
                    and all(character in "0123456789abcdef" for character in head)
                    and original.is_absolute()
                    and original.resolve() == lease.worktree.resolve()
                    and lease.branch == branch
                    and bool(relative_paths)
                    and all(
                        not path.is_absolute()
                        and ".." not in path.parts
                        and path.parts
                        and path.parts[0] in ARCHIVABLE_PRESERVED_DATA_NAMES
                        and (archive / path).exists()
                        for path in relative_paths
                    )
                )
            except (KeyError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
                valid = False
            if not valid:
                errors.append(f"{archive}:evidence-manifest-invalid")
                continue
            target = canonical_root / archive.name
            if target.exists() or target.is_symlink():
                errors.append(f"{archive}:evidence-archive-collision")
                continue
            if dry_run:
                skipped.append(f"{archive}:would relocate preserved evidence")
                continue
            print(
                "phase=cleanup action=relocate-evidence status=starting "
                f"source={json.dumps(str(archive))} "
                f"target={json.dumps(str(target))}",
                flush=True,
            )
            try:
                canonical_root.mkdir(mode=0o700, parents=True, exist_ok=True)
                if canonical_root.is_symlink() or not canonical_root.is_dir():
                    raise OSError("unsafe canonical evidence root")
                replace(archive, target)
            except OSError:
                errors.append(f"{archive}:evidence-relocation-failed")
                continue
            if not target.is_dir() or not (target / "manifest.json").is_file():
                errors.append(f"{archive}:evidence-relocation-verification-failed")
                continue
            removed.append(str(archive))
            print(
                "phase=cleanup action=relocate-evidence status=complete "
                f"target={json.dumps(str(target))}",
                flush=True,
            )
        with contextlib.suppress(OSError):
            legacy_root.rmdir()
    return CleanupResult(
        removed=tuple(sorted(removed)),
        skipped=tuple(sorted(skipped)),
        errors=tuple(sorted(errors)),
    )


def _inside_approved_root(path: Path, roots: Sequence[Path]) -> bool:
    """Accept strict descendants of Gludd's two worktree namespaces only."""
    try:
        candidate = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    for root in roots:
        try:
            canonical_root = root.resolve()
        except (OSError, RuntimeError):
            continue
        if candidate != canonical_root and candidate.is_relative_to(canonical_root):
            return True
    return False


def _matching_refreshed_record(
    candidate: prune_worktrees_safe.WorktreeRecord,
    records: Sequence[prune_worktrees_safe.WorktreeRecord],
) -> prune_worktrees_safe.WorktreeRecord | None:
    """Find an unchanged live registration for a cleanup candidate."""
    return next(
        (
            record
            for record in records
            if record.path == candidate.path
            and record.branch == candidate.branch
            and not record.locked
            and not record.prunable
        ),
        None,
    )


def _invoking_cache_is_safe(worktree: Path, relative_cache: Path) -> bool:
    """Validate every component of one exact invoking-worktree cache path."""
    if relative_cache.is_absolute() or not relative_cache.parts:
        return False
    candidate = worktree / relative_cache
    try:
        for index in range(1, len(relative_cache.parts) + 1):
            component = worktree.joinpath(*relative_cache.parts[:index])
            metadata = component.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or component.is_symlink()
                or component.resolve(strict=True) != component
            ):
                return False
    except (OSError, RuntimeError):
        return False
    return candidate.parent.resolve(strict=True) == candidate.parent


def _clear_terraform_plugin_cache(cache: Path, remove_tree: RemoveTree) -> None:
    """Remove generated provider entries while preserving the tracked marker."""
    children = list(cache.iterdir())
    for child in children:
        metadata = child.lstat()
        if child.name == TERRAFORM_PLUGIN_CACHE_MARKER:
            if child.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                raise OSError("unsafe Terraform plugin-cache marker")
            continue
        if not (
            child.is_symlink()
            or stat.S_ISREG(metadata.st_mode)
            or stat.S_ISDIR(metadata.st_mode)
        ):
            raise OSError("unsafe Terraform plugin-cache entry")

    for child in children:
        if child.name == TERRAFORM_PLUGIN_CACHE_MARKER:
            continue
        metadata = child.lstat()
        if child.is_symlink() or stat.S_ISREG(metadata.st_mode):
            child.unlink()
        elif stat.S_ISDIR(metadata.st_mode):
            remove_tree(child)
        else:
            raise OSError("Terraform plugin-cache entry changed")


def _current_process_ancestry_pids() -> frozenset[int]:
    """Return this cleanup controller's live parent chain, excluding PID 1."""
    try:
        completed = subprocess.run(
            ["/bin/ps", "-axo", "pid=,ppid="],
            check=True,
            capture_output=True,
            text=True,
            timeout=clean_ci_shard_scratch.PROCESS_INSPECTION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProcessInspectionError("controller process inspection failed") from exc

    parents: dict[int, int] = {}
    for line in completed.stdout.splitlines():
        fields = line.strip().split()
        if len(fields) != 2:
            continue
        try:
            pid, parent_pid = (int(field) for field in fields)
        except ValueError:
            continue
        parents[pid] = parent_pid

    current_pid = os.getpid()
    if current_pid not in parents:
        raise ProcessInspectionError("cleanup controller missing from process table")
    ancestry = {current_pid}
    while True:
        next_parent_pid = parents.get(current_pid)
        if next_parent_pid is None or next_parent_pid <= 1:
            break
        if next_parent_pid in ancestry:
            raise ProcessInspectionError("cleanup controller ancestry cycle")
        ancestry.add(next_parent_pid)
        current_pid = next_parent_pid
    return frozenset(ancestry)


def _blocking_invoking_process_pids(
    *,
    worktree: Path,
    active_process_pids: ActiveProcessPids,
    owned_process_pids: OwnedProcessPids,
) -> list[int]:
    """Return worktree users outside the synchronous cleanup controller chain."""
    active_pids = active_process_pids(worktree)
    if not active_pids:
        return []
    owned_pids = owned_process_pids()
    return sorted(set(active_pids).difference(owned_pids))


def clean_invoking_worktree_disposable_caches(
    *,
    worktree: Path,
    records: Sequence[prune_worktrees_safe.WorktreeRecord],
    approved_roots: Sequence[Path],
    approved_worktrees: Sequence[Path] = (),
    cache_paths: Sequence[Path] = INVOKING_DISPOSABLE_CACHE_PATHS,
    refresh_records: RefreshRecords = _registered_worktrees,
    active_process_pids: ActiveProcessPids = clean_ci_shard_scratch._active_process_pids,
    owned_process_pids: OwnedProcessPids = _current_process_ancestry_pids,
    remove_tree: RemoveTree = _remove_tree,
    dry_run: bool = False,
) -> CleanupResult:
    """Remove exact regenerable caches absent an unrelated active process."""
    removed: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    if worktree.is_symlink():
        return CleanupResult((), (), (f"{worktree}:unsafe-invoking-worktree",))
    try:
        path = worktree.resolve(strict=True)
    except (OSError, RuntimeError):
        return CleanupResult((), (), (f"{worktree}:invoking-worktree-unavailable",))
    exact_approved = False
    for approved_worktree in approved_worktrees:
        try:
            exact_approved = exact_approved or approved_worktree.resolve(strict=True) == path
        except (OSError, RuntimeError):
            continue
    if not path.is_dir() or not (
        exact_approved or _inside_approved_root(path, approved_roots)
    ):
        return CleanupResult((), (f"{path}:outside approved namespace",), ())

    selected_cache_paths = tuple(dict.fromkeys(cache_paths))
    if any(
        cache_path not in INVOKING_DISPOSABLE_CACHE_PATHS
        for cache_path in selected_cache_paths
    ):
        return CleanupResult((), (), (f"{path}:invalid-cache-selection",))

    matching_records: list[prune_worktrees_safe.WorktreeRecord] = []
    for record in records:
        try:
            if record.path.resolve(strict=True) == path:
                matching_records.append(record)
        except (OSError, RuntimeError):
            continue
    if len(matching_records) != 1:
        return CleanupResult((), (), (f"{path}:invoking-registration-ambiguous",))
    record = matching_records[0]
    if record.locked:
        return CleanupResult((), (f"{path}:git worktree lock",), ())
    if record.prunable:
        return CleanupResult((), (f"{path}:prunable registration",), ())
    if record.branch is None:
        return CleanupResult((), (f"{path}:detached worktree",), ())

    try:
        initial_pids = _blocking_invoking_process_pids(
            worktree=path,
            active_process_pids=active_process_pids,
            owned_process_pids=owned_process_pids,
        )
    except ProcessInspectionError:
        return CleanupResult((), (), (f"{path}:process-inspection-failed",))
    if initial_pids:
        return CleanupResult(
            (),
            (f"{path}:active-pids={','.join(str(pid) for pid in initial_pids)}",),
            (),
        )

    for relative_cache in selected_cache_paths:
        cache = path / relative_cache
        if cache.is_symlink():
            errors.append(f"{cache}:unsafe-cache")
            continue
        if not cache.exists():
            continue
        if not _invoking_cache_is_safe(path, relative_cache):
            errors.append(f"{cache}:unsafe-cache")
            continue
        try:
            refreshed = _matching_refreshed_record(record, refresh_records())
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            errors.append(f"{cache}:ownership-revalidation-failed")
            continue
        if refreshed is None:
            skipped.append(f"{cache}:registration changed")
            continue
        try:
            refreshed_pids = _blocking_invoking_process_pids(
                worktree=path,
                active_process_pids=active_process_pids,
                owned_process_pids=owned_process_pids,
            )
        except ProcessInspectionError:
            errors.append(f"{cache}:process-revalidation-failed")
            continue
        if refreshed_pids:
            skipped.append(
                f"{cache}:active-pids={','.join(str(pid) for pid in refreshed_pids)}"
            )
            continue
        if cache.is_symlink() or not cache.is_dir():
            errors.append(f"{cache}:unsafe-cache")
            continue
        if dry_run:
            skipped.append(f"{cache}:would remove invoking cache")
            print(
                "phase=cleanup action=invoking-cache status=dry-run "
                f"path={json.dumps(str(cache))}",
                flush=True,
            )
            continue
        print(
            "phase=cleanup action=invoking-cache status=starting "
            f"path={json.dumps(str(cache))}",
            flush=True,
        )
        try:
            if relative_cache == TERRAFORM_PLUGIN_CACHE_PATH:
                _clear_terraform_plugin_cache(cache, remove_tree)
            else:
                remove_tree(cache)
        except OSError:
            errors.append(f"{cache}:removal-failed")
            continue
        if relative_cache == TERRAFORM_PLUGIN_CACHE_PATH:
            try:
                remaining_entries = tuple(cache.iterdir())
            except OSError:
                remaining_entries = (cache,)
            removal_failed = any(
                child.name != TERRAFORM_PLUGIN_CACHE_MARKER
                or child.is_symlink()
                or not child.is_file()
                for child in remaining_entries
            )
        else:
            removal_failed = cache.exists() or cache.is_symlink()
        if removal_failed:
            errors.append(f"{cache}:removal-verification-failed")
            continue
        removed.append(str(cache))
        print(
            "phase=cleanup action=invoking-cache status=complete "
            f"path={json.dumps(str(cache))}",
            flush=True,
        )

    return CleanupResult(tuple(removed), tuple(skipped), tuple(errors))


def clean_inactive_worktree_caches(
    *,
    records: Sequence[prune_worktrees_safe.WorktreeRecord],
    approved_roots: Sequence[Path],
    protected_paths: frozenset[Path],
    active_branches: ActiveBranches,
    refresh_records: RefreshRecords,
    active_workstream_leases: ActiveWorkstreamLeases | None = None,
    lifecycle_proof: LifecycleProof | None = None,
    active_process_pids: ActiveProcessPids = clean_ci_shard_scratch._active_process_pids,
    remove_tree: RemoveTree = _remove_tree,
    remove_materialization: RemoveMaterialization | None = None,
    max_materializations: int = MAX_WORKTREE_MATERIALIZATIONS,
    dry_run: bool = False,
) -> CleanupResult:
    """Remove allowlisted caches only from proven inactive Gludd worktrees."""
    removed: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    materialization_attempts = 0
    try:
        initial_active = active_branches()
        initial_leases = (
            dict(active_workstream_leases())
            if active_workstream_leases is not None
            else {}
        )
    except (OSError, RuntimeError, ValueError):
        return CleanupResult((), (), ("active-workstream-registry:inspection-failed",))

    def lifecycle_decision(
        candidate: prune_worktrees_safe.WorktreeRecord,
        leases: Mapping[str, WorkstreamLease],
    ) -> LifecycleDecision:
        branch = candidate.branch
        if branch is None or lifecycle_proof is None:
            return LifecycleDecision(False, "active logical workstream")
        lease = leases.get(branch)
        if lease is None:
            return LifecycleDecision(False, "active workstream lease missing", True)
        try:
            matches_path = lease.worktree.resolve() == candidate.path.resolve(strict=True)
        except (OSError, RuntimeError):
            return LifecycleDecision(False, "active workstream path unavailable", True)
        if not matches_path:
            return LifecycleDecision(False, "active workstream path mismatch", True)
        return lifecycle_proof(candidate, lease)

    for record in sorted(records, key=lambda item: str(item.path)):
        path = record.path
        completion_lease: WorkstreamLease | None = None
        if not _inside_approved_root(path, approved_roots):
            skipped.append(f"{path}:outside approved namespace")
            continue
        if record.prunable:
            skipped.append(f"{path}:prunable registration")
            continue
        if record.branch is None:
            skipped.append(f"{path}:detached worktree")
            continue
        decision = prune_worktrees_safe.pruning_decision(
            record,
            active_branches=initial_active,
            protected_paths=protected_paths,
        )
        if decision.action == "protect":
            if decision.reason != "active logical workstream":
                skipped.append(f"{path}:{decision.reason}")
                continue
            lifecycle = lifecycle_decision(record, initial_leases)
            if lifecycle.error:
                errors.append(f"{path}:{lifecycle.reason}")
                continue
            if not lifecycle.reclaimable:
                skipped.append(f"{path}:{lifecycle.reason}")
                continue
            if not lifecycle.cache_only:
                completion_lease = initial_leases.get(record.branch)

        try:
            pids = active_process_pids(path)
        except ProcessInspectionError:
            errors.append(f"{path}:process-inspection-failed")
            continue
        if pids:
            skipped.append(f"{path}:active-pids={','.join(str(pid) for pid in pids)}")
            continue

        try:
            refreshed_records = refresh_records()
            refreshed_active = active_branches()
            refreshed_leases = (
                dict(active_workstream_leases())
                if active_workstream_leases is not None
                else {}
            )
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            errors.append(f"{path}:ownership-revalidation-failed")
            continue
        refreshed = _matching_refreshed_record(record, refreshed_records)
        if refreshed is None:
            skipped.append(f"{path}:registration changed")
            continue
        if record.branch in refreshed_active:
            lifecycle = lifecycle_decision(refreshed, refreshed_leases)
            if lifecycle.error:
                errors.append(f"{path}:{lifecycle.reason}")
                continue
            if not lifecycle.reclaimable:
                reason = (
                    "became active logical workstream"
                    if active_workstream_leases is None
                    else lifecycle.reason
                )
                skipped.append(f"{path}:{reason}")
                continue
            if (
                completion_lease is not None
                and refreshed_leases.get(record.branch) != completion_lease
            ):
                skipped.append(f"{path}:completion lease changed")
                continue
            if completion_lease is not None and lifecycle.cache_only:
                completion_lease = None
        elif completion_lease is not None:
            skipped.append(f"{path}:completion lease changed")
            continue
        try:
            refreshed_pids = active_process_pids(path)
        except ProcessInspectionError:
            errors.append(f"{path}:process-revalidation-failed")
            continue
        if refreshed_pids:
            skipped.append(
                f"{path}:active-pids={','.join(str(pid) for pid in refreshed_pids)}"
            )
            continue

        def tool_environment_reclaimable(
            cache: Path,
            candidate_record: prune_worktrees_safe.WorktreeRecord = record,
            candidate_path: Path = path,
            required_lease: WorkstreamLease | None = completion_lease,
        ) -> bool:
            """Repeat the full completion proof immediately before env removal."""
            if required_lease is None:
                skipped.append(
                    f"{cache}:completion proof required for tool environment"
                )
                return False
            try:
                environment_records = refresh_records()
                environment_active = active_branches()
                environment_leases = (
                    dict(active_workstream_leases())
                    if active_workstream_leases is not None
                    else {}
                )
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                errors.append(f"{cache}:tool-environment-revalidation-failed")
                return False
            environment_record = _matching_refreshed_record(
                candidate_record, environment_records
            )
            if environment_record is None:
                skipped.append(f"{cache}:tool environment registration changed")
                return False
            if (
                candidate_record.branch not in environment_active
                or environment_leases.get(candidate_record.branch) != required_lease
            ):
                skipped.append(f"{cache}:tool environment lease changed")
                return False
            environment_lifecycle = lifecycle_decision(
                environment_record, environment_leases
            )
            if environment_lifecycle.error:
                errors.append(f"{cache}:{environment_lifecycle.reason}")
                return False
            if not environment_lifecycle.reclaimable:
                skipped.append(f"{cache}:{environment_lifecycle.reason}")
                return False
            if environment_lifecycle.cache_only:
                skipped.append(
                    f"{cache}:tool environment completion proof downgraded"
                )
                return False
            try:
                environment_pids = active_process_pids(candidate_path)
            except ProcessInspectionError:
                errors.append(
                    f"{cache}:tool-environment-process-inspection-failed"
                )
                return False
            if environment_pids:
                skipped.append(
                    f"{cache}:active-pids="
                    f"{','.join(str(pid) for pid in environment_pids)}"
                )
                return False
            return True

        for cache_name in GENERATED_CACHE_DIR_NAMES:
            cache = path / cache_name
            if cache.is_symlink():
                errors.append(f"{cache}:unsafe-cache")
                continue
            if not cache.exists():
                continue
            if not cache.is_dir() or cache.parent.resolve() != path.resolve():
                errors.append(f"{cache}:unsafe-cache")
                continue
            if (
                cache_name in TOOL_ENVIRONMENT_DIR_NAMES
                and not tool_environment_reclaimable(cache)
            ):
                continue
            if dry_run:
                skipped.append(f"{cache}:would remove cache")
                print(
                    "phase=cleanup action=remove status=dry-run "
                    f"path={json.dumps(str(cache))}",
                    flush=True,
                )
                continue
            print(
                "phase=cleanup action=remove status=starting "
                f"path={json.dumps(str(cache))}",
                flush=True,
            )
            try:
                remove_tree(cache)
            except OSError:
                errors.append(f"{cache}:removal-failed")
                continue
            removed.append(str(cache))
            print(
                "phase=cleanup action=remove status=complete "
                f"path={json.dumps(str(cache))}",
                flush=True,
            )

        if remove_materialization is None:
            continue
        if materialization_attempts >= max_materializations:
            skipped.append(f"{path}:materialization limit reached")
            continue
        if completion_lease is None:
            skipped.append(f"{path}:completion lease proof unavailable")
            continue

        try:
            final_records = refresh_records()
            final_active = active_branches()
            final_leases = (
                dict(active_workstream_leases())
                if active_workstream_leases is not None
                else {}
            )
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            errors.append(f"{path}:materialization-revalidation-failed")
            continue
        final_record = _matching_refreshed_record(record, final_records)
        if final_record is None:
            skipped.append(f"{path}:materialization registration changed")
            continue
        if (
            record.branch not in final_active
            or final_leases.get(record.branch) != completion_lease
        ):
            skipped.append(f"{path}:completion lease changed")
            continue
        final_lifecycle = lifecycle_decision(final_record, final_leases)
        if final_lifecycle.error:
            errors.append(f"{path}:{final_lifecycle.reason}")
            continue
        if not final_lifecycle.reclaimable:
            skipped.append(f"{path}:{final_lifecycle.reason}")
            continue
        if final_lifecycle.cache_only:
            skipped.append(f"{path}:completion proof downgraded")
            continue
        try:
            final_pids = active_process_pids(path)
        except ProcessInspectionError:
            errors.append(f"{path}:materialization-process-inspection-failed")
            continue
        if final_pids:
            skipped.append(
                f"{path}:active-pids={','.join(str(pid) for pid in final_pids)}"
            )
            continue

        materialization_attempts += 1
        outcome = remove_materialization(final_record, dry_run)
        if outcome.error:
            errors.append(f"{path}:{outcome.reason}")
            continue
        if not outcome.reclaimable:
            skipped.append(f"{path}:{outcome.reason}")
            continue
        if dry_run:
            skipped.append(f"{path}:would remove materialization")
        else:
            removed.append(str(path))

    return CleanupResult(
        removed=tuple(sorted(removed)),
        skipped=tuple(sorted(skipped)),
        errors=tuple(sorted(errors)),
    )


def _active_uv_process_pids() -> list[int]:
    """Return every visible uv process so shared-cache pruning stays conservative."""
    try:
        completed = subprocess.run(
            ["/bin/ps", "-axo", "pid=,command="],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProcessInspectionError("uv process inspection failed") from exc
    pids: set[int] = set()
    for line in completed.stdout.splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2:
            continue
        try:
            pid = int(fields[0])
        except ValueError:
            continue
        command = fields[1].split(maxsplit=1)[0]
        if pid != os.getpid() and Path(command).name == "uv":
            pids.add(pid)
    return sorted(pids)


def _run_uv_cache_command(cache_root: Path, operation: str) -> bool:
    """Run one uv cache operation with its lock and a bounded timeout."""
    executable = shutil.which("uv")
    if executable is None:
        return False
    environment = os.environ.copy()
    environment["UV_CACHE_DIR"] = str(cache_root)
    environment["UV_LOCK_TIMEOUT"] = "15"
    try:
        completed = subprocess.run(
            [executable, "cache", operation],
            check=False,
            env=environment,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _run_uv_cache_prune(cache_root: Path) -> bool:
    """Prune unused entries through uv's cache lock."""
    return _run_uv_cache_command(cache_root, "prune")


def _run_uv_cache_clean(cache_root: Path) -> bool:
    """Clear remaining regenerable entries through uv's cache lock."""
    return _run_uv_cache_command(cache_root, "clean")


def prune_shared_uv_cache(
    *,
    cache_root: Path = SHARED_UV_CACHE_ROOT,
    approved_cache_root: Path = SHARED_UV_CACHE_ROOT,
    active_uv_pids: Callable[[], list[int]] = _active_uv_process_pids,
    run_prune: Callable[[Path], bool] = _run_uv_cache_prune,
    run_clean: Callable[[Path], bool] = _run_uv_cache_clean,
    dry_run: bool = False,
    missing_is_clean: bool = False,
) -> CleanupResult:
    """Prune the exact shared uv cache only after two system-wide idle checks."""
    try:
        approved = approved_cache_root.resolve()
    except (OSError, RuntimeError):
        return CleanupResult((), (), (f"{cache_root}:uv-cache-inspection-failed",))
    try:
        candidate = cache_root.resolve(strict=True)
    except FileNotFoundError:
        try:
            absent_candidate = cache_root.resolve()
        except (OSError, RuntimeError):
            return CleanupResult(
                (), (), (f"{cache_root}:uv-cache-inspection-failed",)
            )
        if (
            missing_is_clean
            and absent_candidate == approved
            and not cache_root.is_symlink()
        ):
            return CleanupResult((), (f"{cache_root}:uv-cache-absent",), ())
        return CleanupResult((), (), (f"{cache_root}:uv-cache-inspection-failed",))
    except (OSError, RuntimeError):
        return CleanupResult((), (), (f"{cache_root}:uv-cache-inspection-failed",))
    if (
        candidate != approved
        or cache_root.is_symlink()
        or not cache_root.is_dir()
    ):
        return CleanupResult((), (), (f"{cache_root}:unsafe-uv-cache",))
    try:
        first_pids = active_uv_pids()
    except ProcessInspectionError:
        return CleanupResult((), (), (f"{cache_root}:uv-process-inspection-failed",))
    if first_pids:
        return CleanupResult(
            (),
            (f"{cache_root}:active-uv-pids={','.join(str(pid) for pid in first_pids)}",),
            (),
        )
    try:
        refreshed_pids = active_uv_pids()
    except ProcessInspectionError:
        return CleanupResult((), (), (f"{cache_root}:uv-process-revalidation-failed",))
    if refreshed_pids:
        return CleanupResult(
            (),
            (
                f"{cache_root}:active-uv-pids="
                f"{','.join(str(pid) for pid in refreshed_pids)}",
            ),
            (),
        )
    if dry_run:
        return CleanupResult((), (f"{cache_root}:would prune shared uv cache",), ())
    print(
        "phase=cleanup action=uv-cache-prune status=starting "
        f"path={json.dumps(str(cache_root))}",
        flush=True,
    )
    if not run_prune(cache_root):
        return CleanupResult((), (), (f"{cache_root}:uv-cache-prune-failed",))
    try:
        final_pids = active_uv_pids()
    except ProcessInspectionError:
        return CleanupResult((), (), (f"{cache_root}:uv-process-final-check-failed",))
    if final_pids:
        return CleanupResult(
            (str(cache_root),),
            (f"{cache_root}:active-uv-pids={','.join(str(pid) for pid in final_pids)}",),
            (),
        )
    print(
        "phase=cleanup action=uv-cache-clean status=starting "
        f"path={json.dumps(str(cache_root))}",
        flush=True,
    )
    if not run_clean(cache_root):
        return CleanupResult((), (), (f"{cache_root}:uv-cache-clean-failed",))
    print(
        "phase=cleanup action=uv-cache-clean status=complete "
        f"path={json.dumps(str(cache_root))}",
        flush=True,
    )
    return CleanupResult((str(cache_root),), (), ())


def _combine_cleanup_results(*results: CleanupResult) -> CleanupResult:
    return CleanupResult(
        removed=tuple(item for result in results for item in result.removed),
        skipped=tuple(item for result in results for item in result.skipped),
        errors=tuple(item for result in results for item in result.errors),
    )


def _expected_generated_scratch_refusal(item: str) -> bool:
    """Return whether a generated-scratch skip is an intentional protection."""
    reason = item.rsplit(":", maxsplit=1)[-1]
    return reason in {
        "lease-marker",
        "recent",
        "symlink",
        "unsupported-generated-file",
        "unsupported-file-type",
    } or reason.startswith(("active-pids=", "active-socket-pids="))


def clean_stale_generated_scratch(
    *,
    dry_run: bool = False,
    cleanup_runner: Callable[..., dict[str, list[str]]] | None = None,
) -> CleanupResult:
    """Reclaim only stale, inactive generated test scratch under `/tmp`."""
    print(
        "phase=cleanup action=stale-generated-scratch status=starting",
        flush=True,
    )
    runner = cleanup_runner or clean_ci_shard_scratch.clean_ci_shard_scratch
    try:
        raw_result = runner(dry_run=dry_run)
        removed = raw_result["removed"]
        raw_skipped = raw_result["skipped"]
        if (
            not isinstance(removed, list)
            or not isinstance(raw_skipped, list)
            or not all(isinstance(item, str) for item in (*removed, *raw_skipped))
        ):
            raise ValueError("generated scratch cleanup returned invalid evidence")
    except (
        KeyError,
        OSError,
        ProcessInspectionError,
        RuntimeError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
    ):
        print(
            "phase=cleanup action=stale-generated-scratch status=failed",
            file=sys.stderr,
            flush=True,
        )
        return CleanupResult((), (), ("stale-generated-scratch:inspection-failed",))

    skipped = tuple(
        item for item in raw_skipped if _expected_generated_scratch_refusal(item)
    )
    errors = tuple(
        item for item in raw_skipped if not _expected_generated_scratch_refusal(item)
    )
    print(
        "phase=cleanup action=stale-generated-scratch status=complete "
        f"removed={len(removed)} skipped={len(skipped)} errors={len(errors)}",
        flush=True,
    )
    return CleanupResult(tuple(removed), skipped, errors)


def _automatic_cleanup(
    *,
    dry_run: bool = False,
    receipt_grace_seconds: int = DEFAULT_COMMIT_RECEIPT_GRACE_SECONDS,
) -> CleanupResult:
    """Discover candidates from Git plus the explicit logical ownership registry."""
    try:
        records = _registered_worktrees()
        current = Path.cwd().resolve(strict=True)
        main = records[0].path.resolve(strict=True)
        registry = workstream_registry.WorkstreamRegistry(
            workstream_registry.default_registry_path()
        )
        integration_points = _integration_points(records)
        evidence_archive_root = _canonical_evidence_archive_root()
    except (
        DiskInspectionError,
        OSError,
        RuntimeError,
        ValueError,
        subprocess.SubprocessError,
    ):
        relocation_result = CleanupResult((), (), ())
        invoking_result = CleanupResult((), (), ())
        main_cache_result = CleanupResult((), (), ())
        worktree_result = CleanupResult(
            (), (), ("worktree-discovery:inspection-failed",)
        )
    else:

        def leases() -> dict[str, WorkstreamLease]:
            return _active_workstream_leases(registry)

        def lifecycle(
            record: prune_worktrees_safe.WorktreeRecord,
            lease: WorkstreamLease,
        ) -> LifecycleDecision:
            return _completion_proof(
                record,
                lease,
                integration_points=integration_points,
                now_epoch=int(time.time()),
                receipt_grace_seconds=receipt_grace_seconds,
            )

        roots = (main / ".claude/worktrees", DEFAULT_TMP_WORKTREE_ROOT)
        invoking_result = clean_invoking_worktree_disposable_caches(
            worktree=current,
            records=records,
            approved_roots=roots,
            refresh_records=_registered_worktrees,
            dry_run=dry_run,
        )
        main_cache_result = (
            CleanupResult((), (), ())
            if main == current
            else clean_invoking_worktree_disposable_caches(
                worktree=main,
                records=records,
                approved_roots=(),
                approved_worktrees=(main,),
                cache_paths=(TERRAFORM_PLUGIN_CACHE_PATH,),
                refresh_records=_registered_worktrees,
                dry_run=dry_run,
            )
        )
        try:
            relocation_result = relocate_preserved_evidence(
                leases=leases(),
                approved_roots=roots,
                canonical_root=evidence_archive_root,
                dry_run=dry_run,
            )
        except (OSError, RuntimeError, ValueError):
            relocation_result = CleanupResult(
                (), (), ("release-evidence-relocation:inspection-failed",)
            )
        worktree_result = clean_inactive_worktree_caches(
            records=records,
            approved_roots=roots,
            protected_paths=frozenset({main, current}),
            active_branches=lambda: frozenset(leases()),
            active_workstream_leases=leases,
            lifecycle_proof=lifecycle,
            refresh_records=_registered_worktrees,
            remove_materialization=lambda record, is_dry_run: (
                _remove_worktree_materialization(
                    record,
                    is_dry_run,
                    evidence_archive_root=evidence_archive_root,
                )
            ),
            max_materializations=MAX_WORKTREE_MATERIALIZATIONS,
            dry_run=dry_run,
        )

    scratch_result = clean_stale_generated_scratch(dry_run=dry_run)
    uv_result = prune_shared_uv_cache(
        dry_run=dry_run,
        missing_is_clean=True,
    )
    terraform_provider_result = clean_owned_terraform_provider_caches(
        dry_run=dry_run,
    )
    return _combine_cleanup_results(
        relocation_result,
        invoking_result,
        main_cache_result,
        scratch_result,
        worktree_result,
        uv_result,
        terraform_provider_result,
    )


def _snapshot_text(snapshot: DiskSnapshot) -> str:
    return (
        f"scratch_mb={snapshot.scratch_mb:.1f} "
        f"scratch_limit_mb={check_disk_usage.GLUDD_TMP_LIMIT_MB} "
        f"disk_pct={snapshot.disk_pct:.1f} "
        f"disk_limit_pct={check_disk_usage.DISK_USAGE_PCT_LIMIT}"
    )


def _usage_decreased(before: DiskSnapshot, after: DiskSnapshot) -> bool:
    """Return whether either canonical measurement demonstrably decreased."""
    return after.scratch_mb < before.scratch_mb or after.disk_pct < before.disk_pct


def run_preflight(
    *,
    inspect_usage: InspectUsage = inspect_usage,
    cleanup: Cleanup | None = None,
    max_cleanup_passes: int = MAX_PREFLIGHT_CLEANUP_PASSES,
) -> int:
    """Clean while measured pressure falls, within a strict pass bound."""
    print("phase=inspect status=starting", flush=True)
    try:
        before = inspect_usage()
    except (DiskInspectionError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"phase=inspect status=failed reason={exc}", file=sys.stderr, flush=True)
        return 1
    if not before.is_high:
        print(f"phase=inspect status=healthy {_snapshot_text(before)}", flush=True)
        return 0

    print(f"phase=inspect status=pressure {_snapshot_text(before)}", flush=True)
    if max_cleanup_passes < 1:
        print(
            "phase=cleanup status=failed reason=invalid-pass-limit",
            file=sys.stderr,
            flush=True,
        )
        return 1

    cleanup_action = cleanup or _automatic_cleanup
    current = before
    for pass_number in range(1, max_cleanup_passes + 1):
        print(f"phase=cleanup status=starting pass={pass_number}", flush=True)
        try:
            cleanup_result = cleanup_action()
        except (
            DiskInspectionError,
            OSError,
            RuntimeError,
            ValueError,
            subprocess.SubprocessError,
        ) as exc:
            print(
                f"phase=cleanup status=failed pass={pass_number} reason={exc}",
                file=sys.stderr,
                flush=True,
            )
            return 1
        for item in cleanup_result.skipped[:MAX_CLEANUP_DETAILS_PER_KIND]:
            print(
                "phase=cleanup action=skip "
                f"pass={pass_number} detail={json.dumps(item)}",
                flush=True,
            )
        skipped_omitted = len(cleanup_result.skipped) - MAX_CLEANUP_DETAILS_PER_KIND
        if skipped_omitted > 0:
            print(
                "phase=cleanup action=skip-summary "
                f"pass={pass_number} shown={MAX_CLEANUP_DETAILS_PER_KIND} "
                f"omitted={skipped_omitted}",
                flush=True,
            )
        for item in cleanup_result.errors[:MAX_CLEANUP_DETAILS_PER_KIND]:
            print(
                "phase=cleanup action=refuse "
                f"pass={pass_number} detail={json.dumps(item)}",
                file=sys.stderr,
                flush=True,
            )
        errors_omitted = len(cleanup_result.errors) - MAX_CLEANUP_DETAILS_PER_KIND
        if errors_omitted > 0:
            print(
                "phase=cleanup action=refuse-summary "
                f"pass={pass_number} shown={MAX_CLEANUP_DETAILS_PER_KIND} "
                f"omitted={errors_omitted}",
                file=sys.stderr,
                flush=True,
            )
        print(
            "phase=cleanup status=complete "
            f"pass={pass_number} "
            f"removed={len(cleanup_result.removed)} "
            f"skipped={len(cleanup_result.skipped)} "
            f"errors={len(cleanup_result.errors)}",
            flush=True,
        )

        print(f"phase=recheck status=starting pass={pass_number}", flush=True)
        try:
            after = inspect_usage()
        except (
            DiskInspectionError,
            OSError,
            RuntimeError,
            subprocess.SubprocessError,
        ) as exc:
            print(
                f"phase=recheck status=failed pass={pass_number} reason={exc}",
                file=sys.stderr,
                flush=True,
            )
            return 1
        if cleanup_result.errors:
            print(
                "phase=recheck status=failed "
                f"pass={pass_number} reason=cleanup-errors "
                f"{_snapshot_text(after)} "
                f"cleanup_errors={len(cleanup_result.errors)}",
                file=sys.stderr,
                flush=True,
            )
            return 1
        if not after.is_high:
            print(
                f"phase=recheck status=healthy pass={pass_number} "
                f"{_snapshot_text(after)}",
                flush=True,
            )
            return 0
        if not _usage_decreased(current, after):
            print(
                "phase=recheck status=failed "
                f"pass={pass_number} reason=no-progress {_snapshot_text(after)}",
                file=sys.stderr,
                flush=True,
            )
            return 1
        if pass_number == max_cleanup_passes:
            print(
                "phase=recheck status=failed "
                f"pass={pass_number} reason=pass-limit {_snapshot_text(after)}",
                file=sys.stderr,
                flush=True,
            )
            return 1
        print(
            f"phase=recheck status=pressure pass={pass_number} "
            f"{_snapshot_text(after)}",
            flush=True,
        )
        current = after
    return 1


def _receipt_grace_argument(raw_value: str) -> int:
    """Parse a configurable grace while enforcing the safety floor."""
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("receipt grace must be an integer") from exc
    if value < MIN_COMMIT_RECEIPT_GRACE_SECONDS:
        raise argparse.ArgumentTypeError(
            "receipt grace must be at least "
            f"{MIN_COMMIT_RECEIPT_GRACE_SECONDS} seconds"
        )
    return value


def main(argv: Sequence[str] | None = None) -> int:
    """Run the non-interactive automatic disk cleanup preflight."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report eligible cleanup without mutating caches or worktrees",
    )
    parser.add_argument(
        "--receipt-grace-seconds",
        type=_receipt_grace_argument,
        default=DEFAULT_COMMIT_RECEIPT_GRACE_SECONDS,
        help=(
            "minimum unchanged exact-head commit-receipt age before a clean "
            "materialization may be retired"
        ),
    )
    arguments = parser.parse_args(() if argv is None else argv)
    if arguments.dry_run:
        return run_preflight(
            cleanup=lambda: _automatic_cleanup(
                dry_run=True,
                receipt_grace_seconds=arguments.receipt_grace_seconds,
            )
        )
    if arguments.receipt_grace_seconds != DEFAULT_COMMIT_RECEIPT_GRACE_SECONDS:
        return run_preflight(
            cleanup=lambda: _automatic_cleanup(
                receipt_grace_seconds=arguments.receipt_grace_seconds
            )
        )
    return run_preflight()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
