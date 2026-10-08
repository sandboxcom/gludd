"""Exact-identity and bounded-wait contracts for release CI polling."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import ci_await as await_module  # noqa: E402

SHA = "abc123def4567890abc123def4567890abc123de"  # pragma: allowlist secret
OTHER_SHA = "9999999999999999999999999999999999999999"
TAG = "v0.1.1"


def _payload(
    *,
    run_id: int = 42,
    sha: str = SHA,
    ref: str = TAG,
    workflow: str = "Build and Release",
    event: str = "push",
    status: str = "queued",
    conclusion: str = "",
    created_at: str = "2026-09-27T12:00:00Z",
) -> dict[str, object]:
    return {
        "databaseId": run_id,
        "headSha": sha,
        "headBranch": ref,
        "workflowName": workflow,
        "event": event,
        "status": status,
        "conclusion": conclusion,
        "createdAt": created_at,
    }


def _selector() -> await_module.RunSelector:
    return await_module.RunSelector(
        ref=TAG,
        sha=SHA,
        workflow="Build and Release",
        event="push",
    )


def test_select_latest_run_requires_every_exact_identity_field() -> None:
    exact_old = _payload(run_id=40, created_at="2026-09-27T11:00:00Z")
    exact_new = _payload(run_id=41, created_at="2026-09-27T12:00:00Z")
    candidates = [
        _payload(run_id=1, sha=OTHER_SHA),
        _payload(run_id=2, ref="development"),
        _payload(run_id=3, workflow="Other workflow"),
        _payload(run_id=4, event="workflow_dispatch"),
        exact_old,
        exact_new,
    ]

    assert await_module.select_latest_run(candidates, _selector()) == exact_new


def test_tag_push_head_branch_is_bare_tag_name_not_full_git_ref() -> None:
    """GitHub reports a tag run's headBranch as ref_name, not refs/tags/name."""
    full_ref = _payload(run_id=43, ref=f"refs/tags/{TAG}")
    bare_tag = _payload(run_id=42, ref=TAG)

    assert await_module.select_latest_run([full_ref], _selector()) is None
    assert await_module.select_latest_run([full_ref, bare_tag], _selector()) == bare_tag


def test_select_latest_run_requires_a_run_newer_than_the_pre_push_baseline() -> None:
    selector = await_module.RunSelector(
        ref=TAG,
        sha=SHA,
        workflow="Build and Release",
        event="push",
        after_run_id=100,
    )
    old_success = _payload(
        run_id=100,
        status="completed",
        conclusion="success",
        created_at="2026-09-27T12:00:00Z",
    )
    new_pending = _payload(
        run_id=101,
        created_at="2026-09-27T12:00:01Z",
    )

    assert await_module.select_latest_run([old_success], selector) is None
    assert await_module.select_latest_run([old_success, new_pending], selector) == new_pending


def test_select_latest_run_ignores_malformed_payloads() -> None:
    assert await_module.select_latest_run(
        [None, "run", {}, {"headSha": SHA}],
        _selector(),
    ) is None
    assert await_module.select_latest_run({"runs": []}, _selector()) is None


def test_default_runner_uses_nonthrowing_captured_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["argv"] = list(argv)
        captured.update(kwargs)
        return subprocess.CompletedProcess(list(argv), 0, "[]", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = await_module._run(["gh", "run", "list"])

    assert result.returncode == 0
    assert captured == {
        "argv": ["gh", "run", "list"],
        "check": False,
        "capture_output": True,
        "text": True,
    }


def test_exact_sha_lookup_avoids_server_side_branch_filter_and_filters_locally() -> None:
    calls: list[list[str]] = []

    def runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        calls.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps([_payload(ref="development"), _payload(run_id=44)]),
            "",
        )

    result = await_module.get_latest_run(_selector(), runner=runner)

    assert result is not None
    assert result["databaseId"] == 44
    assert "--commit" in calls[0]
    assert calls[0][calls[0].index("--commit") + 1] == SHA
    assert "--branch" not in calls[0]
    assert "headBranch" in calls[0][calls[0].index("--json") + 1]


def test_generic_branch_lookup_keeps_cli_branch_filter() -> None:
    calls: list[list[str]] = []

    def runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "[]", "")

    selector = await_module.RunSelector(ref="development")

    assert await_module.get_latest_run(selector, runner=runner) is None
    assert "--branch" in calls[0]
    assert calls[0][calls[0].index("--branch") + 1] == "development"
    assert "--commit" not in calls[0]


