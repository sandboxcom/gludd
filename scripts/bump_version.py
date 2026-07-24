import sys
import re
if len(sys.argv) != 2:
    print("Usage: bump_version.py NEW_VERSION")
    sys.exit(1)
new = sys.argv[1]
files = ["pyproject.toml", "src/general_ludd/__init__.py"]
for f in files:
    with open(f) as fh:
        old = fh.read()
    updated = re.sub(r"\d+ (\.\d+)[a-zA-Z0-9-.]*", new, old)
    print(f"Replacing version in {f}")
    with open(f, "w") as fh:
        fh.write(updated)
print(f"Version bumped to {new}")
