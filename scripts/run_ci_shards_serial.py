#!/usr/bin/env python3
"""Run every named CI shard in a fresh process and aggregate release coverage."""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ci_named_shard_files import ISOLATED_TESTS, SHARDS, expand_shard
from run_ci_shards_parallel import _env_for_shard, _parse_shards

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DEFAULT_SHARDS = tuple(SHARDS)
COVERAGE_SHARDS = ROOT / ".coverage-shards-local"
COVERAGE_JSON = ROOT / "coverage.json"
COVERAGE_AUDIT = ROOT / ".gate-logs" / "coverage-local.json"
DEFAULT_COVERAGE_CONFIG = ROOT / "pyproject.toml"
GREENLET_COVERAGE_CONFIG = ROOT / ".coveragerc-greenlet"
GOVERNANCE_MODULE_UTILS = (
    "collections/ansible_collections/general_ludd/governance/plugins/module_utils"
)


def _quote(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def _run_command(command: list[str], *, env: dict[str, str] | None = None) -> int:
    print(f"$ {_quote(command)}", flush=True)
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


def _pytest_command(
    shard: str,
    files: list[str],
    basetemp: Path,
    pytest_args: list[str],
) -> list[str]:
    coverage_config = (
        GREENLET_COVERAGE_CONFIG if shard == "unit-3" else DEFAULT_COVERAGE_CONFIG
    )
    return [
        sys.executable,
        str(SCRIPTS / "adaptive_test.py"),
        *files,
        "--cov=general_ludd",
        f"--cov={GOVERNANCE_MODULE_UTILS}",
        f"--cov-config={coverage_config}",
        "--cov-report=",
        "--cov-fail-under=0",
        "-v",
        *pytest_args,
        f"--basetemp={basetemp / 'pytest'}",
    ]


def _isolated_pytest_command(pytest_args: list[str]) -> list[str]:
    """Run process-heavy tests outside the long-lived coverage workers."""
    return [
        sys.executable,
        "-m",
        "pytest",
        *ISOLATED_TESTS,
        "-v",
        *pytest_args,
    ]


def _save_shard_coverage(shard: str, basetemp: Path, env: dict[str, str]) -> bool:
    coverage_file = Path(env["COVERAGE_FILE"])
    if not coverage_file.is_file():
        # pytest-cov under xdist can leave parallel data. `coverage combine`
        # canonicalizes it into the shard-specific COVERAGE_FILE.
        _run_command(
            [sys.executable, "-m", "coverage", "combine", str(basetemp)],
            env=env,
        )
    if not coverage_file.is_file() or coverage_file.stat().st_size == 0:
        print(f"SHARD-COVERAGE-MISSING shard={shard}", flush=True)
        return False

    destination = COVERAGE_SHARDS / f".coverage.{shard}"
    shutil.copy2(coverage_file, destination)
    print(
        f"SHARD-COVERAGE-SAVED shard={shard} bytes={destination.stat().st_size}",
        flush=True,
    )
    return True


def _aggregate_coverage() -> int:
    """Run `coverage combine`/`coverage report` and the 75% per-file audit."""
    commands = [
        [
            sys.executable,
            "-m",
            "coverage",
            "combine",
            "--keep",
            str(COVERAGE_SHARDS),
        ],
        [sys.executable, "-m", "coverage", "xml"],
        [sys.executable, "-m", "coverage", "json", "-o", str(COVERAGE_JSON)],
        [
            sys.executable,
            "-m",
            "coverage",
            "report",
            "--skip-covered",
            "--show-missing",
            "--fail-under=85",
        ],
        [
            sys.executable,
            str(SCRIPTS / "audit_coverage.py"),
            f"--json-file={COVERAGE_JSON}",
            "--threshold=75",
            "--source=src/general_ludd",
            f"--json-out={COVERAGE_AUDIT}",
        ],
    ]
    result = 0
    for command in commands:
        result = max(result, _run_command(command))
    return result


def run(shards: list[str], pytest_args: list[str]) -> int:
    shutil.rmtree(COVERAGE_SHARDS, ignore_errors=True)
    COVERAGE_SHARDS.mkdir(parents=True)
    COVERAGE_AUDIT.parent.mkdir(parents=True, exist_ok=True)
    erase_rc = _run_command([sys.executable, "-m", "coverage", "erase"])
    if erase_rc:
        print(f"COVERAGE-ERASE-FAIL rc={erase_rc}", flush=True)
        shutil.rmtree(COVERAGE_SHARDS, ignore_errors=True)
        return erase_rc

    failures: dict[str, int] = {}
    isolated_rc = _run_command(_isolated_pytest_command(pytest_args))
    if isolated_rc:
        failures["isolated"] = isolated_rc
        print(f"ISOLATED-TESTS-FAIL rc={isolated_rc}", flush=True)
    else:
        print("ISOLATED-TESTS-PASS rc=0", flush=True)

    for index, shard in enumerate(shards, start=1):
        files = expand_shard(shard)
        if not files:
            print(f"SHARD-EMPTY shard={shard}", flush=True)
            failures[shard] = 2
            continue

        basetemp = Path(tempfile.mkdtemp(prefix=f"gludd-gate-{shard}-", dir="/tmp"))
        try:
            env = _env_for_shard(shard, basetemp)
            env["COVERAGE_FILE"] = str(basetemp / ".coverage")
            print(
                f"=== GATE TEST SHARD {index}/{len(shards)}: {shard} "
                f"files={len(files)} ===",
                flush=True,
            )
            rc = _run_command(
                _pytest_command(shard, files, basetemp, pytest_args),
                env=env,
            )
            coverage_saved = _save_shard_coverage(shard, basetemp, env)
            if rc != 0 or not coverage_saved:
                failures[shard] = rc or 1
                print(f"SHARD-FAIL shard={shard} rc={rc}", flush=True)
            else:
                print(f"SHARD-PASS shard={shard} rc=0", flush=True)
        finally:
            shutil.rmtree(basetemp, ignore_errors=True)

    coverage_rc = _aggregate_coverage()
    if coverage_rc:
        failures["coverage"] = coverage_rc
    print(
        f"SERIAL-SHARD-SUMMARY total={len(shards)} "
        f"failed={len(failures)} failures={failures}",
        flush=True,
    )
    shutil.rmtree(COVERAGE_SHARDS, ignore_errors=True)
    return max(failures.values(), default=0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--shards",
        default=" ".join(DEFAULT_SHARDS),
        help="space or comma separated shard names",
    )
    parser.add_argument("--pytest-args", default="", help="extra pytest arguments")
    args = parser.parse_args()
    return run(_parse_shards(args.shards), shlex.split(args.pytest_args))


if __name__ == "__main__":
    raise SystemExit(main())
