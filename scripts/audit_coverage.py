#!/usr/bin/env python3
"""Coverage audit using coverage.py line and branch arc data.

Usage:
  python3 scripts/audit_coverage.py [--threshold=85] [--source=src/general_ludd]
  [--json-out=.gate-logs/coverage-<ts>.json]
  python3 scripts/audit_coverage.py --json-file=coverage.json --threshold=85

Modes:
  - Default mode: runs pytest with --cov-branch and parses the JSON report it produces.
  - --json-file mode: parses an existing coverage.json file (no pytest run).
"""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def parse_coverage_json(
    json_path: str,
    threshold: float,
    source_path: str,
    per_file_threshold: float = 75.0,
) -> tuple[dict, list[str], bool]:
    with open(json_path) as f:
        raw = f.read()
    data = json.loads(raw)

    files_under: list[str] = []
    files_ok: list[str] = []
    per_file: dict[str, float] = {}
    per_file_branch: dict[str, float] = {}
    missing_arcs: dict[str, list[list[int]]] = {}
    total_statements = covered_statements = 0
    total_branches = covered_branches = 0

    raw_files = data.get("files", {})
    for fpath, finfo in raw_files.items():
        summary = finfo.get("summary", {})
        num_stmts = summary.get("num_statements", 0)
        covered = summary.get("covered_lines", summary.get("num_lines_covered", 0))
        total_statements += int(num_stmts or 0)
        covered_statements += int(covered or 0)

        num_branches = int(summary.get("num_branches", 0) or 0)
        covered_branch_count = int(summary.get("covered_branches", 0) or 0)
        # Older reports omit branch fields. Keep line-only JSON fixtures useful,
        # while real --cov-branch reports always provide these counters.
        branch_pct = (
            100.0 * covered_branch_count / num_branches
            if num_branches
            else (100.0 * covered / num_stmts if num_stmts else 100.0)
        )
        if num_stmts == 0 and num_branches == 0:
            continue

        pct = round(100.0 * covered / num_stmts, 1) if num_stmts else 100.0

        rel = _relative_path(fpath, source_path)
        per_file[rel] = pct
        per_file_branch[rel] = round(branch_pct, 1)
        total_branches += num_branches
        covered_branches += covered_branch_count
        missing = finfo.get("missing_branches", [])
        if missing:
            missing_arcs[rel] = [list(arc) for arc in missing]

        if pct < threshold or branch_pct < per_file_threshold:
            files_under.append(rel)
        else:
            files_ok.append(rel)

    aggregate_branch_pct = (
        round(100.0 * covered_branches / total_branches, 1)
        if total_branches
        else (round(100.0 * sum(per_file.values()) / len(per_file), 1) if per_file else 100.0)
    )
    aggregate_line_pct = (
        round(100.0 * covered_statements / total_statements, 1)
        if total_statements
        else 100.0
    )
    per_file_results = {
        rel: {
            "line_coverage": per_file[rel],
            "branch_coverage": per_file_branch[rel],
            "line_threshold": threshold,
            "branch_threshold": per_file_threshold,
            "passed": rel not in files_under,
        }
        for rel in sorted(per_file)
    }
    all_ok = len(files_under) == 0 and aggregate_branch_pct >= threshold

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "threshold": threshold,
        "branch_threshold": threshold,
        "per_file_threshold": per_file_threshold,
        "source": source_path,
        "total_files": len(per_file),
        "files_below_threshold": len(files_under),
        "passed": all_ok,
        "files_under_threshold": files_under,
        "files_above_threshold": len(files_ok),
        "per_file": dict(sorted(per_file.items())),
        "per_file_branch": dict(sorted(per_file_branch.items())),
        "per_file_results": per_file_results,
        "per_file_thresholds": {
            "line": threshold,
            "branch": per_file_threshold,
        },
        "line_coverage": aggregate_line_pct,
        "branch_coverage": aggregate_branch_pct,
        "total_branches": total_branches,
        "covered_branches": covered_branches,
        "e2e_branch_coverage": aggregate_branch_pct,
        "e2e_branch_totals": {
            "total": total_branches,
            "covered": covered_branches,
            "missing": max(total_branches - covered_branches, 0),
            "coverage_percent": aggregate_branch_pct,
            "total_branches": total_branches,
            "covered_branches": covered_branches,
        },
        "shards": [],
        "failed_shards": [],
        "missing_arcs": dict(sorted(missing_arcs.items())),
        "contexts": {
            rel: sorted(finfo.get("contexts", {}))
            for fpath, finfo in raw_files.items()
            if (rel := _relative_path(fpath, source_path)) in per_file
            and finfo.get("contexts")
        },
    }

    return report, files_under, all_ok


def _relative_path(fpath: str, source_path: str) -> str:
    src_path = Path(source_path).resolve()
    abs_fpath = Path(fpath).resolve()
    try:
        return str(abs_fpath.relative_to(src_path.parent))
    except ValueError:
        return fpath


COVERAGE_AUDIT_TIMEOUT_SECONDS = int(
    os.environ.get("GLUDD_COVERAGE_AUDIT_TIMEOUT_SECONDS", "1800")
)


def _coverage_environment() -> dict[str, str]:
    """Build the child environment for a certified E2E coverage shard.

    The E2E readiness contract is intentionally independent of the caller's
    shell.  In particular, a developer may invoke ``make audit-coverage``
    without exporting ``GLUDD_E2E_ACTIVE``; every shard must still exercise
    the E2E startup/readiness branches.
    """
    env = os.environ.copy()
    env.update({
        "GLUDD_COVERAGE_AUDIT": "1",
        "GLUDD_E2E_ACTIVE": "1",
    })
    return env


