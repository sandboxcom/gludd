"""Print every documented make target, one per line."""
import re
import sys

makefile = sys.argv[1] if len(sys.argv) > 1 else "Makefile"
content = open(makefile).read()
targets = re.findall(r'^(?!#)([a-zA-Z][-a-zA-Z0-9]*):', content, re.MULTILINE)
for t in sorted(set(targets)):
    if not t.startswith('_'):
        print(t)