@pytest.mark.parametrize("failure", ["os-error", "exit", "json", "shape"])
def test_lookup_failures_are_typed_and_fail_closed(failure: str) -> None:
    def runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if failure == "os-error":
            raise OSError("gh unavailable")
        if failure == "exit":
            return subprocess.CompletedProcess(args, 1, "", "API unavailable")
        if failure == "json":
            return subprocess.CompletedProcess(args, 0, "not-json", "")
        return subprocess.CompletedProcess(args, 0, "{}", "")

    with pytest.raises(await_module.AwaitError):
        await_module.get_latest_run(_selector(), runner=runner)


@pytest.mark.parametrize(
    ("status", "conclusion", "expected"),
    [
        ("completed", "success", 0),
        ("completed", "failure", 1),
        ("completed", "cancelled", 1),
    ],
)
def test_ci_await_returns_terminal_result_without_sleeping(
    status: str,
    conclusion: str,
    expected: int,
) -> None:
    sleeps: list[float] = []

    result = await_module.ci_await(
        _selector(),
        timeout=90,
        poll_interval=10,
        fetch=lambda _selector: _payload(status=status, conclusion=conclusion),
        clock=lambda: 0.0,
        sleep=sleeps.append,
        progress=lambda _message: None,
    )

    assert result == expected
    assert sleeps == []


def test_ci_await_retries_lookup_errors_then_succeeds() -> None:
    attempts: Iterator[await_module.AwaitError | dict[str, object]] = iter(
        [
            await_module.AwaitError("temporary GitHub API failure"),
            _payload(status="completed", conclusion="success"),
        ]
    )
    messages: list[str] = []

    def fetch(_selector: await_module.RunSelector) -> dict[str, object] | None:
        value = next(attempts)
        if isinstance(value, Exception):
            raise value
        return value

    assert (
        await_module.ci_await(
            _selector(),
            timeout=90,
            poll_interval=10,
            fetch=fetch,
            clock=iter([0.0, 0.0, 10.0]).__next__,
            sleep=lambda _seconds: None,
            progress=messages.append,
        )
        == 0
    )
    assert any("lookup-error" in message for message in messages)


def test_ci_await_timeout_is_bounded_and_nonzero() -> None:
    clock_values = iter([0.0, 0.0, 5.0])
    sleeps: list[float] = []

    result = await_module.ci_await(
        _selector(),
        timeout=5,
        poll_interval=10,
        fetch=lambda _selector: None,
        clock=clock_values.__next__,
        sleep=sleeps.append,
        progress=lambda _message: None,
    )

    assert result == 2
    assert sleeps == [5.0]


def test_branch_compatibility_wait_reports_pending_state_then_succeeds() -> None:
    runs = iter(
        [
            _payload(ref="development", status="queued"),
            _payload(
                ref="development",
                status="completed",
                conclusion="success",
            ),
        ]
    )
    messages: list[str] = []

    result = await_module.ci_await(
        "development",
        timeout=30,
        poll_interval=10,
        fetch=lambda _selector: next(runs),
        clock=iter([0.0, 0.0, 10.0]).__next__,
        sleep=lambda _seconds: None,
        progress=messages.append,
    )

    assert result == 0
    assert any("status=queued" in message for message in messages)


def test_unknown_pending_status_remains_nonterminal_until_timeout() -> None:
    messages: list[str] = []

    assert (
        await_module.ci_await(
            _selector(),
            timeout=1,
            poll_interval=1,
            fetch=lambda _selector: _payload(status="mystery"),
            clock=iter([0.0, 1.0]).__next__,
            sleep=lambda _seconds: None,
            progress=messages.append,
        )
        == 2
    )
    assert any("status=unknown:mystery" in message for message in messages)


