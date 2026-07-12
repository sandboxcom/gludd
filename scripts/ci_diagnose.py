#!/usr/bin/env python3
"""Auto-diagnose CI failures — group annotations by root cause, print 5-line summary."""
import json
import re
import subprocess
import sys
from collections import Counter

REPO = "sandboxcom/gludd"
FAIL_CONCLUSIONS = {"failure", "cancelled", "timed_out"}


def gh(*args: str) -> "dict | list | None":
    cmd = ["gh"] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout)
    except Exception:
        return None


def fetch_run(branch: str) -> "tuple[str, str, str] | None":
    """Return (databaseId, conclusion, headSha) for latest run on branch."""
    data = gh("run", "list", "--branch", branch, "--limit", "1",
              "--json", "databaseId,conclusion,headSha,status")
    if not data or not isinstance(data, list) or not data:
        return None
    r = data[0]
    return (str(r.get("databaseId", "")), r.get("conclusion", ""), r.get("headSha", ""))


def fetch_jobs(run_id: str) -> "list[dict] | None":
    data = gh("api", f"repos/{REPO}/actions/runs/{run_id}/jobs")
    if data is None:
        return None
    jobs = data.get("jobs")
    return jobs if isinstance(jobs, list) else None


def fetch_annotations(check_run_id: str) -> "list[dict] | None":
    data = gh("api", "--paginate",
              f"repos/{REPO}/check-runs/{check_run_id}/annotations")
    if not isinstance(data, list):
        return None
    return data


def extract_root_cause(msg: str) -> str:
    """Extract a short root-cause label from an annotation message."""
    msg = msg.strip()
    m = re.search(r"(?i)(assertionerror|importerror|module_not_found|syntaxerror|"
                  r"is not subscriptable|has no attribute '\w+'|"
                  r"timeout|killed|OSError\s+\[Errno\s+\d+\]|"
                  r"connection refused|ModuleNotFoundError|E\d{3,4}:\s+\w+)", msg)
    if m:
        return m.group(1)
    m = re.search(r"(?i)(error|failed|FAILED|Traceback)", msg)
    if m:
        return m.group(1).lower()
    return "unknown"


def group_annotations(annotations: "list[dict]") -> "list[tuple[str, int]]":
    """Group failure annotations by (file, root_cause)."""
    groups: Counter = Counter()
    for ann in annotations:
        if ann.get("annotation_level") not in {"failure", "error"}:
            continue
        path = ann.get("path", "?")
        msg = ann.get("message", "")
        cause = extract_root_cause(msg)
        key = f"{path} [{cause}]"
        groups[key] += 1
    return groups.most_common(5)


def main() -> None:
    branch = sys.argv[1] if len(sys.argv) > 1 else "master"

    run = fetch_run(branch)
    if run is None:
        print(f"CI DIAGNOSE: no runs found for branch '{branch}'")
        sys.exit(1)

    run_id, conclusion, head_sha = run
    print(f"CI DIAGNOSE: branch={branch} run={run_id} sha={head_sha[:8]} conclusion={conclusion}")

    if conclusion not in FAIL_CONCLUSIONS:
        print(f"CI DIAGNOSE: run {run_id} is {conclusion} — no failure annotations to diagnose.")
        if conclusion == "success":
            sys.exit(0)
        sys.exit(0)

    jobs = fetch_jobs(run_id)
    if not jobs:
        print("CI DIAGNOSE: could not fetch jobs")
        sys.exit(1)

    all_annotations: "list[dict]" = []
    for job in jobs:
        check_run_url = job.get("check_run_url", "")
        if not check_run_url:
            continue
        cr_id = check_run_url.rstrip("/").rsplit("/", 1)[-1]
        anns = fetch_annotations(cr_id)
        if anns:
            all_annotations.extend(anns)

    if not all_annotations:
        print("CI DIAGNOSE: no failure annotations found in any job")
        sys.exit(1)

    groups = group_annotations(all_annotations)

    total_failures = sum(c for _, c in groups)
    print(f"\nTOP FAILURES ({total_failures} total):")
    for i, (group, count) in enumerate(groups, 1):
        print(f"  {i}. {group} ({count} failures)")
    sys.exit(1)


if __name__ == "__main__":
    main()
