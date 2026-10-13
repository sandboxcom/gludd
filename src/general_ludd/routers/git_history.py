"""Git history and bounded Git control-plane HTTP routes."""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import asdict, is_dataclass
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from general_ludd.commands.make import MakeRunner
from general_ludd.git_automation.batch_push import batch_push
from general_ludd.git_automation.ci_ops import ci_cancel, ci_verdict
from general_ludd.git_automation.feature_branch import feature_done, feature_start, rebranch_onto
from general_ludd.git_automation.release_ops import release_cut, release_delete, release_recut
from general_ludd.git_automation.repo import GitAutomation
from general_ludd.git_automation.ship_commit import ShipCommitError, collect_check, ship_commit
from general_ludd.git_automation.worktree import (
    worktree_cleanup,
    worktree_health_check,
    worktree_merge,
    worktree_merge_all,
)
from general_ludd.history.git_indexer import GitHistoryIndexer
from general_ludd.routers._runtime import IdempotencyStore, StrictRuntimeRequest
from general_ludd.security.capability_guard import RequireCapability

GitOperation = Literal[
    "init", "clone", "commit", "gated_commit", "current_branch", "branch",
    "branch_list", "branch_delete", "worktree_list", "worktree_create",
    "worktree_remove", "merge", "gated_merge", "push", "verify_remote",
    "tag_release", "tag_checkpoint", "release_tag", "checkpoint_tag", "state",
    "batch_push", "release_cut", "release_delete", "release_recut", "ci_verdict",
    "ci_cancel",
    "feature_start", "feature_done", "rebranch_onto", "ship_commit",
    "worktree_merge", "worktree_cleanup", "worktree_health_check", "worktree_merge_all",
]

_READ_ONLY_GIT_OPS = frozenset(
    {"current_branch", "branch_list", "worktree_list", "verify_remote", "state", "ci_verdict",
     "worktree_health_check"}
)


