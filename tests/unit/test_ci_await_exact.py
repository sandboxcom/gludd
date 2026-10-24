"""Exact-identity and bounded-wait contracts for release CI polling."""

from __future__ import annotations

import fcntl
import io
import json
import os
import shlex
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


@pytest.mark.parametrize("conclusion, expected", [("success", 0), ("failure", 1)])
def test_waiter_automatically_publishes_terminal_observation(
    tmp_path: Path, conclusion: str, expected: int,
) -> None:
    path = tmp_path / "ci-current.json"
    result = await_module.ci_await(
        _selector(), 90, fetch=lambda _: _payload(status="completed", conclusion=conclusion),
        observation_path=path, progress=lambda _: None,
    )
    assert result == expected
    record = json.loads(path.read_text())
    assert record["producer"] == "ci-await"
    assert record["identity"]["sha"] == SHA
    assert record["state"] == conclusion
    assert record["evidence"]["run"]["databaseId"] == 42
    assert record["evidence"]["exit_code"] == expected
    assert record["release_ready"] is False


def test_waiter_publishes_pending_before_sleep_and_timeout_before_return(tmp_path: Path) -> None:
    path = tmp_path / "ci-current.json"
    elapsed = [0.0]
    observed: list[str] = []

    def sleep(seconds: float) -> None:
        observed.append(json.loads(path.read_text())["state"])
        elapsed[0] += seconds

    result = await_module.ci_await(
        _selector(), 1, poll_interval=1, fetch=lambda _: _payload(),
        clock=lambda: elapsed[0], sleep=sleep, progress=lambda _: None,
        observation_path=path,
    )
    assert result == 2
    assert observed == ["pending"]
    final = json.loads(path.read_text())
    assert final["state"] == "timeout"
    assert final["evidence"]["exit_code"] == 2
    assert final["evidence"]["run"]["status"] == "queued"


def test_waiter_replaces_old_success_when_lookup_fails(tmp_path: Path) -> None:
    path = tmp_path / "ci-current.json"
    await_module.ci_await(_selector(), 1, observation_path=path,
                          fetch=lambda _: _payload(status="completed", conclusion="success"),
                          progress=lambda _: None)
    observed: list[str] = []
    elapsed = [0.0]

    def fetch(_: await_module.RunSelector) -> dict[str, object]:
        raise await_module.AwaitError("network unavailable")

    def sleep(seconds: float) -> None:
        snapshot = json.loads(path.read_text())
        observed.append(snapshot["state"])
        assert snapshot["evidence"]["run"] is None
        assert "network unavailable" in snapshot["evidence"]["error"]
        elapsed[0] += seconds

    assert await_module.ci_await(
        _selector(), 1, poll_interval=1, fetch=fetch, clock=lambda: elapsed[0],
        sleep=sleep, observation_path=path, progress=lambda _: None,
    ) == 2
    assert observed == ["lookup_error"]
    assert json.loads(path.read_text())["state"] == "timeout"


def test_waiter_refuses_foreign_run_as_durable_success(tmp_path: Path) -> None:
    path = tmp_path / "ci-current.json"
    messages: list[str] = []
    result = await_module.ci_await(
        _selector(), 1, observation_path=path, progress=messages.append,
        fetch=lambda _: _payload(sha=OTHER_SHA, status="completed", conclusion="success"),
    )
    assert result != 0
    assert not path.exists()
    assert any("observation" in message and "identity" in message for message in messages)


def test_waiter_observation_write_failure_is_not_success(tmp_path: Path) -> None:
    path = tmp_path / "manual-history.json"
    path.write_text('{"keep": true}')
    messages: list[str] = []
    assert await_module.ci_await(
        _selector(), 1, observation_path=path, progress=messages.append,
        fetch=lambda _: _payload(status="completed", conclusion="success"),
    ) != 0
    assert json.loads(path.read_text()) == {"keep": True}
    assert any("observation" in message for message in messages)


def test_waiter_validate_only_does_not_create_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "ci-current.json"
    monkeypatch.setattr(await_module, "get_latest_run", lambda _: pytest.fail("no network"))
    assert await_module.main([
        "--ref", TAG, "--sha", SHA, "--observation-output", str(path), "--validate-only",
    ]) == 0
    assert not path.exists()


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


