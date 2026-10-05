"""Tests for pure Azure bootstrap ownership planning."""

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from general_ludd.infra.azure_containerapp_gpu import (
    T4_PROFILE,
    ModelServingRequirement,
)
from general_ludd.infra.azure_containerapp_topology import AzureProfileCapacity
from general_ludd.self_improve.azure_containerapp_bootstrap_planning import (
    azure_bootstrap_legacy_owner_digests,
    azure_bootstrap_owner_digest,
    azure_resource_group_owner_digest,
    plan_azure_bootstrap_topology,
)
from general_ludd.self_improve.azure_containerapp_bootstrap_settings import (
    AzureContainerAppBootstrapSettings,
)


def test_owner_digest_is_project_scoped_and_deterministic(tmp_path: Path) -> None:
    """Environment ownership binds the same Azure names to one project root."""
    settings = cast(
        AzureContainerAppBootstrapSettings,
        SimpleNamespace(
            environment_name="gludd-gpu-environment",
            resource_group="gludd-gpu",
            subscription_id="11111111-2222-3333-4444-555555555555",
        ),
    )

    first = azure_bootstrap_owner_digest(tmp_path, settings)
    second = azure_bootstrap_owner_digest(tmp_path, settings)
    other = azure_bootstrap_owner_digest(tmp_path / "other", settings)

    assert first == second
    assert first != other
    assert len(first) == 64


def test_resource_group_owner_is_stable_across_regional_environments(
    tmp_path: Path,
) -> None:
    """One project-owned group can safely contain environments in many regions."""
    east = cast(
        AzureContainerAppBootstrapSettings,
        SimpleNamespace(
            environment_name="gludd-gpu-environment",
            location="eastus",
            resource_group="gludd-gpu",
            subscription_id="11111111-2222-3333-4444-555555555555",
        ),
    )
    west = cast(
        AzureContainerAppBootstrapSettings,
        SimpleNamespace(
            environment_name="gludd-gpu-environment-westus3",
            location="westus3",
            resource_group=east.resource_group,
            subscription_id=east.subscription_id,
        ),
    )

    assert azure_resource_group_owner_digest(
        tmp_path, east
    ) == azure_resource_group_owner_digest(tmp_path, west)
    assert azure_bootstrap_owner_digest(tmp_path, east) != azure_bootstrap_owner_digest(
        tmp_path, west
    )


def test_regional_failover_can_migrate_the_documented_legacy_environment_owner(
    tmp_path: Path,
) -> None:
    """A region change recognizes only the former project-bound default identity."""
    west = cast(
        AzureContainerAppBootstrapSettings,
        SimpleNamespace(
            environment_name="gludd-gpu-environment-westus3",
            location="westus3",
            resource_group="gludd-gpu",
            subscription_id="11111111-2222-3333-4444-555555555555",
        ),
    )
    east = cast(
        AzureContainerAppBootstrapSettings,
        SimpleNamespace(
            environment_name="gludd-gpu-environment",
            location="eastus",
            resource_group=west.resource_group,
            subscription_id=west.subscription_id,
        ),
    )

    legacy = azure_bootstrap_legacy_owner_digests(tmp_path, west)

    assert azure_bootstrap_owner_digest(tmp_path, west) in legacy
    assert azure_bootstrap_owner_digest(tmp_path, east) in legacy
    assert azure_resource_group_owner_digest(tmp_path, west) not in legacy


