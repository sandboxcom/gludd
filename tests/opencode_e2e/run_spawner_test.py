"""E2E spawner test: launch opencode against _test_project/ for 120s, verify it dispatches subagents."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_PROJECT = ROOT / "tests" / "opencode_e2e" / "_test_project"
OPENCODE_BIN = "opencode"


def _reset_tasks_file(tasks_path: Path) -> None:
    """Reset all checkboxes in TASKS.md to unchecked."""
    content = tasks_path.read_text()
    content = re.sub(r"- \[x\] ", "- [ ] ", content)
    tasks_path.write_text(content)


def main():
    # 1. Check binary
    try:
        subprocess.run(["which", OPENCODE_BIN], capture_output=True, text=True, check=True)
    except Exception:
        print(f"ERROR: {OPENCODE_BIN} binary not found on PATH")
        sys.exit(1)

    # 2. Reset TASKS.md checkboxes
    tasks_path = TEST_PROJECT / "TASKS.md"
    _reset_tasks_file(tasks_path)
    print(f"Reset checkboxes in {tasks_path}")

    # 3. Run setup.sh
    print("Running setup.sh...")
    rc = subprocess.run(
        ["bash", "setup.sh", str(ROOT)], cwd=str(TEST_PROJECT), capture_output=True, text=True, timeout=30
    )
    if rc.returncode != 0:
        print(f"WARN: setup.sh returned {rc.returncode}\n{rc.stderr}")
    else:
        print(f"Setup: {rc.stdout.strip()[-200:]}")

    # 3. Use the spawner
    print(f"Launching opencode with {TEST_PROJECT} for 120s...")
    sys.path.insert(0, str(ROOT / "tests"))
    from opencode_e2e._spawner import OpencodeSpawner, SpawnResult

    spawner = OpencodeSpawner(
        project_dir=str(TEST_PROJECT),
        prompt="Read TASKS.md, then dispatch 10 subagents to complete the tasks. Use make targets only.",
        timeout_sec=120,
    )
    result: SpawnResult = spawner.run()

    # 4. Report
    print("\n=== RESULT ===")
    print(f"Verdict: {result.verdict}")
    print(f"Dispatch calls: {result.total_dispatch_calls}")
    print(f"Tool calls: {result.total_tool_calls}")
    print(f"Messages: {result.total_messages}")
    print(f"Elapsed: {result.elapsed_sec:.1f}s")
    print(f"Killed (timeout): {result.killed}")
    print(f"Text-only stops: {len(result.text_only_stops)}")
    print(f"Dispatch waves: {len(result.dispatch_waves)}")
    for w in result.dispatch_waves[:5]:
        print(f"  seq={w['sequence']} dispatches={w['dispatch_count']}")
    if result.text_only_stops:
        print("Text-only stop previews:")
        for s in result.text_only_stops[:3]:
            print(f"  {s['text_preview'][:100]}")

    # Return exit code
    if result.verdict == "PASS":
        print("\n✅ PASS")
        sys.exit(0)
    else:
        print(f"\n❌ {result.verdict}")
        sys.exit(1)


if __name__ == "__main__":
    main()
