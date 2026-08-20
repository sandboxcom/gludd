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
from typing import cast
from xml.etree import ElementTree


def parse_coverage_json(
    json_path: str,
    threshold: float,
    source_path: str,
    per_file_threshold: float = 75.0,
) -> tuple[dict[str, object], list[str], bool]:
    """Parse a coverage.py JSON report and evaluate line and branch floors."""
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

        if pct < per_file_threshold or branch_pct < per_file_threshold:
            files_under.append(rel)
        else:
            files_ok.append(rel)

    aggregate_branch_pct = (
        round(100.0 * covered_branches / total_branches, 1)
        if total_branches
        else (round(100.0 * sum(per_file.values()) / len(per_file), 1) if per_file else 100.0)
    )
    aggregate_line_pct = round(100.0 * covered_statements / total_statements, 1) if total_statements else 100.0
    per_file_results = {
        rel: {
            "line_coverage": per_file[rel],
            "branch_coverage": per_file_branch[rel],
            "line_threshold": per_file_threshold,
            "branch_threshold": per_file_threshold,
            "passed": rel not in files_under,
        }
        for rel in sorted(per_file)
    }
    all_ok = (
        len(files_under) == 0
        and aggregate_line_pct >= threshold
        and aggregate_branch_pct >= threshold
    )

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
            "line": per_file_threshold,
            "branch": per_file_threshold,
        },
        "line_coverage": aggregate_line_pct,
        "branch_coverage": aggregate_branch_pct,
        "total_branches": total_branches,
        "covered_branches": covered_branches,
        "e2e_branch_coverage": aggregate_branch_pct,
        "e2e_branch_totals": {
            "scope": "tests/e2e",
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
            rel: sorted(
                finfo.get("contexts", {}),
                key=lambda k: (int(k) if isinstance(k, str) and k.isdigit() else 0, k),
            )
            for fpath, finfo in raw_files.items()
            if (rel := _relative_path(fpath, source_path)) in per_file and finfo.get("contexts")
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


COVERAGE_AUDIT_TIMEOUT_SECONDS = int(os.environ.get("GLUDD_COVERAGE_AUDIT_TIMEOUT_SECONDS", "1800"))

PROGRESS_SCHEMA_VERSION = 1


def _read_shard_diagnostics(junit_path: Path) -> list[dict[str, object]]:
    """Read deterministic per-test order and failure context from JUnit XML."""
    if not junit_path.exists():
        return []
    try:
        root = ElementTree.parse(junit_path).getroot()
    except (ElementTree.ParseError, OSError):
        return []

    diagnostics: list[dict[str, object]] = []
    for order, testcase in enumerate(root.iter("testcase"), start=1):
        classname = testcase.get("classname", "")
        name = testcase.get("name", "")
        nodeid = f"{classname}::{name}" if classname else name
        entry: dict[str, object] = {
            "order": order,
            "nodeid": nodeid,
            "status": "passed",
        }
        failure = next(
            (child for child in testcase if child.tag in {"failure", "error"}),
            None,
        )
        skipped = next((child for child in testcase if child.tag == "skipped"), None)
        if failure is not None:
            entry["status"] = "failed"
            entry["failure_context"] = {
                "message": failure.get("message", "")[:2000],
                "text": (failure.text or "").strip()[:4000],
            }
        elif skipped is not None:
            entry["status"] = "skipped"
            entry["skip_reason"] = skipped.get("message", "")
        diagnostics.append(entry)
    return diagnostics


def _progress_path(json_out_path: str) -> Path:
    """Return the durable sidecar path for an in-flight audit.

    The sidecar intentionally differs from the aggregate report: aggregate
    coverage is written only after every E2E file passes, while this file is
    updated after each state transition so an interrupted run is observable.
    """
    return Path(f"{json_out_path}.progress.json")


def _write_progress(path: Path, payload: dict[str, object]) -> None:
    """Atomically publish a progress snapshot without exposing half-written JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True))
    os.replace(temporary, path)


def _progress_snapshot(
    *,
    run_id: str,
    started_at: str,
    files: list[str],
    states: list[dict[str, object]],
    current_index: int,
    status: str,
    complete: bool,
    environment_namespace: str = "unknown",
    error: str | None = None,
) -> dict[str, object]:
    counts = {
        "attempted": sum(entry.get("status") in {"running", "passed", "failed", "timed_out"} for entry in states),
        "passed": sum(entry.get("status") == "passed" for entry in states),
        "failed": sum(entry.get("status") in {"failed", "timed_out"} for entry in states),
        "skipped": sum(entry.get("status") == "skipped" for entry in states),
    }
    payload: dict[str, object] = {
        "schema_version": PROGRESS_SCHEMA_VERSION,
        "kind": "coverage_audit_progress",
        "run_id": run_id,
        "pid": os.getpid(),
        "started_at": started_at,
        "updated_at": datetime.now(UTC).isoformat(),
        "status": status,
        "complete": complete,
        "environment_namespace": environment_namespace,
        "current_index": current_index,
        "total": len(files),
        "counts": counts,
        "files": states,
    }
    if error:
        payload["error"] = error
    return payload


def _publish_progress(
    path: Path,
    *,
    run_id: str,
    started_at: str,
    files: list[str],
    states: list[dict[str, object]],
    current_index: int,
    status: str,
    complete: bool,
    environment_namespace: str = "unknown",
    error: str | None = None,
) -> None:
    _write_progress(
        path,
        _progress_snapshot(
            run_id=run_id,
            started_at=started_at,
            files=files,
            states=states,
            current_index=current_index,
            status=status,
            complete=complete,
            environment_namespace=environment_namespace,
            error=error,
        ),
    )


def _coverage_environment() -> dict[str, str]:
    """Build the child environment for a certified E2E coverage shard.

    The E2E readiness contract is intentionally independent of the caller's
    shell.  In particular, a developer may invoke ``make audit-coverage``
    without exporting ``GLUDD_E2E_ACTIVE``; every shard must still exercise
    the E2E startup/readiness branches.
    """
    env = os.environ.copy()
    env.update(
        {
            "GLUDD_COVERAGE_AUDIT": "1",
            "GLUDD_E2E_ACTIVE": "1",
        }
    )
    return env


def run_pytest_coverage(
    source: str,
    json_out_path: str,
    shard_results: list[dict[str, object]] | None = None,
    progress_json_path: str | None = None,
) -> int:
    """Run E2E files and publish durable progress before aggregate coverage."""
    env = _coverage_environment()
    root = Path(__file__).parent.parent
    files = sorted((root / "tests/e2e").rglob("test_*.py"))
    if not files:
        print("ERROR: no E2E test files found", file=sys.stderr)
        return 2
    progress_path = Path(progress_json_path) if progress_json_path else _progress_path(json_out_path)
    relative_files = [str(path.relative_to(root)) for path in files]
    environment_namespace = (
        env.get("GLUDD_PROJECT_NAMESPACE", "").strip() or env.get("GLUDD_RESOURCE_NAMESPACE", "").strip() or "unscoped"
    )
    run_id = f"{os.getpid()}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}"
    started_at = datetime.now(UTC).isoformat()
    states: list[dict[str, object]] = [
        {
            "path": path,
            "status": "pending",
            "environment_namespace": environment_namespace,
        }
        for path in relative_files
    ]
    _publish_progress(
        progress_path,
        run_id=run_id,
        started_at=started_at,
        files=relative_files,
        states=states,
        current_index=0,
        status="running",
        complete=False,
        environment_namespace=environment_namespace,
    )
    coverage_file = root / f".coverage.audit.{os.getpid()}"
    env["COVERAGE_FILE"] = str(coverage_file)
    coverage_file.unlink(missing_ok=True)
    try:
        results = shard_results if shard_results is not None else []
        for index, test_file in enumerate(files):
            states[index] = {
                "path": relative_files[index],
                "status": "running",
                "environment_namespace": environment_namespace,
                "started_at": datetime.now(UTC).isoformat(),
            }
            _publish_progress(
                progress_path,
                run_id=run_id,
                started_at=started_at,
                files=relative_files,
                states=states,
                current_index=index,
                status="running",
                complete=False,
                environment_namespace=environment_namespace,
            )
            basetemp = Path(f"/tmp/gludd-audit-e2e-{os.getpid()}-{index}")
            junit_path = basetemp / "junit.xml"
            args = [
                sys.executable,
                "-m",
                "pytest",
                str(test_file),
                f"--cov={source}",
                "--cov-branch",
                "--cov-context=test",
                "--cov-append",
                "--cov-fail-under=0",
                "--cov-report=",
                "-n",
                "2",
                "--dist",
                "loadgroup",
                "-q",
                f"--junitxml={junit_path}",
                f"--basetemp={basetemp}",
            ]
            try:
                result = subprocess.run(
                    args,
                    cwd=root,
                    env=env,
                    timeout=COVERAGE_AUDIT_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                states[index] = {
                    "path": relative_files[index],
                    "status": "timed_out",
                    "environment_namespace": environment_namespace,
                    "returncode": 124,
                    "finished_at": datetime.now(UTC).isoformat(),
                }
                for remaining in range(index + 1, len(states)):
                    states[remaining] = {
                        "path": relative_files[remaining],
                        "status": "skipped",
                        "environment_namespace": environment_namespace,
                        "reason": "stopped_after_failure",
                    }
                _publish_progress(
                    progress_path,
                    run_id=run_id,
                    started_at=started_at,
                    files=relative_files,
                    states=states,
                    current_index=index + 1,
                    status="failed",
                    complete=False,
                    environment_namespace=environment_namespace,
                    error="pytest shard timed out",
                )
                results.append(
                    {
                        "path": str(test_file.relative_to(root)),
                        "status": "timed_out",
                        "returncode": 124,
                    }
                )
                print(
                    f"ERROR: coverage pytest timed out for {test_file}",
                    file=sys.stderr,
                )
                return 124
            shard: dict[str, object] = {
                "path": str(test_file.relative_to(root)),
                "status": "passed" if result.returncode == 0 else "failed",
                "returncode": result.returncode,
            }
            results.append(shard)
            states[index] = {
                **shard,
                "environment_namespace": environment_namespace,
                "tests": _read_shard_diagnostics(basetemp / "junit.xml"),
                "finished_at": datetime.now(UTC).isoformat(),
            }
            if result.returncode != 0:
                for remaining in range(index + 1, len(states)):
                    states[remaining] = {
                        "path": relative_files[remaining],
                        "status": "skipped",
                        "environment_namespace": environment_namespace,
                        "reason": "stopped_after_failure",
                    }
                _publish_progress(
                    progress_path,
                    run_id=run_id,
                    started_at=started_at,
                    files=relative_files,
                    states=states,
                    current_index=index + 1,
                    status="failed",
                    complete=False,
                    environment_namespace=environment_namespace,
                    error=f"pytest shard exited with {result.returncode}",
                )
                print(f"E2E coverage file failed: {test_file}", file=sys.stderr)
                return result.returncode
            _publish_progress(
                progress_path,
                run_id=run_id,
                started_at=started_at,
                files=relative_files,
                states=states,
                current_index=index + 1,
                status="running",
                complete=False,
                environment_namespace=environment_namespace,
            )
        report = subprocess.run(
            [sys.executable, "-m", "coverage", "json", "--show-contexts", "-o", json_out_path],
            cwd=root,
            env=env,
            timeout=COVERAGE_AUDIT_TIMEOUT_SECONDS,
        )
        if report.returncode == 0:
            _publish_progress(
                progress_path,
                run_id=run_id,
                started_at=started_at,
                files=relative_files,
                states=states,
                current_index=len(files),
                status="completed",
                complete=True,
                environment_namespace=environment_namespace,
            )
        else:
            _publish_progress(
                progress_path,
                run_id=run_id,
                started_at=started_at,
                files=relative_files,
                states=states,
                current_index=len(files),
                status="failed",
                complete=False,
                environment_namespace=environment_namespace,
                error=f"coverage json exited with {report.returncode}",
            )
        return report.returncode
    except subprocess.TimeoutExpired:
        results = shard_results if shard_results is not None else []
        results.append(
            {
                "path": "<coverage-json>",
                "status": "timed_out",
                "returncode": 124,
            }
        )
        for entry in states:
            if entry.get("status") in {"pending", "running"}:
                entry.update(
                    {
                        "status": "skipped",
                        "reason": "stopped_after_failure",
                        "environment_namespace": environment_namespace,
                    }
                )
        _publish_progress(
            progress_path,
            run_id=run_id,
            started_at=started_at,
            files=relative_files,
            states=states,
            current_index=sum(entry.get("status") != "skipped" for entry in states),
            status="failed",
            complete=False,
            environment_namespace=environment_namespace,
            error="coverage command timed out",
        )
        print(
            f"ERROR: coverage pytest timed out after {COVERAGE_AUDIT_TIMEOUT_SECONDS}s",
            file=sys.stderr,
        )
        return 124


def main() -> None:
    """Run the command-line coverage audit and write its durable report."""
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
            failed_shards = [shard for shard in shard_results if shard["status"] != "passed"]
            failure_report = {
                "generated_at": datetime.now(UTC).isoformat(),
                "threshold": threshold,
                "branch_threshold": threshold,
                "per_file_threshold": 75.0,
                "source": source,
                "passed": False,
                "pytest_exit_code": pyrc,
                "shards": shard_results,
                "failed_shards": failed_shards,
                "error": "coverage JSON was not produced by the audit command",
            }
            with open(json_out, "w") as output_file:
                json.dump(failure_report, output_file, indent=2)
            print(f"Coverage audit failed — report: {json_out}", file=sys.stderr)
            print(
                f"  Failed shards: {len(failed_shards)}",
                file=sys.stderr,
            )
            sys.exit(pyrc if pyrc > 1 else 1)
        print(f"ERROR: coverage.json not found at {json_file}", file=sys.stderr)
        sys.exit(2)

    per_file_threshold = 75.0
    for arg in args:
        if arg.startswith("--per-file-threshold="):
            per_file_threshold = float(arg.split("=", 1)[1])

    report, files_under, all_ok = parse_coverage_json(json_file, threshold, source, per_file_threshold)

    report["pytest_exit_code"] = pyrc
    report["shards"] = shard_results
    report["failed_shards"] = [shard for shard in shard_results if shard["status"] != "passed"]

    with open(json_out, "w") as output_file:
        json.dump(report, output_file, indent=2)

    print(f"Coverage audit complete — report: {json_out}")
    print(f"  Aggregate threshold: {threshold}%")
    print(f"  Per-file threshold: {per_file_threshold}%")
    print(f"  Files analyzed: {report['total_files']}")
    print(f"  Files below {per_file_threshold}%: {report['files_below_threshold']}")

    if pyrc != 0:
        print(f"Coverage test command failed with exit code {pyrc}", file=sys.stderr)
        sys.exit(pyrc if pyrc > 1 else 1)

    if files_under or not all_ok:
        if files_under:
            print("\nFiles below per-file threshold:")
        for file_path in sorted(files_under):
            per_file = cast(dict[str, float], report["per_file"])
            per_file_branch = cast(dict[str, float], report["per_file_branch"])
            line_pct = per_file[file_path]
            branch_pct = per_file_branch[file_path]
            reasons = []
            if line_pct < per_file_threshold:
                reasons.append(f"line<{per_file_threshold:.1f}%")
            if branch_pct < per_file_threshold:
                reasons.append(f"branch<{per_file_threshold:.1f}%")
            print(
                f"  line={line_pct:.1f}% branch={branch_pct:.1f}% "
                f"({', '.join(reasons)})  {file_path}"
            )
        aggregate_line = cast(float, report["line_coverage"])
        aggregate_branch = cast(float, report["branch_coverage"])
        if aggregate_line < threshold or aggregate_branch < threshold:
            print("\nAggregate coverage below threshold:")
            print(
                f"  line={aggregate_line:.1f}% branch={aggregate_branch:.1f}% "
                f"required={threshold:.1f}%"
            )
        sys.exit(1)

    print(
        f"\nAll {report['total_files']} files meet the {per_file_threshold}% per-file threshold; "
        f"aggregate coverage meets {threshold}%."
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