def test_owner_digest_is_stable_across_linked_worktrees(tmp_path: Path) -> None:
    """One Git common directory represents one project across worktree paths."""
    main = tmp_path / "main"
    common = main / ".git"
    private = common / "worktrees" / "feature"
    sibling_private = common / "worktrees" / "sibling"
    linked = tmp_path / "feature"
    sibling = tmp_path / "sibling"
    private.mkdir(parents=True)
    sibling_private.mkdir(parents=True)
    linked.mkdir()
    sibling.mkdir()
    (private / "commondir").write_text("../..\n")
    (private / "gitdir").write_text(f"{linked}/.git\n")
    (linked / ".git").write_text(f"gitdir: {private}\n")
    (sibling_private / "commondir").write_text("../..\n")
    (sibling_private / "gitdir").write_text(f"{sibling}/.git\n")
    (sibling / ".git").write_text(f"gitdir: {sibling_private}\n")
    settings = cast(
        AzureContainerAppBootstrapSettings,
        SimpleNamespace(
            environment_name="gludd-gpu-environment",
            resource_group="gludd-gpu",
            subscription_id="11111111-2222-3333-4444-555555555555",
        ),
    )

    assert azure_bootstrap_owner_digest(
        main, settings
    ) == azure_bootstrap_owner_digest(linked, settings)
    legacy = azure_bootstrap_legacy_owner_digests(
        linked,
        settings,
        privacy_policy_digest="c" * 64,
    )
    assert len(legacy) == 6
    assert azure_bootstrap_owner_digest(linked, settings) in legacy
    assert azure_resource_group_owner_digest(linked, settings) not in legacy
    main_legacy = azure_bootstrap_legacy_owner_digests(main, settings)
    assert len(main_legacy) == 3
    assert azure_bootstrap_owner_digest(main, settings) in main_legacy
    assert azure_resource_group_owner_digest(main, settings) not in main_legacy


def test_legacy_owner_catalog_rejects_untrusted_policy_identity(tmp_path: Path) -> None:
    """Never derive an adoption candidate from malformed policy material."""
    settings = cast(
        AzureContainerAppBootstrapSettings,
        SimpleNamespace(
            environment_name="gludd-gpu-environment",
            resource_group="gludd-gpu",
            subscription_id="11111111-2222-3333-4444-555555555555",
        ),
    )

    try:
        azure_bootstrap_legacy_owner_digests(
            tmp_path,
            settings,
            privacy_policy_digest="not-a-digest",
        )
    except ValueError as error:
        assert "privacy_policy_digest" in str(error)
    else:
        raise AssertionError("malformed policy identity must fail closed")


def test_bootstrap_topology_is_right_sized_and_emits_content_free_progress() -> None:
    """Configured demand is bounded by inventory while every phase stays observable."""
    settings = cast(
        AzureContainerAppBootstrapSettings,
        SimpleNamespace(
            max_hourly_cost_microusd=3_000_000,
            peak_concurrency=3,
            per_replica_concurrency=2,
            profile_capacities=(
                AzureProfileCapacity(
                    profile=T4_PROFILE,
                    max_replicas=3,
                    hourly_cost_microusd_per_replica=1_000_000,
                ),
            ),
            ttl_minutes=60,
        ),
    )
    requirement = ModelServingRequirement(
        model_id="Qwen/Qwen2.5-Coder-1.5B-Instruct",
        revision="a" * 40,
        parameter_count=1_500_000_000,
        weight_bits=8,
        kv_cache_mib=2_048,
        runtime_overhead_mib=3_072,
    )
    progress: list[str] = []

    plan = plan_azure_bootstrap_topology(settings, requirement, progress.append)

    assert plan.batches == (("self-improve",),)
    assert len(plan.apps) == 1
    assert plan.apps[0].max_replicas == 2
    assert plan.apps[0].workload_profile_type == T4_PROFILE.workload_profile_type
    assert plan.max_hourly_cost_microusd == 2_000_000
    assert progress
    assert all(
        message.startswith("SELF_IMPROVE_AZURE_BOOTSTRAP phase=")
        and "task_count=1" in message
        and message.endswith("secret_output=false")
        for message in progress
    )


