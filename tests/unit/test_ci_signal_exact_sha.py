"""Exact-SHA, idempotent GitHub Actions signaling for release candidates."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import ci_signal_exact_sha as signal_module  # noqa: E402
from ci_signal_exact_sha import (  # noqa: E402
    SignalError,
    WorkflowRun,
    main,
    signal_exact_sha,
)

SHA = "abc123def4567890abc123def4567890abc123de"
OTHER_SHA = "9999999999999999999999999999999999999999"
RUN_URL = "https://github.com/sandboxcom/gludd/actions/runs/4242"


def _completed(
    argv: Sequence[str],
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(list(argv), returncode, stdout, stderr)


def _run_payload(
    *,
    sha: str = SHA,
    run_id: int = 4242,
    url: str = RUN_URL,
    status: str = "queued",
    conclusion: str = "",
    event: str = "push",
) -> dict[str, object]:
    return {
        "databaseId": run_id,
        "headSha": sha,
        "url": url,
        "status": status,
        "conclusion": conclusion,
        "event": event,
    }


class FakeRunner:
    def __init__(
        self,
        run_lists: Sequence[Sequence[dict[str, object]]],
        *,
        status: str = "",
        remote_sha: str = SHA,
        dispatch_stdout: str = "",
    ) -> None:
        self._run_lists = list(run_lists)
        self._last_run_list: Sequence[dict[str, object]] = []
        self.status = status
        self.remote_sha = remote_sha
        self.dispatch_stdout = dispatch_stdout
        self.calls: list[list[str]] = []

    def __call__(
        self,
        argv: Sequence[str],
        cwd: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd
        args = list(argv)
        self.calls.append(args)
        if args[:3] == ["git", "branch", "--show-current"]:
            return _completed(args, stdout="release/beta3-candidate\n")
        if args[:3] == ["git", "rev-parse", "--verify"]:
            return _completed(args, stdout=f"{SHA}\n")
        if args[:3] == ["git", "status", "--porcelain=v1"]:
            return _completed(args, stdout=self.status)
        if args[:2] == ["git", "ls-remote"]:
            return _completed(
                args,
                stdout=f"{self.remote_sha}\trefs/heads/release/beta3-candidate\n",
            )
        if args[:3] == ["gh", "run", "list"]:
            if self._run_lists:
                self._last_run_list = self._run_lists.pop(0)
            return _completed(args, stdout=json.dumps(self._last_run_list))
        if args[:3] == ["gh", "workflow", "run"]:
            return _completed(args, stdout=self.dispatch_stdout)
        raise AssertionError(f"unexpected command: {args}")

    @property
    def dispatch_count(self) -> int:
        return sum(call[:3] == ["gh", "workflow", "run"] for call in self.calls)

    @property
    def gh_calls(self) -> list[list[str]]:
        return [call for call in self.calls if call and call[0] == "gh"]


def _signal(
    runner: FakeRunner,
    state_dir: Path,
    *,
    discovery_polls: int = 1,
    confirm_polls: int = 1,
) -> signal_module.SignalResult:
    return signal_exact_sha(
        ref="release/beta3-candidate",
        remote="sandboxcom",
        repo="sandboxcom/gludd",
        workflow="Build and Release",
        discovery_polls=discovery_polls,
        confirm_polls=confirm_polls,
        poll_interval=0,
        state_dir=state_dir,
        run=runner,
        sleep=lambda _seconds: None,
    )


def test_existing_exact_sha_run_returns_url_without_dispatch(tmp_path: Path) -> None:
    runner = FakeRunner(
        [[_run_payload(status="completed", conclusion="success")]]
    )

    result = _signal(runner, tmp_path)

    assert result.dispatched is False
    assert result.sha == SHA
    assert result.url == RUN_URL
    assert runner.dispatch_count == 0
    list_call = runner.gh_calls[0]
    assert "--commit" in list_call
    assert list_call[list_call.index("--commit") + 1] == SHA


@pytest.mark.parametrize("conclusion", ["cancelled", "failure"])
def test_terminal_unsuccessful_run_dispatches_replacement(
    tmp_path: Path,
    conclusion: str,
) -> None:
    replacement_url = "https://github.com/sandboxcom/gludd/actions/runs/4343"
    runner = FakeRunner(
        [
            [
                _run_payload(
                    status="completed",
                    conclusion=conclusion,
                    event="workflow_dispatch",
                )
            ],
            [
                _run_payload(
                    status="completed",
                    conclusion=conclusion,
                    event="workflow_dispatch",
                )
            ],
            [
                _run_payload(
                    run_id=4343,
                    url=replacement_url,
                    event="workflow_dispatch",
                )
            ],
        ]
    )

    result = _signal(runner, tmp_path)

    assert result.dispatched is True
    assert result.url == replacement_url
    assert runner.dispatch_count == 1


def test_terminal_unsuccessful_run_replaces_stale_dispatch_marker(
    tmp_path: Path,
) -> None:
    marker_path, _lock_path = signal_module._state_paths(
        tmp_path,
        repo="sandboxcom/gludd",
        workflow="Build and Release",
        sha=SHA,
    )
    signal_module._write_marker(
        marker_path,
        sha=SHA,
        ref="release/beta3-candidate",
        repo="sandboxcom/gludd",
        workflow="Build and Release",
        dispatch_url=RUN_URL,
    )
    replacement_url = "https://github.com/sandboxcom/gludd/actions/runs/4344"
    runner = FakeRunner(
        [
            [
                _run_payload(
                    status="completed",
                    conclusion="cancelled",
                    event="workflow_dispatch",
                )
            ],
            [
                _run_payload(
                    status="completed",
                    conclusion="cancelled",
                    event="workflow_dispatch",
                )
            ],
            [
                _run_payload(
                    run_id=4344,
                    url=replacement_url,
                    event="workflow_dispatch",
                )
            ],
        ]
    )

    result = _signal(runner, tmp_path)

    assert result.dispatched is True
    assert result.url == replacement_url
    assert runner.dispatch_count == 1


def test_run_payload_parser_rejects_incomplete_data_and_builds_url_fallback() -> None:
    assert WorkflowRun.from_payload(None, "sandboxcom/gludd") is None
    assert WorkflowRun.from_payload({}, "sandboxcom/gludd") is None

    parsed = WorkflowRun.from_payload(
        {"databaseId": 73, "headSha": SHA},
        "sandboxcom/gludd",
    )
    assert parsed is not None
    assert parsed.url == "https://github.com/sandboxcom/gludd/actions/runs/73"


def test_missing_run_dispatches_once_then_confirms_exact_sha_url(tmp_path: Path) -> None:
    runner = FakeRunner([[], [_run_payload(event="workflow_dispatch")]])

    result = _signal(runner, tmp_path)

    assert result.dispatched is True
    assert result.url == RUN_URL
    assert runner.dispatch_count == 1


def test_nonmatching_run_does_not_count_as_exact_sha_evidence(tmp_path: Path) -> None:
    runner = FakeRunner(
        [
            [_run_payload(sha=OTHER_SHA, run_id=41)],
            [_run_payload(event="workflow_dispatch")],
        ]
    )

    result = _signal(runner, tmp_path)

    assert result.dispatched is True
    assert result.sha == SHA
    assert runner.dispatch_count == 1


def test_successful_dispatch_marker_prevents_duplicate_when_run_is_delayed(
    tmp_path: Path,
) -> None:
    first = FakeRunner([[], []], dispatch_stdout=RUN_URL)
    with pytest.raises(SignalError, match="not visible"):
        _signal(first, tmp_path)
    assert first.dispatch_count == 1
    assert list(tmp_path.glob("*.json")), "a durable exact-SHA dispatch marker is required"

    second = FakeRunner([[], []])
    with pytest.raises(SignalError, match="not visible"):
        _signal(second, tmp_path)
    assert second.dispatch_count == 0


@pytest.mark.parametrize(
    ("runner", "message"),
    [
        (FakeRunner([], status=" M README.md\n"), "dirty"),
        (FakeRunner([], remote_sha=OTHER_SHA), "not local HEAD"),
    ],
)
def test_signal_fails_closed_before_gh_when_local_remote_guard_fails(
    tmp_path: Path,
    runner: FakeRunner,
    message: str,
) -> None:
    with pytest.raises(SignalError, match=message):
        _signal(runner, tmp_path)

    assert runner.gh_calls == []


def test_example_mode_is_network_free_and_returns_a_run_url(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(
            [
                "--example",
                "--ref",
                "release/beta3-candidate",
                "--repo",
                "sandboxcom/gludd",
                "--workflow",
                "Build and Release",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "GHA-SIGNAL-EXISTING" in output
    assert "GHA_RUN_URL=https://github.com/sandboxcom/gludd/actions/runs/example" in output


def test_main_forwards_real_signal_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_signal(**kwargs: object) -> signal_module.SignalResult:
        captured.update(kwargs)
        return signal_module._example_result("release/beta3-candidate", "example/repo")

    monkeypatch.setattr(signal_module, "signal_exact_sha", fake_signal)

    assert (
        main(
            [
                "--ref",
                "release/beta3-candidate",
                "--remote",
                "sandboxcom",
                "--repo",
                "example/repo",
                "--workflow",
                "build.yml",
                "--discovery-polls",
                "2",
                "--confirm-polls",
                "3",
                "--poll-interval",
                "0.25",
                "--state-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert captured["ref"] == "release/beta3-candidate"
    assert captured["repo"] == "example/repo"
    assert captured["workflow"] == "build.yml"
    assert captured["discovery_polls"] == 2
    assert captured["confirm_polls"] == 3
    assert captured["poll_interval"] == 0.25
    assert captured["state_dir"] == tmp_path


def test_main_reports_signal_error_without_dispatch_retry(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def blocked(**_kwargs: object) -> None:
        raise SignalError("remote moved")

    monkeypatch.setattr(signal_module, "signal_exact_sha", blocked)

    assert main([]) == 1
    assert "GHA-SIGNAL-BLOCKED error=remote moved" in capsys.readouterr().err


def test_release_candidate_push_path_uses_idempotent_signal_script() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    trigger = makefile.split("ci-trigger-committed-head:", 1)[1].split(
        "\nci-push-committed-head:",
        1,
    )[0]
    combined_line = next(
        line
        for line in makefile.splitlines()
        if line.startswith("ci-push-committed-head:")
    )

    assert "scripts/ci_signal_exact_sha.py" in trigger
    assert "_require-gh" in trigger
    assert "$(SSH_KEY)" not in trigger
    assert "/Users/shawnwilson/.ssh/sandboxcom_gludd_rsa" in trigger
    assert "gha-ready" not in trigger
    assert "workflow-gate" not in trigger
    assert "ci-trigger-committed-head" in combined_line


class FeatureSignalAdapter(FakeRunner):
    """Combine existing GH response coverage with the shared unit Git boundary."""

    def __init__(self, run_lists: Sequence[Sequence[dict[str, object]]]) -> None:
        super().__init__(run_lists)
        from tests.unit.test_ci_remote_head_guard import DiagnosticGitAdapter

        self.git = DiagnosticGitAdapter()
        self.move_on_lookup = False

    def __call__(
        self, argv: Sequence[str], cwd: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if argv[0] == "git":
            return self.git(argv, cwd)
        result = super().__call__(argv, cwd)
        if self.move_on_lookup and list(argv[:3]) == ["gh", "run", "list"]:
            self.git.head = OTHER_SHA
            self.git.remote_head = OTHER_SHA
        return result


def _feature_signal(runner: FeatureSignalAdapter, state_dir: Path) -> signal_module.SignalResult:
    from tests.unit.test_ci_remote_head_guard import BASE_SHA, FEATURE_REF, FULL_SHA

    return signal_exact_sha(
        ref=FEATURE_REF, purpose="feature-ci", expected_branch=FEATURE_REF, expected_sha=FULL_SHA,
        baseline_ref="master", baseline_sha=BASE_SHA, discovery_polls=1, confirm_polls=1,
        poll_interval=0, state_dir=state_dir, run=runner, sleep=lambda _seconds: None,
    )


@pytest.mark.parametrize("existing", (False, True))
def test_feature_signal_preserves_exact_sha_discovery_and_dispatch(
    tmp_path: Path, existing: bool,
) -> None:
    from tests.unit.test_ci_remote_head_guard import FULL_SHA

    runner = FeatureSignalAdapter([[_run_payload(sha=FULL_SHA)]] if existing
                                  else [[], [], [_run_payload(sha=FULL_SHA, event="workflow_dispatch")]])
    result = _feature_signal(runner, tmp_path)
    assert result.sha == FULL_SHA
    assert result.dispatched is not existing
    assert runner.dispatch_count == (0 if existing else 1)
    baseline_reads = [args for args in runner.git.calls if args[:4] == ["git", "ls-remote", "--exit-code", "--heads"]]
    assert len(baseline_reads) == (1 if existing else 2)


def test_feature_signal_rechecks_pinned_source_after_lookup_before_dispatch(tmp_path: Path) -> None:
    runner = FeatureSignalAdapter([[], []])
    runner.move_on_lookup = True
    with pytest.raises(SignalError, match="saved expected candidate"):
        _feature_signal(runner, tmp_path)
    assert runner.dispatch_count == 0
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize("field", ("ref", "remote", "repo", "workflow", "purpose"))
def test_feature_signal_refuses_wrong_scope_before_git_or_gh(tmp_path: Path, field: str) -> None:
    from tests.unit.test_ci_remote_head_guard import BASE_SHA, FEATURE_REF, FULL_SHA

    values = {
        "ref": FEATURE_REF, "remote": "sandboxcom", "repo": "sandboxcom/gludd",
        "workflow": "Build and Release", "purpose": "feature-ci",
    }
    values[field] = "foreign"
    runner = FeatureSignalAdapter([])
    assert set(values) == {"ref", "remote", "repo", "workflow", "purpose"}
    with pytest.raises(SignalError):
        signal_exact_sha(
            ref=values["ref"], remote=values["remote"], repo=values["repo"],
            workflow=values["workflow"], purpose=values["purpose"],
            expected_branch=FEATURE_REF, expected_sha=FULL_SHA,
            baseline_ref="master", baseline_sha=BASE_SHA, state_dir=tmp_path, run=runner,
        )
    assert runner.git.calls == runner.gh_calls == []


def test_signal_provider_errors_remain_failures_without_dispatch(tmp_path: Path) -> None:
    for payload in ("invalid-json", "{}"):
        def run(
            argv: Sequence[str], cwd: str | None = None, *, response: str = payload,
        ) -> subprocess.CompletedProcess[str]:
            del cwd
            return _completed(argv, stdout=response)

        with pytest.raises(SignalError, match="JSON"):
            signal_module._list_exact_runs(
                repo="sandboxcom/gludd", workflow="Build and Release", sha=SHA, run=run, cwd=None,
            )

    def broken(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        del argv, cwd
        raise OSError("owned lookup unavailable")

    with pytest.raises(SignalError, match="owned lookup unavailable"):
        signal_module._checked(["owned"], run=broken, cwd=None, operation="lookup")

    def refused(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        del cwd
        return _completed(argv, stderr="owned lookup refused", returncode=73)

    with pytest.raises(SignalError, match="owned lookup refused"):
        signal_module._checked(["owned"], run=refused, cwd=None, operation="lookup")
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize("payload", ("invalid", "[]"))
def test_signal_marker_refuses_malformed_private_state(tmp_path: Path, payload: str) -> None:
    marker = tmp_path / "owned-marker.json"
    marker.write_text(payload, encoding="utf-8")
    with pytest.raises(SignalError, match="dispatch marker"):
        signal_module._read_marker(marker)


def test_signal_marker_write_failure_keeps_no_accepted_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "owned-marker.json"

    def refused_replace(_source: object, _destination: object) -> None:
        raise OSError("owned atomic write refused")

    assert vars(signal_module)["os"] is os
    monkeypatch.setattr(os, "replace", refused_replace)
    with pytest.raises(SignalError, match="owned atomic write refused"):
        signal_module._write_marker(
            marker, sha=SHA, ref="feature/owned", repo="sandboxcom/gludd",
            workflow="Build and Release", dispatch_url=RUN_URL,
        )
    assert not marker.exists()
    assert list(tmp_path.iterdir()) == []


def test_signal_lock_wait_and_timeout_are_bounded_native_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    assert vars(signal_module)["fcntl"] is fcntl
    assert vars(signal_module)["time"] is time
    real_unlock = fcntl.LOCK_UN

    def briefly_busy(_descriptor: int, mode: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1 and mode != real_unlock:
            raise BlockingIOError

    monkeypatch.setattr(fcntl, "flock", briefly_busy)
    messages: list[str] = []
    sleeps: list[float] = []
    with signal_module._signal_lock(
        tmp_path / "owned.lock", sleep=sleeps.append, progress=messages.append,
    ):
        assert sleeps == [1.0]
    assert len(messages) == 1

    def always_busy(_descriptor: int, _mode: int) -> None:
        raise BlockingIOError

    ticks = iter((0.0, 2.0))
    monkeypatch.setattr(fcntl, "flock", always_busy)
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    with pytest.raises(SignalError, match="timed out"), signal_module._signal_lock(
        tmp_path / "owned-timeout.lock", sleep=sleeps.append,
        progress=messages.append, timeout=1.0,
    ):
        pytest.fail("an unowned lock must not admit dispatch")


@pytest.mark.parametrize("arguments", (
    ["--discovery-polls", "0"], ["--confirm-polls", "0"], ["--poll-interval", "-1"],
))
def test_signal_cli_rejects_invalid_poll_bounds(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as result:
        main(arguments)
    assert result.value.code == 2


def test_contract_and_docs_pin_network_free_behavioral_example() -> None:
    contract = json.loads(
        (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
    )
    entry = next(
        item
        for item in contract["targets"]
        if item["name"] == "ci-trigger-committed-head"
    )
    documentation = (ROOT / "docs" / "CI_EXACT_SHA_SIGNAL.md").read_text(
        encoding="utf-8"
    )

    assert "EXAMPLE=1" in entry["behavior"]
    assert entry["behavior"] in documentation
    assert "GHA_RUN_URL=" in documentation
    assert "github.com/orgs/community/discussions/25702" in documentation
    assert "github.com/orgs/community/discussions/46775" in documentation