class GitOperationRequest(StrictRuntimeRequest):
    """Allowlisted Git control-plane operation with bounded arguments."""

    op: GitOperation
    path: StrictStr = Field(min_length=1, max_length=4096)
    clone_url: StrictStr | None = Field(default=None, max_length=4096)
    target_dir: StrictStr | None = Field(default=None, max_length=4096)
    git_clone_timeout: StrictInt = Field(default=120, ge=1, le=600)
    clone_allow_local: StrictBool = True
    message: StrictStr | None = Field(default=None, max_length=4096)
    files: list[StrictStr] = Field(default_factory=list, max_length=256)
    gate_cmd: list[StrictStr] = Field(default_factory=list, max_length=32)
    branch: StrictStr | None = Field(default=None, max_length=512)
    feature_name: StrictStr | None = Field(default=None, max_length=512)
    base: StrictStr | None = Field(default=None, max_length=512)
    new_base: StrictStr | None = Field(default=None, max_length=512)
    commits: list[StrictStr] = Field(default_factory=list, max_length=256)
    push: StrictBool = False
    skip_gate: StrictBool = False
    max_age_hours: StrictInt = Field(default=24, ge=1, le=87600)
    remote_name: StrictStr = Field(default="sandboxcom", min_length=1, max_length=256)
    target_branch: StrictStr = Field(default="development", min_length=1, max_length=512)
    worktree_path: StrictStr | None = Field(default=None, max_length=4096)
    source: StrictStr | None = Field(default=None, max_length=512)
    target: StrictStr | None = Field(default=None, max_length=512)
    strategy: Literal["ff", "no-ff", "squash"] = "ff"
    tag: StrictStr | None = Field(default=None, max_length=512)
    todo_id: StrictStr | None = Field(default=None, max_length=512)
    sha: StrictStr | None = Field(default=None, max_length=128)
    expected_sha: StrictStr | None = Field(default=None, max_length=128)
    ssh_key_path: StrictStr | None = Field(default=None, max_length=4096)
    ref_type: Literal["heads", "tags"] = "heads"
    threshold: StrictInt = Field(default=5, ge=1, le=1000)
    force: StrictBool = False
    check_ci: StrictBool = True
    release_tag: StrictStr | None = Field(default=None, max_length=512)
    release_message: StrictStr = Field(default="", max_length=4096)
    release_remote: StrictStr = Field(default="sandboxcom", max_length=256)
    release_repo: StrictStr = Field(default="sandboxcom/gludd", max_length=512)
    skip_readme_check: StrictBool = False
    skip_ci_check: StrictBool = False
    run_id: StrictStr | None = Field(default=None, max_length=128)
    remote: StrictStr = Field(default="origin", max_length=256)
    state_ref: StrictStr = Field(default="", max_length=512)
    state_gha_head_sha: StrictStr = Field(default="", max_length=128)
    state_worktree_target_ref: StrictStr = Field(default="HEAD", max_length=512)
    state_preserve_branch_patterns: list[StrictStr] = Field(default_factory=list, max_length=64)
    state_reconciled_preserve_heads: list[StrictStr] = Field(default_factory=list, max_length=256)
    state_reconciled_preserve_head_file: StrictStr = Field(
        default="config/reconciled_preserved_heads.txt", max_length=4096
    )
    state_assert_clean: StrictBool = False
    state_assert_no_feature_on_master: StrictBool = False
    state_assert_merge_ready: StrictBool = False
    state_assert_remote_head: StrictBool = False
    state_assert_gha_matches_local: StrictBool = False
    state_assert_no_unintegrated_worktrees: StrictBool = False
    state_assert_no_unintegrated_branches: StrictBool = False
    idempotency_key: StrictStr | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("files", "state_preserve_branch_patterns", "state_reconciled_preserve_heads", "commits")
    @classmethod
    def _bound_list_items(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 4096 for item in value):
            raise ValueError("list entries must be non-empty and at most 4096 characters")
        return value

    @field_validator("feature_name", "base", "new_base", "remote_name", "target_branch")
    @classmethod
    def _reject_option_ref(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value.startswith("-")):
            raise ValueError("Git refs must be non-empty and must not begin with '-'")
        return value

    @field_validator("commits")
    @classmethod
    def _reject_option_commits(cls, value: list[str]) -> list[str]:
        if any(item.startswith("-") for item in value):
            raise ValueError("commit refs must not begin with '-'")
        return value

    @field_validator("gate_cmd")
    @classmethod
    def _allowlist_gate_command(cls, value: list[str]) -> list[str]:
        if not value:
            return value
        if value[0] != "make":
            raise ValueError("gate_cmd must invoke an allowlisted make target")
        for token in value[1:]:
            if not token or len(token) > 512 or any(char.isspace() for char in token):
                raise ValueError("gate_cmd tokens must be bounded, non-empty argv values")
            if token.startswith("-") or any(char in token for char in ";|&`$<>(){}[]"):
                raise ValueError("gate_cmd contains a forbidden token")
        return value

    @model_validator(mode="after")
    def _require_operation_fields(self) -> GitOperationRequest:
        required: dict[str, tuple[str, ...]] = {
            "clone": ("clone_url", "target_dir"),
            "commit": ("message",),
            "gated_commit": ("message", "gate_cmd"),
            "branch": ("branch",),
            "branch_delete": ("branch",),
            "worktree_create": ("branch", "worktree_path"),
            "worktree_remove": ("worktree_path",),
            "merge": ("source", "target"),
            "gated_merge": ("source", "target", "gate_cmd"),
            "push": ("branch",),
            "verify_remote": ("branch", "expected_sha"),
            "tag_release": ("tag",),
            "tag_checkpoint": ("tag",),
            "checkpoint_tag": ("todo_id", "sha"),
            "release_cut": ("release_tag",),
            "release_delete": ("release_tag",),
            "release_recut": ("release_tag",),
            "ci_cancel": ("run_id",),
            "feature_start": ("feature_name",),
            "feature_done": ("feature_name",),
            "rebranch_onto": ("new_base",),
            "ship_commit": ("message",),
            "worktree_merge": ("branch", "target"),
            "worktree_cleanup": ("branch",),
        }
        missing = [name for name in required.get(self.op, ()) if not getattr(self, name)]
        if missing:
            raise ValueError(f"{self.op} requires: {', '.join(missing)}")
        if self.op == "ship_commit" and self.skip_gate:
            raise ValueError("ship_commit does not permit skip_gate")
        return self


def _as_json(value: object) -> dict[str, Any]:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    raise TypeError("git operation returned an unsupported result")


