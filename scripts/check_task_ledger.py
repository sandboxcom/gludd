import subprocess
import sys
from pathlib import Path

task_path = Path("TASKS.md")
if not task_path.is_file():
    print("ERROR: TASKS.md not found")
    sys.exit(1)

c = task_path.read_text(encoding="utf-8")

if "Current Session" not in c:
    print("ERROR: TASKS.md lacks 'Current Session' section")
    sys.exit(1)

# Check that staged files have corresponding task entries
r = subprocess.run(
    ["git", "diff", "--cached", "--name-only"],
    capture_output=True,
    text=True,
    timeout=5,
    check=False,
)
staged = [s for s in r.stdout.strip().split("\n") if s]
if staged:
    current_sec = c.split("## Current Session")[1]
    for s in staged:
        if s not in current_sec:
            print(f"ERROR: staged file '{s}' not mentioned in TASKS.md Current Session")
            sys.exit(1)

print("OK: TASKS.md current and covers staged changes")
