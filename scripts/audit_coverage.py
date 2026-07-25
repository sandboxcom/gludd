#!/usr/bin/env python3
"""Coverage audit: run pytest with coverage, parse results, flag files below threshold.

Usage:
  python3 scripts/audit_coverage.py [--threshold=85] [--source=src/general_ludd]
  [--json-out=.gate-logs/coverage-<ts>.json]
  python3 scripts/audit_coverage.py --json-file=coverage.json --threshold=85

Modes:
  - Default mode: runs pytest with --cov and parses the JSON report it produces.
  - --json-file mode: parses an existing coverage.json file (no pytest run).
"""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def parse_coverage_json(json_path: str, threshold: float, source_path: str) -> tuple[dict, list[str], bool]:
    with open(json_path) as f:
        raw = f.read()
    data = json.loads(raw)

    # --- AgentConfig search in raw coverage.json ---
    import re as _re
    _matches = list(_re.finditer(r'\bAgentConfig\b', raw))
    print(f"\n=== AgentConfig search in {json_path} ===")
    print(f"  File size: {len(raw)} bytes")
    print(f"  Whole-word \\bAgentConfig\\b matches: {len(_matches)}")
    for _i, _m in enumerate(_matches):
        _s = max(0, _m.start() - 80)
        _e = min(len(raw), _m.end() + 80)
        print(f"  Match {_i+1} at char {_m.start()}: ...{raw[_s:_e]}...")
    if not _matches:
        print("  (no whole-word matches found)")
    _subs = len(_re.findall(r'AgentConfig', raw))
    print(f"  Any substring 'AgentConfig' occurrences: {_subs}")
    print("=== End AgentConfig search ===\n")

    files_under: list[str] = []
    files_ok: list[str] = []
    per_file: dict[str, float] = {}

    raw_files = data.get("files", {})
    for fpath, finfo in raw_files.items():
        summary = finfo.get("summary", {})
        num_stmts = summary.get("num_statements", 0)
        covered = summary.get("covered_lines", summary.get("num_lines_covered", 0))

        if num_stmts == 0:
            continue

        pct = round(100.0 * covered / num_stmts, 1)

        rel = _relative_path(fpath, source_path)
        per_file[rel] = pct

        if pct < threshold:
            files_under.append(rel)
        else:
            files_ok.append(rel)

    all_ok = len(files_under) == 0

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "threshold": threshold,
        "source": source_path,
        "total_files": len(per_file),
        "files_below_threshold": len(files_under),
        "passed": all_ok,
        "files_under_threshold": files_under,
        "files_above_threshold": len(files_ok),
        "per_file": dict(sorted(per_file.items())),
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


def run_pytest_coverage(source: str, json_out_path: str) -> int:
    """Run one bounded pytest process and write only this audit's coverage data."""
    env = os.environ.copy()
    env["GLUDD_COVERAGE_AUDIT"] = "1"
    try:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/",
                f"--cov={source}",
                f"--cov-report=json:{json_out_path}",
                "--cov-report=term-missing",
                "-q",
            ],
            cwd=Path(__file__).parent.parent,
            env=env,
            timeout=COVERAGE_AUDIT_TIMEOUT_SECONDS,
        ).returncode
    except subprocess.TimeoutExpired:
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

    # Run pytest with coverage if no existing JSON supplied
    if not any(arg.startswith("--json-file=") for arg in sys.argv[1:]):
        pyrc = run_pytest_coverage(source, json_file)
    else:
        pyrc = 0

    if not Path(json_file).exists():
        print(f"ERROR: coverage.json not found at {json_file}", file=sys.stderr)
        sys.exit(2)

    report, files_under, _all_ok = parse_coverage_json(json_file, threshold, source)

    report["pytest_exit_code"] = pyrc

    with open(json_out, "w") as f:
        json.dump(report, f, indent=2)

    print(f"Coverage audit complete — report: {json_out}")
    print(f"  Threshold: {threshold}%")
    print(f"  Files analyzed: {report['total_files']}")
    print(f"  Files below {threshold}%: {report['files_below_threshold']}")

    if files_under:
        print("\nFiles below threshold:")
        for f in sorted(files_under):
            pct = report["per_file"][f]
            print(f"  {pct:5.1f}%  {f}")
        sys.exit(1)

    print(f"\nAll {report['total_files']} files meet the {threshold}% threshold.")
    sys.exit(0)


if __name__ == "__main__":
    main()