class _ProgressPipe(io.StringIO):
    """Record only output explicitly made visible to a piped caller."""

    def __init__(self) -> None:
        super().__init__()
        self.flushed: list[str] = []

    def flush(self) -> None:
        self.flushed.append(self.getvalue())
        super().flush()


def test_default_progress_is_flushed_before_first_remote_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = _ProgressPipe()
    monkeypatch.setattr(sys, "stdout", pipe)

    def fetch(_selector: await_module.RunSelector) -> dict[str, object]:
        assert pipe.flushed, "startup must be observable before remote I/O"
        assert "=== CI-AWAIT:" in pipe.flushed[-1]
        return _payload(status="completed", conclusion="success")

    assert await_module.ci_await(_selector(), 90, fetch=fetch, clock=lambda: 0.0) == 0
    assert "TERMINAL SUCCESS" in pipe.flushed[-1]


def test_default_progress_flushes_heartbeat_before_sleep_and_terminal_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = _ProgressPipe()
    monkeypatch.setattr(sys, "stdout", pipe)
    runs = iter([_payload(), _payload(status="completed", conclusion="failure")])

    def sleep(seconds: float) -> None:
        assert seconds == 10
        assert pipe.flushed, "heartbeat must be observable before waiting"
        assert "heartbeat; next check in 10s" in pipe.flushed[-1]

    assert await_module.ci_await(
        _selector(), 90, poll_interval=10,
        fetch=lambda _selector: next(runs),
        clock=iter([0.0, 0.0, 10.0]).__next__, sleep=sleep,
    ) == 1
    assert "TERMINAL FAILURE" in pipe.flushed[-1]


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
    "run",
    [
        _payload(run_id=73),
        _payload(run_id=73, status="completed", conclusion="failure"),
        _payload(run_id=73, status="completed", conclusion="success"),
        None,
    ],
    ids=["queued", "failure", "success", "no-run"],
)
def test_snapshot_mode_guidance_preserves_lookup_stdout_and_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], run: dict[str, object] | None,
) -> None:
    path = tmp_path / "current.json"
    sentinel = b'{"existing": "observation must remain unchanged"}\n'
    path.write_bytes(sentinel)
    queried: list[await_module.RunSelector] = []

    def lookup(selector: await_module.RunSelector) -> dict[str, object] | None:
        queried.append(selector)
        return run

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("snapshot mode must not wait or write an observation")

    monkeypatch.setattr(await_module, "get_latest_run", lookup)
    monkeypatch.setattr(await_module, "ci_await", forbidden)
    monkeypatch.setattr(await_module, "write_snapshot", forbidden)
    assert await_module.main([
        "--ref", TAG, "--sha", SHA, "--workflow", "Build and Release",
        "--event", "push", "--after-run-id", "17", "--snapshot-only",
        "--observation-output", str(path),
    ]) == 0
    captured = capsys.readouterr()
    assert captured.out == ("73\n" if run is not None else "0\n")
    assert path.read_bytes() == sentinel
    assert queried == [await_module.RunSelector(
        ref=TAG, sha=SHA, workflow="Build and Release", event="push", after_run_id=17,
    )]
    assert captured.err.count("AGENT_NEXT") == 1
    for token in (
        "target=ci-await", "outcome=snapshot", "mode=snapshot-only",
        "native_status=0", "lookup_only=true", "terminal_verdict=false",
        "ci_green=false", "observation_written=false", "gate_evidence=false",
        "release_ready=false", "CI_AWAIT_SNAPSHOT_ONLY=0",
        "same selector and after-run ID",
        "CI_AWAIT_AFTER_RUN_ID only when awaiting a future run",
    ):
        assert token in captured.err


