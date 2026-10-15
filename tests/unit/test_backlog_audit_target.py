"""Native Make entrypoint and observation contracts for the backlog auditor."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import check_make_help, check_make_target_contract, makefile_layout

ROOT = Path(makefile_layout.__file__).resolve().parents[1]
FRAGMENT = ROOT / "make/50-development-and-guardrails.mk"
VARIABLES = {
    "BACKLOG_AUDIT_VALIDATE_ONLY", "BACKLOG_AUDIT_COLLECT", "BACKLOG_AUDIT_VERDICTS",
    "OBSERVED_ROOT", "OBSERVED_HEARTBEAT_SECS", "OBSERVED_QUIET_SECS",
    "OBSERVED_MAX_SECS", "OBSERVED_RETAIN_RUNS",
}


def _run(
    tmp_path: Path,
    *,
    validate_only: str = "0",
    collect: str = "1",
    verdicts: str = "1",
    result: int = 0,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
    """Execute the real recipe and observer, replacing only the CLI boundary."""
    source = FRAGMENT.read_text(encoding="utf-8")
    recipe = check_make_target_contract._stanzas(source)["backlog-audit"]
    trace = tmp_path / "trace.jsonl"
    boundary = tmp_path / "boundary.py"
    boundary.write_text(
        "import json, os, pathlib, sys\n"
        f"trace = pathlib.Path({str(trace)!r})\n"
        "args = sys.argv[1:]\n"
        "def record(kind):\n"
        "    with trace.open('a') as stream:\n"
        "        stream.write(json.dumps(dict(kind=kind, args=args)) + '\\n')\n"
        "if args[:3] == ['run', 'python', 'scripts/stream_command.py']:\n"
        "    record('observer')\n"
        f"    os.execv(sys.executable, [sys.executable, {str(ROOT / 'scripts/stream_command.py')!r}, *args[3:]])\n"
        "elif args[:3] == ['run', 'python', 'scripts/backlog_audit.py']:\n"
        "    record('audit')\n"
        "    print('BACKLOG-BOUNDARY test-only', flush=True)\n"
        f"    raise SystemExit({result})\n"
        "else: raise AssertionError(args)\n",
        encoding="utf-8",
    )
    makefile = tmp_path / "Makefile"
    makefile.write_text(recipe + "\n", encoding="utf-8")
    argv = [
        "make", "--no-print-directory", "-f", str(makefile), "backlog-audit",
        f"UV={shlex.join([sys.executable, str(boundary)])}",
        f"BACKLOG_AUDIT_VALIDATE_ONLY={validate_only}",
        f"BACKLOG_AUDIT_COLLECT={collect}",
        f"BACKLOG_AUDIT_VERDICTS={verdicts}",
        f"OBSERVED_ROOT={tmp_path / '.gate-logs/observed'}",
        "OBSERVED_HEARTBEAT_SECS=1", "OBSERVED_QUIET_SECS=10",
        "OBSERVED_MAX_SECS=30", "OBSERVED_RETAIN_RUNS=2",
    ]
    process = subprocess.run(
        argv, cwd=tmp_path, env=dict(os.environ), text=True, capture_output=True,
        check=False, timeout=45,
    )
    records = [
        json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()
    ] if trace.exists() else []
    return process, records


def test_backlog_target_is_public_registered_and_documented() -> None:
    contract = check_make_target_contract.load_contract(ROOT / "config/make_target_contract.json")
    matches = [record for record in contract["targets"] if record["name"] == "backlog-audit"]
    assert len(matches) == 1
    assert set(matches[0]["make_variables"]) == VARIABLES
    assert "BACKLOG_AUDIT_VALIDATE_ONLY=1" in matches[0]["behavior"]
    assert check_make_target_contract.validate_contract(ROOT / "Makefile", contract) == []
    source = makefile_layout.compose_makefile(ROOT / "Makefile")
    entries = [
        line for line in source.splitlines()
        if (match := check_make_help.HELP_TARGET_RE.match(line)) is not None
        and match.group(1) == "backlog-audit"
    ]
    assert len(entries) == 1
    assert "Observed checked-task audit" in entries[0]
    assert "BACKLOG_AUDIT_VALIDATE_ONLY, BACKLOG_AUDIT_COLLECT, BACKLOG_AUDIT_VERDICTS, OBSERVED_*" in entries[0]
    assert "backlog-audit" in source.split(".PHONY:", 1)[1].split("\n\n", 1)[0]


def test_validation_only_does_not_start_a_runner_or_claim_proof(tmp_path: Path) -> None:
    process, records = _run(tmp_path, validate_only="1")
    assert process.returncode == 0, process.stdout + process.stderr
    assert records == []
    assert "proof=none" in process.stdout
    assert "release_ready=false" in process.stdout
    assert "gate_evidence=false" in process.stdout
    assert not (tmp_path / ".gate-logs").exists()


@pytest.mark.parametrize("collect", ["0", "1"])
@pytest.mark.parametrize("verdicts", ["0", "1"])
def test_native_audit_is_observed_once_with_exact_requested_sections(
    tmp_path: Path, collect: str, verdicts: str,
) -> None:
    process, records = _run(tmp_path, collect=collect, verdicts=verdicts)
    assert process.returncode == 0, process.stdout + process.stderr
    assert [record["kind"] for record in records] == ["observer", "audit"]
    observer = records[0]["args"]
    child = records[1]["args"]
    assert isinstance(observer, list)
    assert isinstance(child, list)
    assert observer[observer.index("--label") + 1] == "backlog-audit"
    assert observer[observer.index("--heartbeat-secs") + 1] == "1"
    assert observer[observer.index("--quiet-secs") + 1] == "10"
    assert observer[observer.index("--max-secs") + 1] == "30"
    assert child[:3] == ["run", "python", "scripts/backlog_audit.py"]
    assert child[child.index("--repo-root") + 1] == str(tmp_path)
    assert ("--no-collect" in child) is (collect == "0")
    assert ("--no-backlog-verdicts" in child) is (verdicts == "0")
    assert "BACKLOG-BOUNDARY test-only" in process.stdout
    assert "state=passed rc=0" in process.stdout


def test_native_audit_failure_remains_nonzero(tmp_path: Path) -> None:
    process, records = _run(tmp_path, result=17)
    assert process.returncode != 0
    assert [record["kind"] for record in records] == ["observer", "audit"]
    assert "state=failed rc=17" in process.stdout


@pytest.mark.parametrize("value", ["", "2", "true", "-1"])
@pytest.mark.parametrize("option", ["validate_only", "collect", "verdicts"])
def test_invalid_boolean_policy_is_refused_before_any_subprocess(
    tmp_path: Path, option: str, value: str,
) -> None:
    flags = {"validate_only": "0", "collect": "1", "verdicts": "1"}
    flags[option] = value
    process, records = _run(
        tmp_path, validate_only=flags["validate_only"], collect=flags["collect"],
        verdicts=flags["verdicts"],
    )
    assert process.returncode != 0
    assert records == []
    assert "must be 0 or 1" in process.stdout
