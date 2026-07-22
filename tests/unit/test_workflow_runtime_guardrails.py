from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
BUILD_WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
DEEPSEEK_E2E = ROOT / "tests" / "e2e" / "test_game_building_deepseek.py"
SHARD_SUMMARY = ROOT / "scripts" / "run_ci_shard_summary.py"


def _makefile() -> str:
    return MAKEFILE.read_text(encoding="utf-8")


def _target_block(target: str) -> str:
    lines = _makefile().splitlines()
    prefix = target + ":"
    start = next((idx for idx, line in enumerate(lines) if line.startswith(prefix)), None)
    assert start is not None, f"{target} target missing"
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        line = lines[idx]
        if line and not line.startswith((" ", chr(9), "#")) and re.match(r"[a-zA-Z0-9_.-]+:", line):
            end = idx
            break
    return chr(10).join(lines[start:end])


def _workflow_job_block(job_name: str) -> str:
    lines = BUILD_WORKFLOW.read_text(encoding="utf-8").splitlines()
    prefix = "  " + job_name + ":"
    start = next((idx for idx, line in enumerate(lines) if line == prefix), None)
    assert start is not None, f"{job_name} job missing"
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        line = lines[idx]
        if line.startswith("  ") and not line.startswith("    ") and line.endswith(":"):
            end = idx
            break
    return chr(10).join(lines[start:end])


def test_long_running_make_paths_have_timeout_and_live_progress() -> None:
    task = _target_block("task")
    assert "GLUDD_TASK_TIMEOUT" in task
    assert "scripts/task_runner.py" in task
    assert "Running task with" in task
    assert "TASK TIMEOUT" in task

    shard_summary = SHARD_SUMMARY.read_text(encoding="utf-8")
    assert "still running" in shard_summary
    assert "elapsed=" in shard_summary
    assert "SIGTERM" in shard_summary

    assert "ci_shards_parallel_status.py" in _target_block("test-ci-shards-parallel-status")
    assert "ci-await BRANCH=master" in _target_block("release-deploy")


def test_deepseek_live_job_is_secret_gated_and_nonblocking_in_ci() -> None:
    workflow = BUILD_WORKFLOW.read_text(encoding="utf-8")
    game_job = _workflow_job_block("game-building")
    deepseek_test = DEEPSEEK_E2E.read_text(encoding="utf-8")

    assert "continue-on-error: true" in game_job
    assert "secrets.DEEPSEEK_API_KEY" in game_job
    assert "DEEPSEEK_API_KEY" in game_job
    assert "@pytest.mark.skipif(not _DEEPSEEK_KEY" in deepseek_test
    assert not re.search(r"sk-[A-Za-z0-9]{8,}", workflow)


def test_release_job_has_pre_publish_and_post_deploy_smoke_gates() -> None:
    release_job = _workflow_job_block("release")
    assert "Verify staged assets (pre-publish gate)" in release_job
    assert "PRE-PUBLISH GATE: PASS" in release_job
    assert "Verify release completeness (blocking)" in release_job
    assert "Post-deploy smoke test" in release_job
    assert "Post-deploy verify" in release_job


def test_pytest_args_starting_with_dash_are_passed_as_option_values() -> None:
    for target in [
        "test-ci-shard-summary",
        "test-ci-shards-parallel",
        "test-ci-shards-parallel-bg",
    ]:
        block = _target_block(target)
        assert "--pytest-args=" in block
        assert "--pytest-args \"" not in block