@pytest.mark.parametrize("snapshot_enabled", [False, True])
def test_validate_mode_guidance_is_offline_and_preserves_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], snapshot_enabled: bool,
) -> None:
    path = tmp_path / "current.json"
    sentinel = b'{"existing": "observation must remain unchanged"}\n'
    path.write_bytes(sentinel)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("validate mode must not query, wait or write an observation")

    for name in ("get_latest_run", "ci_await", "write_snapshot"):
        monkeypatch.setattr(await_module, name, forbidden)
    args = [
        "--ref", TAG, "--sha", SHA, "--workflow", "Build and Release",
        "--event", "push", "--after-run-id", "17", "--timeout", "90",
        "--poll-interval", "10", "--validate-only",
        "--observation-output", str(path),
    ]
    if snapshot_enabled:
        args.append("--snapshot-only")
    assert await_module.main(args) == 0
    captured = capsys.readouterr()
    assert captured.out == (
        f"CI-AWAIT-VALIDATED ref={TAG} sha={SHA} workflow='Build and Release' "
        "event=push after_run_id=17 timeout=90s interval=10s\n"
    )
    assert path.read_bytes() == sentinel
    assert captured.err.count("AGENT_NEXT") == 1
    for token in (
        "target=ci-await", "outcome=validated", "mode=validate-only",
        "native_status=0", "configuration_only=true", "network=false",
        "terminal_verdict=false", "ci_green=false", "observation_written=false",
        "gate_evidence=false", "release_ready=false",
        "CI_AWAIT_VALIDATE_ONLY=0", "CI_AWAIT_SNAPSHOT_ONLY=0",
    ):
        assert token in captured.err


def test_snapshot_lookup_failure_guidance_preserves_native_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "current.json"
    sentinel = b'{"existing": "observation must remain unchanged"}\n'
    path.write_bytes(sentinel)

    def lookup(_selector: await_module.RunSelector) -> None:
        raise await_module.AwaitError("owned lookup refusal")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("failed snapshot lookup must not wait or write an observation")

    monkeypatch.setattr(await_module, "get_latest_run", lookup)
    monkeypatch.setattr(await_module, "ci_await", forbidden)
    monkeypatch.setattr(await_module, "write_snapshot", forbidden)
    assert await_module.main([
        "--ref", TAG, "--sha", SHA, "--snapshot-only",
        "--observation-output", str(path),
    ]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert path.read_bytes() == sentinel
    assert captured.err.startswith("CI-AWAIT-SNAPSHOT-BLOCKED error=owned lookup refusal\n")
    assert captured.err.count("AGENT_NEXT") == 1
    for token in (
        "target=ci-await", "outcome=lookup-failed", "mode=snapshot-only",
        "native_status=1", "terminal_verdict=false", "ci_green=false",
        "observation_written=false", "gate_evidence=false", "release_ready=false",
        "Repair the reported lookup input before retry",
    ):
        assert token in captured.err


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


class _DiagnosticWorkspace:
    """Run the real waiter and unchanged AA032 rule with confined adapters."""

    def __init__(self, path: Path, run: dict[str, object]) -> None:
        self.history = path / "history.json"
        self.state = path / "state.json"
        self.restart = path / "restart-count"
        self.observation = path / "observation.json"
        self.calls = path / "gh-calls.jsonl"
        self.history.write_text(json.dumps({
            "last_push_sha": SHA, "last_checked_sha": "", "operator_note": "retain",
        }))
        self.restart.write_text("3")
        payload = path / "run.json"
        payload.write_text(json.dumps([run]))
        binary = path / "bin"
        binary.mkdir()
        for name, body in {
            "gh": (
                "import json, os, pathlib, subprocess, sys\n"
                "with open(os.environ['GLUDD_TEST_GH_CALLS'], 'a') as stream:\n"
                "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "if os.environ.get('GLUDD_TEST_GH_FAIL'):\n"
                "    print('owned lookup unavailable', file=sys.stderr)\n"
                "    sys.exit(7)\n"
                "if pushed := os.environ.get('GLUDD_TEST_GH_NEW_PUSH'):\n"
                "    subprocess.run([sys.executable, os.environ['GLUDD_TEST_HISTORY_OWNER'], "
                "'record-push', pushed], check=True, capture_output=True)\n"
                "print(pathlib.Path(os.environ['GLUDD_TEST_GH_RUNS']).read_text())\n"
            ),
            "git": f"print({OTHER_SHA!r})\n",
        }.items():
            script = binary / name
            script.write_text(f"#!{sys.executable}\n{body}")
            script.chmod(0o755)
        self.environment = os.environ.copy()
        for key in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "FORCE"):
            self.environment.pop(key, None)
        self.environment.update({
            "PATH": f"{binary}{os.pathsep}{Path(sys.executable).parent}"
                    f"{os.pathsep}{os.environ.get('PATH', '')}",
            "GLUDD_CI_STATE_FILE": str(self.state),
            "GLUDD_CI_HISTORY_FILE": str(self.history),
            "GLUDD_CI_RESTART_COUNT_FILE": str(self.restart),
            "GLUDD_PUSH_STATE_FILE": str(path / "push-state.json"),
            "GLUDD_TEST_GH_RUNS": str(payload),
            "GLUDD_TEST_GH_CALLS": str(self.calls),
            "GLUDD_TEST_HISTORY_OWNER": str(ROOT / "scripts" / "ci_check_cooldown.py"),
        })
        self.guard_file = path / "guard.mk"
        rule = _recipe("_ci-verdict-history-guard")
        old = "STATE_FILE=/tmp/gludd-ci-verdict-history.json;"
        assert rule.count(old) == 1
        self.guard_file.write_text(rule.replace(
            old, f"STATE_FILE={shlex.quote(str(self.history))};",
        ) + "\n")
        self.command = [
            sys.executable, str(ROOT / "scripts" / "ci_await.py"),
            "--ref", str(run["headBranch"]), "--sha", SHA,
            "--workflow", "Build and Release", "--event", "workflow_dispatch",
            "--timeout", "1", "--poll-interval", "0.01",
            "--observation-output", str(self.observation),
        ]

    def guard(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["make", "--no-print-directory", "-f", str(self.guard_file),
             "_ci-verdict-history-guard"],
            env=self.environment, capture_output=True, text=True, check=False, timeout=10,
        )

    def wait(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*self.command, *extra], env=self.environment,
            capture_output=True, text=True, check=False, timeout=10,
        )


