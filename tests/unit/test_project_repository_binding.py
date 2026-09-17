"""Cross-host repository bindings for managed self-improvement."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import general_ludd.projects.manager as manager_module
import general_ludd.projects.repository_binding as binding_module
import general_ludd.projects.workspace as workspace_module
from general_ludd.projects.manager import seed_from_config
from general_ludd.projects.repository_binding import (
    ProjectRepositoryBinding,
    ProjectRepositoryBindingStale,
    ProjectRepositoryRegistry,
    ProjectRepositoryUnavailable,
    repository_fingerprint,
)
from general_ludd.self_improve.codex_comparison import CodexReference
from general_ludd.self_improve.managed_runner import (
    ApprovedSelfImprovePlan,
    TaskSpec,
)


def _binding() -> ProjectRepositoryBinding:
    return ProjectRepositoryBinding.for_project(
        project_id="proj-cross-host",
        workspace_path="team/cross-host",
        repo_url="https://example.com/org/repository.git",
    )


def _make_repo(base: Path, workspace_key: str) -> Path:
    repo = base / workspace_key / "repo"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    return repo


def test_distinct_host_roots_resolve_one_stable_binding(tmp_path: Path) -> None:
    binding = _binding()
    controller_base = tmp_path / "controller"
    worker_base = tmp_path / "worker"
    controller_repo = _make_repo(controller_base, binding.workspace_key)
    worker_repo = _make_repo(worker_base, binding.workspace_key)

    controller = ProjectRepositoryRegistry((binding,), base_dir=controller_base)
    worker = ProjectRepositoryRegistry((binding,), base_dir=worker_base)

    assert controller.resolve(binding.project_id, binding.digest) == controller_repo.resolve()
    assert worker.resolve(binding.project_id, binding.digest) == worker_repo.resolve()
    assert controller_repo.resolve() != worker_repo.resolve()


@pytest.mark.parametrize(
    "workspace_path",
    (
        "/etc",
        "../escape",
        "team/../../escape",
        "team\\escape",
        "team//escape",
        "team/./escape",
    ),
)
def test_binding_rejects_unconfined_workspace_keys(workspace_path: str) -> None:
    with pytest.raises(ValueError, match="workspace"):
        ProjectRepositoryBinding.for_project(
            project_id="proj-confined",
            workspace_path=workspace_path,
            repo_url="https://example.com/org/repository.git",
        )


@pytest.mark.parametrize(
    "project_id",
    ("", " leading", "contains/slash", "x" * 129, 42),
)
def test_binding_rejects_ambiguous_project_identifiers(project_id: object) -> None:
    with pytest.raises(ValueError, match="project_id"):
        ProjectRepositoryBinding(
            project_id=project_id,  # type: ignore[arg-type]
            workspace_key="team/repository",
            repository_fingerprint="a" * 64,
        )


@pytest.mark.parametrize(
    "locator",
    ("", " leading", "trailing ", "line\nfeed", "carriage\rreturn", "\x00", 42),
)
def test_repository_fingerprint_rejects_noncanonical_locators(locator: object) -> None:
    with pytest.raises(ValueError, match="locator"):
        repository_fingerprint(locator)


def test_binding_rejects_invalid_fingerprint_and_bounds_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        ProjectRepositoryBinding(
            project_id="proj-invalid-fingerprint",
            workspace_key="team/repository",
            repository_fingerprint="A" * 64,
        )

    binding = _binding()
    monkeypatch.setattr(binding_module, "_MAX_BINDING_BYTES", 1)
    with pytest.raises(ValueError, match="bounded representation"):
        binding.to_json()


@pytest.mark.parametrize(
    ("raw", "message"),
    (
        (None, "bounded JSON"),
        ("", "bounded JSON"),
        ("{", "malformed JSON"),
        ("[]", "fields are malformed"),
        ('{"project_id":"a","project_id":"b"}', "duplicate field"),
        (
            '{"project_id":"proj","repository_fingerprint":"'
            + "a" * 64
            + '","schema_version":2,"workspace_key":"team/repo"}',
            "schema version",
        ),
    ),
)
def test_binding_json_rejects_malformed_payloads(raw: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ProjectRepositoryBinding.from_json(raw)


def test_binding_json_rejects_noncanonical_and_oversized_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _binding().to_json()
    noncanonical = raw.replace("{", "{ ", 1)
    with pytest.raises(ValueError, match="not canonical"):
        ProjectRepositoryBinding.from_json(noncanonical)

    monkeypatch.setattr(binding_module, "_MAX_BINDING_BYTES", 1)
    with pytest.raises(ValueError, match="bounded JSON"):
        ProjectRepositoryBinding.from_json(raw)


def test_registry_fails_closed_for_unknown_and_stale_identity(tmp_path: Path) -> None:
    binding = _binding()
    _make_repo(tmp_path, binding.workspace_key)
    registry = ProjectRepositoryRegistry((binding,), base_dir=tmp_path)

    with pytest.raises(ProjectRepositoryUnavailable):
        registry.resolve("proj-unknown", binding.digest)
    with pytest.raises(ProjectRepositoryBindingStale):
        registry.resolve(binding.project_id, "0" * 64)


def test_registry_rejects_invalid_duplicate_and_excess_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _binding()
    with pytest.raises(ValueError, match="ProjectRepositoryBinding"):
        ProjectRepositoryRegistry((object(),), base_dir=tmp_path)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="duplicate"):
        ProjectRepositoryRegistry((binding, binding), base_dir=tmp_path)

    other = ProjectRepositoryBinding.for_project(
        project_id="proj-other",
        workspace_path="team/other",
        repo_url="https://example.com/org/other.git",
    )
    monkeypatch.setattr(binding_module, "_MAX_REGISTRY_ENTRIES", 1)
    with pytest.raises(ValueError, match="entry limit"):
        ProjectRepositoryRegistry((binding, other), base_dir=tmp_path)


def test_registry_resolution_rejects_unavailable_repository_states(tmp_path: Path) -> None:
    binding = _binding()
    registry = ProjectRepositoryRegistry((binding,), base_dir=tmp_path)
    with pytest.raises(ProjectRepositoryUnavailable, match="unavailable"):
        registry.resolve(binding.project_id, binding.digest)

    repo_root = tmp_path / binding.workspace_key / "repo"
    repo_root.mkdir(parents=True)
    with pytest.raises(ProjectRepositoryUnavailable, match="unavailable"):
        registry.resolve(binding.project_id, binding.digest)

    (repo_root / ".git").mkdir()
    assert registry.resolve(binding.project_id, binding.digest) == repo_root.resolve()


def test_registry_resolution_rejects_repository_symlink_escape(tmp_path: Path) -> None:
    binding = _binding()
    workspace_root = tmp_path / binding.workspace_key
    workspace_root.mkdir(parents=True)
    outside_repo = tmp_path.parent / f"{tmp_path.name}-outside-repo"
    outside_repo.mkdir()
    (outside_repo / ".git").mkdir()
    (workspace_root / "repo").symlink_to(outside_repo, target_is_directory=True)

    registry = ProjectRepositoryRegistry((binding,), base_dir=tmp_path)
    with pytest.raises(ProjectRepositoryUnavailable, match="unavailable"):
        registry.resolve(binding.project_id, binding.digest)


@pytest.mark.parametrize(
    ("raw", "message"),
    (
        ("{", "malformed JSON"),
        ("[]", "fields are malformed"),
        ('{"bindings":[],"bindings":[],"schema_version":1}', "duplicate field"),
        ('{"bindings":[],"schema_version":2}', "schema version"),
        ('{"bindings":{},"schema_version":1}', "entries are malformed"),
        ('{"bindings":[1],"schema_version":1}', "fields are malformed"),
    ),
)
def test_registry_json_rejects_malformed_payloads(
    raw: str,
    message: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match=message):
        ProjectRepositoryRegistry.from_json(raw, base_dir=tmp_path)


def test_registry_json_rejects_noncanonical_and_bounded_payloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = ProjectRepositoryRegistry((_binding(),), base_dir=tmp_path).to_json()
    with pytest.raises(ValueError, match="not canonical"):
        ProjectRepositoryRegistry.from_json(raw.replace("{", "{ ", 1), base_dir=tmp_path)

    monkeypatch.setattr(binding_module, "_MAX_REGISTRY_BYTES", 1)
    with pytest.raises(ValueError, match="bounded representation"):
        ProjectRepositoryRegistry((_binding(),), base_dir=tmp_path).to_json()
    with pytest.raises(ValueError, match="bounded representation"):
        ProjectRepositoryRegistry.from_json(raw, base_dir=tmp_path)


def test_registry_empty_and_process_environment_are_snapshotted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = ProjectRepositoryRegistry.from_json("", base_dir=tmp_path)
    assert empty.get("proj-unknown") is None

    binding = _binding()
    raw = ProjectRepositoryRegistry((binding,), base_dir=tmp_path).to_json()
    monkeypatch.setenv("GLUDD_PROJECT_REPOSITORY_BINDINGS", raw)
    monkeypatch.setenv("GLUDD_PROJECT_WORKSPACE_BASE", str(tmp_path))
    snapshot = ProjectRepositoryRegistry.from_environment()
    assert snapshot.base_dir == tmp_path.resolve()
    assert snapshot.get(binding.project_id) == binding


def test_environment_registry_changes_apply_only_to_new_restart_snapshot(
    tmp_path: Path,
) -> None:
    original = _binding()
    replacement = ProjectRepositoryBinding.for_project(
        project_id=original.project_id,
        workspace_path=original.workspace_key,
        repo_url="https://example.com/org/replacement.git",
    )
    first_base = tmp_path / "host-one"
    second_base = tmp_path / "host-two"
    first_repo = _make_repo(first_base, original.workspace_key)
    _make_repo(second_base, replacement.workspace_key)
    environment = {
        "GLUDD_PROJECT_REPOSITORY_BINDINGS": ProjectRepositoryRegistry(
            (original,)
        ).to_json(),
        "GLUDD_PROJECT_WORKSPACE_BASE": str(first_base),
    }
    running_snapshot = ProjectRepositoryRegistry.from_environment(environment)

    environment.update(
        {
            "GLUDD_PROJECT_REPOSITORY_BINDINGS": ProjectRepositoryRegistry(
                (replacement,)
            ).to_json(),
            "GLUDD_PROJECT_WORKSPACE_BASE": str(second_base),
        }
    )
    restarted_snapshot = ProjectRepositoryRegistry.from_environment(environment)

    assert running_snapshot.resolve(original.project_id, original.digest) == (
        first_repo.resolve()
    )
    with pytest.raises(ProjectRepositoryBindingStale):
        restarted_snapshot.resolve(original.project_id, original.digest)
    assert restarted_snapshot.resolve(
        replacement.project_id,
        replacement.digest,
    ).is_relative_to(second_base.resolve())


def test_config_seed_project_identity_is_stable_across_restarts() -> None:
    config = {
        "projects": [
            {
                "name": "stable",
                "weight": 50,
                "workspace_path": "team/stable",
                "repo_url": "https://example.com/org/stable.git",
            }
        ]
    }

    first = seed_from_config(config).list_active()[0]
    second = seed_from_config(config).list_active()[0]

    assert first.project_id == second.project_id
    assert first.project_id.startswith("proj-")

    changed_mapping = seed_from_config(
        {
            "projects": [
                {
                    "name": "stable",
                    "weight": 50,
                    "workspace_path": "team/replacement",
                    "repo_url": "https://example.com/org/replacement.git",
                }
            ]
        }
    ).list_active()[0]
    assert changed_mapping.project_id == first.project_id
    original_binding = ProjectRepositoryBinding.for_project(
        project_id=first.project_id,
        workspace_path=first.workspace_path,
        repo_url=first.repo_url,
    )
    changed_binding = ProjectRepositoryBinding.for_project(
        project_id=changed_mapping.project_id,
        workspace_path=changed_mapping.workspace_path,
        repo_url=changed_mapping.repo_url,
    )
    assert changed_binding.digest != original_binding.digest


def test_project_identity_and_remote_resolution_cover_defensive_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert manager_module._stable_config_project_id(
        {"project_id": "  proj-explicit  "}
    ) == "proj-explicit"
    assert manager_module._normalize_repo_url(
        "ssh://builder@EXAMPLE.com/org/repository.git"
    ) == "example.com/org/repository"

    monkeypatch.delenv("GLUDD_SELF_REPO_URL", raising=False)
    monkeypatch.setattr(
        manager_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=" https://example.com/org/repository.git\n",
        ),
    )
    assert manager_module._resolve_self_repo_url() == (
        "https://example.com/org/repository.git"
    )
    monkeypatch.setattr(
        manager_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="ignored"),
    )
    assert manager_module._resolve_self_repo_url() == ""

    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise OSError("git unavailable")

    monkeypatch.setattr(manager_module.subprocess, "run", unavailable)
    assert manager_module._resolve_self_repo_url() == ""


def test_project_manager_defensive_selection_and_duplicate_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = manager_module.ProjectManager()
    manager.add_project("first", 10.0, project_id="proj-first")
    manager.add_project("last", 10.0, project_id="proj-last")
    with pytest.raises(manager_module.ProjectAllocationError, match="already registered"):
        manager.add_project("duplicate", 1.0, project_id="proj-first")

    monkeypatch.setattr("random.random", lambda: 2.0)
    selected = manager.select_project()
    assert selected is not None
    assert selected.project_id == "proj-last"


def test_relationship_normalization_and_persistence_cover_all_location_kinds() -> None:
    string_contract = manager_module.normalize_relationship_config(
        {
            "relation": "external",
            "location": "https://example.com/org/other.git",
            "interface_contract": "stable-v1",
            "controlled_by_gludd": False,
        }
    )
    invalid_contract = manager_module.normalize_relationship_config(
        {
            "relation": "sibling",
            "location": "../other",
            "interface_contract": 42,
        }
    )
    assert string_contract is not None
    assert string_contract["interface_contract"] == "stable-v1"
    assert string_contract["_controlled_explicit"] is True
    assert invalid_contract is not None
    assert invalid_contract["interface_contract"] == "{}"


@pytest.mark.asyncio
async def test_project_persistence_updates_and_relationships_fail_closed_on_self() -> None:
    existing = SimpleNamespace()
    project_repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=existing),
        create=AsyncMock(),
    )
    await manager_module.persist_project(
        project_repo,
        project_id="proj-existing",
        name="renamed",
        weight=25.0,
        description="updated",
        workspace_path="team/existing",
        repo_url="https://example.com/org/existing.git",
    )
    assert existing.name == "renamed"
    assert existing.active is True
    project_repo.create.assert_not_awaited()

    relationship_repo = SimpleNamespace(
        add_relationship=AsyncMock(side_effect=lambda data: data),
    )
    rows = await manager_module.persist_relationships_from_config(
        relationship_repo,
        project_id="proj-self",
        edges=[
            {"location_kind": "gludd_project_name", "location_value": "self"},
            {"location_kind": "directory", "location_value": "team/other"},
            {"location_kind": "url", "location_value": "https://example.com/self"},
            {"location_kind": "unknown", "location_value": "unresolved"},
        ],
        name_to_id={"self": "proj-self"},
        workspace_to_id={"team/other": "proj-other"},
        repo_url_to_id={"https://example.com/self": "proj-self"},
    )
    assert rows[0]["related_project_id"] is None
    assert rows[1]["related_project_id"] == "proj-other"
    assert rows[1]["controlled_by_gludd"] is True
    assert rows[2]["related_project_id"] is None
    assert rows[3]["related_project_id"] is None


def test_workspace_materialization_reports_clone_failure_and_cache_hit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git = MagicMock()
    git.clone.side_effect = [
        SimpleNamespace(success=False, already_present=False, message="clone failed"),
        SimpleNamespace(success=True, already_present=True, message="already present"),
    ]
    monkeypatch.setattr(
        "general_ludd.git_automation.repo.GitAutomation",
        lambda: git,
    )

    assert manager_module.materialize_project_workspace(
        "https://example.com/org/failed.git",
        "team/failed",
        base_dir=str(tmp_path),
    ) is None
    cached = manager_module.materialize_project_workspace(
        "https://example.com/org/cached.git",
        "team/cached",
        base_dir=str(tmp_path),
    )
    assert cached == str((tmp_path / "team" / "cached" / "repo").resolve())
    assert git.clone.call_count == 2


def test_daemon_workspace_initialization_uses_confined_configured_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd.daemon import _init_project_workspaces

    workspace_base = tmp_path / "daemon-host"
    monkeypatch.setattr(
        workspace_module,
        "default_workspace_base",
        lambda: str(workspace_base),
    )
    project = SimpleNamespace(
        project_id="proj-configured",
        workspace_path="org/configured",
        repo_url="https://example.com/org/configured.git",
    )
    invalid = SimpleNamespace(
        project_id="proj-invalid",
        workspace_path="../escape",
        repo_url="https://example.com/org/invalid.git",
    )
    manager = SimpleNamespace(list_active=lambda: [project, invalid])

    workspaces = _init_project_workspaces(manager)

    assert set(workspaces) == {"proj-configured"}
    assert workspaces["proj-configured"].root.resolve() == (
        workspace_base / "org" / "configured"
    ).resolve()
    assert workspaces["proj-configured"].repo_dir.is_dir()
    assert not (tmp_path / "escape").exists()


def test_managed_approval_resolver_uses_same_confined_workspace_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd.routers.self_improve import _resolve_non_config_project_repo

    binding = ProjectRepositoryBinding.for_project(
        project_id="proj-configured",
        workspace_path="org/configured",
        repo_url="https://example.com/org/configured.git",
    )
    workspace_base = tmp_path / "approval-host"
    repo_root = _make_repo(workspace_base, binding.workspace_key)
    monkeypatch.setattr(
        workspace_module,
        "default_workspace_base",
        lambda: str(workspace_base),
    )
    project = SimpleNamespace(
        project_id=binding.project_id,
        workspace_path=binding.workspace_key,
        repo_url="https://example.com/org/configured.git",
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            _project_manager=SimpleNamespace(get_project=lambda _project_id: project)
        )
    )

    assert _resolve_non_config_project_repo(app, binding.project_id) == (
        repo_root.resolve()
    )


@pytest.mark.parametrize(
    "manager",
    (
        None,
        SimpleNamespace(get_project=lambda _project_id: None),
        SimpleNamespace(
            get_project=lambda _project_id: SimpleNamespace(
                active=False,
                workspace_path="team/inactive",
                repo_url="https://example.com/org/inactive.git",
            )
        ),
        SimpleNamespace(
            get_project=lambda _project_id: SimpleNamespace(
                active=True,
                workspace_path="../escape",
                repo_url="https://example.com/org/escape.git",
            )
        ),
    ),
)
def test_managed_binding_resolver_fails_closed_for_untrusted_project_state(
    manager: object,
) -> None:
    from general_ludd.routers.self_improve import _resolve_project_repository_binding

    app = SimpleNamespace(state=SimpleNamespace(_project_manager=manager))

    with pytest.raises(HTTPException) as exc_info:
        _resolve_project_repository_binding(app, "proj-configured")
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "managed self-improve project binding is unavailable"


@pytest.mark.parametrize(
    "changes",
    (
        {"schema_version": True},
        {"project_id": 42},
        {"project_id": " leading"},
        {"kind": "config"},
        {"title": "  "},
        {"worktree_path": "relative/worktree"},
    ),
)
def test_legacy_non_config_plan_rejects_ambiguous_identity(
    tmp_path: Path,
    changes: dict[str, object],
) -> None:
    from general_ludd.routers.self_improve import _NonConfigPlanSpec

    values: dict[str, object] = {
        "schema_version": 1,
        "project_id": "proj-configured",
        "kind": "code",
        "title": "Approved code change",
        "description": "A bounded legacy plan.",
        "worktree_path": str(tmp_path.resolve()),
    }
    values.update(changes)

    with pytest.raises(ValueError, match="non-config plan"):
        _NonConfigPlanSpec(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("missing", "artifact is missing"),
        ("malformed", "artifact is malformed"),
        ("fields", "fields are malformed"),
        ("types", "field types are malformed"),
        ("project", "project identity drifted"),
        ("canonical", "artifact is not canonical"),
    ),
)
def test_legacy_non_config_plan_json_fails_closed_for_identity_drift(
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    from general_ludd.routers.self_improve import _NonConfigPlanSpec

    spec = _NonConfigPlanSpec(
        schema_version=1,
        project_id="proj-configured",
        kind="code",
        title="Approved code change",
        description="A bounded legacy plan.",
        worktree_path=str(tmp_path.resolve()),
    )
    raw = spec.to_json()
    if case == "missing":
        candidate: object = None
    elif case == "malformed":
        candidate = "{"
    elif case == "fields":
        candidate = "{}"
    elif case == "types":
        value = json.loads(raw)
        value["schema_version"] = True
        candidate = json.dumps(value, separators=(",", ":"), sort_keys=True)
    elif case == "project":
        value = json.loads(raw)
        value["project_id"] = "proj-other"
        candidate = json.dumps(value, separators=(",", ":"), sort_keys=True)
    else:
        candidate = raw.replace("{", "{ ", 1)

    with pytest.raises(ValueError, match=message):
        _NonConfigPlanSpec.from_json(
            candidate,
            expected_project_id="proj-configured",
        )


def test_bound_plan_never_serializes_an_absolute_host_path(tmp_path: Path) -> None:
    binding = _binding()
    controller_repo = _make_repo(tmp_path / "controller", binding.workspace_key)
    worker_repo = _make_repo(tmp_path / "worker", binding.workspace_key)
    plan = ApprovedSelfImprovePlan.approve(
        approval_id="approval-cross-host",
        todo_id="TODO-CROSS-HOST",
        project_id=binding.project_id,
        repo_root=controller_repo,
        repository_binding_digest=binding.digest,
        task=TaskSpec(
            task_id="S83.401",
            objective="Prove cross-host repository binding.",
            canonical_make_commands=(
                "make test-files TESTFILES=tests/unit/test_project_repository_binding.py",
            ),
        ),
        reference=CodexReference(
            baseline_sha="a" * 40,
            reference_sha="b" * 40,
            changed_files=frozenset({"src/general_ludd/example.py"}),
            test_files=frozenset({"tests/unit/test_example.py"}),
            changed_lines=1,
            elapsed_seconds=0.1,
        ),
        prompt="Return one bounded improvement.",
        required_output_tokens=512,
        max_attempts=1,
    )

    raw = plan.to_json()
    hydrated = ApprovedSelfImprovePlan.from_json(raw)
    rebound = hydrated.bind_execution_repository(
        worker_repo,
        repository_binding_digest=binding.digest,
    )

    assert str(controller_repo.resolve()) not in raw
    assert hydrated.repo_root is None
    assert rebound.repo_root == worker_repo.resolve()
    assert rebound.identity_digest == plan.identity_digest
    assert rebound.to_json() == raw
