"""Exact-SHA, idempotent GitHub Actions signaling for release candidates."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

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
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
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