def _dispatch_git(req: GitOperationRequest) -> tuple[dict[str, Any], bool]:
    git = GitAutomation(req.path)
    op = req.op
    if op in {"feature_done", "worktree_merge", "worktree_merge_all"}:
        if (req.target or "development").removeprefix("refs/heads/") == "master":
            raise ValueError("master promotion requires reviewed make release-promote")
        gate = MakeRunner(cwd=req.path).run_gate()
        if (
            gate.target != "gate" or gate.success is not True or gate.exit_code != 0
            or gate.timed_out or gate.oom_killed or gate.error is not None
        ):
            raise ValueError(f"{op} requires a successful canonical gate before mutation")
    if op == "init":
        result = _as_json(git.init_repo(req.path))
        return result, bool(result.get("created"))
    if op == "clone":
        result = _as_json(git.clone(
            req.clone_url or "", req.target_dir or "", float(req.git_clone_timeout),
            allow_local=req.clone_allow_local,
        ))
        return result, bool(result.get("success")) and not bool(result.get("already_present"))
    if op == "commit":
        if not git.changed_files():
            return {"sha": "", "message": "nothing to commit", "success": True}, False
        sha = git.commit(req.message or "")
        return {"sha": sha, "success": True}, True
    if op == "gated_commit":
        result = _as_json(git.gated_commit(req.files, req.message or "", req.gate_cmd))
        return result, bool(result.get("success"))
    if op == "current_branch":
        return {"branch": git.current_branch()}, False
    if op == "branch":
        try:
            branch = git.create_branch(req.branch or "")
        except subprocess.CalledProcessError as exc:
            detail = f"{exc.stderr or ''} {exc.output or ''}".lower()
            if "already exists" in detail:
                return {"branch": req.branch, "already_present": True}, False
            raise
        return {"branch": branch}, True
    if op == "branch_list":
        return {"branches": git.list_branches()}, False
    if op == "branch_delete":
        deleted = git.delete_branch(req.branch or "")
        return {"branch": req.branch, "deleted": deleted}, deleted
    if op == "worktree_list":
        return {"worktrees": [asdict(item) for item in git.list_worktrees(req.path)]}, False
    if op == "worktree_create":
        if req.base is None:
            result = _as_json(git.create_worktree(req.path, req.branch or "", req.worktree_path or ""))
        else:
            result = _as_json(git.create_worktree(
                req.path, req.branch or "", req.worktree_path or "", base_branch=req.base,
            ))
        return result, bool(result.get("success"))
    if op == "worktree_remove":
        removed = git.remove_worktree(req.path, req.worktree_path or "")
        return {"path": req.worktree_path, "removed": removed}, removed
    if op == "merge":
        result = _as_json(git.merge_branch(req.path, req.source or "", req.target or "", req.strategy))
        return result, bool(result.get("success"))
    if op == "gated_merge":
        result = _as_json(git.gated_merge(req.source or "", req.target or "", req.gate_cmd, req.strategy))
        return result, bool(result.get("success"))
    if op == "push":
        result = _as_json(git.push_to_remote(req.path, req.remote, req.branch))
        return result, bool(result.get("success"))
    if op == "verify_remote":
        result = _as_json(git.verify_remote(
            req.remote, req.branch or "", req.expected_sha or "", req.ssh_key_path,
            ref_type=req.ref_type,
        ))
        return result, False
    if op == "tag_release":
        return {"tag": git.tag_release(req.tag or "")}, True
    if op == "tag_checkpoint":
        return {"tag": git.tag_checkpoint(req.tag or "")}, True
    if op == "release_tag":
        return {"tag": git.create_release_tag(req.path)}, True
    if op == "checkpoint_tag":
        return {"tag": git.create_checkpoint_tag(req.path, req.todo_id or "", req.sha or "")}, True
    if op == "state":
        result = _as_json(git.workflow_state(
            remote=req.remote,
            ref=req.state_ref,
            gha_head_sha=req.state_gha_head_sha,
            worktree_target_ref=req.state_worktree_target_ref,
            preserve_branch_patterns=tuple(req.state_preserve_branch_patterns),
            reconciled_preserve_heads=tuple(req.state_reconciled_preserve_heads),
            reconciled_preserve_head_file=req.state_reconciled_preserve_head_file,
            assert_clean=req.state_assert_clean,
            assert_no_feature_on_master=req.state_assert_no_feature_on_master,
            assert_merge_ready=req.state_assert_merge_ready,
            assert_remote_head=req.state_assert_remote_head,
            assert_gha_matches_local=req.state_assert_gha_matches_local,
            assert_no_unintegrated_worktrees=req.state_assert_no_unintegrated_worktrees,
            assert_no_unintegrated_branches=req.state_assert_no_unintegrated_branches,
        ))
        return result, False
    if op == "batch_push":
        result = _as_json(batch_push(
            req.path, req.remote, req.branch or "master", req.threshold, req.force,
            check_ci=req.check_ci,
        ))
        return result, bool(result.get("pushed"))
    if op == "release_cut":
        result = _as_json(release_cut(
            req.release_tag or "", req.release_message, req.branch or "master", req.path,
            req.release_remote, skip_readme_check=req.skip_readme_check,
            skip_ci_check=req.skip_ci_check,
        ))
        return result, bool(result.get("success"))
    if op == "release_delete":
        result = _as_json(release_delete(
            req.release_tag or "", req.path, req.release_remote, req.release_repo,
        ))
        return result, bool(result.get("success"))
    if op == "release_recut":
        result = _as_json(release_recut(
            req.release_tag or "", req.release_message, req.branch or "master", req.path,
            req.release_remote,
        ))
        return result, bool(result.get("success"))
    if op == "ci_verdict":
        return dict(ci_verdict(req.branch or "development", req.sha)), False
    if op == "ci_cancel":
        return dict(ci_cancel(req.run_id or "")), True
    if op == "feature_start":
        branch = feature_start(git=git, name=req.feature_name or "", base=req.base or "master")
        return {"branch": branch, "success": True}, True
    if op == "feature_done":
        result = feature_done(git=git, name=req.feature_name or "", target=req.target or "development")
        return result, bool(result.get("success"))
    if op == "rebranch_onto":
        result = rebranch_onto(git=git, new_base=req.new_base or "", commits=req.commits)
        return result, bool(result.get("success"))
    if op == "ship_commit":
        collect_check(req.path)
        sha = ship_commit(req.message or "", git=git, files=req.files, push=req.push, repo_root=req.path)
        return {"sha": sha, "success": True}, True
    if op == "worktree_merge":
        result = _as_json(worktree_merge(req.path, req.branch or "", req.target or ""))
        return result, bool(result.get("success"))
    if op == "worktree_cleanup":
        result = worktree_cleanup(req.path, req.branch or "")
        return result, bool(result.get("cleaned") or result.get("branch_removed"))
    if op == "worktree_health_check":
        violations = worktree_health_check(req.path, req.max_age_hours, req.remote_name, req.target_branch)
        return {"violations": [
            {"worktree_path": item.worktree_path, "branch": item.branch,
             "reason": item.reason, "severity": item.severity}
            for item in violations
        ]}, False
    if op == "worktree_merge_all":
        result = worktree_merge_all(req.path, req.target or "development")
        return result, bool(result.get("merged") or result.get("skipped"))
    raise ValueError("unsupported git operation")


