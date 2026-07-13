import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGINS_DIR = ROOT / ".opencode" / "plugins"

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

def main() -> int:
    errors = 0
    for d in (PLUGIN_DIR, PLUGINS_DIR):
        if d.exists():
            for f in sorted(d.glob("*.ts")):
                if not check_ts_file(f):
                    errors += 1
    if errors:
        print(f"\n{errors} plugin file(s) have syntax errors")
        return 1
    print("All plugin .ts files: syntax OK")
    return 0

if __name__ == "__main__":
    sys.exit(main())
