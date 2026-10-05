"""Branch contracts for the beta4 Git and release control plane."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from general_ludd.git_automation import duplicate_targets, release_ops
from general_ludd.git_automation.duplicate_targets import DuplicateTarget
from general_ludd.git_automation.types import ReleaseCutResult, ReleaseDeleteResult, ReleaseRecutResult
from general_ludd.routers import git_history


@dataclass
class _Worktree:
    path: str = "/worktree"
    branch: str = "topic"


class _Git:
    changed = True

    def __init__(self, path: str) -> None:
        self.path = path

    def init_repo(self, _path: str) -> dict[str, object]:
        return {"created": True}

    def clone(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        return {"success": True, "already_present": False}

    def changed_files(self) -> list[str]:
        return ["file"] if self.changed else []

    def commit(self, message: str) -> str:
        return f"sha-{message}"

    def gated_commit(self, *_args: object) -> dict[str, object]:
        return {"success": True}

    def current_branch(self) -> str:
        return "development"

    def create_branch(self, branch: str) -> str:
        return branch

    def list_branches(self) -> list[str]:
        return ["development"]

    def delete_branch(self, _branch: str) -> bool:
        return True

    def list_worktrees(self, _path: str) -> list[_Worktree]:
        return [_Worktree()]

    def create_worktree(self, *_args: object) -> dict[str, object]:
        return {"success": True}

    def remove_worktree(self, *_args: object) -> bool:
        return True

    def merge_branch(self, *_args: object) -> dict[str, object]:
        return {"success": True}

    def gated_merge(self, *_args: object) -> dict[str, object]:
        return {"success": True}

    def push_to_remote(self, *_args: object) -> dict[str, object]:
        return {"success": True}

    def verify_remote(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        return {"verified": True}

    def tag_release(self, tag: str) -> str:
        return tag

    def tag_checkpoint(self, tag: str) -> str:
        return tag

    def create_release_tag(self, _path: str) -> str:
        return "release"

    def create_checkpoint_tag(self, *_args: object) -> str:
        return "checkpoint"

    def workflow_state(self, **_kwargs: object) -> dict[str, object]:
        return {"clean": True}


def _request(op: str, **kwargs: object) -> git_history.GitOperationRequest:
    return git_history.GitOperationRequest(op=op, path="/repo", **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("op", "kwargs", "changed"),
    [
        ("init", {}, True),
        ("clone", {"clone_url": "https://example.test/repo", "target_dir": "/target"}, True),
        ("commit", {"message": "message"}, True),
        ("gated_commit", {"message": "message", "gate_cmd": ["make", "gate"]}, True),
        ("current_branch", {}, False),
        ("branch", {"branch": "topic"}, True),
        ("branch_list", {}, False),
        ("branch_delete", {"branch": "topic"}, True),
        ("worktree_list", {}, False),
        ("worktree_create", {"branch": "topic", "worktree_path": "/wt"}, True),
        ("worktree_remove", {"worktree_path": "/wt"}, True),
        ("merge", {"source": "topic", "target": "development"}, True),
        (
            "gated_merge",
            {"source": "topic", "target": "development", "gate_cmd": ["make", "gate"]},
            True,
        ),
        ("push", {"branch": "topic"}, True),
        ("verify_remote", {"branch": "topic", "expected_sha": "a" * 40}, False),
        ("tag_release", {"tag": "v1"}, True),
        ("tag_checkpoint", {"tag": "checkpoint"}, True),
        ("release_tag", {}, True),
        ("checkpoint_tag", {"todo_id": "todo", "sha": "a" * 40}, True),
        ("state", {}, False),
    ],
)
def test_git_dispatch_allowlist(op: str, kwargs: dict[str, object], changed: bool) -> None:
    with patch("general_ludd.routers.git_history.GitAutomation", _Git):
        result, did_change = git_history._dispatch_git(_request(op, **kwargs))
    assert isinstance(result, dict)
    assert did_change is changed


def test_git_dispatch_noop_commit_and_existing_branch() -> None:
    _Git.changed = False
    try:
        with patch("general_ludd.routers.git_history.GitAutomation", _Git):
            result, changed = git_history._dispatch_git(_request("commit", message="nothing"))
        assert result["message"] == "nothing to commit"
        assert changed is False
    finally:
        _Git.changed = True

    git = MagicMock(spec=_Git)
    git.create_branch.side_effect = subprocess.CalledProcessError(
        1,
        ["git"],
        stderr="fatal: branch already exists",
    )
    with patch("general_ludd.routers.git_history.GitAutomation", return_value=git):
        result, changed = git_history._dispatch_git(_request("branch", branch="topic"))
    assert result == {"branch": "topic", "already_present": True}
    assert changed is False


def test_git_dispatch_release_and_ci_adapters() -> None:
    with (
        patch(
            "general_ludd.routers.git_history.batch_push",
            return_value={"pushed": True},
        ),
        patch(
            "general_ludd.routers.git_history.release_cut",
            return_value=ReleaseCutResult(success=True, tag="v1", branch="master"),
        ),
        patch(
            "general_ludd.routers.git_history.release_delete",
            return_value=ReleaseDeleteResult(success=True, tag="v1"),
        ),
        patch(
            "general_ludd.routers.git_history.release_recut",
            return_value=ReleaseRecutResult(success=True, tag="v1"),
        ),
        patch("general_ludd.routers.git_history.ci_verdict", return_value={"green": True}),
        patch("general_ludd.routers.git_history.ci_cancel", return_value={"cancelled": True}),
    ):
        cases = [
            (_request("batch_push", branch="topic"), True),
            (_request("release_cut", release_tag="v1"), True),
            (_request("release_delete", release_tag="v1"), True),
            (_request("release_recut", release_tag="v1"), True),
            (_request("ci_verdict", branch="topic"), False),
            (_request("ci_cancel", run_id="123"), True),
        ]
        for request, expected_changed in cases:
            result, changed = git_history._dispatch_git(request)
            assert result
            assert changed is expected_changed


def test_git_request_validation_and_json_boundary() -> None:
    assert git_history.GitOperationRequest._bound_list_items([]) == []
    with pytest.raises(ValueError, match="list entries"):
        git_history.GitOperationRequest._bound_list_items([""])
    assert git_history.GitOperationRequest._allowlist_gate_command([]) == []
    for command in (["pytest"], ["make", ""], ["make", "-f"], ["make", "bad;token"]):
        with pytest.raises(ValueError):
            git_history.GitOperationRequest._allowlist_gate_command(command)
    with pytest.raises(ValidationError, match="requires"):
        _request("clone")
    with pytest.raises(TypeError, match="unsupported"):
        git_history._as_json(object())
    invalid = git_history.GitOperationRequest.model_construct(op="invalid", path="/repo")
    with patch("general_ludd.routers.git_history.GitAutomation", _Git), pytest.raises(ValueError):
        git_history._dispatch_git(invalid)


@pytest.mark.parametrize(
    ("side_effect", "expected"),
    [
        (subprocess.CalledProcessError(3, ["git"], stderr="bad"), (3, "bad")),
        (subprocess.TimeoutExpired(["git"], 1), (1, "git status timed out after 120.0s")),
        (FileNotFoundError(), (1, "git executable not found")),
    ],
)
def test_run_git_failures(side_effect: BaseException, expected: tuple[int, str]) -> None:
    with patch("general_ludd.git_automation.release_ops.subprocess.run", side_effect=side_effect):
        assert release_ops._run_git(["status"], ".") == expected


def test_run_git_and_gh_success_and_failures() -> None:
    proc = SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
    with patch("general_ludd.git_automation.release_ops.subprocess.run", return_value=proc):
        assert release_ops._run_git(["status"], ".") == (0, "ok")
        assert release_ops._run_gh(["run", "list"]) == (0, "ok")
    failures: list[tuple[BaseException, str]] = [
        (subprocess.CalledProcessError(2, ["gh"], stderr="denied"), "denied"),
        (subprocess.TimeoutExpired(["gh"], 1), "timed out"),
        (FileNotFoundError(), "not found"),
    ]
    for error, message in failures:
        with patch("general_ludd.git_automation.release_ops.subprocess.run", side_effect=error):
            rc, output = release_ops._run_gh(["run"])
        assert rc != 0
        assert message in output


def test_release_git_helpers_are_bounded_and_fail_closed() -> None:
    with pytest.raises(ValueError, match="begins"):
        release_ops._reject_leading_dash("--force", kind="ref")
    proc = SimpleNamespace(returncode=0, stdout="v1\nv2\n", stderr="")
    with patch("general_ludd.git_automation.release_ops.subprocess.run", return_value=proc):
        assert release_ops._git_tag_exists("v1", ".") is True
        assert release_ops._git_rev_parse(".") == "v1\nv2"
        assert release_ops._run_git_tag_exists("v2", ".") is True
    proc = SimpleNamespace(returncode=1, stdout="", stderr="bad")
    with patch("general_ludd.git_automation.release_ops.subprocess.run", return_value=proc):
        assert release_ops._git_rev_parse(".") == ""


def test_tag_push_and_delete_adapters() -> None:
    with patch("general_ludd.git_automation.release_ops._run_git", side_effect=[(1, "tag bad")]):
        assert release_ops._git_tag_push("v1", "msg", ".") == (1, "tag bad")
    with patch(
        "general_ludd.git_automation.release_ops._run_git",
        side_effect=[(0, "tagged"), (1, "push bad")],
    ):
        assert "push failed" in release_ops._git_tag_push("v1", None, ".")[1]
    with patch(
        "general_ludd.git_automation.release_ops._run_git",
        side_effect=[(0, "tagged"), (0, "pushed")],
    ):
        assert release_ops._git_tag_push("v1", None, ".") == (0, "Pushed tag v1 to sandboxcom/v1")
    with patch("general_ludd.git_automation.release_ops._run_git", return_value=(0, "ok")) as run:
        assert release_ops._git_push_branch(".", "origin", "topic") == (0, "ok")
        assert release_ops._git_tag_delete_local("v1", ".") == (0, "ok")
        assert release_ops._git_tag_delete_remote("v1", ".", "origin") == (0, "ok")
        assert run.call_count == 3
    with patch("general_ludd.git_automation.release_ops._run_gh", return_value=(0, "ok")):
        assert release_ops._gh_release_delete("v1", "owner/repo") == (0, "ok")


@pytest.mark.parametrize(
    ("payload", "rc", "text"),
    [
        ([], 1, "no run"),
        ([{"conclusion": "success", "databaseId": 1, "status": "completed"}], 0, "GREEN"),
        ([{"conclusion": "cancelled", "databaseId": 2, "status": "completed"}], 0, "BYPASS"),
        ([{"conclusion": "failure", "databaseId": 3, "status": "completed"}], 1, "RED"),
        ([{"conclusion": None, "databaseId": 4, "status": "queued"}], 2, "PENDING"),
    ],
)
def test_require_ci_green_conclusions(payload: list[dict[str, object]], rc: int, text: str) -> None:
    with patch("general_ludd.git_automation.release_ops._run_gh", return_value=(0, json.dumps(payload))):
        result_rc, output = release_ops._run_require_ci_green("a" * 40, "development")
    assert result_rc == rc
    assert text in output


def test_require_ci_green_transport_and_parse_failures() -> None:
    with patch("general_ludd.git_automation.release_ops._git_rev_parse", return_value=""):
        assert release_ops._run_require_ci_green()[0] == 1
    with patch("general_ludd.git_automation.release_ops._run_gh", return_value=(1, "offline")):
        assert "gh run list failed" in release_ops._run_require_ci_green("a" * 40)[1]
    with patch("general_ludd.git_automation.release_ops._run_gh", return_value=(0, "not-json")):
        assert "could not parse" in release_ops._run_require_ci_green("a" * 40)[1]


def test_readme_status_missing_stale_and_current(tmp_path: Path) -> None:
    fake_file = tmp_path / "a" / "b" / "c" / "release_ops.py"
    fake_file.parent.mkdir(parents=True)
    with patch("general_ludd.git_automation.release_ops.__file__", str(fake_file)):
        assert release_ops.verify_readme_status("v1")[0] == 1
        (tmp_path / "README.md").write_text("no status here")
        assert release_ops.verify_readme_status("v1")[0] == 1
        (tmp_path / "README.md").write_text("Status as of v2")
        assert release_ops.verify_readme_status("v1")[0] == 1
        assert release_ops.verify_readme_status("v2")[0] == 0


def test_release_repository_errors_fail_closed() -> None:
    with patch("general_ludd.git_automation.release_ops._run_git_tag_exists", side_effect=OSError("bad repo")):
        assert release_ops.release_cut("v1").success is False
        assert release_ops.release_delete("v1").success is False
        assert release_ops.release_recut("v1").success is False


def test_duplicate_target_value_and_main_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert DuplicateTarget("a", 2, [1, 2]) == DuplicateTarget("a", 2, [1, 2])
    assert DuplicateTarget("a", 2, [1, 2]) != object()
    assert "DuplicateTarget" in repr(DuplicateTarget("a", 2, [1, 2]))

    missing = tmp_path / "missing"
    with patch.object(sys, "argv", ["check", str(missing)]):
        assert duplicate_targets.main() == 1
    makefile = tmp_path / "Makefile"
    makefile.write_text("a:\n\t@true\na:\n\t@true\n")
    with patch.object(sys, "argv", ["check", str(makefile)]):
        assert duplicate_targets.main() == 1
    assert "DUPLICATE TARGETS" in capsys.readouterr().err
    makefile.write_text("a:\n\t@true\n")
    with patch.object(sys, "argv", ["check", str(makefile)]):
        assert duplicate_targets.main() == 0
