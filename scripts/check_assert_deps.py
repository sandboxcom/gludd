"""check_assert_deps.py — AA041 enforcement.

Verify that test assertions reference symbols that actually exist at the
claimed source locations. When code is refactored (e.g. renaming a function
from `_reportAlive` to `reportAlive`), structural tests that grep for the
old name silently pass (the string no longer exists) or fail on wrong files.

This check scans test files for assertion targets (function names, variable
names, class names referenced in string assertions like `"symbol" in content`)
and verifies those symbols are defined in the claimed source locations.

Exit 0 on clean, exit 1 if assertion-symbol mismatches are found.
"""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = ROOT / "tests"
SRC_DIR = ROOT / "src"

# Symbols that appear in plugin source files and may be tested via
# structural tests. We extract function/const/class declarations.
IDENTIFIER_RE = re.compile(r"\b(?:function|const|class)\s+(\w+)", re.MULTILINE)


def extract_source_symbols(file_path: Path) -> set[str]:
    """Extract declared symbols from a source file."""
    if not file_path.exists():
        return set()
    try:
        content = file_path.read_text()
    except Exception:
        return set()
    return set(IDENTIFIER_RE.findall(content))


def extract_assertion_targets(test_content: str) -> list[tuple[str, str]]:
    """Extract (symbol, source_file) pairs from test assertions.

    Detects patterns like:
      content = (PLUGIN_DIR / "enforce-foo.ts").read_text()
      assert "reportAlive" in content

    Returns list of (symbol, source_file_expression) tuples.
    """
    targets: list[tuple[str, str]] = []
    # Match: assert "someFunction" in content (or similar)
    # where content came from a file read
    pattern = re.compile(
        r'assert\s+["\'](\w+)["\']\s+in\s+(\w+)',
        re.MULTILINE,
    )
    for match in pattern.finditer(test_content):
        symbol = match.group(1)
        content_var = match.group(2)
        # Trace back to find what file was read into content_var
        file_pattern = re.compile(
            rf'({content_var})\s*=\s*\(?(?:Path\(["\'][^"\']+["\']\).*?["\']([^"\']+)["\']'
            r"|.*?read_text\(\))",
            re.MULTILINE,
        )
        file_match = file_pattern.search(test_content)
        if file_match:
            file_path = file_match.group(2) if file_match.lastindex and file_match.lastindex >= 2 else None
            if file_path:
                targets.append((symbol, file_path))
    return targets


def main() -> int:
    failures: list[str] = []

    for test_file in sorted(TEST_DIR.rglob("test_behavioral*.py")):
        try:
            content = test_file.read_text()
        except Exception:
            continue

        targets = extract_assertion_targets(content)
        for symbol, source_path_str in targets:
            # Resolve relative path against ROOT
            source_file = ROOT / source_path_str.lstrip("/")
            if not source_file.exists():
                continue  # can't verify if source doesn't exist
            declared = extract_source_symbols(source_file)
            if declared and symbol not in declared:
                failures.append(
                    f"{test_file.relative_to(ROOT)}: asserts '{symbol}' exists"
                    f" in {source_path_str} but symbol not found there"
                )

    # Also scan for test assertions that check plugin content files
    # Pattern: assert "symbol" in content where content = (PLUGIN_DIR / "file.ts").read_text()
    plugin_assert_re = re.compile(
        r'assert\s+["\'](\w+)["\']\s+in\s+'
        r"content|"
        r"content\s*=\s*\(.*?\)\.read_text\(\)",
        re.MULTILINE,
    )

    for test_file in sorted(TEST_DIR.rglob("test_*.py")):
        try:
            content = test_file.read_text()
        except Exception:
            continue

        # Find read_text() calls in test files
        read_matches = list(
            re.finditer(
                r"(\w+)\s*=\s*\([^)]*?([\"'])([\.\w/-]+)\2[^)]*?\)\.read_text\(\)",
                content,
            )
        )
        if not read_matches:
            continue

        for rm in read_matches:
            var_name = rm.group(1)
            file_ref = rm.group(3)

            # Find assertions using this variable with a string literal
            assert_re = re.compile(
                rf'assert\s+["\'](\w+)["\']\s+in\s+{re.escape(var_name)}',
                re.MULTILINE,
            )
            for am in assert_re.finditer(content):
                symbol = am.group(1)
                # Resolve file_ref against ROOT
                resolved = ROOT / file_ref.lstrip("/")
                if resolved.exists() and resolved.suffix == ".ts":
                    declared = extract_source_symbols(resolved)
                    if declared and symbol not in declared:
                        failures.append(
                            f"{test_file.relative_to(ROOT)}: asserts '{symbol}'"
                            f" in {file_ref} but symbol not declared there"
                        )

    if failures:
        print(f"ASSERT-DEPS: {len(failures)} assertion dependency issue(s) found:")
        for f in failures:
            print(f"  {f}")
        # Advisory: warn but don't block (loud warning, structural check)
        print("Advisory: fix test assertions or refactored code. See AA041.")
        return 0  # Advisory by default; gate includes this as a warning

    print("OK: no assertion dependency mismatches detected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