def register(app: FastAPI, daemon_state: dict[str, object]) -> None:
    """Register Git history and authenticated Git operation routes."""
    indexer = GitHistoryIndexer()
    mutation_store = IdempotencyStore()

    @app.post(
        "/admin/git/operation",
        dependencies=[Depends(RequireCapability(resource="admin:git", action="execute"))],
    )
    async def admin_git_operation(req: GitOperationRequest) -> dict[str, Any]:
        async def _run() -> dict[str, Any]:
            try:
                result, changed = await asyncio.to_thread(_dispatch_git, req)
            except (ValueError, subprocess.CalledProcessError, RuntimeError, ShipCommitError) as exc:
                raise HTTPException(status_code=422, detail=f"git operation rejected: {exc}") from exc
            except Exception as exc:
                raise HTTPException(status_code=500, detail="git operation failed") from exc
            return {"result": result, "changed": changed}

        payload = req.model_dump(exclude={"idempotency_key"}, mode="json")
        if req.op in _READ_ONLY_GIT_OPS:
            return await _run()
        return await mutation_store.run(
            key=req.idempotency_key,
            payload=payload,
            producer=_run,
        )

    @app.get("/api/git/history")
    async def search_history(
        q: str = Query(default="", description="Search commit messages and file paths"),
        since: str = Query(default="", description="Filter commits since date (YYYY-MM-DD or ISO)"),
        author: str = Query(default="", description="Filter by author (partial match)"),
        path: str = Query(default="", description="Filter by file path (partial match)"),
        limit: int = Query(default=100, ge=1, le=500, description="Max results"),
        offset: int = Query(default=0, ge=0, description="Pagination offset"),
    ) -> list[dict[str, object]]:
        try:
            results = indexer.search(
                query=q,
                since=since,
                author=author,
                path_filter=path,
                limit=limit,
                offset=offset,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Search failed: {exc}") from exc
        return [r.to_dict() for r in results]

    @app.get("/api/git/history/stats")
    async def history_stats() -> dict[str, object]:
        try:
            return indexer.stats()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Stats failed: {exc}") from exc

    @app.post("/api/git/history/reindex")
    async def reindex_history() -> dict[str, object]:
        try:
            count = indexer.index()
            return {"status": "ok", "indexed": count}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Reindex failed: {exc}") from exc
