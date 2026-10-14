"""Native heartbeat ownership stops only after its current exact terminal observation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
REPO = "sandboxcom/gludd"
BRANCH = "development"
SHA = "a" * 40
OTHER_SHA = "b" * 40
MARKER = f"PIPELINE-TERMINAL-OBSERVED repo={REPO} branch={BRANCH} sha={SHA}"


def _stanza(content: str, name: str) -> str:
    start = content.index(f"\n{name}:") + 1
    end = content.find("\n\n", start)
    return content[start:end if end >= 0 else len(content)]


def _run(workflow: str, number: int) -> dict[str, object]:
    return {"workflowName": workflow, "databaseId": number,
            "headSha": SHA, "headBranch": "development", "event": "push",
            "status": "completed", "conclusion": "success",
            "createdAt": f"2026-10-13T00:00:0{number}Z", "url": ""}


def _fixture(
    tmp_path: Path, case: str, *, count: int = 2, stop: str | None = "1",
    pin: str = SHA, validate: int = 0, plan: int = 0, interval: int = 300,
) -> tuple[list[str], dict[str, str]]:
    content = compose_makefile(ROOT / "Makefile")
    carriers = "\n".join(line for line in content.splitlines()
                         if line.startswith(("PIPELINE_STATUS_", "STATUS_HEARTBEAT_")) and "?=" in line)
    (tmp_path / "Makefile").write_text(
        carriers + "\n\n" + _stanza(content, "pipeline-status") + "\n\n"
        + _stanza(content, "status-heartbeat") + "\n\n"
        + "gate-status:\n\t@echo GATE-STATUS-FIXTURE\n\n"
        + "active-work-status:\n\t@echo ACTIVE-WORK-FIXTURE\n", encoding="utf-8",
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    wrapper = scripts / "pipeline_status.py"
    wrapper.write_text(
        "import json, os, sys, time\nfrom pathlib import Path\n"
        + f"sys.path.insert(0, {str(ROOT)!r})\n"
        + "from scripts import pipeline_status as owner, active_work_status, automatic_disk_cleanup\n"
        + "from scripts import ci_failure_ledger, ci_check_cooldown\n"
        + "case = os.environ['GLUDD_HEARTBEAT_CASE']\n"
        + "runs = json.loads(os.environ['GLUDD_HEARTBEAT_RUNS'])\n"
        + "calls = Path(os.environ['GLUDD_HEARTBEAT_CALLS'])\n"
        + "with calls.open('a') as stream: stream.write('pipeline-status\\n')\n"
        + "def unavailable(*args, **kwargs): raise ValueError('fixture observation unavailable')\n"
        + "def fetch(*args, **kwargs):\n"
        + "    if case == 'lookup-error': raise owner.PipelineStatusError('fixture query unavailable')\n"
        + "    return runs\n"
        + "def detail(run_id, repo):\n"
        + "    result = {**next(run for run in runs if run['databaseId'] == run_id), 'jobs': []}\n"
        + "    if case == 'detail-pending' and run_id == 1: result.update(status='in_progress', conclusion='')\n"
        + f"    if case == 'detail-identity': result['headSha'] = {OTHER_SHA!r}\n"
        + "    if case == 'streaming':\n"
        + "        receipt = Path(os.environ['GLUDD_HEARTBEAT_RECEIPT'])\n"
        + "        for attempt in range(200):\n"
        + "            if receipt.exists(): break\n"
        + "            time.sleep(0.01)\n"
        + "        else: raise ValueError('producer output was buffered')\n"
        + "    return result\n"
        + "owner.fetch_runs = fetch\n"
        + f"owner.remote_head = lambda **kwargs: {SHA!r}\n"
        + "active_work_status.collect_status = lambda: {'open_task_ids': [], 'git': {'head': 'fixture'}}\n"
        + "automatic_disk_cleanup.inspect_usage = lambda: automatic_disk_cleanup.DiskSnapshot(0.0, 80.0)\n"
        + "ci_failure_ledger.read_ledger = lambda path: {'runs': {}, 'families': {}}\n"
        + "ci_failure_ledger.fetch_run = detail\n"
        + "ci_check_cooldown.load_history = lambda: {}\n"
        + "if case == 'history-error': ci_check_cooldown.load_history = unavailable\n"
        + "if case == 'writer-error': owner.write_snapshot = unavailable\n"
        + "sys.exit(owner.main(sys.argv[1:]))\n", encoding="utf-8",
    )
    (scripts / "ci_failure_ledger.py").write_text(
        "import os, sys\nsys.exit(1 if os.environ['GLUDD_HEARTBEAT_CASE'] == 'ledger-error' else 0)\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sleep = bin_dir / "sleep"
    sleep.write_text(
        f"#!{sys.executable}\nimport os, signal\nfrom pathlib import Path\n"
        "calls = Path(os.environ['GLUDD_HEARTBEAT_SLEEPS'])\n"
        "with calls.open('a') as stream: stream.write('sleep\\n')\n"
        "if os.environ['GLUDD_HEARTBEAT_SEED_MARKER'] == '1':\n"
        "    resource = Path(os.environ['GLUDD_RESOURCE_ROOT']) / os.environ['GLUDD_PROJECT_NAMESPACE']\n"
        "    for marker in resource.glob('status-heartbeat-terminal.*'):\n"
        + f"        marker.write_text({MARKER + ' verdict=success release_ready=false'!r} + '\\n')\n"
        + "if os.environ['GLUDD_HEARTBEAT_COUNT'] == '0': os.kill(os.getppid(), signal.SIGTERM)\n",
        encoding="utf-8",
    )
    sleep.chmod(0o755)
    git = bin_dir / "git"
    git.write_text(f"#!{sys.executable}\nprint({SHA!r})\n", encoding="utf-8")
    git.chmod(0o755)
    uv = bin_dir / "uv.py"
    uv.write_text(
        "import os, sys\nassert sys.argv[1:3] == ['run', 'python']\n"
        "os.execv(sys.executable, [sys.executable, *sys.argv[3:]])\n", encoding="utf-8",
    )
    runs = [_run("Build and Release", 1), _run("Molecule Tests", 2), _run("Presentation", 3)]
    if case in {"failure", "cancelled", "skipped", "timed_out"}:
        runs[0]["conclusion"] = "failure" if case == "failure" else case
    elif case == "sibling-failure":
        runs[-1]["conclusion"] = "failure"
    elif case in {"pending", "failure-pending"}:
        runs[1].update(status="in_progress", conclusion="")
        if case == "failure-pending":
            runs[0]["conclusion"] = "failure"
    elif case == "detail-pending":
        runs[0]["conclusion"] = "failure"
    elif case == "missing":
        runs.pop(1)
    elif case == "empty":
        runs.clear()
    elif case in {"foreign-sha", "foreign-branch"}:
        for run in runs:
            run["headSha" if case == "foreign-sha" else "headBranch"] = (
                OTHER_SHA if case == "foreign-sha" else "master"
            )
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
               PYTHONPATH=f"{ROOT}{os.pathsep}{ROOT / 'src'}", PYTHONDONTWRITEBYTECODE="1",
               GLUDD_PROJECT_NAMESPACE="gludd-heartbeat-native-fixture",
               GLUDD_RESOURCE_ROOT=str(tmp_path / "resources"),
               GLUDD_HEARTBEAT_CASE=case, GLUDD_HEARTBEAT_RUNS=json.dumps(runs),
               GLUDD_HEARTBEAT_CALLS=str(tmp_path / "calls"),
               GLUDD_HEARTBEAT_SLEEPS=str(tmp_path / "sleeps"),
               GLUDD_HEARTBEAT_RECEIPT=str(tmp_path / "stream-receipt"),
               GLUDD_HEARTBEAT_SEED_MARKER="0", GLUDD_HEARTBEAT_COUNT=str(count))
    command = ["make", "--no-print-directory", "status-heartbeat", f"UV={sys.executable} {uv}",
               f"PYTHON={sys.executable}", f"INTERVAL={interval}", f"COUNT={count}",
               f"STATUS_HEARTBEAT_VALIDATE_ONLY={plan}",
               f"PIPELINE_STATUS_REPO={REPO}", f"PIPELINE_STATUS_BRANCH={BRANCH}",
               "PIPELINE_STATUS_REMOTE=sandboxcom", f"PIPELINE_STATUS_SHA={pin}",
               f"PIPELINE_STATUS_FAILURE_LEDGER={tmp_path / 'ledger.json'}",
               f"PIPELINE_STATUS_VALIDATE_ONLY={validate}",
               f"PIPELINE_STATUS_OUTPUT={tmp_path / 'current.json'}"]
    if stop is not None:
        command.append(f"STATUS_HEARTBEAT_STOP_ON_TERMINAL={stop}")
    return command, env


def _execute(tmp_path: Path, command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)


@pytest.mark.parametrize("case", ["success", "failure", "cancelled", "skipped", "timed_out", "sibling-failure"])
def test_opted_in_continuous_heartbeat_stops_after_exact_complete_terminal_result(
    tmp_path: Path, case: str,
) -> None:
    command, env = _fixture(tmp_path, case, count=0)
    result = _execute(tmp_path, command, env)
    expected = "success" if case == "success" else "failure"
    assert result.returncode == (0 if expected == "success" else 2), result.stdout + result.stderr
    assert (tmp_path / "calls").read_text().splitlines() == ["pipeline-status"]
    assert not (tmp_path / "sleeps").exists()
    assert f"STATUS-HEARTBEAT-STOP sha={SHA} outcome={expected} release_ready=false" in result.stdout
    current = json.loads((tmp_path / "current.json").read_text())
    assert current["state"] == expected
    assert current["release_ready"] is False
    assert current["gate_evidence"] is False
    assert not list((tmp_path / "resources").rglob("status-heartbeat-terminal.*"))


@pytest.mark.parametrize("case", [
    "pending", "failure-pending", "missing", "empty", "foreign-sha", "foreign-branch",
    "detail-pending", "detail-identity", "ledger-error", "lookup-error", "writer-error", "history-error",
])
def test_incomplete_or_error_current_observation_cannot_end_heartbeat(tmp_path: Path, case: str) -> None:
    command, env = _fixture(tmp_path, case, count=2)
    result = _execute(tmp_path, command, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "calls").read_text().splitlines() == ["pipeline-status"] * 2
    assert (tmp_path / "sleeps").read_text().splitlines() == ["sleep"]
    assert "STATUS-HEARTBEAT-STOP" not in result.stdout
    assert "PIPELINE-TERMINAL-OBSERVED" not in result.stdout
    assert not list((tmp_path / "resources").rglob("status-heartbeat-terminal.*"))


@pytest.mark.parametrize("case", ["lookup-error", "ledger-error", "writer-error"])
def test_prior_same_sha_snapshot_and_marker_cannot_stop_failed_current_tick(tmp_path: Path, case: str) -> None:
    from scripts.proof_observations import write_snapshot

    command, env = _fixture(tmp_path, case, count=3)
    write_snapshot(tmp_path / "current.json", producer="pipeline-status",
                   identity={"repo": "sandboxcom/gludd", "branch": "development", "sha": SHA},
                   state="success", evidence={"retained": "previous-terminal-success"})
    env["GLUDD_HEARTBEAT_SEED_MARKER"] = "1"
    result = _execute(tmp_path, command, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "calls").read_text().splitlines() == ["pipeline-status"] * 3
    assert (tmp_path / "sleeps").read_text().splitlines() == ["sleep"] * 2
    assert "STATUS-HEARTBEAT-STOP" not in result.stdout
    assert "PIPELINE-TERMINAL-OBSERVED" not in result.stdout
    assert not list((tmp_path / "resources").rglob("status-heartbeat-terminal.*"))


@pytest.mark.parametrize("stop", [None, "0"])
def test_default_and_explicit_continuous_compatibility_retains_next_tick(tmp_path: Path, stop: str | None) -> None:
    command, env = _fixture(tmp_path, "success", count=0, stop=stop)
    result = _execute(tmp_path, command, env)
    assert result.returncode != 0
    assert (tmp_path / "calls").read_text().splitlines() == ["pipeline-status"]
    assert (tmp_path / "sleeps").read_text().splitlines() == ["sleep"]
    assert "STATUS-HEARTBEAT-STOP" not in result.stdout


@pytest.mark.parametrize("pin", ["", OTHER_SHA, SHA[:12]])
def test_opt_in_never_stops_for_unpinned_or_different_commanded_sha(tmp_path: Path, pin: str) -> None:
    command, env = _fixture(tmp_path, "success", count=2, pin=pin)
    result = _execute(tmp_path, command, env)
    assert result.returncode == 0
    assert (tmp_path / "calls").read_text().splitlines() == ["pipeline-status"] * 2
    assert (tmp_path / "sleeps").read_text().splitlines() == ["sleep"]
    assert "STATUS-HEARTBEAT-STOP" not in result.stdout


def test_pipeline_validate_only_is_never_terminal_stop_evidence(tmp_path: Path) -> None:
    command, env = _fixture(tmp_path, "success", count=2, validate=1)
    result = _execute(tmp_path, command, env)
    assert result.returncode == 0
    assert (tmp_path / "calls").read_text().splitlines() == ["pipeline-status"] * 2
    assert not (tmp_path / "current.json").exists()
    assert "PIPELINE-TERMINAL-OBSERVED" not in result.stdout
    assert "STATUS-HEARTBEAT-STOP" not in result.stdout


@pytest.mark.parametrize("stop", ["", "2", "true"])
def test_invalid_terminal_stop_policy_fails_before_owners(tmp_path: Path, stop: str) -> None:
    command, env = _fixture(tmp_path, "success", stop=stop, plan=1)
    result = _execute(tmp_path, command, env)
    assert result.returncode == 2
    assert "STATUS_HEARTBEAT_STOP_ON_TERMINAL must be 0 or 1" in result.stdout
    assert not (tmp_path / "calls").exists()
    assert not (tmp_path / "resources").exists()


def test_terminal_opt_in_plan_invokes_no_owners_or_files(tmp_path: Path) -> None:
    command, env = _fixture(tmp_path, "success", plan=1)
    result = _execute(tmp_path, command, env)
    assert result.returncode == 0
    assert "STATUS-HEARTBEAT-PLAN" in result.stdout
    assert "release_ready=false" in result.stdout
    assert not (tmp_path / "calls").exists()
    assert not (tmp_path / "resources").exists()
    assert not (tmp_path / "current.json").exists()


def test_terminal_opt_in_retains_five_minute_floor(tmp_path: Path) -> None:
    command, env = _fixture(tmp_path, "success", interval=299)
    result = _execute(tmp_path, command, env)
    assert result.returncode == 2
    assert "INTERVAL must be >= 300 seconds" in result.stdout
    assert not (tmp_path / "calls").exists()


@pytest.mark.parametrize("pin", [SHA, ""])
def test_banner_distinguishes_observed_candidate_from_advanced_local_head(
    tmp_path: Path, pin: str,
) -> None:
    command, env = _fixture(tmp_path, "success", count=1, pin=pin)
    git = tmp_path / "bin/git"
    git.write_text(f"#!{sys.executable}\nprint({OTHER_SHA!r})\n", encoding="utf-8")
    result = _execute(tmp_path, command, env)
    assert result.returncode == 0, result.stdout + result.stderr
    banner = next(line for line in result.stdout.splitlines() if line.startswith("=== PIPELINE HEARTBEAT"))
    assert f" sha={pin or 'remote-head'} " in banner
    assert f" local_sha={OTHER_SHA} " in banner
    current = json.loads((tmp_path / "current.json").read_text())
    assert current["identity"]["sha"] == SHA
    assert current["release_ready"] is False
    assert current["gate_evidence"] is False


def test_opt_in_pipeline_output_streams_before_detail_observation_completes(tmp_path: Path) -> None:
    command, env = _fixture(tmp_path, "streaming", count=0)
    with subprocess.Popen(command, cwd=tmp_path, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True) as process:
        assert process.stdout is not None
        lines = []
        for line in process.stdout:
            lines.append(line)
            if line.startswith("PIPELINE-SNAPSHOT-JOBS"):
                (tmp_path / "stream-receipt").touch()
        assert process.wait(timeout=10) == 0, "".join(lines)
    assert (tmp_path / "stream-receipt").exists()
    assert "STATUS-HEARTBEAT-STOP" in "".join(lines)
    assert not (tmp_path / "sleeps").exists()
