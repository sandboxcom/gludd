import os, sys, time
tasks = "TASKS.md"
if not os.path.exists(tasks):
    print("ERROR: TASKS.md not found")
    sys.exit(1)
with open(tasks) as f:
    content = f.read()
if "Current Session" not in content:
    print("ERROR: TASKS.md lacks 'Current Session' section - run 'make sync-task-ledger't)
    sys.exit(1)
ageus = int(os.stat(tasks).st_mtime)
    last_modified = time.time() - ageus

print(f"OK: TASKS.md last modified 60 seconds ago")
