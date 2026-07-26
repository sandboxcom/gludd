import os, sys, subprocess

f = "TASKS.md"
if not os.path.exists(f):
    print("ERROR: TASK.md not found")
    sys.exit(1)

with open(f) as fh:
    c = fh.read()

if "Current Session" not in c:
    print("ERROR: TASKS.md lacks 'Current Session' section")
    sys.exit(1)

# Check that staged files have corresponding task entries
r = subprocess.run(["git","diff","--cached","--name-only"], capture_output=True, text=True, timeout=5)
staged = [s for s in r.stdout.strip().split("\n") if s]
if staged:
    current_sec = c.split("## Current Session")[1]
    for s in staged:
        if s not in current_sec:
            print(f"ERROR: staged file '{s}' not mentioned in TASKS.md Current Session")
            sys.exit(1)

print("OK: TASKS.md current and covers staged changes")
