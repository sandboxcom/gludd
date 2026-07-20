import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGINS_DIR = ROOT / ".opencode" / "plugins"

def _is_transient_fixture(path: Path) -> bool:
    return path.name.startswith("zzz_") and path.name.endswith("_test.ts")

def check_ts_file(path: Path) -> bool:
    """Validate TypeScript syntax using node --check."""
    result = subprocess.run(
        ["node", "--check", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"SYNTAX ERROR in {path}:")
        print(result.stderr.strip())
        return False
    return True

def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    # Optional positional arg: an explicit plugin directory to scan instead of
    # the default .opencode/plugin + .opencode/plugins. Used by the test suite
    # to validate syntax in an isolated temp dir without polluting the real
    # plugin directory (which would race with the concurrent parse-all test).
    dirs: list[Path]
    explicit_dir = bool(args)
    if explicit_dir:
        dirs = [Path(args[0]).resolve()]
    else:
        dirs = [PLUGIN_DIR, PLUGINS_DIR]
    errors = 0
    for d in dirs:
        if d.exists():
            for f in sorted(d.glob("*.ts")):
                if not explicit_dir and _is_transient_fixture(f):
                    continue
                if not check_ts_file(f):
                    errors += 1
    if errors:
        print(f"\n{errors} plugin file(s) have syntax errors")
        return 1
    print("All plugin .ts files: syntax OK")
    return 0

if __name__ == "__main__":
    sys.exit(main())
