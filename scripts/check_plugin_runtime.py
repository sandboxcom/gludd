import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGINS_DIR = ROOT / ".opencode" / "plugins"

DANGEROUS_IMPORTS = [
    (re.compile(r'''import\s+.*from\s+["']child_process["']''', re.MULTILINE), "bare 'child_process' import (use 'node:child_process')"),
    (re.compile(r'''import\s+.*from\s+["']fs["']''', re.MULTILINE), "bare 'fs' import (use 'node:fs')"),
    (re.compile(r'''from\s+["']@opencode/plugin["']''', re.MULTILINE), "wrong package '@opencode/plugin' (use '@opencode-ai/plugin')"),
]


def check_runtime_load(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    script = "import('./" + rel.replace("'", "\\'") + "')"
    result = subprocess.run(
        ["node", "--experimental-strip-types", "-e", script],
        capture_output=True, text=True, timeout=30,
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        output = stderr or stdout
        print(f"RUNTIME LOAD FAILED in {path.relative_to(ROOT)}:")
        print(output[:800] or "(no output)")
        return False
    return True


def check_dangerous_imports(path: Path) -> bool:
    content = path.read_text()
    ok = True
    for pattern, label in DANGEROUS_IMPORTS:
        if pattern.search(content):
            print(f"DANGEROUS IMPORT in {path.relative_to(ROOT)}: {label}")
            ok = False
    return ok


def main() -> int:
    errors = 0
    ts_files = []
    for d in (PLUGIN_DIR, PLUGINS_DIR):
        if d.exists():
            ts_files.extend(sorted(d.glob("*.ts")))

    if not ts_files:
        print("No plugin .ts files found — runtime check skipped (OK)")
        return 0

    for f in ts_files:
        if not check_runtime_load(f):
            errors += 1
        if not check_dangerous_imports(f):
            errors += 1

    if errors:
        print(f"\n{errors} runtime/import check(s) failed")
        return 1
    print("All plugin .ts files: runtime load + import checks OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
