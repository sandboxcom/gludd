"""Detect naming collisions between import aliases and local definitions.

The isWatchdogDisengaged bug (Session 52): an import alias
(isDisengaged as isWatchdogDisengaged) collided with a local function
(function isDisengaged), causing the alias to be undefined at runtime
under --experimental-strip-types. This test scans all .ts plugin files
for similar collisions before they reach production.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIRS = [
    ROOT / ".opencode" / "plugin",
    ROOT / ".opencode" / "plugin" / "impl",
    ROOT / ".opencode" / "lib",
    ROOT / ".opencode" / "plugins",
]


def _all_ts_files() -> list[Path]:
    files: list[Path] = []
    for d in PLUGIN_DIRS:
        if d.exists():
            files.extend(sorted(d.glob("*.ts")))
    return files


def _find_import_aliases(src: str) -> list[tuple[str, str, int]]:
    """Find import aliases: returns [(original, alias, line_number)]."""
    results: list[tuple[str, str, int]] = []
    for i, line in enumerate(src.split("\n"), 1):
        stripped = line.strip()
        if stripped.startswith(("//", "*", "/*")) or "import" not in stripped:
            continue
        for m in re.finditer(r"(\w+)\s+as\s+(\w+)", line):
            original, alias = m.group(1), m.group(2)
            if original != alias:
                results.append((original, alias, i))
    return results


def _has_local_definition(src: str, name: str) -> bool:
    """Check if a local function/const/class with the given name exists."""
    return bool(re.search(rf"(?:export\s+)?(?:function|const|class|let|var)\s+{re.escape(name)}\b", src))


class TestNoImportAliasCollisions:
    """Import aliases must not collide with local function/const/class names."""

    @pytest.mark.parametrize("fpath", _all_ts_files(), ids=lambda p: p.name)
    def test_no_alias_shadows_local_definition(self, fpath: Path):
        src = fpath.read_text()
        aliases = _find_import_aliases(src)
        violations: list[str] = []
        for original, alias, line in aliases:
            if _has_local_definition(src, alias):
                violations.append(
                    f"  line {line}: import alias '{alias}' (from '{original}') collides with local definition"
                )
        assert not violations, (
            f"{fpath.name}: import alias / local definition collisions detected.\n"
            f"This causes ReferenceError under --experimental-strip-types:\n" + "\n".join(violations)
        )
