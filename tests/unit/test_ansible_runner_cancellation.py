"""Cancellation ownership contracts for both Ansible execution backends."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from general_ludd.ansible import core_runner
from general_ludd.ansible import runner as runner_module_source
from general_ludd.ansible.core_runner import AnsibleResult, CoreAnsibleRunner
from general_ludd.ansible.runner import AnsibleRunnerAdapter


def _successful_result() -> MagicMock:
    result = MagicMock()
    result.model_dump.return_value = {
        "status": "successful",
        "rc": 0,
        "events": [],
        "stats": {},
        "host_results": {},
        "error": None,
    }
    return result


def test_adapter_forwards_owner_cancellation_callback() -> None:
    """The network-facing adapter must not discard its owner's cancel signal."""
    adapter = AnsibleRunnerAdapter()
    callback = MagicMock(return_value=False)
    adapter._core_runner.run_playbook = MagicMock(return_value=_successful_result())

    result = adapter.run_playbook("noop.yml", cancel_requested=callback)

    assert result["status"] == "successful"
    assert adapter._core_runner.run_playbook.call_args.kwargs["cancel_requested"] is callback


def test_isolated_runner_uses_official_cancel_callback_and_normalizes_result(
    tmp_path,
) -> None:
    """ansible-runner owns container cancellation and reports one stable result."""
    isolation = MagicMock()
    isolation.enabled = True
    isolation.to_runner_kwargs.return_value = {"container_image": "example.invalid/ee@sha256:test"}
    runner = CoreAnsibleRunner(process_isolation=isolation)
    callback = MagicMock(return_value=False)
    raw_result = MagicMock(rc=254, status="canceled", stats={}, events=[])
    runner_module = MagicMock()
    runner_module.run.return_value = raw_result

    with (
        patch.object(core_runner, "_HAS_ANSIBLE_RUNNER", True),
        patch.object(core_runner, "ansible_runner", runner_module),
    ):
        result = runner._execute_with_runner(
            playbook_path=str(tmp_path / "cancel.yml"),
            cancel_requested=callback,
        )

    assert runner_module.run.call_args.kwargs["cancel_callback"] is callback
    assert result.status == "cancelled"
    assert result.rc == 130
    assert result.error == "playbook cancelled by owner"


def test_native_runner_cancellation_terminates_owned_process_tree() -> None:
    """Native cancellation kills and reaps the exact child instead of timing out."""
    queue = MagicMock()
    process = MagicMock()
    process.pid = 12345
    process.is_alive.return_value = True
    context = MagicMock()
    context.Queue.return_value = queue
    context.Process.return_value = process
    callback = MagicMock(return_value=True)
    runner = CoreAnsibleRunner()

    with (
        patch.object(core_runner.multiprocessing, "get_context", return_value=context),
        patch.object(runner, "_terminate_tree") as terminate_tree,
    ):
        result = runner._run_with_timeout(
            timeout=30.0,
            cancel_requested=callback,
            playbook_path="/tmp/cancel.yml",
        )

    callback.assert_called_once_with()
    terminate_tree.assert_called_once_with(process)
    assert result == AnsibleResult(
        status="cancelled",
        rc=130,
        error="playbook cancelled by owner",
    )


def test_core_run_playbook_passes_cancellation_to_native_supervisor() -> None:
    """A direct core caller with a cancel signal always gets a bounded child."""
    runner = CoreAnsibleRunner()
    callback = MagicMock(return_value=False)
    expected = AnsibleResult(status="successful", rc=0)

    with patch.object(runner, "_run_with_timeout", return_value=expected) as run_bounded:
        result = runner.run_playbook(
            "/tmp/cancel.yml",
            timeout=12.0,
            cancel_requested=callback,
        )

    assert result is expected
    assert run_bounded.call_args.kwargs["cancel_requested"] is callback


def test_native_runner_poll_preserves_normal_child_result() -> None:
    """A false cancellation signal does not disturb a normally exiting child."""
    queue = MagicMock()
    queue.get_nowait.return_value = (
        "ok",
        AnsibleResult(status="successful", rc=0).model_dump(),
    )
    process = MagicMock()
    process.pid = None
    process.is_alive.side_effect = [True, False]
    context = MagicMock()
    context.Queue.return_value = queue
    context.Process.return_value = process
    callback = MagicMock(return_value=False)

    with (
        patch.object(core_runner.multiprocessing, "get_context", return_value=context),
        patch.object(core_runner.time, "monotonic", side_effect=[10.0, 10.1]),
    ):
        result = CoreAnsibleRunner()._run_with_timeout(
            timeout=30.0,
            cancel_requested=callback,
            playbook_path="/tmp/success.yml",
        )

    assert result.status == "successful"
    callback.assert_called_once_with()
    process.join.assert_called_once_with(0.25)


def test_native_runner_cancel_check_failure_reaps_without_leaking_message() -> None:
    """A broken owner callback fails closed and exposes only its exception class."""
    queue = MagicMock()
    process = MagicMock()
    process.pid = 12345
    process.is_alive.return_value = True
    context = MagicMock()
    context.Queue.return_value = queue
    context.Process.return_value = process
    callback = MagicMock(side_effect=RuntimeError("credential=must-not-leak"))
    runner = CoreAnsibleRunner()

    with (
        patch.object(core_runner.multiprocessing, "get_context", return_value=context),
        patch.object(runner, "_terminate_tree") as terminate_tree,
    ):
        result = runner._run_with_timeout(
            timeout=30.0,
            cancel_requested=callback,
            playbook_path="/tmp/cancel-check.yml",
        )

    terminate_tree.assert_called_once_with(process)
    assert result.status == "failed"
    assert result.error == "playbook cancellation callback failed: RuntimeError"
    assert "must-not-leak" not in result.error


