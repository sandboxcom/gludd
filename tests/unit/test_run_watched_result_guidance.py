"""Teach result handling without replacing the native observed-command verdict."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

from tests.unit.test_run_watched_trace import (
    ROOT,
    _assert_recorder_arguments,
    _child_payload,
    _fixture,
    _make,
    _native_child,
    _real_uv,
    _status,
)


def _assert_guide(
    result: subprocess.CompletedProcess[str], outcome: str, native_status: int,
) -> str:
    """Require one outcome-specific prompt on stderr, with no proof inflation."""
    prompts = [
        line for line in result.stderr.splitlines()
        if line.startswith("AGENT_NEXT target=run-watched ")
    ]
    assert len(prompts) == 1, result.stdout + result.stderr
    prompt = prompts[0]
    assert "outcome=" + outcome + " " in prompt
    assert "native_status=" + str(native_status) + " " in prompt
    assert "gate_evidence=false" in prompt and "release_ready=false" in prompt
    assert "fixture-only-private-environment" not in prompt
    assert "AGENT_NEXT target=run-watched " not in result.stdout
    return prompt


@pytest.mark.parametrize("watched_guidance_status", [0, 7, 125, 130])
def test_native_status_drives_one_result_prompt_without_changing_argv(
    tmp_path: Path, watched_guidance_status: int,
) -> None:
    fixture = _fixture(tmp_path)
    fixture.environment["GLUDD_WATCHED_TRACE_RECORDER_EXIT"] = str(watched_guidance_status)
    result = _make(fixture, "1")
    assert result.returncode == (0 if watched_guidance_status == 0 else 2)
    _assert_recorder_arguments(fixture, True)
    outcome = "passed" if watched_guidance_status == 0 else "failed"
    prompt = _assert_guide(result, outcome, watched_guidance_status)
    if watched_guidance_status == 0:
        assert "next_owner=observed-status" in prompt and "receipt" in prompt
        assert "observed command" in prompt
        assert "no receipt means no verified observation" in prompt
    else:
        assert "watched-recorder-error:" + str(watched_guidance_status) in result.stderr
        assert "Error " + str(watched_guidance_status) in result.stderr
        assert "changed" in prompt and "retry" in prompt
        assert "failure before native start produces no new receipt" in prompt
        assert "never reuse an older current success" in prompt
        assert "reconcile potentially applied work" in prompt
        assert not (fixture.directory / "observed").exists()


@pytest.mark.parametrize("watched_guidance_command", ["", "   "])
def test_empty_command_refusal_teaches_input_repair_before_native_io(
    tmp_path: Path, watched_guidance_command: str,
) -> None:
    fixture = _fixture(tmp_path)
    with fixture.makefile.open("a", encoding="utf-8") as stream:
        stream.write("\noverride CMD := " + watched_guidance_command + "\n")
    result = _make(fixture, "1")
    assert result.returncode == 2
    assert "Usage: make run-watched" in result.stdout and "Error 1" in result.stderr
    prompt = _assert_guide(result, "refused", 1)
    assert "input=CMD" in prompt and "next_owner=run-watched" in prompt
    assert not (fixture.directory / "calls.json").exists()
    assert not (fixture.directory / "observed").exists()


@pytest.mark.parametrize("watched_guidance_option", ["", "2"])
def test_invalid_trace_policy_refusal_teaches_literal_input_before_native_io(
    tmp_path: Path, watched_guidance_option: str,
) -> None:
    fixture = _fixture(tmp_path)
    result = _make(fixture, watched_guidance_option, watched_trace_context="bootstrap")
    assert result.returncode == 2
    assert "RUN_WATCHED_PYTEST_TRACE must be 0 or 1" in result.stderr
    prompt = _assert_guide(result, "refused", 2)
    assert "input=RUN_WATCHED_PYTEST_TRACE" in prompt
    assert "next_owner=run-watched" in prompt
    assert not (fixture.directory / "calls.json").exists()
    assert not (fixture.directory / "observed").exists()


def test_namesake_file_cannot_suppress_native_observation_or_guidance(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    source = compose_makefile(ROOT / "Makefile")
    declarations = [line for line in source.splitlines() if line == ".PHONY: run-watched"]
    assert len(declarations) <= 1, "Duplicate producer declarations are not a valid fixture binding"
    with fixture.makefile.open("a", encoding="utf-8") as stream:
        stream.write("\n" + "\n".join(declarations) + "\n")
    namesake = fixture.directory / "run-watched"
    namesake.write_text("foreign namesake marker\n", encoding="utf-8")
    _native_child(fixture)
    result = _make(fixture, "1", watched_trace_uv=_real_uv(fixture))
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_guide(result, "passed", 0)
    status = _status(fixture)
    assert status["state"] == "passed" and status["exit_code"] == 0
    assert _child_payload(fixture)["argv"] == ["literal child argument"]
    assert namesake.read_text(encoding="utf-8") == "foreign namesake marker\n"
    assert len(declarations) == 1, "The actual public owner must declare one literal PHONY binding"


def test_real_timeout_prompt_keeps_native_receipt_and_descendant_cleanup(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _native_child(fixture, watched_trace_sleep=True)
    result = _make(
        fixture, "1", watched_trace_uv=_real_uv(fixture), watched_trace_limits=("0", "1"),
    )
    assert result.returncode == 2 and "Error 124" in result.stderr
    prompt = _assert_guide(result, "failed", 124)
    assert "changed" in prompt and "retry" in prompt
    status = _status(fixture)
    assert status["state"] == "timed_out" and status["exit_code"] == 124
    assert status["termination_reason"] == "max-runtime-timeout"
    assert status["trace_path"] == str(fixture.directory / "observed/demo/run-1.pytest.jsonl")
    child_pid = _child_payload(fixture)["pid"]
    assert isinstance(child_pid, int) and child_pid > 0
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
