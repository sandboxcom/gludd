"""SC.7: No plugin .ts file in .opencode/plugin/ may have named exports.

Only ``export default`` and type-only exports (``export type``,
``export interface``) are permitted. Named exports such as
``export const``, ``export function``, ``export class``, and ``export {name}``
crash opencode's auto-discovery loader (``getLegacyPlugins()`` iterates
``Object.values(mod)`` and rejects any export that is not a function).

This test scans **every** ``.ts`` file under ``.opencode/plugin/`` recursively
— both top-level plugin files and subdirectory implementation modules
(e.g. ``impl/``). Even though subdirectory files are not auto-discovered
today, keeping them free of named exports prevents future regressions if
opencode's loader is ever changed to recurse.

Run: make test-specific TESTFILE='tests/unit/test_no_named_exports'
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"

ALL_TS_FILES: list[Path] = (
    sorted(PLUGIN_DIR.rglob("*.ts")) if PLUGIN_DIR.is_dir() else []
)

NAMED_EXPORT_RES: list[re.Pattern[str]] = [
    re.compile(
        r"^\s*export\s+(?:const|let|var)\s+\w+", re.MULTILINE
    ),
    re.compile(
        r"^\s*export\s+(?:async\s+)?function\s+\w+", re.MULTILINE
    ),
    re.compile(
        r"^\s*export\s+class\s+\w+", re.MULTILINE
    ),
    re.compile(
        r"^\s*export\s+(?!type\b)\s*\{", re.MULTILINE
    ),
]


@pytest.mark.parametrize(
    "ts_file",
    ALL_TS_FILES,
    ids=lambda p: str(p.relative_to(PLUGIN_DIR)),
)
def test_no_named_exports(ts_file: Path) -> None:
    """No .ts file under .opencode/plugin/ may have named exports.

    Named exports crash opencode's legacy loader. Only ``export default``
    and ``export type`` are safe.
    """
    content = ts_file.read_text()
    violations: list[str] = []
    for pattern in NAMED_EXPORT_RES:
        for m in pattern.finditer(content):
            line_num = content[: m.start()].count("\n") + 1
            line_text = content.split("\n")[line_num - 1].strip()
            violations.append(
                f"line {line_num}: {line_text[:80]}"
            )
    assert not violations, (
        f"{ts_file.relative_to(ROOT)} has named exports.\n"
        f"Only `export default` and `export type` are allowed in "
        f".opencode/plugin/. Named exports crash opencode's loader.\n"
        f"Violations:\n  " + "\n  ".join(violations)
    )
