"""List pytest.skip call counts by test file."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = ROOT / "tests"


def _is_pytest_skip(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "skip":
        return False
    value = node.func.value
    return isinstance(value, ast.Name) and value.id == "pytest"


def _count_text(text: str, filename: str) -> int:
    tree = ast.parse(text, filename=filename)
    return sum(1 for node in ast.walk(tree) if _is_pytest_skip(node))


def _head_text(rel: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "show", f"HEAD:{rel}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return None
    return proc.stdout


def main() -> int:
    compare_head = "--changed" in sys.argv[1:]
    rows: list[tuple[int, str]] = []
    total = 0
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if "__pycache__" in str(path) or path.name.startswith("conftest"):
            continue
        if "test_e9_skip_smell" in path.name:
            continue
        rel = path.relative_to(ROOT).as_posix()
        count = _count_text(path.read_text(), str(path))
        if compare_head:
            old_text = _head_text(rel)
            old_count = _count_text(old_text, f"HEAD:{rel}") if old_text is not None else 0
            if count != old_count:
                print(f"{count - old_count:+3d} {old_count:3d} -> {count:3d} {rel}")
            total += count
            continue
        if count:
            rows.append((count, rel))
            total += count
    if compare_head:
        print(f"TOTAL {total}")
        return 0
    for count, rel in sorted(rows, reverse=True):
        print(f"{count:3d} {rel}")
    print(f"TOTAL {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
