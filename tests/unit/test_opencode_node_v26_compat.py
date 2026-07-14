from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGINS_DIR = ROOT / ".opencode" / "plugins"

REQUIRE_RE = re.compile(r"\brequire\s*\(")


def _collect_plugin_files() -> list[Path]:
    files: list[Path] = []
    for d in (PLUGIN_DIR, PLUGINS_DIR):
        if d.exists():
            files.extend(sorted(d.glob("*.ts")))
    return files


def _node_check_v26_compat(filepath: Path) -> tuple[int, str, str]:
    """Run node --experimental-strip-types <filepath> and return (exit_code, stdout, stderr)."""
    result = subprocess.run(
        ["node", "--experimental-strip-types", str(filepath)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


class TestOpencodeNodeV26Compat:
    def test_all_plugin_files_load_with_strip_types(self):
        errors: list[str] = []
        for f in _collect_plugin_files():
            code, stdout, stderr = _node_check_v26_compat(f)
            if code != 0:
                relative = f.relative_to(ROOT)
                detail = stderr or stdout or f"exit code {code}"
                errors.append(f"{relative}: {detail}")
        assert not errors, (
            f"{len(errors)} plugin file(s) fail Node v26 "
            f"--experimental-strip-types loading:\n"
            + "\n".join(errors)
        )

    @staticmethod
    def _test_known_node_v26_patterns():
        """Bisect: confirm that known-bad patterns (try-inside-catch without
        semicolon separator) are rejected by node --experimental-strip-types."""
        patterns: list[tuple[str, bool]] = [
            ("catch { try { console.log(1) } }", True),
            ("catch (e: any) { try { console.log(1) } }", True),
            ("catch { ; try { console.log(1) } }", False),
            ("try { console.log(1) } catch { ; try { console.log(2) } }", False),
        ]
        for snippet, should_fail in patterns:
            result = subprocess.run(
                ["node", "--experimental-strip-types", "-e", snippet],
                capture_output=True, text=True, timeout=10,
            )
            failed = result.returncode != 0
            assert failed == should_fail, (
                f"Snippet {snippet!r}: expected fail={should_fail}, "
                f"got fail={failed} (exit {result.returncode}, "
                f"stderr={result.stderr.strip()!r})"
            )

    def test_no_require_calls_in_plugin_files(self):
        """require() is not available in ESM context (Node v26).
        All plugin files use static import — require() calls inside function
        bodies are runtime bombs.  This is NOT caught by the parse-time
        --experimental-strip-types check above.
        """
        violations: list[tuple[str, list[int]]] = []
        for f in _collect_plugin_files():
            lines = f.read_text().split("\n")
            line_nums: list[int] = []
            for i, line in enumerate(lines, 1):
                if REQUIRE_RE.search(line):
                    line_nums.append(i)
            if line_nums:
                violations.append((str(f.relative_to(ROOT)), line_nums))
        assert not violations, (
            f"{len(violations)} plugin file(s) contain require() calls "
            f"(not available in ESM context — will fail at runtime):\n"
            + "\n".join(f"  {relpath}: lines {lns}" for relpath, lns in violations)
            + "\n\nReplace require() with top-level import or fs usage."
        )
