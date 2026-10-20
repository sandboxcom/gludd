"""Native legacy log routing must retain GitHub's actual failure status."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JOB_ID = "118932830925"


def _native_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    failure_stage: str = "",
    client_status: int = 0,
    log_lines: int = 3,
    validate_only: str = "0",
    selected_job: str = JOB_ID,
) -> tuple[subprocess.CompletedProcess[str], list[list[str]], Path]:
    source = (ROOT / "make/30-ci-and-release.mk").read_text(encoding="utf-8")
    body = source.split("\nci-job-log:", 1)[1].split("\n\n", 1)[0]
    recipe = tmp_path / "Makefile"
    recipe.write_text("CI_JOB_LOG_VALIDATE_ONLY ?= 0\nci-job-log:" + body + "\n", encoding="utf-8")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "resource_arbiter.py").write_bytes((ROOT / "scripts/resource_arbiter.py").read_bytes())
    binaries = tmp_path / "bin"
    binaries.mkdir()
    trace = tmp_path / "gh-trace.jsonl"
    fake = binaries / "gh"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        f"trace = pathlib.Path({str(trace)!r})\n"
        "args = sys.argv[1:]\n"
        "with trace.open('a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps(args) + '\\n')\n"
        "lookup = '--json' in args\n"
        f"failed = {'lookup'!r} if lookup else {'logs'!r}\n"
        f"if lookup: print({selected_job!r})\n"
        f"elif {log_lines} > 0:\n"
        f"    for number in range({log_lines}): print('line-' + str(number))\n"
        f"if failed == {failure_stage!r}:\n"
        "    print('native GitHub client unavailable', file=sys.stderr)\n"
        f"    raise SystemExit({client_status})\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    resources = tmp_path / "owned resources"
    monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("GLUDD_RESOURCE_ROOT", str(resources))
    environment = os.environ.copy()
    for name in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "GNUMAKEFLAGS"):
        environment.pop(name, None)
    result = subprocess.run(
        [
            "make", "--no-print-directory", "-f", str(recipe), "ci-job-log",
            "RUN=39511340719", "JOB=validate (webkit)", f"PYTHON={sys.executable}",
            f"CI_JOB_LOG_VALIDATE_ONLY={validate_only}",
        ],
        cwd=tmp_path, env=environment, capture_output=True, text=True, check=False, timeout=15,
    )
    calls: list[list[str]] = []
    if trace.exists():
        for line in trace.read_text(encoding="utf-8").splitlines():
            value: object = json.loads(line)
            assert isinstance(value, list)
            arguments: list[str] = []
            for argument in value:
                assert isinstance(argument, str)
                arguments.append(argument)
            calls.append(arguments)
    return result, calls, resources


@pytest.mark.parametrize(("failure_stage", "client_status"), [("lookup", 17), ("logs", 29)])
def test_native_client_failure_is_fatal_and_retains_its_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_stage: str, client_status: int,
) -> None:
    result, calls, resources = _native_log(
        tmp_path, monkeypatch, failure_stage=failure_stage, client_status=client_status,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert f"Error {client_status}" in result.stderr
    assert "native GitHub client unavailable" in result.stdout + result.stderr
    assert len(calls) == (1 if failure_stage == "lookup" else 2)
    assert not list(resources.glob("ci-job-log-*"))


def test_empty_download_is_not_successful_log_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, calls, resources = _native_log(tmp_path, monkeypatch, log_lines=0)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "downloaded log is empty" in result.stdout
    assert len(calls) == 2
    assert not list(resources.glob("ci-job-log-*"))


def test_success_keeps_the_original_four_hundred_line_tail_and_owned_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, calls, resources = _native_log(tmp_path, monkeypatch, log_lines=550)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"--- job id: {JOB_ID} ---" in result.stdout
    output = [line for line in result.stdout.splitlines() if line.startswith("line-")]
    assert output == [f"line-{number}" for number in range(150, 550)]
    assert len(calls) == 2
    assert calls[1] == ["run", "view", "-R", "sandboxcom/gludd", "--log", f"--job={JOB_ID}"]
    assert not list(resources.glob("ci-job-log-*"))


@pytest.mark.parametrize("selected_job", ["", "not-a-job"])
def test_missing_or_malformed_selected_job_never_fetches_a_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected_job: str,
) -> None:
    result, calls, resources = _native_log(tmp_path, monkeypatch, selected_job=selected_job)
    assert result.returncode != 0
    assert len(calls) == 1
    assert not resources.exists()


def test_log_plan_is_offline_and_creates_no_resource_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, calls, resources = _native_log(tmp_path, monkeypatch, validate_only="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CI-JOB-LOG VALIDATED run=39511340719" in result.stdout
    assert calls == [] and not resources.exists()


def test_invalid_log_policy_fails_before_client_or_resource_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, calls, resources = _native_log(tmp_path, monkeypatch, validate_only="2")
    assert result.returncode != 0
    assert "CI_JOB_LOG_VALIDATE_ONLY must be 0 or 1" in result.stdout
    assert calls == [] and not resources.exists()
