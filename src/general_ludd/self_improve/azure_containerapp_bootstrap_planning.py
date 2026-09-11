"""Pure ownership and topology planning for configured Azure candidates."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path

from general_ludd.git_automation.locking import git_common_directory
from general_ludd.infra.azure_containerapp_gpu import ModelServingRequirement
from general_ludd.infra.azure_containerapp_topology import (
    AzureFleetConstraints,
    AzureRunnerDemand,
    AzureRunnerTopologyPlan,
    TopologyTrace,
    plan_azure_runner_topology,
)
from general_ludd.self_improve.azure_containerapp_bootstrap_settings import (
    AzureContainerAppBootstrapSettings,
)

_PROTOCOL = "gludd-configured-azure-containerapp-bootstrap-v1"
_LEGACY_ENVIRONMENT_PROTOCOL = "gludd-owned-azure-containerapp-environment-v1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_LINKED_WORKTREES = 128
_MAX_GITDIR_BYTES = 4_096


def _owner_digest(
    identity_root: Path,
    settings: AzureContainerAppBootstrapSettings,
) -> str:
    """Hash one explicit project identity using the stable ownership protocol."""
    encoded = json.dumps(
        {
            "environment": settings.environment_name,
            "project_identity": hashlib.sha256(
                str(identity_root).encode("utf-8", errors="surrogatepass")
            ).hexdigest(),
            "protocol": _PROTOCOL,
            "resource_group": settings.resource_group,
            "subscription_id": settings.subscription_id,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def azure_bootstrap_owner_digest(
    repo_root: Path,
    settings: AzureContainerAppBootstrapSettings,
) -> str:
    """Bind environment ownership to one canonical project and Azure scope."""
    common_directory = git_common_directory(str(repo_root))
    identity_root = (
        Path(common_directory).parent if common_directory is not None else repo_root
    )
    return _owner_digest(identity_root, settings)


def _legacy_environment_owner_digest(
    identity_root: Path,
    settings: AzureContainerAppBootstrapSettings,
    privacy_policy_digest: str,
) -> str:
    """Reproduce the retired standalone live-proof owner identity exactly."""
    environment_id = (
        f"/subscriptions/{settings.subscription_id}/resourceGroups/"
        f"{settings.resource_group}/providers/Microsoft.App/managedEnvironments/"
        f"{settings.environment_name}"
    )
    encoded = json.dumps(
        {
            "environment_id": environment_id.casefold(),
            "policy_digest": privacy_policy_digest,
            "project_root": str(identity_root.resolve(strict=True)),
            "protocol": _LEGACY_ENVIRONMENT_PROTOCOL,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _registered_project_roots(repo_root: Path) -> tuple[Path, ...]:
    """Return bounded verified roots sharing this checkout's Git common dir."""
    canonical_root = repo_root.resolve(strict=True)
    common_directory = git_common_directory(str(canonical_root))
    if common_directory is None:
        return (canonical_root,)
    common = Path(common_directory)
    roots = {canonical_root, common.parent.resolve(strict=True)}
    worktrees = common / "worktrees"
    try:
        entries = sorted(worktrees.iterdir(), key=lambda path: path.name)
    except OSError:
        return tuple(sorted(roots, key=str))
    for entry in entries[:_MAX_LINKED_WORKTREES]:
        try:
            raw = (entry / "gitdir").read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if len(raw.encode("utf-8")) > _MAX_GITDIR_BYTES:
            continue
        lines = raw.splitlines()
        if len(lines) != 1:
            continue
        git_file = Path(lines[0])
        if not git_file.is_absolute() or git_file.name != ".git":
            continue
        try:
            candidate = git_file.parent.resolve(strict=True)
        except OSError:
            continue
        if git_common_directory(str(candidate)) == common_directory:
            roots.add(candidate)
    return tuple(sorted(roots, key=str))


def azure_bootstrap_legacy_owner_digests(
    repo_root: Path,
    settings: AzureContainerAppBootstrapSettings,
    *,
    privacy_policy_digest: str | None = None,
) -> tuple[str, ...]:
    """Return exact identities emitted by Gludd's two retired owner protocols."""
    if privacy_policy_digest is not None and (
        not isinstance(privacy_policy_digest, str)
        or _DIGEST.fullmatch(privacy_policy_digest) is None
    ):
        raise ValueError("privacy_policy_digest must be a SHA-256 digest")
    current = azure_bootstrap_owner_digest(repo_root, settings)
    roots = _registered_project_roots(repo_root)
    candidates = {_owner_digest(root, settings) for root in roots}
    if privacy_policy_digest is not None:
        candidates.update(
            _legacy_environment_owner_digest(root, settings, privacy_policy_digest)
            for root in roots
        )
    candidates.discard(current)
    return tuple(sorted(candidates))


def plan_azure_bootstrap_topology(
    settings: AzureContainerAppBootstrapSettings,
    requirement: ModelServingRequirement,
    progress_sink: Callable[[str], None],
) -> AzureRunnerTopologyPlan:
    """Size one bounded runner topology from observed profile capacities."""
    max_profile_replicas = max(
        capacity.max_replicas for capacity in settings.profile_capacities
    )
    constraints = AzureFleetConstraints(
        profile_capacities=settings.profile_capacities,
        max_apps=1,
        max_total_replicas=max_profile_replicas,
        max_replicas_per_app=max_profile_replicas,
        max_hourly_cost_microusd=settings.max_hourly_cost_microusd,
        ttl_minutes=settings.ttl_minutes,
    )

    def trace(event: TopologyTrace) -> None:
        progress_sink(
            "SELF_IMPROVE_AZURE_BOOTSTRAP "
            f"phase={event.phase} task_count={event.task_count} "
            f"batch_count={event.batch_count} app_count={event.app_count} "
            f"total_max_replicas={event.total_max_replicas} secret_output=false"
        )

    return plan_azure_runner_topology(
        (
            AzureRunnerDemand(
                task_id="self-improve",
                requirement=requirement,
                peak_concurrency=settings.peak_concurrency,
                per_replica_concurrency=settings.per_replica_concurrency,
            ),
        ),
        constraints,
        trace_sink=trace,
    )


__all__ = (
    "azure_bootstrap_legacy_owner_digests",
    "azure_bootstrap_owner_digest",
    "plan_azure_bootstrap_topology",
)
