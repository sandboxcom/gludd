"""Native Make CI verdicts use the existing exact all-workflow owner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40


def _stanza(content: str, name: str) -> str:
    start = content.index(f"\n{name}:") + 1
    end = content.find("\n\n", start)
    return content[start:end if end >= 0 else len(content)]


def _run(workflow: str, number: int) -> dict[str, object]:
    return {"workflowName": workflow, "databaseId": number,
            "headSha": SHA, "headBranch": "development", "event": "push",
            "status": "completed", "conclusion": "success",
            "createdAt": f"2026-10-13T00:00:0{number}Z", "url": ""}


def _sandbox(
    tmp_path: Path, runs: list[dict[str, object]],
    target: str = "ci-verdict", validate_only: int = 0, owner_crash: bool = False,
    make_variables: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    content = compose_makefile(ROOT / "Makefile")
    carriers = "\n".join(line for line in content.splitlines()
                         if line.startswith(("_CI_VERDICT_COMMAND =", "CI_VERDICT_VALIDATE_ONLY ?=")))
    (tmp_path / "Makefile").write_text(
        carriers + "\n\n" + _stanza(content, "ci-verdict") + "\n\n"
        + _stanza(content, "ci-verdict-safe") + "\n", encoding="utf-8",
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("pipeline_status.py", "ci_check_cooldown.py"):
        wrapper = (
            "raise RuntimeError('canonical verdict owner unavailable')\n"
            if owner_crash and name == "pipeline_status.py" else
            "import runpy\n"
            + f"runpy.run_path({str(ROOT / 'scripts' / name)!r}, run_name='__main__')\n"
        )
        (scripts / name).write_text(wrapper, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "runs = json.loads(os.environ['GLUDD_VERDICT_TEST_RUNS'])\n"
        "with open(os.environ['GLUDD_VERDICT_TEST_CALLS'], 'a') as stream:\n"
        "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "print(json.dumps(runs[-1] if '--jq' in sys.argv else runs))\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    git = bin_dir / "git"
    git.write_text(
        f"#!{sys.executable}\nimport sys\n"
        f"print('development' if sys.argv[1:] == ['branch', '--show-current'] else {SHA!r})\n",
        encoding="utf-8",
    )
    git.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
               GLUDD_VERDICT_TEST_RUNS=json.dumps(runs),
               GLUDD_VERDICT_TEST_CALLS=str(tmp_path / "calls.jsonl"),
               GLUDD_CI_STATE_FILE=str(tmp_path / "cooldown.json"),
               GLUDD_CI_HISTORY_FILE=str(tmp_path / "history.json"),
               GLUDD_CI_RESTART_COUNT_FILE=str(tmp_path / "restart-count"),
               GLUDD_PUSH_STATE_FILE=str(tmp_path / "push.json"))
    return subprocess.run(
        ["make", "--no-print-directory", target, f"PYTHON={sys.executable}",
         f"SHA={SHA}", "CI_BRANCH=development", "CI_CHECK_COOLDOWN_SEC=600",
         f"CI_VERDICT_VALIDATE_ONLY={int(validate_only)}", *make_variables],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30,
    )


@pytest.mark.parametrize("case", [
    "success", "build-failure", "molecule-failure", "pages-failure",
    "pending", "missing-build", "missing-molecule", "wrong-sha",
])
def test_ci_verdict_uses_complete_exact_workflow_set(tmp_path: Path, case: str) -> None:
    runs = [_run("Build and Release", 1), _run("Molecule Tests", 2),
            _run("Validate and Deploy Presentation to Pages", 3)]
    if case.endswith("-failure"):
        index = {"build-failure": 0, "molecule-failure": 1, "pages-failure": 2}[case]
        runs[index]["conclusion"] = "failure"
    elif case == "pending":
        runs[0].update(status="in_progress", conclusion="")
    elif case.startswith("missing-"):
        runs.pop(0 if case == "missing-build" else 1)
    elif case == "wrong-sha":
        for run in runs:
            run["headSha"] = "b" * 40
    result = _sandbox(tmp_path, runs)
    output = result.stdout + result.stderr
    assert (result.returncode == 0) is (case == "success"), output
    assert "Build and Release" in output
    assert "Molecule Tests" in output
    assert SHA in output
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert len(calls) == 1
    assert "--jq" not in calls[0]


def test_ci_verdict_safe_records_failure_without_recursive_make_exit_aliasing(tmp_path: Path) -> None:
    runs = [_run("Build and Release", 1), _run("Molecule Tests", 2),
            _run("Validate and Deploy Presentation to Pages", 3)]
    runs[0]["conclusion"] = "failure"
    result = _sandbox(tmp_path, runs, "ci-verdict-safe")
    assert result.returncode != 0, result.stdout + result.stderr
    history = json.loads((tmp_path / "history.json").read_text())
    assert history["last_checked_sha"] == SHA
    assert history["last_verdict"] == "failure"
    assert (tmp_path / "restart-count").read_text() == "0"


def test_ci_verdict_safe_validate_only_never_queries_or_records(tmp_path: Path) -> None:
    result = _sandbox(tmp_path, [], "ci-verdict-safe", validate_only=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PIPELINE_STATUS_VALIDATE_ONLY_PASS" in result.stdout
    for name in ("calls.jsonl", "cooldown.json", "history.json", "restart-count", "push.json"):
        assert not (tmp_path / name).exists(), name


@pytest.mark.parametrize("flag", [-1, 2])
def test_ci_verdict_safe_rejects_bad_validate_flag(tmp_path: Path, flag: int) -> None:
    runs = [_run("Build and Release", 1), _run("Molecule Tests", 2),
            _run("Validate and Deploy Presentation to Pages", 3)]
    result = _sandbox(tmp_path, runs, "ci-verdict-safe", validate_only=flag)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "CI_VERDICT_VALIDATE_ONLY must be 0 or 1" in result.stdout
    for name in ("calls.jsonl", "cooldown.json", "history.json", "restart-count", "push.json"):
        assert not (tmp_path / name).exists(), name


@pytest.mark.parametrize(
    "incomplete", ["pending", "failure-pending", "failure-missing", "missing", "wrong-sha"],
)
def test_ci_verdict_safe_incomplete_evidence_never_verifies_push_or_resets_budget(
    tmp_path: Path, incomplete: str,
) -> None:
    history = tmp_path / "history.json"
    restart = tmp_path / "restart-count"
    history.write_text(json.dumps({"last_push_sha": SHA, "retained": "owned"}))
    restart.write_text("2")
    before = history.read_bytes()
    runs = [_run("Build and Release", 1), _run("Molecule Tests", 2)]
    if incomplete in {"pending", "failure-pending"}:
        runs[1].update(status="in_progress", conclusion="")
    elif incomplete in {"missing", "failure-missing"}:
        runs.pop()
    else:
        for run in runs:
            run["headSha"] = "b" * 40
    if incomplete.startswith("failure-"):
        runs[0]["conclusion"] = "failure"
    result = _sandbox(tmp_path, runs, "ci-verdict-safe")
    assert result.returncode != 0, result.stdout + result.stderr
    assert history.read_bytes() == before
    assert restart.read_text() == "2"


def test_ci_verdict_safe_owner_crash_is_not_a_terminal_failure_authorization(
    tmp_path: Path,
) -> None:
    history = tmp_path / "history.json"
    restart = tmp_path / "restart-count"
    history.write_text(json.dumps({"last_push_sha": SHA, "retained": "owned"}))
    restart.write_text("2")
    before = history.read_bytes()
    runs = [_run("Build and Release", 1), _run("Molecule Tests", 2)]
    result = _sandbox(tmp_path, runs, "ci-verdict-safe", owner_crash=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "canonical verdict owner unavailable" in result.stderr
    assert history.read_bytes() == before
    assert restart.read_text() == "2"


def test_ci_verdict_safe_complete_success_records_only_the_owned_push(tmp_path: Path) -> None:
    history = tmp_path / "history.json"
    history.write_text(json.dumps({"last_push_sha": SHA, "retained": "owned"}))
    (tmp_path / "restart-count").write_text("2")
    runs = [_run("Build and Release", 1), _run("Molecule Tests", 2)]
    result = _sandbox(tmp_path, runs, "ci-verdict-safe")
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads(history.read_text())
    assert record["last_checked_sha"] == SHA
    assert record["last_verdict"] == "success"
    assert record["retained"] == "owned"
    assert (tmp_path / "restart-count").read_text() == "0"
