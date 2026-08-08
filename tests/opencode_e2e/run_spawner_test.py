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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_PROJECT_SRC = ROOT / "tests" / "opencode_e2e" / "_test_project"
OPENCODE_BIN = "opencode"

PROMPT_SEQUENCE = [
    "Read TASKS.md, then dispatch 10 subagents to complete the tasks. Use make targets only.",
    "Keep working. Check which tasks remain in TASKS.md and dispatch another wave of 10 subagents.",
    "Continue working on remaining tasks. Every subagent should do exactly one trivial operation.",
    "Still running. Check TASKS.md again. Any unchecked items need subagents dispatched.",
    "Keep going. Read TASKS.md, find unchecked items, dispatch exactly 10 subagents.",
    "Continue. Remaining tasks still need work. Dispatch another 10 subagents.",
    "Read TASKS.md. If any tasks remain, dispatch 10 subagents. If all done, say ALL DONE.",
    "Almost done. Final check: read TASKS.md. Dispatch 10 subagents for any remaining tasks.",
    "Last prompt. If all tasks checked, say ALL DONE. Otherwise dispatch 10 subagents.",
    "Final prompt. All tasks should be complete by now. Confirm by reading TASKS.md.",
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
    if effective_timeout <= 120:
        prompt_sequence = PROMPT_SEQUENCE[:2]
        prompt_interval = 60
    elif effective_timeout <= 600:
        prompt_sequence = PROMPT_SEQUENCE[:4]
        prompt_interval = 120
    else:
        prompt_sequence = PROMPT_SEQUENCE
        prompt_interval = args.prompt_interval

    # 2. Set up project dir
    if args.no_temp:
        project_dir = TEST_PROJECT_SRC
        _cleanup = False
        print(f"Using source test project: {project_dir}")
    else:
        project_dir = _make_temp_project()
        _cleanup = True

    try:
        # 3. Reset checkboxes
        tasks_path = project_dir / "TASKS.md"
        _reset_tasks_file(tasks_path)
        print(f"Reset checkboxes in {tasks_path}")

        # 4. Run spawner
        sys.path.insert(0, str(ROOT / "tests"))
        from opencode_e2e._spawner import OpencodeSpawner, SpawnResult

        spawner = OpencodeSpawner(
            project_dir=str(project_dir),
            prompt=prompt_sequence[0] if prompt_sequence else "Read TASKS.md.",
            timeout_sec=effective_timeout,
            prompt_sequence=prompt_sequence[1:] if len(prompt_sequence) > 1 else [],
            prompt_interval_sec=prompt_interval,
            progress_interval_sec=args.progress_interval,
        )

        print(
            f"Launching opencode for {effective_timeout}s "
            f"(prompts={len(prompt_sequence)}, interval={prompt_interval}s)..."
        )
        result: SpawnResult = spawner.run()

        # 5. Report
        print("\n=== RESULT ===")
        print(f"Verdict: {result.verdict}")
        print(f"Reason: {result.verdict_reason}")
        print(f"Dispatch calls: {result.total_dispatch_calls}")
        print(f"Tool calls: {result.total_tool_calls}")
        print(f"Messages: {result.total_messages}")
        print(f"Elapsed: {result.elapsed_sec:.1f}s")
        print(f"Killed (timeout): {result.killed}")
        print(f"Depth max: {result.depth_count}")
        print(f"Prompts sent: {result.prompts_sent}")
        print(f"Text-only stops: {len(result.text_only_stops)}")
        print(f"Dispatch waves: {len(result.dispatch_waves)}")
        nz_count = sum(
            1 for w in result.dispatch_waves if isinstance(w["dispatch_count"], int) and w["dispatch_count"] > 0
        )
        print(f"Non-zero waves: {nz_count}")

        print("\nPer-wave dispatch counts:")
        for w in result.dispatch_waves[:15]:
            print(f"  seq={w['sequence']} dispatches={w['dispatch_count']}")

        if result.per_wave_violations:
            print(f"\nPer-wave violations ({len(result.per_wave_violations)} waves with <10 dispatches):")
            for v in result.per_wave_violations[:10]:
                print(f"  seq={v['sequence']} dispatches={v['dispatch_count']}: {v.get('reason', '')}")

        if result.text_only_stops:
            print(f"\nText-only stops ({len(result.text_only_stops)}):")
            for s in result.text_only_stops[:3]:
                print(f"  {s['text_preview'][:100]}")

        print(f"\nLog: {result.log_path}")
        print(f"Progress: {result.progress_log}")

        if result.verdict == "PASS":
            print("\nPASS")
            return 0
        else:
            print(f"\n{result.verdict}: {result.verdict_reason}")
            return 1

    finally:
        if _cleanup and not args.no_cleanup:
            _cleanup_temp_project(project_dir)


if __name__ == "__main__":
    sys.exit(main())
