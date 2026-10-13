"""Native failure boundaries for authenticated Git operations and gate cleanup."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from ansible.module_utils import basic
from ansible_collections.general_ludd.agent.plugins.modules import gludd_git
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from general_ludd.commands.make import MakeRunner
from general_ludd.git_automation.repo import GitAutomation
from general_ludd.routers import git_history
from general_ludd.security.permissions import Capability, PermissionSpec


@pytest.fixture
def native_repo(tmp_path: Path) -> GitAutomation:
    root = tmp_path / "gludd-git-native-repo"
    root.mkdir()
    git = GitAutomation(str(root))
    git.init_repo()
    git._run_git("checkout", "-B", "development")
    (root / "Makefile").write_text(".PHONY: gate\ngate:\n\t@:\n", encoding="utf-8")
    (root / ".gitignore").write_text(".gludd/\n", encoding="utf-8")
    (root / "owned.txt").write_text("base\n", encoding="utf-8")
    git.add_all()
    git.commit("test: native baseline")
    return git


def _app(*, capability: bool = True) -> FastAPI:
    app = FastAPI()
    git_history.register(app, {})
    spec = PermissionSpec(
        agent_type="native-git-coverage-test",
        capabilities=[Capability(resource="admin:git", actions=["execute"])] if capability else [],
    )

    @app.middleware("http")
    async def attach_permission(request: Request, call_next: Any) -> Any:
        request.state.auth_spec = spec
        return await call_next(request)

    return app


def _snapshot(git: GitAutomation) -> tuple[str, str, str]:
    return (
        git._run_git("show-ref", "--heads").stdout,
        git._run_git("rev-parse", "HEAD").stdout,
        git._run_git("status", "--porcelain").stdout,
    )


@pytest.mark.parametrize("capability", (False, True))
def test_real_capability_guard_controls_read_only_native_dispatch(
    native_repo: GitAutomation, capability: bool,
) -> None:
    with TestClient(_app(capability=capability)) as client:
        response = client.post(
            "/admin/git/operation", json={"op": "current_branch", "path": native_repo.repo_path},
        )
    if capability:
        assert response.status_code == 200
        assert response.json() == {"result": {"branch": "development"}, "changed": False}
    else:
        assert response.status_code == 403
        assert response.json()["detail"]["error"] == "forbidden: insufficient_capability"


def test_http_internal_native_failure_does_not_expose_repository_path(tmp_path: Path) -> None:
    private_path = tmp_path / "private-repository-token-do-not-disclose" / "missing"
    with TestClient(_app()) as client:
        response = client.post(
            "/admin/git/operation", json={"op": "tag_release", "path": str(private_path), "tag": "test"},
        )
    assert response.status_code == 500
    assert response.json() == {"detail": "git operation failed"}
    assert "private-repository-token" not in response.text


@pytest.mark.parametrize(
    ("op", "parameters"),
    (
        ("feature_done", {"feature_name": "topic"}),
        ("worktree_merge", {"branch": "feature/topic", "target": "development"}),
        ("worktree_merge_all", {}),
    ),
)
def test_real_canonical_gate_failure_prevents_composite_git_mutation(
    native_repo: GitAutomation, op: str, parameters: dict[str, str],
) -> None:
    root = Path(native_repo.repo_path)
    (root / "Makefile").write_text(".PHONY: gate\ngate:\n\t$(error native gate refused)\n", encoding="utf-8")
    before = _snapshot(native_repo)
    with TestClient(_app()) as client:
        response = client.post(
            "/admin/git/operation", json={"op": op, "path": str(root), **parameters},
        )
    assert response.status_code == 422
    assert "successful canonical gate before mutation" in response.json()["detail"]
    assert _snapshot(native_repo) == before


def test_native_http_worktree_default_base_and_owned_teardown(native_repo: GitAutomation) -> None:
    root = Path(native_repo.repo_path)
    destination = root.parent / "gludd-git-native-linked-worktree"
    initial_sha = native_repo._run_git("rev-parse", "HEAD").stdout.strip()
    try:
        with TestClient(_app()) as client:
            response = client.post(
                "/admin/git/operation",
                json={"op": "worktree_create", "path": str(root), "branch": "feature/topic",
                      "worktree_path": str(destination)},
            )
        assert response.status_code == 200
        assert response.json()["changed"] is True
        assert response.json()["result"]["success"] is True
        assert GitAutomation(str(destination))._run_git("rev-parse", "HEAD").stdout.strip() == initial_sha
    finally:
        assert native_repo.remove_worktree(str(root), str(destination))
    assert not destination.exists()
    assert [item.path for item in native_repo.list_worktrees(str(root))] == [str(root.resolve())]


@pytest.mark.parametrize("missing", ("source", "target"))
def test_native_gated_merge_failed_ref_keeps_trunk_exactly_unchanged(
    native_repo: GitAutomation, missing: str,
) -> None:
    before = _snapshot(native_repo)
    source = "unavailable-source" if missing == "source" else "development"
    target = "unavailable-target" if missing == "target" else "development"
    result = native_repo.gated_merge(source, target, ["make", "gate"])
    assert result.success is False
    assert result.commit_sha is None
    assert _snapshot(native_repo) == before


def test_native_empty_squash_commit_failure_rolls_back_before_gate(native_repo: GitAutomation) -> None:
    before = _snapshot(native_repo)
    result = native_repo.gated_merge("development", "development", ["make", "gate"], strategy="squash")
    assert result.success is False
    assert "squash commit failed" in result.message
    assert _snapshot(native_repo) == before


@pytest.mark.parametrize("stage_missing", (False, True))
def test_native_gated_commit_refuses_unstaged_or_missing_files_without_commit(
    native_repo: GitAutomation, stage_missing: bool,
) -> None:
    before = _snapshot(native_repo)
    files = ["missing.txt"] if stage_missing else []
    result = native_repo.gated_commit(files, "must not commit", ["make", "gate"])
    assert result.success is False
    assert result.commit_sha is None
    assert ("failed to stage files" if stage_missing else "commit failed after gate passed") in result.message
    assert _snapshot(native_repo) == before


@pytest.mark.parametrize("op", ("feature_done", "worktree_merge", "worktree_merge_all"))
def test_native_http_master_ref_alias_cannot_bypass_reviewed_promotion(
    native_repo: GitAutomation, op: str,
) -> None:
    before = _snapshot(native_repo)
    with TestClient(_app()) as client:
        response = client.post(
            "/admin/git/operation",
            json={"op": op, "path": native_repo.repo_path, "feature_name": "topic",
                  "branch": "feature/topic", "target": "refs/heads/master"},
        )
    assert response.status_code == 422
    assert "reviewed make release-promote" in response.json()["detail"]
    assert _snapshot(native_repo) == before


@pytest.mark.parametrize("strategy", ("no-ff", "squash"))
def test_native_gated_merge_preserves_requested_commit_topology(
    native_repo: GitAutomation, strategy: str,
) -> None:
    native_repo.create_branch("feature/topic")
    (Path(native_repo.repo_path) / "owned.txt").write_text("feature\n", encoding="utf-8")
    native_repo.add(["owned.txt"])
    feature_sha = native_repo.commit("test: feature delta")
    native_repo._run_git("checkout", "development", "--")
    result = native_repo.gated_merge("feature/topic", "development", ["make", "gate"], strategy=strategy)
    assert result.success is True and result.gate_returncode == 0
    assert result.commit_sha == native_repo._run_git("rev-parse", "HEAD").stdout.strip()
    parents = native_repo._run_git("rev-list", "--parents", "-n", "1", "HEAD").stdout.split()[1:]
    assert len(parents) == (2 if strategy == "no-ff" else 1)
    assert (feature_sha in parents) is (strategy == "no-ff")
    assert (Path(native_repo.repo_path) / "owned.txt").read_text(encoding="utf-8") == "feature\n"
    assert native_repo._run_git("status", "--porcelain").stdout == ""


def test_native_authenticated_feature_done_runs_gate_merges_and_deletes_feature(
    native_repo: GitAutomation,
) -> None:
    native_repo.create_branch("feature/topic")
    (Path(native_repo.repo_path) / "owned.txt").write_text("feature\n", encoding="utf-8")
    native_repo.add(["owned.txt"])
    feature_sha = native_repo.commit("test: feature delta")
    with TestClient(_app()) as client:
        response = client.post(
            "/admin/git/operation",
            json={"op": "feature_done", "path": native_repo.repo_path, "feature_name": "topic"},
        )
    assert response.status_code == 200
    assert response.json()["changed"] is True
    assert response.json()["result"]["target"] == "development"
    assert native_repo.current_branch() == "development"
    assert "feature/topic" not in native_repo.list_branches()
    parents = native_repo._run_git("rev-list", "--parents", "-n", "1", "HEAD").stdout.split()[1:]
    assert len(parents) == 2 and feature_sha in parents
    assert (Path(native_repo.repo_path) / "owned.txt").read_text(encoding="utf-8") == "feature\n"


def test_gated_merge_timeout_fault_restores_actual_fast_forward(
    native_repo: GitAutomation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_repo.create_branch("feature/topic")
    (Path(native_repo.repo_path) / "owned.txt").write_text("feature\n", encoding="utf-8")
    native_repo.add(["owned.txt"])
    native_repo.commit("test: feature delta")
    native_repo._run_git("checkout", "development", "--")
    before = _snapshot(native_repo)
    real_run = subprocess.run
    observed_gate: list[list[str]] = []

    def gate_timeout(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command == ["make", "gate"]:
            observed_gate.append(command)
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return real_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", gate_timeout)
    result = native_repo.gated_merge("feature/topic", "development", ["make", "gate"])
    assert observed_gate == [["make", "gate"]]
    assert result.success is False and result.gate_returncode == 124
    assert "rolled back" in result.message
    assert _snapshot(native_repo) == before
    assert (Path(native_repo.repo_path) / "owned.txt").read_text(encoding="utf-8") == "base\n"


def test_real_make_gate_timeout_reaps_process_group_and_closes_owned_pipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "Makefile").write_text(".PHONY: gate\ngate:\n\t@sleep 30\n", encoding="utf-8")
    real_popen = subprocess.Popen
    processes: list[subprocess.Popen[str]] = []

    def observe_process(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", observe_process)
    result = MakeRunner(cwd=tmp_path).run_gate(timeout_s=1)
    assert result.success is False and result.timed_out is True
    assert result.oom_killed is False and result.duration_s < 10
    assert len(processes) == 1
    assert processes[0].poll() is not None
    assert processes[0].stdout is not None and processes[0].stdout.closed
    assert processes[0].stderr is not None and processes[0].stderr.closed


def test_native_ansible_ship_commit_gate_bypass_refused_before_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Rejected(Exception):
        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

    def fail_json(_module: object, **payload: Any) -> None:
        raise Rejected(payload)

    def unexpected_client(**_options: object) -> None:
        raise AssertionError("gate bypass must be rejected before HTTP client construction")

    args = {"path": "/repo", "op": "ship_commit", "message": "unsafe", "skip_gate": True}
    monkeypatch.setattr(basic, "_ANSIBLE_ARGS", json.dumps({"ANSIBLE_MODULE_ARGS": args}).encode())
    monkeypatch.setattr(basic, "_ANSIBLE_PROFILE", "legacy")
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", fail_json)
    monkeypatch.setattr(gludd_git, "GluddClient", unexpected_client)
    with pytest.raises(Rejected) as rejected:
        gludd_git.main()
    assert rejected.value.payload["changed"] is False
    assert rejected.value.payload["msg"] == "ship_commit does not permit skip_gate"