def test_validate_only_cli_is_network_free_and_reports_exact_selector(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        await_module,
        "get_latest_run",
        lambda _selector: pytest.fail("validate-only must not query GitHub"),
    )

    assert (
        await_module.main(
            [
                "--ref",
                TAG,
                "--timeout",
                "5400",
                "--sha",
                SHA,
                "--workflow",
                "Build and Release",
                "--event",
                "push",
                "--poll-interval",
                "10",
                "--validate-only",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "CI-AWAIT-VALIDATED" in output
    assert f"sha={SHA}" in output
    assert f"ref={TAG}" in output


def test_snapshot_cli_returns_latest_exact_run_id_before_tag_mutation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        await_module,
        "get_latest_run",
        lambda _selector: _payload(run_id=73),
    )

    assert (
        await_module.main(
            [
                "--ref",
                TAG,
                "--sha",
                SHA,
                "--workflow",
                "Build and Release",
                "--event",
                "push",
                "--snapshot-only",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.strip() == "73"


@pytest.mark.parametrize(
    "args",
    [
        ["--sha", SHA[:-1], "--validate-only"],
        ["--timeout", "0", "--validate-only"],
        ["--poll-interval", "0", "--validate-only"],
        ["--after-run-id", "-1", "--validate-only"],
    ],
)
def test_cli_rejects_unbounded_or_inexact_configuration(args: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        await_module.main(args)

    assert error.value.code == 2


@pytest.mark.parametrize(
    "args",
    [
        ["--ref", " ", "--validate-only"],
        ["--repo", " ", "--validate-only"],
    ],
)
def test_cli_rejects_empty_identity_components(args: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        await_module.main(args)

    assert error.value.code == 2


def test_main_forwards_valid_configuration_to_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_wait(
        selector: object,
        timeout: int,
        *,
        poll_interval: float,
    ) -> int:
        captured.update(
            selector=selector,
            timeout=timeout,
            poll_interval=poll_interval,
        )
        return 7

    monkeypatch.setattr(await_module, "ci_await", fake_wait)

    assert (
        await_module.main(
            [
                "--ref",
                TAG,
                "--timeout",
                "90",
                "--sha",
                SHA,
                "--poll-interval",
                "10",
            ]
        )
        == 7
    )
    assert captured["timeout"] == 90
    assert captured["poll_interval"] == 10
    selector = captured["selector"]
    assert isinstance(selector, await_module.RunSelector)
    assert selector.sha == SHA


def _recipe(target: str) -> str:
    makefile = compose_makefile(ROOT / "Makefile")
    start = makefile.index(f"\n{target}:")
    end = makefile.find("\n\n", start)
    return makefile[start : len(makefile) if end == -1 else end]


def test_ci_await_make_target_forwards_exact_identity_and_validation_mode() -> None:
    recipe = _recipe("ci-await")

    for variable in (
        "BRANCH",
        "TIMEOUT",
        "SHA",
        "CI_AWAIT_WORKFLOW",
        "CI_AWAIT_EVENT",
        "CI_AWAIT_INTERVAL",
        "CI_AWAIT_VALIDATE_ONLY",
        "CI_AWAIT_AFTER_RUN_ID",
        "CI_AWAIT_SNAPSHOT_ONLY",
    ):
        assert f"$({variable})" in recipe
    assert "--validate-only" in recipe


@pytest.mark.parametrize("target", ["release-cut", "release-recut"])
def test_release_paths_await_exact_tag_run_before_final_verification(target: str) -> None:
    recipe = _recipe(target)
    await_pos = recipe.index("CI_AWAIT_SNAPSHOT_ONLY=0")
    artifact_pos = recipe.index("verify-release-artifact")
    completeness_pos = recipe.index("verify-release-completeness")

    assert await_pos < artifact_pos < completeness_pos
    assert 'BRANCH="$(TAG)"' in recipe
    assert "Build and Release" in recipe
    assert "CI_AWAIT_EVENT=push" in recipe
    assert "RELEASE_AWAIT_TIMEOUT" in recipe
    assert "RELEASE_AWAIT_INTERVAL" in recipe
    assert "CI_AWAIT_SNAPSHOT_ONLY=1" in recipe
    assert "CI_AWAIT_AFTER_RUN_ID" in recipe
    assert "VERIFY_POLLS" not in recipe
    assert "sleep 60" not in recipe

    snapshot_pos = recipe.index("CI_AWAIT_SNAPSHOT_ONLY=1")
    tag_mutation = (
        recipe.index("git-tag-push")
        if target == "release-cut"
        else recipe.index("git push sandboxcom :refs/tags")
    )
    assert snapshot_pos < tag_mutation < await_pos


def test_feature_doc_records_measured_wait_improvement_and_practitioner_evidence() -> None:
    overview = (ROOT / "docs" / "features" / "BETA4_DUAL_TRACK_CI.md").read_text(
        encoding="utf-8"
    )
    operations = (
        ROOT
        / "docs"
        / "features"
        / "beta4-dual-track-ci"
        / "exact-sha-promotion.md"
    ).read_text(encoding="utf-8")
    documentation = f"{overview}\n{operations}"

    assert "(beta4-dual-track-ci/exact-sha-promotion.md)" in overview
    assert "Exact-identity release wait" in documentation
    assert "average discovery latency" in documentation
    assert "github.com/cli/cli/issues/5474" in documentation
    assert "github.com/orgs/community/discussions/24626" in documentation
    assert "github.com/orgs/community/discussions/5673" in documentation
    assert "github.com/orgs/community/discussions/158805" in documentation
    assert "bare tag name" in documentation
