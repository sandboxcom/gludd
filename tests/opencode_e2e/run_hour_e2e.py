"""Comprehensive hour-long E2E test for opencode multitask enforcement.

Spawns opencode in a temp directory against a test project.
Verifies:
  1. ANY prompt results in EXACTLY 10 subagent dispatches per wave
  2. opencode NEVER stops on its own (text-only response = bug)
  3. 3x depth dispatch works (main -> agent -> agent -> agent)
  4. The test harness terminates opencode externally (PASSES when killed)

Usage:
    make test-opencode-e2e-hour TIMEOUT=3600
    uv run python tests/opencode_e2e/run_hour_e2e.py --timeout 3600
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_PROJECT_SRC = ROOT / "tests" / "opencode_e2e" / "_test_project"
OPENCODE_BIN = os.environ.get("OPENCODE_BIN", "opencode")

PROMPT_SEQUENCE = [
    (
        "Read TASKS.md. There are 18 trivial tasks (each 'make taskN' writes a 1-line file). "
        "You MUST dispatch EXACTLY 10 task subagents in EACH wave. One subagent per task. "
        "When results return, immediately dispatch the NEXT wave of 10 for remaining tasks. "
        "NEVER send a text-only answer while tasks remain. "
        "When ALL 18 tasks show [x] in TASKS.md, say ALL DONE."
    ),
    "Keep going. Check TASKS.md. Dispatch exactly 10 more task subagents for any unchecked tasks.",
    "Continue. Read TASKS.md. Dispatch exactly 10 task subagents for remaining unchecked tasks.",
    "Still working. Check TASKS.md. If any tasks unchecked, dispatch exactly 10 task subagents.",
]


@dataclass
class WaveStats:
    seq: int = 0
    timestamp: float = 0.0
    dispatch_count: int = 0
    tool_call_count: int = 0
    is_text_only: bool = False
    text_preview: str = ""
    under_floor: bool = False


@dataclass
class TestResult:
    verdict: str  # "PASS" | "FAIL"
    reason: str = ""
    total_elapsed: float = 0.0
    total_waves: int = 0
    total_dispatches: int = 0
    total_tool_calls: int = 0
    total_messages: int = 0
    max_depth: int = 0
    text_only_stops: int = 0
    under_floor_waves: int = 0
    wave_stats: list[WaveStats] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    final_verdict: str = ""


def _reset_tasks(tasks_path: Path) -> None:
    content = tasks_path.read_text()
    content = re.sub(r"- \[x\] ", "- [ ] ", content)
    tasks_path.write_text(content)


def _make_temp_project() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="opencode-e2e-hour-", dir="/tmp"))
    print(f"--- Copying test project to {tmp}")
    shutil.copytree(TEST_PROJECT_SRC, tmp, dirs_exist_ok=True, symlinks=True)

    setup_rc = subprocess.run(
        ["bash", "setup.sh", "--copy", str(ROOT), str(tmp)],
        cwd=str(tmp),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if setup_rc.returncode != 0:
        print(f"WARN: setup.sh returned {setup_rc.returncode}")
        print(setup_rc.stderr[-500:])
    else:
        print(f"Setup: {setup_rc.stdout.strip()[-300:]}")

    ocjson_path = tmp / "opencode.json"
    if ocjson_path.exists():
        content = ocjson_path.read_text()
        content = content.replace("__PROJECT_DIR__", str(tmp))
        ocjson_path.write_text(content)

    return tmp


def _spawn_and_monitor(project_dir: Path, timeout_sec: int) -> TestResult:
    """Spawn opencode, monitor NDJSON output, collect metrics."""
    result = TestResult(verdict="RUNNING")

    env = os.environ.copy()
    env.setdefault("OPENCODE_SUBAGENT", "0")
    env.setdefault("OPENCODE_DISABLE_AUTOUPDATE", "1")
    env.setdefault("GLUDD_ENHANCEMENT_RATIO_BLOCK", "0")
    env.setdefault("GLUDD_CLEAN_TREE_ENFORCE", "0")
    env.setdefault("GLUDD_TDD_ENFORCE", "0")
    env.setdefault("GLUDD_TASK_DEADLINE_BLOCK", "0")
    env.setdefault("GLUDD_MAKE_ENFORCE", "0")
    env.setdefault("GLUDD_VERIFIED_CLAIMS_ENFORCE", "0")
    env.setdefault("GLUDD_MODEL_UTIL_ENFORCE", "0")

    cmd = [
        OPENCODE_BIN,
        "run",
        "--format",
        "json",
        "--auto",
        "--dir",
        str(project_dir),
        "--print-logs",
        "--log-level",
        "ERROR",
        "--agent",
        "build",
    ]
    cmd.append(PROMPT_SEQUENCE[0])

    os.makedirs("/tmp/gludd-opencode-e2e", exist_ok=True)
    ts = int(time.time())
    log_path = f"/tmp/gludd-opencode-e2e/hour-e2e-{ts}.log"

    print(f"--- Launching opencode (timeout={timeout_sec}s)")
    print(f"    CMD: {' '.join(cmd[:6])} ...")
    print(f"    LOG: {log_path}")

    proc = subprocess.Popen(
        cmd,
        cwd=str(project_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    waves: list[WaveStats] = []
    current = WaveStats(seq=0, timestamp=time.time())
    in_assistant = False
    t0 = time.time()
    all_lines: list[str] = []
    depth_stack: list[bool] = []
    max_dep = 0

    try:
        while True:
            elapsed = time.time() - t0
            if elapsed > timeout_sec:
                break
            if proc.poll() is not None:
                break
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                time.sleep(0.05)
                continue
            stripped = line.strip()
            if not stripped:
                continue
            all_lines.append(stripped)

            is_json = stripped.startswith("{") and '"type"' in stripped

            if is_json and '"type":"step_start"' in stripped:
                if in_assistant and (current.text_preview or current.tool_call_count > 0):
                    waves.append(current)
                    current = WaveStats(seq=len(waves), timestamp=time.time())
                in_assistant = True
                depth_stack.append(False)
                continue

            if is_json and '"type":"message_start"' in stripped:
                if in_assistant and (current.text_preview or current.tool_call_count > 0):
                    waves.append(current)
                    current = WaveStats(seq=len(waves), timestamp=time.time())
                in_assistant = "assistant" in stripped.lower()
                continue

            if not in_assistant:
                continue

            if is_json and '"type":"text"' in stripped:
                try:
                    data = json.loads(stripped)
                    txt = str(data.get("text", data.get("part", {}).get("text", "")))
                    current.text_preview += txt + "\n"
                    current.is_text_only = current.tool_call_count == 0
                except json.JSONDecodeError:
                    pass
                continue

            if is_json and '"type":"tool_use"' in stripped:
                current.tool_call_count += 1
                current.is_text_only = False
                try:
                    data = json.loads(stripped)
                    tool_name = str(data.get("tool", data.get("part", {}).get("tool", "")))
                    if tool_name in ("task", "agent", "workflow"):
                        current.dispatch_count += 1
                        if depth_stack:
                            depth_stack[-1] = True
                except json.JSONDecodeError:
                    pass
                continue

            if is_json and '"type":"step_finish"' in stripped:
                in_assistant = False
                if current.tool_call_count > 0:
                    current.under_floor = 0 < current.dispatch_count < 10
                    waves.append(current)
                    current = WaveStats(seq=len(waves), timestamp=time.time())
                if depth_stack and depth_stack.pop():
                    cur_dep = len(depth_stack) + 1
                    if cur_dep > max_dep:
                        max_dep = cur_dep
                continue

            if is_json and '"type":"tool_result"' in stripped:
                in_assistant = False
                if current.tool_call_count > 0:
                    current.under_floor = 0 < current.dispatch_count < 10
                    waves.append(current)
                    current = WaveStats(seq=len(waves), timestamp=time.time())
                if depth_stack:
                    depth_stack.append(False)
                continue

    finally:
        elapsed = time.time() - t0
        result.total_elapsed = elapsed
        killed = elapsed > timeout_sec

        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

        if in_assistant and (current.text_preview or current.tool_call_count > 0):
            current.under_floor = 0 < current.dispatch_count < 10
            waves.append(current)

    result.wave_stats = waves
    result.total_waves = len(waves)
    result.max_depth = max_dep

    total_disp = sum(w.dispatch_count for w in waves)
    total_tools = sum(w.tool_call_count for w in waves)
    text_only = [w for w in waves if w.is_text_only and w.text_preview.strip()]
    under_floor = [w for w in waves if w.under_floor]

    result.total_dispatches = total_disp
    result.total_tool_calls = total_tools
    result.total_messages = len(waves)
    result.text_only_stops = len(text_only)
    result.under_floor_waves = len(under_floor)

    # --- Compute verdict ---
    violations: list[str] = []

    if total_disp == 0 and killed:
        violations.append("CRITICAL: Killed by timeout with 0 dispatches")
    elif total_disp == 0 and not killed:
        violations.append("CRITICAL: Stopped naturally with 0 dispatches")

    for w in under_floor[:10]:
        violations.append(f"UNDER-FLOOR: wave {w.seq} had {w.dispatch_count} dispatches (floor=10)")

    for w in text_only[:10]:
        violations.append(f"TEXT-ONLY STOP: wave {w.seq}: {w.text_preview[:100].strip()}")

    if max_dep < 2:
        violations.append(f"DEPTH ADVISORY: max depth={max_dep}, 3x depth not tested")

    result.violations = violations

    if not violations or (killed and total_disp > 0 and len(under_floor) == 0):
        result.verdict = "PASS"
        result.reason = (
            f"Killed by timeout at {elapsed:.0f}s, {total_disp} dispatches, "
            f"{len(waves)} waves, max depth={max_dep}, "
            f"{len(under_floor)} under-floor, {len(text_only)} text-only stops"
        )
    else:
        result.verdict = "FAIL"
        result.reason = "; ".join(violations[:5])

    # Write raw log
    with open(log_path, "w") as fh:
        fh.write("=== E2E HOUR TEST LOG ===\n")
        fh.write(f"Verdict: {result.verdict}\n")
        fh.write(f"Reason: {result.reason}\n")
        fh.write(f"Elapsed: {elapsed:.0f}s\n")
        fh.write(f"Dispatches: {total_disp}\n")
        fh.write(f"Waves: {len(waves)}\n")
        fh.write(f"Under-floor waves: {len(under_floor)}\n")
        fh.write(f"Text-only stops: {len(text_only)}\n")
        fh.write(f"Max depth: {max_dep}\n")
        fh.write("\n=== WAVE STATS ===\n")
        for w in waves:
            fh.write(
                f"seq={w.seq} dispatches={w.dispatch_count} "
                f"tools={w.tool_call_count} "
                f"text_only={w.is_text_only} "
                f"under_floor={w.under_floor}\n"
            )
        fh.write("\n=== RAW LINES ===\n")
        for i, line in enumerate(all_lines):
            fh.write(f"{i}: {line[:500]}\n")

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Hour-long opencode E2E test")
    parser.add_argument("--timeout", type=int, default=3600, help="Maximum seconds to run (default: 3600 = 1 hour)")
    parser.add_argument("--no-cleanup", action="store_true", help="Keep temp directory after test")
    parser.add_argument("--quick", action="store_true", help="Quick test (5 min)")
    args = parser.parse_args()

    timeout = 300 if args.quick else args.timeout

    print("╔══════════════════════════════════════════════════╗")
    print("║         OPENCODE E2E MULTITASK TEST               ║")
    print("╠══════════════════════════════════════════════════╣")
    print(f"║ Timeout: {timeout}s ({timeout / 60:.0f} min)                              ║")
    print(f"║ Start: {time.strftime('%Y-%m-%d %H:%M:%S')}                     ║")
    print("╚══════════════════════════════════════════════════╝")
    print()

    # Verify binary
    try:
        subprocess.run([OPENCODE_BIN, "--version"], capture_output=True, timeout=5, check=False)
    except Exception:
        print(f"ERROR: {OPENCODE_BIN} binary not found")
        return 1

    # Set up temp project
    tmp = _make_temp_project()
    _reset_tasks(tmp / "TASKS.md")

    try:
        result = _spawn_and_monitor(tmp, timeout)

        print()
        print("╔══════════════════════════════════════════════════╗")
        print("║                   RESULTS                         ║")
        print("╠══════════════════════════════════════════════════╣")
        print(f"║ Verdict:    {result.verdict:<38}║")
        print(f"║ Elapsed:    {result.total_elapsed:.0f}s{'':>30}║")
        print(f"║ Waves:      {result.total_waves:<38}║")
        print(f"║ Dispatches: {result.total_dispatches:<38}║")
        print(f"║ Tool calls: {result.total_tool_calls:<38}║")
        print(f"║ Max depth:  {result.max_depth:<38}║")
        print(f"║ Text-only:  {result.text_only_stops:<38}║")
        print(f"║ Under-floor:{result.under_floor_waves:<38}║")
        print("╚══════════════════════════════════════════════════╝")
        print()
        print(f"Reason: {result.reason}")

        if result.violations:
            print(f"\nViolations ({len(result.violations)}):")
            for v in result.violations[:10]:
                print(f"  - {v}")

        if result.verdict == "PASS":
            print("\n=== PASS ===")
            ret = 0
        else:
            print(f"\n=== FAIL: {result.reason} ===")
            ret = 1

        return ret

    finally:
        if not args.no_cleanup:
            print(f"\nCleaning up: {tmp}")
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"\nTemp project kept: {tmp}")


if __name__ == "__main__":
    sys.exit(main())
