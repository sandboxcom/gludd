"""Regression tests for the beta.3 integration/E2E/Molecule release gate."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"


def _makefile() -> str:
    return MAKEFILE.read_text(encoding="utf-8")


def _target_block(name: str) -> str:
    content = _makefile()
    match = re.search(rf"^{re.escape(name)}:", content, re.MULTILINE)
    assert match is not None, f"Makefile target {name!r} is missing"
    following = content[match.end() :]
    next_target = re.search(r"\n[A-Za-z0-9_.-]+:", following)
    end = match.end() + next_target.start() if next_target else len(content)
    return content[match.start() : end]


def _fake_runner(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "calls.log"
    runner = tmp_path / "gate-runner.py"
    runner.write_text(
        """#!/usr/bin/env python3
import os
import sys
import time
from pathlib import Path

call = " ".join(sys.argv[1:])
with Path(os.environ["GLUDD_GATE_CALL_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(call + "\\n")
print("LIVE-RUNNER " + call, flush=True)
barrier_dir = os.environ.get("GLUDD_GATE_BARRIER_DIR", "")
if barrier_dir and call.startswith("tests/integration/"):
    barrier = Path(barrier_dir)
    barrier.mkdir(parents=True, exist_ok=True)
    (barrier / f"{os.getppid()}-{os.getpid()}").touch()
    deadline = time.monotonic() + 10
    while len(list(barrier.iterdir())) < 2:
        if time.monotonic() >= deadline:
            raise SystemExit("concurrent release-gate barrier timed out")
        time.sleep(0.01)
needle = os.environ.get("GLUDD_GATE_FAIL_MATCH", "")
if needle and needle in call:
    raise SystemExit(23)
""",
        encoding="utf-8",
    )
    runner.chmod(0o755)
    return runner, log


def _run_release_phases(
    tmp_path: Path,
    *,
    fail_match: str = "",
    barrier_dir: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], str, str]:
    runner, log = _fake_runner(tmp_path)
    status = tmp_path / "gate-status"
    status.write_text(
        "=== GATE-REFRESH fixture ===\n"
        "lint PASS 0\n"
        "=== GATE: PASSED ===\n",
        encoding="utf-8",
    )
    command = f"{sys.executable} {runner}"
    env = {
        **os.environ,
        "GLUDD_GATE_CALL_LOG": str(log),
        "GLUDD_GATE_FAIL_MATCH": fail_match,
    }
    if barrier_dir is not None:
        env["GLUDD_GATE_BARRIER_DIR"] = str(barrier_dir)
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "gate-release-phases",
            f"GATE_STATUS_FILE={status}",
            f"GATE_RELEASE_PYTEST={command}",
            f"GATE_RELEASE_MAKE={command}",
            "GATE_RELEASE_WORKERS=2",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    return result, status.read_text(encoding="utf-8"), calls


def test_public_full_gates_delegate_to_one_real_release_phase_target() -> None:
    for target in ("gate-full", "gate-all"):
        recipe = _target_block(target)
        assert "gate-release-phases" in recipe
        assert "echo run python" not in recipe


def test_release_phase_recipe_contains_real_bounded_commands() -> None:
    recipe = _target_block("gate-release-phases")
    assert "$(GATE_RELEASE_PYTEST) tests/integration/" in recipe
    assert "$(GATE_RELEASE_PYTEST) tests/e2e/" in recipe
    assert "-n \"$(GATE_RELEASE_WORKERS)\"" in recipe
    assert "molecule/playbooks/*/" in recipe
    assert "molecule-test SCENARIO=" in recipe
    assert "echo run python" not in recipe


def test_direct_release_pytest_targets_own_unique_basetemps() -> None:
    for target, prefix in (
        ("test-integration", "gludd-test-integration-"),
        ("test-e2e", "gludd-test-e2e-"),
    ):
        recipe = _target_block(target)
        assert f"mktemp -d /tmp/{prefix}XXXXXX" in recipe
        assert '--basetemp="$$BT"' in recipe
        assert "trap 'exit 130' INT TERM" in recipe


def test_release_pytest_phases_bound_failure_output() -> None:
    """One root failure must not dump hundreds of megabytes of assertion data."""
    recipe = _target_block("gate-release-phases")
    integration_line = next(
        line for line in recipe.splitlines() if "tests/integration/" in line
    )
    e2e_line = next(line for line in recipe.splitlines() if "tests/e2e/" in line)

    for line in (integration_line, e2e_line):
        assert "--maxfail=1" in line
        assert "--tb=line" in line
    assert "-n 1" in e2e_line


def test_release_phases_stream_live_output_and_record_terminal_success(
    tmp_path: Path,
) -> None:
    result, status, calls = _run_release_phases(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "LIVE-RUNNER tests/integration/" in result.stdout
    assert "LIVE-RUNNER tests/e2e/" in result.stdout
    assert "tests/integration/" in calls
    assert " -n 2 " in f" {calls} "
    assert "tests/e2e/" in calls
    assert "molecule-test SCENARIO=" in calls
    assert "integration PASS 0" in status
    assert "e2e PASS 0" in status
    assert "molecule PASS 0" in status
    assert status.count("=== GATE: PASSED ===") == 1
    assert "=== GATE: FAILED ===" not in status


@pytest.mark.parametrize(
    ("fail_match", "failed_phase", "not_called"),
    [
        ("tests/integration/", "integration", "tests/e2e/"),
        ("tests/e2e/", "e2e", "molecule-test SCENARIO="),
        ("molecule-test SCENARIO=", "molecule", ""),
    ],
)
def test_release_phases_fail_closed_on_real_command_failure(
    tmp_path: Path,
    fail_match: str,
    failed_phase: str,
    not_called: str,
) -> None:
    result, status, calls = _run_release_phases(tmp_path, fail_match=fail_match)

    assert result.returncode != 0
    assert f"{failed_phase} FAIL 23" in status
    assert "=== GATE: FAILED ===" in status
    assert "=== GATE: PASSED ===" not in status
    if not_called:
        assert not_called not in calls


def test_release_phase_worker_cap_rejects_more_than_two(tmp_path: Path) -> None:
    runner, log = _fake_runner(tmp_path)
    status = tmp_path / "gate-status"
    status.write_text("=== GATE: PASSED ===\n", encoding="utf-8")
    command = f"{sys.executable} {runner}"
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "gate-release-phases",
            f"GATE_STATUS_FILE={status}",
            f"GATE_RELEASE_PYTEST={command}",
            f"GATE_RELEASE_MAKE={command}",
            "GATE_RELEASE_WORKERS=3",
        ],
        cwd=ROOT,
        env={**os.environ, "GLUDD_GATE_CALL_LOG": str(log)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    assert "must be 1 or 2" in result.stdout + result.stderr
    assert "=== GATE: FAILED ===" in status.read_text(encoding="utf-8")
    assert not log.exists()


def test_concurrent_release_phases_own_distinct_tmp_namespaces(tmp_path: Path) -> None:
    """Overlapping gates must not share or delete each other's pytest basetemp."""
    barrier = tmp_path / "barrier"
    run_dirs = [tmp_path / "run-a", tmp_path / "run-b"]
    for run_dir in run_dirs:
        run_dir.mkdir()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_run_release_phases, run_dir, barrier_dir=barrier)
            for run_dir in run_dirs
        ]
        outcomes = [future.result(timeout=30) for future in futures]

    owned_roots: list[Path] = []
    for result, status, calls in outcomes:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "=== GATE: PASSED ===" in status
        pytest_calls = [
            line
            for line in calls.splitlines()
            if line.startswith(("tests/integration/", "tests/e2e/"))
        ]
        assert len(pytest_calls) == 2
        basetemps = []
        for call in pytest_calls:
            match = re.search(r"(?:^| )--basetemp=([^ ]+)", call)
            assert match is not None, call
            basetemps.append(Path(match.group(1)))
        assert {path.name for path in basetemps} == {"integration", "e2e"}
        assert basetemps[0].parent == basetemps[1].parent
        owned_roots.append(basetemps[0].parent)

    assert owned_roots[0] != owned_roots[1]
    assert all(root.name.startswith("gludd-gate-release-") for root in owned_roots)
    assert all(not root.exists() for root in owned_roots)