def run_pytest_coverage(
    source: str,
    json_out_path: str,
    shard_results: list[dict[str, object]] | None = None,
) -> int:
    """Run certified serial E2E files, then emit JSON only after all pass."""
    env = _coverage_environment()
    root = Path(__file__).parent.parent
    files = sorted((root / "tests/e2e").rglob("test_*.py"))
    if not files:
        print("ERROR: no E2E test files found", file=sys.stderr)
        return 2
    coverage_file = root / f".coverage.audit.{os.getpid()}"
    env["COVERAGE_FILE"] = str(coverage_file)
    coverage_file.unlink(missing_ok=True)
    try:
        results = shard_results if shard_results is not None else []
        for index, test_file in enumerate(files):
            basetemp = Path(f"/tmp/gludd-audit-e2e-{os.getpid()}-{index}")
            args = [sys.executable, "-m", "pytest", str(test_file),
                    f"--cov={source}", "--cov-branch", "--cov-context=test",
                    "--cov-append", "--cov-fail-under=0", "--cov-report=",
                    "-n", "2", "--dist", "loadgroup", "-q",
                    f"--basetemp={basetemp}"]
            result = subprocess.run(args, cwd=root, env=env,
                                    timeout=COVERAGE_AUDIT_TIMEOUT_SECONDS)
            shard = {
                "path": str(test_file.relative_to(root)),
                "status": "passed" if result.returncode == 0 else "failed",
                "returncode": result.returncode,
            }
            results.append(shard)
            if result.returncode != 0:
                print(f"E2E coverage file failed: {test_file}", file=sys.stderr)
                return result.returncode
        report = subprocess.run(
            [sys.executable, "-m", "coverage", "json", "--show-contexts",
             "-o", json_out_path], cwd=root, env=env,
            timeout=COVERAGE_AUDIT_TIMEOUT_SECONDS,
        )
        return report.returncode
    except subprocess.TimeoutExpired:
        results = shard_results if shard_results is not None else []
        results.append({
            "path": "<timeout>",
            "status": "timed_out",
            "returncode": 124,
        })
        print(
            "ERROR: coverage pytest timed out after "
            f"{COVERAGE_AUDIT_TIMEOUT_SECONDS}s",
            file=sys.stderr,
        )
        return 124


def main() -> None:
    threshold = 85.0
    source = "src/general_ludd"
    json_file: str | None = None
    json_out: str | None = None

    args = sys.argv[1:]

    for arg in args:
        if arg.startswith("--threshold="):
            threshold = float(arg.split("=", 1)[1])
        elif arg.startswith("--source="):
            source = arg.split("=", 1)[1]
        elif arg.startswith("--json-file="):
            json_file = arg.split("=", 1)[1]
        elif arg.startswith("--json-out="):
            json_out = arg.split("=", 1)[1]

    root = Path(__file__).parent.parent
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    logs_dir = root / ".gate-logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    if json_out is None:
        json_out = str(logs_dir / f"coverage-{ts}.json")

    if json_file is None:
        json_file = str(logs_dir / f"coverage-data-{ts}.json")

    shard_results: list[dict[str, object]] = []

    # Run pytest with coverage if no existing JSON supplied
    if not any(arg.startswith("--json-file=") for arg in sys.argv[1:]):
        pyrc = run_pytest_coverage(source, json_file, shard_results)
    else:
        pyrc = 0

    if not Path(json_file).exists():
        if pyrc != 0:
            failure_report = {
                "generated_at": datetime.now(UTC).isoformat(),
                "threshold": threshold,
                "branch_threshold": threshold,
                "per_file_threshold": 75.0,
                "source": source,
                "passed": False,
                "pytest_exit_code": pyrc,
                "shards": shard_results,
                "failed_shards": [
                    shard for shard in shard_results if shard["status"] != "passed"
                ],
                "error": "coverage JSON was not produced because an E2E shard failed",
            }
            with open(json_out, "w") as f:
                json.dump(failure_report, f, indent=2)
            print(f"Coverage audit failed — report: {json_out}", file=sys.stderr)
            print(
                f"  Failed shards: {len(failure_report['failed_shards'])}",
                file=sys.stderr,
            )
            sys.exit(pyrc if pyrc > 1 else 1)
        print(f"ERROR: coverage.json not found at {json_file}", file=sys.stderr)
        sys.exit(2)

    per_file_threshold = 75.0
    for arg in args:
        if arg.startswith("--per-file-threshold="):
            per_file_threshold = float(arg.split("=", 1)[1])

    report, files_under, all_ok = parse_coverage_json(
        json_file, threshold, source, per_file_threshold
    )

    report["pytest_exit_code"] = pyrc
    report["shards"] = shard_results
    report["failed_shards"] = [
        shard for shard in shard_results if shard["status"] != "passed"
    ]

    with open(json_out, "w") as f:
        json.dump(report, f, indent=2)

    print(f"Coverage audit complete — report: {json_out}")
    print(f"  Threshold: {threshold}%")
    print(f"  Files analyzed: {report['total_files']}")
    print(f"  Files below {threshold}%: {report['files_below_threshold']}")

    if pyrc != 0:
        print(f"Coverage test command failed with exit code {pyrc}", file=sys.stderr)
        sys.exit(pyrc if pyrc > 1 else 1)

    if files_under or not all_ok:
        print("\nFiles below threshold:")
        for f in sorted(files_under):
            pct = report["per_file"][f]
            print(f"  {pct:5.1f}%  {f}")
        sys.exit(1)

    print(f"\nAll {report['total_files']} files meet the {threshold}% threshold.")
    sys.exit(0)


if __name__ == "__main__":
    main()
