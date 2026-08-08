"""Real E2E test: launch opencode with deepseek model, complete tasks, verify output.

Usage:
    uv run python tests/opencode_e2e/run_real_e2e.py [--timeout N] [--tasks N]

This actually spends API tokens. It:
  1. Copies _test_project/ to a temp dir
  2. Resets all TASKS.md entries to unchecked
  3. Spawns opencode --format json against the project
  4. Captures and parses NDJSON output
  5. Checks how many task output files were created
  6. Reports PASS/FAIL
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from opencode_e2e._spawner import OpencodeSpawner  # noqa: E402

TEST_PROJECT_SRC = ROOT / "tests" / "opencode_e2e" / "_test_project"


def _reset_tasks(tasks_path: Path) -> None:
    content = tasks_path.read_text()
    content = re.sub(r"- \[x\] ", "- [ ] ", content)
    tasks_path.write_text(content)


def _make_temp_project() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="opencode-e2e-real-", dir="/tmp"))
    print(f"Copying test project to {tmp} ...")
    shutil.copytree(TEST_PROJECT_SRC, tmp, dirs_exist_ok=True, symlinks=True)
    # Remove pre-existing output so we start clean
    output_dir = tmp / "output"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(exist_ok=True)
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
        last = rc.stdout.strip().split("\n")[-2:] if rc.stdout else []
        for line in last:
            print(f"  setup: {line.strip()}")
    return tmp


def _check_outputs(project_dir: Path, expected_tasks: int) -> tuple[int, int]:
    output_dir = project_dir / "output"
    if not output_dir.exists():
        return 0, expected_tasks
    completed = 0
    for i in range(1, expected_tasks + 1):
        task_file = output_dir / f"task{i}.txt"
        if task_file.exists() and task_file.stat().st_size > 0:
            completed += 1
    return completed, expected_tasks


def main() -> int:
    parser = argparse.ArgumentParser(description="Real opencode E2E test")
    parser.add_argument("--timeout", type=int, default=600, help="Timeout in seconds (default: 600)")
    parser.add_argument("--tasks", type=int, default=18, help="Expected task count (default: 18)")
    parser.add_argument("--no-cleanup", action="store_true", help="Keep temp dir after test")
    parser.add_argument("--model", type=str, default="deepseek/deepseek-v4-pro", help="Model to use")
    args = parser.parse_args()

    tmp = _make_temp_project()
    _reset_tasks(tmp / "TASKS.md")

    prompt = (
        f"Read TASKS.md. There are {args.tasks} trivial tasks (each writes a 1-line file via make taskN). "
        "You MUST dispatch EXACTLY 10 task subagents per wave. Each subagent runs ONE `make taskN` command. "
        "When all subagent results return, immediately dispatch the next wave for remaining tasks. "
        "Repeat until ALL tasks show [x] in TASKS.md. NEVER send a text-only answer. "
        "Say ALL DONE only when every task is checked."
    )

    print("\n=== Launcing opencode E2E test ===")
    print(f"Model: {args.model}")
    print(f"Timeout: {args.timeout}s")
    print(f"Tasks: {args.tasks}")
    print(f"Project: {tmp}")
    print("")

    spawner = OpencodeSpawner(
        project_dir=str(tmp),
        prompt=prompt,
        timeout_sec=args.timeout,
        model=args.model,
        progress_interval_sec=30,
    )

    t0 = time.time()
    result = spawner.run()
    elapsed = time.time() - t0

    completed, total = _check_outputs(tmp, args.tasks)

    print("\n=== Results ===")
    print(f"Elapsed: {elapsed:.0f}s")
    print(f"Verdict: {result.verdict}")
    print(f"Reason: {result.verdict_reason}")
    print(f"Dispatches: {result.total_dispatch_calls}")
    nz_waves = sum(
        1 for w in result.dispatch_waves if isinstance(w.get("dispatch_count"), int) and w["dispatch_count"] > 0
    )
    print(f"Waves (non-zero): {nz_waves} / {len(result.dispatch_waves)}")
    print(f"Tool calls: {result.total_tool_calls}")
    print(f"Messages: {result.total_messages}")
    print(f"Max depth: {result.depth_count}")
    print(f"Killed by timeout: {result.killed}")
    print(f"Text-only stops: {len(result.text_only_stops)}")
    print(f"Per-wave violations: {len(result.per_wave_violations)}")
    print(f"\nOutput files: {completed}/{total} completed")

    if result.per_wave_violations:
        print("\nUnder-floor waves:")
        for v in result.per_wave_violations[:10]:
            print(f"  seq={v.get('sequence')} dispatches={v.get('dispatch_count')}")

    if result.text_only_stops:
        print("\nText-only stops:")
        for s in result.text_only_stops[:5]:
            preview = str(s.get("text_preview", ""))[:100]
            print(f"  seq={s.get('sequence')}: {preview}")

    print(f"\nStructured log: {result.log_path}")
    print(f"Progress log: {result.progress_log}")

    if completed > 0:
        print(f"\nPASSED: {completed}/{total} tasks completed")
    elif result.total_dispatch_calls > 0:
        print(f"\nPASSED (partial): {result.total_dispatch_calls} dispatches made")
    else:
        print("\nFAILED: 0 tasks, 0 dispatches")

    if not args.no_cleanup:
        print(f"\nCleaning up: {tmp}")
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print(f"\nTemp project kept: {tmp}")

    return 0 if (completed > 0 or result.total_dispatch_calls > 0) else 1


if __name__ == "__main__":
    sys.exit(main())