@pytest.mark.parametrize("conclusion, status", [("failure", 1), ("success", 0)])
def test_diagnostic_terminal_cli_records_exact_feedback_before_next_push_guard(
    tmp_path: Path, conclusion: str, status: int,
) -> None:
    workspace = _DiagnosticWorkspace(tmp_path, _payload(
        ref="feature/owned-feedback", event="workflow_dispatch",
        status="completed", conclusion=conclusion,
    ))
    before = workspace.guard()
    assert before.returncode != 0
    assert "never CI-verified" in before.stdout

    result = workspace.wait()

    assert result.returncode == status, result.stdout + result.stderr
    assert len(workspace.calls.read_text().splitlines()) == 1
    history = json.loads(workspace.history.read_text())
    assert history["last_push_sha"] == SHA
    assert history["last_checked_sha"] == SHA
    assert history["last_verdict_kind"] == "diagnostic-workflow-dispatch"
    assert history["last_verdict"] == (
        "failure" if conclusion == "failure" else "diagnostic_success"
    )
    assert history["diagnostic_run"]["id"] == 42
    assert history["operator_note"] == "retain"
    assert workspace.restart.read_text() == "0"
    assert workspace.guard().returncode == 0
    for token in (
        f"native_status={status}",
        "history_recorded=true", "canonical_push=false", "ci_green=false",
        "gate_evidence=false", "release_ready=false",
    ):
        assert token in result.stdout + result.stderr


@pytest.mark.parametrize(
    "mode", [
        "validate", "snapshot", "pending", "lookup-error", "foreign-push",
        "observation-error", "no-observation",
    ],
)
def test_diagnostic_cli_without_complete_owned_terminal_proof_never_records_history(
    tmp_path: Path, mode: str,
) -> None:
    run = _payload(
        ref="diagnostic/owned-feedback", event="workflow_dispatch",
        status="completed", conclusion="success",
    )
    if mode == "pending":
        run.update(status="queued", conclusion="")
    workspace = _DiagnosticWorkspace(tmp_path, run)
    if mode == "foreign-push":
        workspace.history.write_text(json.dumps({
            "last_push_sha": OTHER_SHA, "last_checked_sha": "", "keep": "foreign",
        }))
    if mode == "observation-error":
        workspace.observation.write_text('{"keep": "manual history"}')
    if mode == "no-observation":
        workspace.command = workspace.command[:-2]
    if mode == "lookup-error":
        workspace.environment["GLUDD_TEST_GH_FAIL"] = "1"
    before = workspace.history.read_bytes()
    extra = {"validate": "--validate-only", "snapshot": "--snapshot-only"}

    result = workspace.wait(*([extra[mode]] if mode in extra else []))

    assert result.returncode == (
        2 if mode in {"pending", "lookup-error"}
        else 1 if mode == "observation-error" else 0
    ), result.stdout + result.stderr
    assert workspace.history.read_bytes() == before
    assert not workspace.state.exists()
    assert workspace.restart.read_text() == "3"
    assert "history_recorded=true" not in result.stdout + result.stderr
    if mode == "validate":
        assert not workspace.calls.exists()
    else:
        assert workspace.calls.exists()


