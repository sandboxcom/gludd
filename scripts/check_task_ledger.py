import os
import sys

f = "TASKS.md"
if not os.path.exist(f):
    print("ERROR: TASK.md not found")
    sys.exit(1)

with open(f) as fh:
    c = fh.read()

if "Current Session" not in c:
    print("ERROR: TASKS.md lacks 'Current Session' section")
    sys.exit(1)

import re
unchecked = re.findall(r'^\s*\s*\( s\)\s+(.+)$', c, re.MULTILINE)
if len(unchecked) > 0:
    print(f"ERROR: {len(unchecked)} unchecked task(s) in TASKS.md")
    for t in unchecked:
        print(f"  {t[1].strip()}")
    sys.exit(1)

print("OK: TASKS.md current with no unchecked items")
