#!/usr/bin/env python3
"""Generate coverage JSON from existing .coverage.* data files and parse results."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
GATE_LOGS = ROOT / ".gate-logs"


def find_latest_coverage_data() -> Path | None:
    """Find the most recent combined .coverage.audit.* file (not per-worker)."""
    candidates = sorted(
        ROOT.glob(".coverage.audit.*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for c in candidates:
        if c.is_file() and c.stat().st_size > 0:
            return c
    return None


def main():
    data_file = find_latest_coverage_data()
    if data_file is None:
        print("ERROR: no coverage data file found", file=sys.stderr)
        sys.exit(1)

    print(f"Using coverage data: {data_file} ({data_file.stat().st_size} bytes)")

    json_out = GATE_LOGS / "coverage-branch.json"
    GATE_LOGS.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["COVERAGE_FILE"] = str(data_file.absolute())

    result = subprocess.run(
        [sys.executable, "-m", "coverage", "json", "--show-contexts", "--fail-under=0", "-o", str(json_out)],
        cwd=ROOT,
        env=env,
        timeout=300,
    )
    if result.returncode != 0:
        print(f"ERROR: coverage json failed with exit {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)

    with open(json_out) as f:
        data = json.load(f)

    files_info = data.get("files", {})
    total_branches = 0
    covered_branches = 0
    total_statements = 0
    covered_statements = 0
    per_file_branch = {}

    for fpath, finfo in sorted(files_info.items()):
        summary = finfo.get("summary", {})
        ns = int(summary.get("num_statements", 0) or 0)
        cl = int(summary.get("covered_lines", 0) or 0)
        nb = int(summary.get("num_branches", 0) or 0)
        cb = int(summary.get("covered_branches", 0) or 0)
        total_statements += ns
        covered_statements += cl
        total_branches += nb
        covered_branches += cb
        if nb > 0:
            pct = 100.0 * cb / nb
        elif ns > 0:
            pct = 100.0 * cl / ns
        else:
            continue
        rel = fpath.replace(str(ROOT) + "/", "").replace("src/", "")
        per_file_branch[rel] = round(pct, 1)

    agg_branch = round(100.0 * covered_branches / total_branches, 1) if total_branches > 0 else 0.0
    agg_line = round(100.0 * covered_statements / total_statements, 1) if total_statements > 0 else 0.0

    print(f"\n{'=' * 60}")
    print(f"Aggregate line coverage:   {agg_line}%")
    print(f"Aggregate branch coverage: {agg_branch}%")
    print(f"Total branches: {total_branches}, Covered: {covered_branches}")
    print(f"Files analyzed: {len(per_file_branch)}")
    print(f"{'=' * 60}")

    sorted_files = sorted(per_file_branch.items(), key=lambda x: x[1])
    below_75 = [(f, p) for f, p in sorted_files if p < 75.0]

    print(f"\nBottom 20 files by branch coverage:")
    for fpath, pct in sorted_files[:20]:
        flag = " ***BELOW 75%" if pct < 75.0 else ""
        print(f"  {pct:5.1f}%  {fpath}{flag}")

    if below_75:
        print(f"\nFiles below 75% branch threshold: {len(below_75)}")
    else:
        print(f"\nAll files at or above 75% branch threshold")

    if agg_branch < 85.0:
        print(f"\nAGGREGATE BRANCH COVERAGE {agg_branch}% IS BELOW 85% TARGET")
        print(f"Shortfall: {85.0 - agg_branch:.1f} percentage points")

    report = {
        "aggregate_line_coverage": agg_line,
        "aggregate_branch_coverage": agg_branch,
        "total_branches": total_branches,
        "covered_branches": covered_branches,
        "per_file_branch": dict(sorted_files),
        "below_75": dict(below_75),
    }
    report_path = GATE_LOGS / "branch-coverage-report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport saved to: {report_path}")

    sys.exit(0 if agg_branch >= 85.0 else 1)


if __name__ == "__main__":
    main()