def test_legacy_catalog_tolerates_missing_worktree_inventory(tmp_path: Path) -> None:
    """A checkout without linked-worktree metadata still has deterministic ownership."""
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    settings = cast(
        AzureContainerAppBootstrapSettings,
        SimpleNamespace(
            environment_name="gludd-gpu-environment",
            resource_group="gludd-gpu",
            subscription_id="11111111-2222-3333-4444-555555555555",
        ),
    )

    assert azure_bootstrap_legacy_owner_digests(main, settings) == (
        azure_bootstrap_owner_digest(main, settings),
    )


def test_legacy_catalog_handles_a_non_git_project_root(tmp_path: Path) -> None:
    """A canonical project root remains safe before Git metadata exists."""
    settings = cast(
        AzureContainerAppBootstrapSettings,
        SimpleNamespace(
            environment_name="gludd-gpu-environment",
            resource_group="gludd-gpu",
            subscription_id="11111111-2222-3333-4444-555555555555",
        ),
    )

    assert azure_bootstrap_legacy_owner_digests(tmp_path, settings) == (
        azure_bootstrap_owner_digest(tmp_path, settings),
    )


def test_legacy_catalog_ignores_untrusted_linked_worktree_metadata(
    tmp_path: Path,
) -> None:
    """Malformed, foreign, or unbounded Git metadata cannot become an owner."""
    main = tmp_path / "main"
    common = main / ".git"
    worktrees = common / "worktrees"
    worktrees.mkdir(parents=True)

    unreadable = worktrees / "01-unreadable" / "gitdir"
    unreadable.mkdir(parents=True)
    invalid_utf8 = worktrees / "02-invalid-utf8"
    invalid_utf8.mkdir()
    (invalid_utf8 / "gitdir").write_bytes(b"\xff")
    oversized = worktrees / "03-oversized"
    oversized.mkdir()
    (oversized / "gitdir").write_text("x" * 4_097)
    multiline = worktrees / "04-multiline"
    multiline.mkdir()
    (multiline / "gitdir").write_text("/first/.git\n/second/.git\n")
    relative = worktrees / "05-relative"
    relative.mkdir()
    (relative / "gitdir").write_text("relative/.git\n")
    wrong_name = worktrees / "06-wrong-name"
    wrong_name.mkdir()
    (wrong_name / "gitdir").write_text(f"{tmp_path}/checkout/git-data\n")
    missing = worktrees / "07-missing"
    missing.mkdir()
    (missing / "gitdir").write_text(f"{tmp_path}/missing/.git\n")
    foreign_root = tmp_path / "foreign"
    (foreign_root / ".git").mkdir(parents=True)
    foreign = worktrees / "08-foreign"
    foreign.mkdir()
    (foreign / "gitdir").write_text(f"{foreign_root}/.git\n")
    settings = cast(
        AzureContainerAppBootstrapSettings,
        SimpleNamespace(
            environment_name="gludd-gpu-environment",
            resource_group="gludd-gpu",
            subscription_id="11111111-2222-3333-4444-555555555555",
        ),
    )

    legacy = azure_bootstrap_legacy_owner_digests(
        main,
        settings,
        privacy_policy_digest="d" * 64,
    )

    assert len(legacy) == 2
    assert azure_bootstrap_owner_digest(main, settings) in legacy
    assert all(len(digest) == 64 for digest in legacy)


def test_legacy_owner_catalog_rejects_non_text_policy_identity(tmp_path: Path) -> None:
    """A runtime type violation also fails before an owner digest is derived."""
    settings = cast(
        AzureContainerAppBootstrapSettings,
        SimpleNamespace(
            environment_name="gludd-gpu-environment",
            resource_group="gludd-gpu",
            subscription_id="11111111-2222-3333-4444-555555555555",
        ),
    )

    try:
        azure_bootstrap_legacy_owner_digests(
            tmp_path,
            settings,
            privacy_policy_digest=cast(str, object()),
        )
    except ValueError as error:
        assert "privacy_policy_digest" in str(error)
    else:
        raise AssertionError("non-text policy identity must fail closed")