def test_native_runner_timeout_still_reaps_owned_process_tree() -> None:
    """Polling for cancellation preserves the independent wall-clock deadline."""
    queue = MagicMock()
    process = MagicMock()
    process.pid = 12345
    process.is_alive.return_value = True
    context = MagicMock()
    context.Queue.return_value = queue
    context.Process.return_value = process
    runner = CoreAnsibleRunner()

    with (
        patch.object(core_runner.multiprocessing, "get_context", return_value=context),
        patch.object(core_runner.time, "monotonic", side_effect=[10.0, 41.0]),
        patch.object(runner, "_terminate_tree") as terminate_tree,
    ):
        result = runner._run_with_timeout(
            timeout=30.0,
            playbook_path="/tmp/timeout.yml",
        )

    terminate_tree.assert_called_once_with(process)
    assert result.status == "failed"
    assert result.rc == 124
    assert result.error == "playbook timed out after 30.0s"


def test_owner_callback_replaces_nonpositive_timeout_with_finite_default() -> None:
    """Cancellation cannot be paired with an unkillable inline execution path."""
    runner = CoreAnsibleRunner()
    callback = MagicMock(return_value=False)
    expected = AnsibleResult(status="successful", rc=0)

    with (
        patch.object(core_runner, "_env_default_timeout", return_value=19.0),
        patch.object(runner, "_run_with_timeout", return_value=expected) as run_bounded,
    ):
        result = runner.run_playbook(
            "/tmp/cancel.yml",
            timeout=0,
            cancel_requested=callback,
        )

    assert result is expected
    assert run_bounded.call_args.kwargs["timeout"] == 19.0
    assert run_bounded.call_args.kwargs["cancel_requested"] is callback


@pytest.mark.parametrize(
    "role",
    [
        "bom_detect",
        "font_analyze",
        "homoglyph_scan",
        "i18n_extract",
        "locale_format",
    ],
)
def test_role_argument_conversion_rejects_missing_required_input(role: str) -> None:
    """Every known role omits an incomplete argument rather than inventing input."""
    assert runner_module_source._convert_role_args(role, {}) == []


def test_role_argument_conversion_honors_explicit_i18n_output() -> None:
    """An explicitly confined i18n output path does not allocate a temp path."""
    assert runner_module_source._convert_role_args(
        "i18n_extract",
        {"directory": "/input", "output_dir": "/output"},
    ) == ["--source-dir", "/input", "--output-dir", "/output"]


def test_phonetic_conversion_without_text_retains_fixed_method() -> None:
    """The fixed IPA method is emitted even when optional text is absent."""
    assert runner_module_source._convert_role_args("phonetic_transcribe", {}) == [
        "--method",
        "ipa",
    ]


def test_isolated_runner_forwards_complete_supported_option_surface(tmp_path) -> None:
    """Cancellation composes with every maintained ansible-runner option."""
    isolation = MagicMock()
    isolation.to_runner_kwargs.return_value = {"process_isolation": True}
    runner = CoreAnsibleRunner(process_isolation=isolation)
    raw_result = MagicMock(
        rc=0,
        status="successful",
        stats=[],
        events=[{"event": "runner_on_ok"}],
    )
    runner_module = MagicMock()
    runner_module.run.return_value = raw_result
    callback = MagicMock(return_value=False)

    with (
        patch.object(core_runner, "_HAS_ANSIBLE_RUNNER", True),
        patch.object(core_runner, "ansible_runner", runner_module),
    ):
        result = runner._execute_with_runner(
            playbook_path=str(tmp_path / "all-options.yml"),
            inventory=["localhost,"],
            extravars={"safe": True},
            verbosity=2,
            check=True,
            tags=["one"],
            skip_tags=["two"],
            connection="ssh",
            become=True,
            extra_env={"ANSIBLE_FORCE_COLOR": "0"},
            timeout=20.0,
            cancel_requested=callback,
        )

    kwargs = runner_module.run.call_args.kwargs
    assert kwargs["inventory"] == ["localhost,"]
    assert kwargs["extravars"] == {"safe": True}
    assert kwargs["verbosity"] == 2
    assert kwargs["cmdline"] == [
        "--check",
        "--tags",
        "one",
        "--skip-tags",
        "two",
        "--become",
        "--connection",
        "ssh",
    ]
    assert kwargs["envvars"] == {"ANSIBLE_FORCE_COLOR": "0"}
    assert kwargs["cancel_callback"] is callback
    assert result.status == "successful"
    assert result.stats == {}
    assert result.events == [{"event": "runner_on_ok"}]


def test_isolated_runner_missing_status_and_rc_fails_closed(tmp_path) -> None:
    """Malformed ansible-runner results cannot be mistaken for success."""
    isolation = MagicMock()
    isolation.to_runner_kwargs.return_value = {}
    runner = CoreAnsibleRunner(process_isolation=isolation, private_data_dir=str(tmp_path))
    raw_result = MagicMock()
    raw_result.rc = None
    raw_result.status = None
    raw_result.stats = None
    raw_result.events = None
    runner_module = MagicMock()
    runner_module.run.return_value = raw_result

    with (
        patch.object(core_runner, "_HAS_ANSIBLE_RUNNER", True),
        patch.object(core_runner, "ansible_runner", runner_module),
    ):
        result = runner._execute_with_runner(playbook_path="missing.yml")

    assert result.status == "failed"
    assert result.rc == 1
    assert result.stats == {}
    assert result.events == []
