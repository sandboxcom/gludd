#!/usr/bin/env python3
"""Parse coverage-branch.json and report branch coverage stats."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
JSON_PATH = ROOT / ".gate-logs" / "coverage-branch.json"

if not JSON_PATH.exists():
    print(f"ERROR: {JSON_PATH} not found", file=sys.stderr)
    sys.exit(1)

with open(JSON_PATH) as f:
    raw = f.read()
data = json.loads(raw)

files_info = data.get("files", {})
total_branches = 0
covered_branches = 0
total_statements = 0
covered_statements = 0
per_file = {}

for fpath, finfo in sorted(files_info.items()):
    summary = finfo.get("summary", {})
    ns = int(summary.get("num_statements", 0) or 0)
    cl = int(summary.get("covered_lines", 0) or int(summary.get("num_lines_covered", 0) or 0))
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
    per_file[rel] = {
        "branch_pct": round(pct, 1),
        "line_pct": round(100.0 * cl / ns, 1) if ns > 0 else 100.0,
        "statements": ns,
        "branches": nb,
        "covered_branches": cb,
    }

agg_branch = round(100.0 * covered_branches / total_branches, 1) if total_branches > 0 else 0.0
agg_line = round(100.0 * covered_statements / total_statements, 1) if total_statements > 0 else 0.0

print(f"Aggregate line coverage:   {agg_line}% ({covered_statements}/{total_statements})")
print(f"Aggregate branch coverage: {agg_branch}% ({covered_branches}/{total_branches})")
print(f"Files with branch data: {len(per_file)}")
print()

sorted_by_branch = sorted(per_file.items(), key=lambda x: x[1]["branch_pct"])
below_75 = [(f, d) for f, d in sorted_by_branch if d["branch_pct"] < 75.0]

print("Bottom 30 files by branch coverage:")
for fpath, d in sorted_by_branch[:30]:
    flag = " *** <75%" if d["branch_pct"] < 75.0 else ""
    print(f"  {d['branch_pct']:5.1f}%  (stmt:{d['statements']:4d} br:{d['branches']:4d})  {fpath}{flag}")

if below_75:
    print(f"\nFiles below 75% branch threshold: {len(below_75)}")
    for fpath, d in below_75:
        print(f"  {d['branch_pct']:5.1f}%  (stmt:{d['statements']:4d} br:{d['branches']:4d})  {fpath}")
else:
    print(f"\nAll files at or above 75% branch threshold")

if agg_branch < 85.0:
    print(f"\nAGGREGATE BRANCH COVERAGE {agg_branch}% IS BELOW 85% TARGET")
    print(f"Shortfall: {85.0 - agg_branch:.1f} pp")
    print(f"Need ~{int(total_branches * 0.85) - covered_branches} more branches covered")
