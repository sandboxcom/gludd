import re
import subprocess
import sys
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGINS_DIR = ROOT / ".opencode" / "plugins"

DANGEROUS_IMPORTS = [
    (re.compile(r'''import\s+.*from\s+["']child_process["']''', re.MULTILINE), "bare 'child_process' import (use 'node:child_process')"),
    (re.compile(r'''import\s+.*from\s+["']fs["']''', re.MULTILINE), "bare 'fs' import (use 'node:fs')"),
    (re.compile(r'''from\s+["']@opencode/plugin["']''', re.MULTILINE), "wrong package '@opencode/plugin' (use '@opencode-ai/plugin')"),
]


def _is_transient_fixture(path: Path) -> bool:
    return path.name.startswith("zzz_") and path.name.endswith("_test.ts")


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def check_runtime_load(path: Path) -> bool:
    try:
        specifier = "./" + path.relative_to(ROOT).as_posix()
    except ValueError:
        specifier = path.resolve().as_uri()
    script = "import(" + json.dumps(specifier) + ")"
    result = subprocess.run(
        ["node", "--experimental-strip-types", "-e", script],
        capture_output=True, text=True, timeout=30,
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        output = stderr or stdout
        print(f"RUNTIME LOAD FAILED in {_display_path(path)}:")
        print(output[:800] or "(no output)")
        return False
    return True


def check_dangerous_imports(path: Path) -> bool:
    content = path.read_text()
    ok = True
    for pattern, label in DANGEROUS_IMPORTS:
        if pattern.search(content):
            print(f"DANGEROUS IMPORT in {_display_path(path)}: {label}")
            ok = False
    return ok


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    errors = 0
    ts_files = []
    explicit_dir = bool(args)
    dirs = [Path(args[0]).resolve()] if explicit_dir else [PLUGIN_DIR, PLUGINS_DIR]
    for d in dirs:
        if d.exists():
            ts_files.extend(sorted(d.glob("*.ts")))

    if not ts_files:
        print("No plugin .ts files found — runtime check skipped (OK)")
        return 0

    for f in ts_files:
        if not explicit_dir and _is_transient_fixture(f):
            continue
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