def test_diagnostic_cli_history_lock_failure_invalidates_terminal_success(
    tmp_path: Path,
) -> None:
    workspace = _DiagnosticWorkspace(tmp_path, _payload(
        ref="feature/owned-feedback", event="workflow_dispatch",
        status="completed", conclusion="success",
    ))
    before = workspace.history.read_bytes()
    lock = workspace.history.with_name(f"{workspace.history.name}.lock")
    with lock.open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = workspace.wait()
        assert result.returncode == 1, result.stdout + result.stderr
        assert "history writer busy" in result.stdout
        assert "outcome=diagnostic-feedback-failed" in result.stdout
        assert workspace.history.read_bytes() == before
        assert workspace.restart.read_text() == "3"
        assert not workspace.state.exists()
        snapshot = json.loads(workspace.observation.read_text())
        assert snapshot["state"] == "lookup_error"
        assert "history writer busy" in snapshot["evidence"]["error"]
    assert workspace.wait().returncode == 0
    assert workspace.restart.read_text() == "0"


@pytest.mark.parametrize("writer", ["record-push", "record-verdict"])
def test_existing_native_history_cli_writers_share_diagnostic_lock(
    tmp_path: Path, writer: str,
) -> None:
    workspace = _DiagnosticWorkspace(tmp_path, _payload(
        ref="feature/owned-feedback", event="workflow_dispatch",
        status="completed", conclusion="success",
    ))
    before = workspace.history.read_bytes()
    args = [writer, SHA] if writer == "record-push" else [writer, "failure", SHA]
    lock = workspace.history.with_name(f"{workspace.history.name}.lock")
    with lock.open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "ci_check_cooldown.py"), *args],
            env=workspace.environment, capture_output=True, text=True, check=False, timeout=10,
        )
    assert result.returncode != 0
    assert "history writer busy" in result.stdout + result.stderr
    assert workspace.history.read_bytes() == before
    assert workspace.restart.read_text() == "3"
    assert not workspace.state.exists()


def test_diagnostic_feedback_cannot_certify_canonical_terminal_verdict(tmp_path: Path) -> None:
    from argparse import Namespace

    from scripts import pipeline_status

    run = _payload(
        ref="feature/owned-feedback", event="workflow_dispatch",
        status="completed", conclusion="success",
    )
    workspace = _DiagnosticWorkspace(tmp_path, run)
    assert workspace.wait().returncode == 0
    history = json.loads(workspace.history.read_text())
    assert history["last_verdict"] == "diagnostic_success"
    summary = pipeline_status.evaluate_runs([run], SHA, branch="feature/owned-feedback")
    args = Namespace(
        repo="sandboxcom/gludd", branch="feature/owned-feedback",
        ledger_observation_exit_code=0,
    )
    assert pipeline_status._terminal_verdict(args, summary, SHA) is None
    assert summary.missing_workflows
    assert json.loads(workspace.observation.read_text())["release_ready"] is False


def test_diagnostic_cli_rechecks_push_ownership_after_actual_query_boundary(tmp_path: Path) -> None:
    workspace = _DiagnosticWorkspace(tmp_path, _payload(
        ref="feature/owned-feedback", event="workflow_dispatch",
        status="completed", conclusion="success",
    ))
    workspace.environment["GLUDD_TEST_GH_NEW_PUSH"] = OTHER_SHA

    result = workspace.wait()

    assert result.returncode == 0, result.stdout + result.stderr
    history = json.loads(workspace.history.read_text())
    assert history["last_push_sha"] == OTHER_SHA
    assert history["last_checked_sha"] == ""
    assert workspace.restart.read_text() == "4"
    assert not workspace.state.exists()
    assert "history_recorded=true" not in result.stdout + result.stderr
