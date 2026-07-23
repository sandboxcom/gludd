#!/usr/bin/env python3
"""Automated git bisect for regression finding.

Usage: make git-bisect GOOD=<sha> BAD=<sha> TEST=<test-command> [TIMEOUT=<seconds>]

The script:
  1. Validates GOOD/BAD are reachable commits
  2. Aborts any stale bisect state
  3. Runs `git bisect start BAD GOOD`
  4. Runs `git bisect run <test_command>` with per-step timeout
  5. Reports the first bad commit
  6. Cleans up bisect state on any exit path

AGENTS.md constraint: the test command MUST be fast (<60s per invocation is
ideal; TIMEOUT > 300s triggers a loud warning before starting).
"""
import argparse
import os
import signal
import subprocess
import sys
import time

_MAX_IDEAL_TIMEOUT = 60
_MAX_WARN_TIMEOUT = 300


def _run(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    """Run a command, returning the CompletedProcess.  On timeout, log and re-raise."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        print(f"TIMEOUT: {e.cmd} exceeded {timeout}s", file=sys.stderr)
        raise


def _die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    _cleanup_bisect()
    sys.exit(code)


def _cleanup_bisect() -> None:
    """Best-effort abort any in-progress bisect.  Never fails."""
    try:
        r = subprocess.run(
            ["git", "bisect", "reset"], capture_output=True, text=True, timeout=10
        )
        if r.returncode != 0 and "not bisecting" not in (r.stderr + r.stdout):
            print(f"  [warn] bisect reset: {r.stderr.strip()}", file=sys.stderr)
    except Exception:
        pass


def _commit_exists(ref: str) -> bool:
    r = subprocess.run(
        ["git", "cat-file", "-t", ref], capture_output=True, text=True, timeout=5
    )
    return r.returncode == 0 and r.stdout.strip() == "commit"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Automated git bisect runner")
    p.add_argument("--good", required=True, help="Good (working) commit SHA")
    p.add_argument("--bad", required=True, help="Bad (broken) commit SHA")
    p.add_argument("--test", required=True, help="Test command (e.g. 'make test TESTFILE=...')")
    p.add_argument("--timeout", type=int, default=60, help="Per-step timeout in seconds (default 60)")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    good, bad, test_cmd, timeout = args.good, args.bad, args.test, args.timeout

    # ── Validate commits ────────────────────────────────────────────────
    if not _commit_exists(good):
        _die(f"GOOD commit '{good}' does not exist or is not a commit")
    if not _commit_exists(bad):
        _die(f"BAD commit '{bad}' does not exist or is not a commit")

    print(f"GOOD:  {good}")
    print(f"BAD:   {bad}")
    print(f"TEST:  {test_cmd}")

    # ── Timeout warning ─────────────────────────────────────────────────
    if timeout > _MAX_WARN_TIMEOUT:
        print(
            f"\nWARNING: TIMEOUT={timeout}s exceeds {_MAX_WARN_TIMEOUT}s maximum recommended.\n"
            f"  Bisect may test many commits; a slow test makes bisect impractical.\n"
            f"  AGENTS.md requires tests be fast. Consider narrowing the test scope.\n",
            file=sys.stderr,
        )
    elif timeout > _MAX_IDEAL_TIMEOUT:
        print(f"  (TIMEOUT={timeout}s > {_MAX_IDEAL_TIMEOUT}s ideal; bisect may be slow)\n")

    # ── Clean stale bisect state ────────────────────────────────────────
    _cleanup_bisect()

    # ── Start bisect ────────────────────────────────────────────────────
    print("\n--- Starting git bisect ---")
    r = _run(["git", "bisect", "start", bad, good], timeout=10)
    if r.returncode != 0:
        _die(f"git bisect start failed:\n{r.stderr}")

    # ── Run bisect ──────────────────────────────────────────────────────
    print(f"--- Running bisect (timeout={timeout}s per step) ---")
    start_ts = time.time()
    try:
        r = _run(["git", "bisect", "run", "sh", "-c", test_cmd], timeout=timeout * 100)
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start_ts
        print(f"\n  Bisect timed out after {elapsed:.0f}s (per-step timeout={timeout}s × many steps)",
              file=sys.stderr)
        _cleanup_bisect()
        sys.exit(1)

    elapsed = time.time() - start_ts
    print(f"\n--- Bisect finished ({elapsed:.0f}s) ---")

    # ── Report ──────────────────────────────────────────────────────────
    if r.returncode == 0:
        # bisect run exits 0 on first bad found
        print(f"\n{r.stdout}")
        if "is the first bad commit" in r.stdout:
            for line in r.stdout.splitlines():
                if "is the first bad commit" in line:
                    print(f"\n=== FIRST BAD COMMIT: {line.strip()} ===")
                    break
    else:
        print(f"Bisect returned exit={r.returncode}", file=sys.stderr)
        print(r.stdout)
        print(r.stderr, file=sys.stderr)

    # ── Cleanup ─────────────────────────────────────────────────────────
    _cleanup_bisect()
    print("Bisect state cleaned up.")
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
