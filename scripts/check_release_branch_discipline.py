#!/usr/bin/env python3
"""check_release_branch_discipline.py — AC002: release-branch-discipline.

Verifies release branch promotion preconditions:
(a) current branch is a release branch,
(b) remote tip CI is green,
(c) local HEAD matches remote tip,
(d) no uncommitted changes.
"""

import os
import subprocess
import sys


def run_git(args):
    result = subprocess.run(["git"] + args, capture_output=True, text=True)
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def get_current_branch():
    out, _, rc = run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    return out if rc == 0 else None


def check_uncommitted():
    out, _, rc = run_git(["status", "--porcelain"])
    if rc != 0:
        print("AC002: git status failed")
        sys.exit(2)
    if out.strip():
        print("AC002: BLOCKED — uncommitted changes in working tree")
        sys.exit(1)


def check_release_branch(branch):
    if not branch or not branch.startswith("release/"):
        print(f"AC002: SKIP — '{branch}' is not a release branch")
        sys.exit(0)


def check_ci_green(branch):
    try:
        result = subprocess.run(
            ["make", "ci-verdict", f"BRANCH={branch}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        print("AC002: INCONCLUSIVE — CI verdict check timed out")
        sys.exit(2)

    if result.returncode != 0:
        print(f"AC002: BLOCKED — CI not green on {branch}")
        print(result.stdout[-300:] if result.stdout else "No CI verdict output")
        sys.exit(1)

    if "conclusion: success" not in result.stdout:
        print(f"AC002: BLOCKED — CI conclusion is not success on {branch}")
        sys.exit(1)

    print(f"AC002: PASS — CI green on {branch}")


def check_remote_match():
    local, _, _ = run_git(["rev-parse", "HEAD"])
    out, _, rc = run_git(["ls-remote", "sandboxcom", "HEAD"])
    if rc != 0:
        print("AC002: INCONCLUSIVE — cannot fetch remote")
        sys.exit(2)
    remote_sha = out.split()[0] if out else ""
    if local != remote_sha:
        print(f"AC002: BLOCKED — local HEAD {local[:8]} != remote tip {remote_sha[:8]}")
        sys.exit(1)
    print("AC002: PASS — local HEAD matches remote tip")


def main():
    branch = get_current_branch()
    if not branch:
        print("AC002: ERROR — cannot determine current branch")
        sys.exit(2)

    check_release_branch(branch)

    force = os.environ.get("FORCE") == "1"
    if force:
        print(f"AC002: FORCE enabled — skipping checks on {branch}")
        sys.exit(0)

    check_uncommitted()
    check_remote_match()
    check_ci_green(branch)

    print(f"AC002: ALL PASS — {branch} is ready for promotion")
    sys.exit(0)


if __name__ == "__main__":
    main()
