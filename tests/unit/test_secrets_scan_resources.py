"""Native secrets-scan resource and unchanged audit-routing contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest
from detect_secrets.core.usage import ParserBuilder
from scripts import security_audit_observability as observer
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / ".secrets.baseline"


def _run_scan(
    tmp_path: Path,
    *,
    target: str,
    host_cores: int,
    project_limit: str,
    scanner_status: int = 0,
    scan_args: str = "",
    baseline_mutation: str = "metadata",
) -> tuple[subprocess.CompletedProcess[str], Path]:
    capture = tmp_path / "native-scanner-argv.json"
    scanner = tmp_path / "detect-secrets"
    scanner.write_text(
        f"#!{sys.executable}\n"
        + dedent(
            """
            import hashlib
            import json
            import sys
            from pathlib import Path

            argv = [Path(sys.argv[0]).name, *sys.argv[1:]]
            baseline = Path(argv[argv.index("--baseline") + 1])
            assert baseline.resolve() != Path(__CANONICAL__).resolve()
            assert baseline.resolve().is_relative_to(Path(__FIXTURE__).resolve())
            evidence = {
                "argv": argv,
                "baseline": str(baseline),
                "baseline_sha256": hashlib.sha256(baseline.read_bytes()).hexdigest(),
            }
            Path(__CAPTURE__).write_text(json.dumps(evidence), encoding="utf-8")
            payload = json.loads(baseline.read_text(encoding="utf-8"))
            mutation = __MUTATION__
            if mutation == "new-finding":
                payload["results"].setdefault("fixture/new-finding.py", []).append({
                    "filename": "fixture/new-finding.py",
                    "type": "Secret Keyword",
                    "hashed_secret": "f" * 40,
                    "line_number": 1,
                })
            elif mutation == "malformed":
                payload = {}
            else:
                assert mutation == "metadata"
                payload["generated_at"] = "2026-10-21T00:00:00Z"
            baseline.write_text(json.dumps(payload) + "\\n", encoding="utf-8")
            raise SystemExit(__SCANNER_STATUS__)
            """
        )
        .replace("__CANONICAL__", json.dumps(str(BASELINE)))
        .replace("__FIXTURE__", json.dumps(str(tmp_path)))
        .replace("__CAPTURE__", json.dumps(str(capture)))
        .replace("__MUTATION__", json.dumps(baseline_mutation))
        .replace("__SCANNER_STATUS__", str(scanner_status)),
        encoding="utf-8",
    )
    scanner.chmod(0o700)
    spy = tmp_path / "native-scanner-spy.py"
    uv_capture = tmp_path / "native-uv-argv.json"
    spy.write_text(
        dedent(
            """
            import json
            import subprocess
            import sys
            from pathlib import Path

            if sys.argv[1] == "probe":
                assert sys.argv[2] == "-c"
                prefix = "import multiprocessing; multiprocessing.cpu_count = lambda: __HOST_CORES__; "
                completed = subprocess.run(
                    [sys.executable, "-c", prefix + sys.argv[3]], check=False,
                )
                raise SystemExit(completed.returncode)

            assert sys.argv[1] == "uv"
            argv = sys.argv[2:]
            Path(__UV_CAPTURE__).write_text(json.dumps(argv), encoding="utf-8")
            assert argv[0] == "run"
            if argv[1] == "detect-secrets":
                command = [__SCANNER__, *argv[2:]]
            else:
                assert argv[1] == "python"
                command = [sys.executable, *argv[2:]]
            completed = subprocess.run(command, check=False)
            raise SystemExit(completed.returncode)
            """
        )
        .replace("__HOST_CORES__", str(host_cores))
        .replace("__SCANNER__", json.dumps(str(scanner)))
        .replace("__UV_CAPTURE__", json.dumps(str(uv_capture))),
        encoding="utf-8",
    )
    native_make = (ROOT / "make/40-cross-version-and-worktrees.mk").read_text(
        encoding="utf-8"
    )
    assert native_make.count("define _secrets_scan_native_cores\n") == 1
    assert native_make.count("\nscan-secrets:\n") == 1
    assert native_make.count("\nsecrets-scan:\n") == 1
    fixture_make = tmp_path / "native-secrets.mk"
    fixture_make.write_text(
        native_make
        .replace(
            "/tmp/gludd-secrets-baseline.",
            shlex.quote(str(tmp_path / "gludd-secrets-baseline.")),
        )
        .replace(
            "/tmp/gludd-push-state.json",
            shlex.quote(str(tmp_path / "gludd-push-state.json")),
        ),
        encoding="utf-8",
    )
    interpreter = shlex.quote(sys.executable)
    probe = shlex.quote(str(spy))
    before = BASELINE.read_bytes()
    env = {
        key: value for key, value in os.environ.items()
        if key not in {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "GLUDD_WORKER_LIMIT"}
    }
    env.update({
        "TMPDIR": str(tmp_path),
        "PATH": str(tmp_path) + os.pathsep + env.get("PATH", ""),
        "VIRTUAL_ENV": sys.prefix,
        "UV_PROJECT_ENVIRONMENT": sys.prefix,
        "UV_NO_SYNC": "1",
        "UV_PYTHON": sys.executable,
        "UV_PYTHON_DOWNLOADS": "never",
    })
    result = subprocess.run(
        [
            "make", "--no-print-directory", "-f", str(fixture_make), target,
            f"UV={interpreter} {probe} uv",
            f"PYTHON={interpreter} {probe} probe",
            f"GLUDD_WORKER_LIMIT={project_limit}",
            f"ARGS={scan_args}",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert BASELINE.read_bytes() == before
    if capture.is_file():
        assert uv_capture.is_file()
        evidence = json.loads(capture.read_text(encoding="utf-8"))
        assert evidence["baseline_sha256"] == hashlib.sha256(before).hexdigest()
        assert not Path(evidence["baseline"]).exists()
    return result, capture


@pytest.mark.parametrize("target", ["scan-secrets", "secrets-scan"])
@pytest.mark.parametrize(
    ("host_cores", "project_limit", "expected_cores"),
    [
        (8, "8", 5),
        (2, "8", 2),
        (8, "5", 2),
        (8, "4", 1),
    ],
    ids=["normal-host", "small-host", "constrained-project", "minimum-budget"],
)
def test_native_scanner_argv_respects_host_and_project_budget(
    tmp_path: Path,
    target: str,
    host_cores: int,
    project_limit: str,
    expected_cores: int,
) -> None:
    result, capture = _run_scan(
        tmp_path, target=target, host_cores=host_cores, project_limit=project_limit,
        scan_args="tests/fixtures",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    argv = json.loads(capture.read_text(encoding="utf-8"))["argv"]
    assert argv[:4] == ["detect-secrets", "--cores", str(expected_cores), "scan"]
    assert argv[4] == "--baseline"
    assert argv[6:] == ["tests/fixtures"]
    assert argv.count("--cores") == 1

    native_parser = ParserBuilder().add_console_use_arguments()._parser
    parsed = native_parser.parse_args([*argv[1:4], "--string", "ordinary-text"])
    assert parsed.num_cores == [expected_cores]


@pytest.mark.parametrize("target", ["scan-secrets", "secrets-scan"])
@pytest.mark.parametrize("project_limit", ["0", "3"])
def test_insufficient_project_budget_refuses_before_native_scanner(
    tmp_path: Path, target: str, project_limit: str,
) -> None:
    result, capture = _run_scan(
        tmp_path, target=target, host_cores=8, project_limit=project_limit,
    )

    assert result.returncode != 0
    assert "GLUDD_WORKER_LIMIT must be at least 4" in result.stderr
    assert not capture.exists()


def test_native_scan_preserves_failure_status_and_disposable_cleanup(tmp_path: Path) -> None:
    result, capture = _run_scan(
        tmp_path, target="scan-secrets", host_cores=8, project_limit="8", scanner_status=7,
    )

    assert result.returncode != 0
    assert "Error 7" in result.stderr
    assert capture.is_file()


@pytest.mark.parametrize("baseline_mutation", ["new-finding", "malformed"])
def test_watchdog_scan_rejects_successful_new_or_invalid_inventory(
    tmp_path: Path, baseline_mutation: str,
) -> None:
    """A refresh exit0 cannot authorize an unvalidated new inventory."""
    result, capture = _run_scan(
        tmp_path, target="secrets-scan", host_cores=8, project_limit="8",
        baseline_mutation=baseline_mutation,
    )

    assert result.returncode != 0
    assert capture.is_file()
    argv = json.loads(capture.read_text(encoding="utf-8"))["argv"]
    assert argv[:4] == ["detect-secrets", "--cores", "5", "scan"]
    assert argv[4] == "--baseline"
    assert argv[6:] == []
    assert argv.count("--cores") == 1


def test_security_audit_retains_existing_native_scan_owner_and_all_phases(tmp_path: Path) -> None:
    commands = observer._audit_commands(
        argparse.Namespace(
            validate_only=False,
            make_command="make",
            sast_report=tmp_path / "report.json",
            sast_summary=tmp_path / "summary.json",
            sast_baseline=tmp_path / "baseline.json",
        )
    )

    assert [name for name, _, _ in commands] == [
        "secrets-scan", "sast", "pip-audit", "npm-audit", "security-backlog",
    ]
    assert commands[0] == (
        "secrets-scan", ["make", "--no-print-directory", "secrets-scan", "ARGS="], True,
    )
    assert all(sensitive is False for _, _, sensitive in commands[1:])


def test_native_secret_budget_has_make_contract_help_and_documentation() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    contract = json.loads((ROOT / "config/make_target_contract.json").read_text(encoding="utf-8"))
    entries = {entry["name"]: entry for entry in contract["targets"]}
    for target in ("scan-secrets", "secrets-scan", "security-audit"):
        assert "GLUDD_WORKER_LIMIT" in entries[target]["make_variables"]
        assert "GLUDD_WORKER_LIMIT=8" in entries[target]["behavior"]
    assert "export GLUDD_WORKER_LIMIT" in makefile
    assert "read-only; GLUDD_WORKER_LIMIT, ARGS" in makefile
    assert "SECURITY_AUDIT_HEARTBEAT_SECS ?= 15" in makefile
    assert "SECURITY_AUDIT_PHASE_TIMEOUT_SECS ?= 1800" in makefile

    docs = (ROOT / "docs/security/audit-observability.md").read_text(encoding="utf-8")
    reference = (ROOT / "docs/CONFIG_REFERENCE.md").read_text(encoding="utf-8")
    assert "worker_limit - 3" in docs
    assert "limit below 4 refuses" in docs
    assert "cannot raise parallelism on a smaller runner" in docs
    assert "Secrets scans require at least 4 slots" in reference
