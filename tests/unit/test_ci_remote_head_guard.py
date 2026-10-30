from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from ci_remote_head_guard import (  # noqa: E402
    GuardError,
    collect_state,
    guard_state,
    main,
    validate_expected_head,
    validate_feature_ci,
    validate_feature_ci_inputs,
)


def _completed(
    argv: Sequence[str],
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(list(argv), returncode, stdout, stderr)


def test_collect_state_defaults_empty_ref_and_remote_to_current_branch() -> None:
    newline = chr(10)

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, "development" + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, "abc123" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _completed(args, "")
        if args == ["git", "ls-remote", "sandboxcom", "refs/heads/development"]:
            return _completed(args, "abc123" + chr(9) + "refs/heads/development" + newline)
        raise AssertionError(f"unexpected argv: {args!r}")

    state = collect_state("", "", run=run)

    assert state.branch == "development"
    assert state.remote == "sandboxcom"
    assert state.remote_ref == "refs/heads/development"
    assert state.remote_head == "abc123"
    assert guard_state(state) == []


def test_guard_blocks_stale_remote_head() -> None:
    newline = chr(10)

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, "release-sync" + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, "newhead" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _completed(args, "")
        if args == ["git", "ls-remote", "sandboxcom", "refs/heads/release-sync"]:
            return _completed(args, "oldhead" + chr(9) + "refs/heads/release-sync" + newline)
        raise AssertionError(f"unexpected argv: {args!r}")

    state = collect_state("release-sync", "sandboxcom", run=run)

    errors = guard_state(state)

    assert errors == ["remote sandboxcom/refs/heads/release-sync is oldhead, not local HEAD newhead"]


def test_guard_blocks_dirty_local_state_for_remote_ci() -> None:
    newline = chr(10)

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, "development" + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, "abc123" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _completed(args, " M Makefile" + newline + "?? scripts/new_guard.py" + newline)
        if args == ["git", "ls-remote", "sandboxcom", "refs/heads/development"]:
            return _completed(args, "abc123" + chr(9) + "refs/heads/development" + newline)
        raise AssertionError(f"unexpected argv: {args!r}")

    state = collect_state("development", "sandboxcom", run=run)

    errors = guard_state(state)

    assert "2 local dirty path(s) would make local tests differ from remote CI" in errors


def test_main_reports_blocked_state_for_missing_remote_branch(capsys: pytest.CaptureFixture[str]) -> None:
    newline = chr(10)

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, "feature-x" + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, "abc123" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _completed(args, "")
        if args == ["git", "ls-remote", "sandboxcom", "refs/heads/feature-x"]:
            return _completed(args, "")
        raise AssertionError(f"unexpected argv: {args!r}")

    rc = main(["--ref", "feature-x"], run=run)

    captured = capsys.readouterr()
    assert rc == 1
    assert "REMOTE-HEAD-BLOCKED" in captured.out
    assert "remote branch sandboxcom/refs/heads/feature-x does not exist" in captured.out


FULL_SHA = "a" * 40
BASE_SHA = "b" * 40
FEATURE_REF = "feature/owned"


class DiagnosticGitAdapter:
    """Unit coverage adapter; real Git/hook/provenance proof stays in the native owner."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.branch = FEATURE_REF
        self.head = FULL_SHA
        self.remote_head = FULL_SHA
        self.status = ""
        self.local_url = "https://github.com/sandboxcom/gludd.git"
        self.fetch_url = self.local_url
        self.push_url = self.local_url
        self.baseline_ref = "master"
        self.baseline_rows = BASE_SHA + "\trefs/heads/master\n"
        self.local_baseline = BASE_SHA
        self.bad_workflow_commit = ""
        self.workflow_history = ""
        self.failure: tuple[str, ...] = ()
        self.failure_status = 74

    def __call__(
        self, argv: Sequence[str], cwd: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd
        args = list(argv)
        self.calls.append(args)
        if self.failure and args[:len(self.failure)] == list(self.failure):
            return _completed(args, stderr="owned-native-failure", returncode=self.failure_status)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, self.branch + "\n")
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, self.head + "\n")
        if args[:3] == ["git", "status", "--porcelain=v1"]:
            return _completed(args, self.status)
        if args[:3] == ["git", "ls-remote", "sandboxcom"]:
            return _completed(args, self.remote_head + "\trefs/heads/" + self.branch + "\n" if self.remote_head else "")
        if args in (["git", "check-ref-format", "refs/heads/" + FEATURE_REF],
                    ["git", "check-ref-format", "refs/heads/" + self.baseline_ref]):
            return _completed(args)
        if args[:4] == ["git", "config", "--local", "--get-all"]:
            return _completed(args, self.local_url + "\n")
        if args == ["git", "remote", "get-url", "--all", "sandboxcom"]:
            return _completed(args, self.fetch_url + "\n")
        if args == ["git", "remote", "get-url", "--push", "--all", "sandboxcom"]:
            return _completed(args, self.push_url + "\n")
        if args[:4] == ["git", "ls-remote", "--exit-code", "--heads"]:
            return _completed(args, self.baseline_rows)
        if args == ["git", "rev-parse", "--verify", BASE_SHA + "^{commit}"]:
            return _completed(args, self.local_baseline + "\n")
        if args[:3] == ["git", "merge-base", "--is-ancestor"] or args[:3] == ["git", "diff", "--quiet"]:
            return _completed(args)
        if args[:3] == ["git", "log", "--format=%H"]:
            return _completed(args, self.workflow_history)
        if args[:3] == ["git", "cat-file", "-t"]:
            return _completed(args, "tree\n" if args[3].startswith(self.bad_workflow_commit + ":")
                              and self.bad_workflow_commit else "blob\n")
        raise AssertionError("unexpected owned Git call: " + repr(args))


def _diagnostic_parameters() -> dict[str, str]:
    return {
        "expected_branch": FEATURE_REF, "expected_sha": FULL_SHA,
        "baseline_ref": "master", "baseline_sha": BASE_SHA,
        "remote": "sandboxcom", "repo": "sandboxcom/gludd", "workflow": "Build and Release",
    }


@pytest.mark.parametrize(("field", "value"), (
    ("expected_branch", ""), ("expected_sha", ""), ("expected_sha", "bad"),
    ("expected_branch", "master"), ("expected_branch", "refs/tags/v0.1.2"),
    ("remote", "foreign"), ("repo", "foreign/repo"), ("workflow", "foreign.yml"),
    ("baseline_ref", ""), ("baseline_ref", "feature/owned"), ("baseline_sha", "c" * 39),
))
def test_diagnostic_input_policy_rejects_before_native_calls(field: str, value: str) -> None:
    parameters = _diagnostic_parameters()
    parameters[field] = value
    with pytest.raises(GuardError):
        validate_feature_ci_inputs(**parameters)


@pytest.mark.parametrize("baseline_ref", (
    "master", "main", "development", "release-candidate/v012-git-mutex-20261022",
    "release-candidate/owned/nested",
))
def test_diagnostic_baseline_namespace_keeps_publication_target_fence(baseline_ref: str) -> None:
    parameters = _diagnostic_parameters()
    parameters["baseline_ref"] = baseline_ref
    validate_feature_ci_inputs(**parameters)
    parameters["expected_branch"] = "release-candidate/owned"
    with pytest.raises(GuardError, match="feature/"):
        validate_feature_ci_inputs(**parameters)


@pytest.mark.parametrize("variable", ("FORCE", "GLUDD_FORCE_PUSH"))
def test_diagnostic_input_policy_refuses_force(
    monkeypatch: pytest.MonkeyPatch, variable: str,
) -> None:
    monkeypatch.setenv(variable, "1")
    with pytest.raises(GuardError, match="force"):
        validate_feature_ci_inputs(**_diagnostic_parameters())


@pytest.mark.parametrize("baseline_ref", ("master", "release-candidate/v012-git-mutex-20261022"))
@pytest.mark.parametrize("publication", (False, True))
def test_diagnostic_cli_uses_complete_native_baseline_provenance_and_scope(
    publication: bool, baseline_ref: str, capsys: pytest.CaptureFixture[str],
) -> None:
    adapter = DiagnosticGitAdapter()
    adapter.baseline_ref = baseline_ref
    adapter.baseline_rows = BASE_SHA + "\trefs/heads/" + baseline_ref + "\n"
    adapter.remote_head = "" if publication else FULL_SHA
    arguments = [
        "--purpose", "feature-ci", "--ref", FEATURE_REF, "--remote", "sandboxcom",
        "--expected-branch", FEATURE_REF, "--expected-sha", FULL_SHA,
        "--baseline-ref", baseline_ref, "--baseline-sha", BASE_SHA,
    ] + (["--publication"] if publication else [])
    assert main(arguments, run=adapter) == 0
    assert "FEATURE-CI-SOURCE-READY" in capsys.readouterr().out
    assert ["git", "check-ref-format", "refs/heads/" + baseline_ref] in adapter.calls
    assert ["git", "ls-remote", "--exit-code", "--heads", "sandboxcom", "refs/heads/" + baseline_ref] in adapter.calls
    assert ["git", "diff", "--quiet", BASE_SHA, FULL_SHA, "--", ".github/workflows"] in adapter.calls
    assert ["git", "merge-base", "--is-ancestor", BASE_SHA, FULL_SHA] in adapter.calls


@pytest.mark.parametrize(("field", "value", "message"), (
    ("remote_ref", "refs/heads/other", "heads-ref"),
    ("dirty_count", 1, "clean"), ("local_head", "c" * 40, "expected candidate"),
    ("branch", "feature/other", "expected candidate"),
    ("remote_head", "c" * 40, "remote head"),
))
def test_diagnostic_state_must_match_saved_native_identity(
    field: str, value: object, message: str,
) -> None:
    adapter = DiagnosticGitAdapter()
    state = collect_state(FEATURE_REF, run=adapter)
    assert field in {"remote_ref", "dirty_count", "local_head", "branch", "remote_head"}
    if field == "dirty_count":
        assert isinstance(value, int)
    else:
        assert isinstance(value, str)
    state = replace(
        state,
        remote_ref=value if field == "remote_ref" and isinstance(value, str) else state.remote_ref,
        dirty_count=value if field == "dirty_count" and isinstance(value, int) else state.dirty_count,
        local_head=value if field == "local_head" and isinstance(value, str) else state.local_head,
        branch=value if field == "branch" and isinstance(value, str) else state.branch,
        remote_head=value if field == "remote_head" and isinstance(value, str) else state.remote_head,
    )
    parameters = _diagnostic_parameters()
    parameters.pop("remote")
    assert set(parameters) == {
        "expected_branch", "expected_sha", "baseline_ref", "baseline_sha", "repo", "workflow",
    }
    with pytest.raises(GuardError, match=message):
        validate_feature_ci(
            state, run=adapter, expected_branch=parameters["expected_branch"],
            expected_sha=parameters["expected_sha"], baseline_ref=parameters["baseline_ref"],
            baseline_sha=parameters["baseline_sha"], repo=parameters["repo"],
            workflow=parameters["workflow"],
        )


@pytest.mark.parametrize(("field", "value", "message"), (
    ("local_url", "https://github.com/foreign/repo.git", "remote identity"),
    ("fetch_url", "/owned/rewrite", "remote identity"),
    ("push_url", "https://github.com/foreign/repo.git", "remote identity"),
    ("baseline_rows", "", "published baseline"),
    ("baseline_rows", "c" * 40 + "\trefs/heads/master\n", "published baseline"),
    ("local_baseline", "c" * 40, "local object"),
    ("bad_workflow_commit", BASE_SHA, "provenance"),
    ("bad_workflow_commit", FULL_SHA, "provenance"),
    ("workflow_history", "c" * 40, "workflow history"),
))
def test_diagnostic_provenance_refuses_invalid_native_evidence(
    field: str, value: str, message: str,
) -> None:
    adapter = DiagnosticGitAdapter()
    setattr(adapter, field, value)
    state = collect_state(FEATURE_REF, run=adapter)
    parameters = _diagnostic_parameters()
    parameters.pop("remote")
    assert set(parameters) == {
        "expected_branch", "expected_sha", "baseline_ref", "baseline_sha", "repo", "workflow",
    }
    with pytest.raises(GuardError, match=message):
        validate_feature_ci(
            state, run=adapter, expected_branch=parameters["expected_branch"],
            expected_sha=parameters["expected_sha"], baseline_ref=parameters["baseline_ref"],
            baseline_sha=parameters["baseline_sha"], repo=parameters["repo"],
            workflow=parameters["workflow"],
        )


@pytest.mark.parametrize("prefix", (
    ("git", "branch"), ("git", "status"), ("git", "config"), ("git", "check-ref-format"),
    ("git", "check-ref-format", "refs/heads/master"),
    ("git", "ls-remote", "--exit-code"), ("git", "rev-parse", "--verify", BASE_SHA + "^{commit}"),
    ("git", "merge-base"), ("git", "diff"), ("git", "cat-file"), ("git", "log"),
))
def test_diagnostic_cli_retains_native_git_failure_status(
    prefix: tuple[str, ...], capsys: pytest.CaptureFixture[str],
) -> None:
    adapter = DiagnosticGitAdapter()
    adapter.failure = prefix
    assert main([
        "--purpose", "feature-ci", "--ref", FEATURE_REF, "--expected-branch", FEATURE_REF,
        "--expected-sha", FULL_SHA, "--baseline-ref", "master", "--baseline-sha", BASE_SHA,
    ], run=adapter) == 74
    assert "owned-native-failure" in capsys.readouterr().err


def test_expected_pair_preserves_empty_default_and_refuses_partial_mismatch() -> None:
    state = collect_state(FEATURE_REF, run=DiagnosticGitAdapter())
    validate_expected_head(state, "", "")
    validate_expected_head(state, FEATURE_REF, FULL_SHA.upper())
    for branch, sha in (("", FULL_SHA), (FEATURE_REF, ""), (FEATURE_REF, "bad"), ("other", FULL_SHA)):
        with pytest.raises(GuardError):
            validate_expected_head(state, branch, sha)


def test_canonical_json_and_native_error_keep_original_scope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    adapter = DiagnosticGitAdapter()
    assert main(["--json"], run=adapter) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["is_clean"] and payload["remote_matches_local"]
    adapter.failure = ("git", "branch")
    assert main([], run=adapter) == 1
    assert "REMOTE-HEAD-UNKNOWN" in capsys.readouterr().out
    assert main(["--publication"], run=DiagnosticGitAdapter()) == 1
    assert "restricted" in capsys.readouterr().out


def test_remote_collection_consumes_existing_workflow_count_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import workflow_state_guard as workflow_owner

    adapter = DiagnosticGitAdapter()
    rows = (" M tracked.txt", "A  staged.txt", "?? untracked.txt", "D  removed.txt", "?? loose.txt")
    adapter.status = "\n".join(rows) + "\n"
    staged = workflow_owner._staged_count
    untracked = workflow_owner._untracked_count
    observed: list[tuple[str, tuple[str, ...]]] = []

    def staged_provider(lines: Sequence[str]) -> int:
        observed.append(("staged", tuple(lines)))
        return staged(lines)

    def untracked_provider(lines: Sequence[str]) -> int:
        observed.append(("untracked", tuple(lines)))
        return untracked(lines)

    monkeypatch.setattr(workflow_owner, "_staged_count", staged_provider)
    monkeypatch.setattr(workflow_owner, "_untracked_count", untracked_provider)
    state = collect_state(FEATURE_REF, run=adapter)
    assert (state.dirty_count, state.staged_count, state.untracked_count) == (5, 2, 2)
    assert state.status == list(rows)
    assert observed == [("staged", rows), ("untracked", rows)]
    errors = guard_state(state)
    assert "2 staged change(s) would not be present in the remote CI run" in errors
    assert "5 local dirty path(s) would make local tests differ from remote CI" in errors


def test_guard_script_uses_system_python_compatible_optional_annotations() -> None:
    script = (ROOT / "scripts" / "ci_remote_head_guard.py").read_text()

    assert " | None" not in script
    assert "from typing import Optional" in script
