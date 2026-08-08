"""E2E spawner test: launch opencode against a temp copy of _test_project/, verify
multitask dispatch behavior over a configurable duration (default 3600s = 1 hour).

Verdict logic:
  PASS = (killed by timeout AND dispatched >0) OR (completed all tasks with >=5 dispatch waves)
  FAIL = <5 dispatch waves AND not killed by timeout, or depth never reaches 2+
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_PROJECT_SRC = ROOT / "tests" / "opencode_e2e" / "_test_project"
OPENCODE_BIN = "opencode"

PROMPT_SEQUENCE = [
    (
        "Read TASKS.md. There are 18 tasks. You MUST dispatch EXACTLY 10 task subagents "
        "in EACH wave. When subagent results arrive, immediately dispatch the NEXT wave "
        "of 10 task subagents for remaining unchecked tasks. Repeat until ALL 18 tasks "
        "are completed. NEVER send a text-only answer while tasks remain — always include "
        "task dispatches. Each subagent runs ONE make taskN command. When ALL 18 tasks "
        "show [x] in TASKS.md, say ALL DONE and exit."
    ),
    "Keep going. Read TASKS.md now. Any unchecked tasks remain — dispatch exactly 10 more task subagents.",
    "Still working. Check TASKS.md. Dispatch 10 task subagents for any remaining unchecked tasks.",
    (
        "Continue. Read TASKS.md. If any tasks unchecked, dispatch exactly 10 "
        "task subagents. Say ALL DONE only when all 18 are [x]."
    ),
]


def _reset_tasks_file(tasks_path: Path) -> None:
    content = tasks_path.read_text()
    content = re.sub(r"- \[x\] ", "- [ ] ", content)
    tasks_path.write_text(content)


def _make_temp_project() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="opencode-e2e-", dir="/tmp"))
    print(f"Copying test project to {tmp} ...")
    shutil.copytree(TEST_PROJECT_SRC, tmp, dirs_exist_ok=True, symlinks=True)
    print(f"Running setup.sh --copy {ROOT} {tmp} ...")
    rc = subprocess.run(
        ["bash", "setup.sh", "--copy", str(ROOT), str(tmp)],
        cwd=str(tmp),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if rc.returncode != 0:
        print(f"WARN: setup.sh returned {rc.returncode}\n{rc.stderr}")
    else:
        print(f"Setup: {rc.stdout.strip()[-200:]}")
    return tmp


def _cleanup_temp_project(tmp: Path) -> None:
    print(f"Cleaning up temp project {tmp} ...")
    shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="E2E spawner test for opencode")
    parser.add_argument("--timeout", type=int, default=60, help="Timeout in seconds (default: 60)")
    parser.add_argument(
        "--prompt-interval",
        type=int,
        default=300,
        help="Seconds between follow-up prompts (default: 300)",
    )
    parser.add_argument(
        "--progress-interval",
        type=int,
        default=30,
        help="Seconds between progress log writes (default: 30)",
    )
    parser.add_argument(
        "--no-temp",
        action="store_true",
        help="Run against the original _test_project/ (skip temp copy)",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Keep temp dir after test (for debugging)",
    )
    args = parser.parse_args()

    # 1. Check binary
    try:
        subprocess.run(["which", OPENCODE_BIN], capture_output=True, text=True, check=True)
    except Exception:
        print(f"ERROR: {OPENCODE_BIN} binary not found on PATH")
        return 1

    # Reduce prompt sequence for short runs
    effective_timeout = args.timeout

    # 2. Set up project dir
    if args.no_temp:
        project_dir = TEST_PROJECT_SRC
        _cleanup = False
        print(f"Using source test project: {project_dir}")
    else:
        project_dir = _make_temp_project()
        _cleanup = True

    try:
        sys.path.insert(0, str(ROOT / "tests"))
        from opencode_e2e._spawner import OpencodeSpawner, SpawnResult

        prompt_text = PROMPT_SEQUENCE[0] if PROMPT_SEQUENCE else "Read TASKS.md."
        tasks_path = project_dir / "TASKS.md"

        overall_start = time.time()
        run_index = 0
        total_dispatches = 0
        total_waves = 0
        total_nz_waves = 0
        total_msgs = 0
        total_tools = 0
        max_depth = 0
        all_violations: list[dict] = []
        all_stops: list[dict] = []

        while time.time() - overall_start < effective_timeout:
            run_index += 1
            _reset_tasks_file(tasks_path)

            remaining = effective_timeout - int(time.time() - overall_start)
            if remaining <= 10:
                break
            run_timeout = min(remaining, 300)

            spawner = OpencodeSpawner(
                project_dir=str(project_dir),
                prompt=prompt_text,
                timeout_sec=run_timeout,
                prompt_sequence=[],
                prompt_interval_sec=60,
                progress_interval_sec=args.progress_interval,
            )

            print(f"\n--- Run {run_index}: opencode for {run_timeout}s ---")
            result: SpawnResult = spawner.run()

            total_dispatches += result.total_dispatch_calls
            total_waves += len(result.dispatch_waves)
            total_nz_waves += sum(
                1 for w in result.dispatch_waves if isinstance(w["dispatch_count"], int) and w["dispatch_count"] > 0
            )
            total_msgs += result.total_messages
            total_tools += result.total_tool_calls
            max_depth = max(max_depth, result.depth_count)
            all_violations.extend(result.per_wave_violations)
            all_stops.extend(result.text_only_stops)

            print(
                f"  Run {run_index}: verdict={result.verdict}, "
                f"dispatches={result.total_dispatch_calls}, "
                f"elapsed={result.elapsed_sec:.0f}s, killed={result.killed}"
            )

            if not result.killed and result.total_dispatch_calls == 0 and run_index < 2:
                print(f"  WARNING: Run {run_index} stopped with 0 dispatches before timeout")
                break

        elapsed_total = time.time() - overall_start

        # Accumulated verdict
        if total_dispatches == 0:
            verdict = "FAIL"
            reason = "0 dispatches across all runs"
        elif total_nz_waves < run_index:
            verdict = "PASS"
            reason = (
                f"{run_index} run(s) in {elapsed_total:.0f}s, "
                f"{total_dispatches} dispatches, {total_nz_waves} non-zero waves"
            )
        else:
            verdict = "PASS"
            reason = (
                f"{run_index} run(s) in {elapsed_total:.0f}s, "
                f"{total_dispatches} dispatches, {total_nz_waves} non-zero waves"
            )

        if max_depth < 2:
            reason += f" (depth advisory: max={max_depth})"

        # 5. Report
        print("\n=== ACCUMULATED RESULT ===")
        print(f"Runs: {run_index}")
        print(f"Total elapsed: {elapsed_total:.0f}s")
        print(f"Verdict: {verdict}")
        print(f"Reason: {reason}")
        print(f"Total dispatches: {total_dispatches}")
        print(f"Total non-zero waves: {total_nz_waves} / {total_waves}")
        print(f"Max depth: {max_depth}")
        print(f"Total messages: {total_msgs}")
        print(f"Total tool calls: {total_tools}")
        print(f"Text-only stops: {len(all_stops)}")
        print(f"Per-wave violations: {len(all_violations)}")

        if all_violations:
            print("\nViolations (waves with <10 dispatches):")
            for v in all_violations[:20]:
                print(f"  seq={v['sequence']} dispatches={v['dispatch_count']}")

        if verdict == "PASS":
            print("\nPASS")
            return 0
        else:
            print(f"\n{verdict}: {reason}")
            return 1

    finally:
        if _cleanup and not args.no_cleanup:
            _cleanup_temp_project(project_dir)


if __name__ == "__main__":
    sys.exit(main())
