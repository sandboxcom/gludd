"""Known repaired advisory floors for the locked Node quality-tool graph."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("package", "affected"),
    [
        ("braces", "<=3.0.3"),
        ("js-yaml", ">=5.0.0,<5.4.1"),
        ("markdown-it", "<14.3.1"),
        ("smol-toml", "<=1.8.0"),
        ("katex", ">=0.11.0,<0.18.2"),
    ],
)
def test_quality_lock_excludes_versions_with_available_security_fixes(package: str, affected: str) -> None:
    """All copies exclude the published affected range, including nested copies."""
    lock = json.loads((ROOT / ".opencode/package-lock.json").read_text())
    copies = [
        (name, record["version"])
        for name, record in lock["packages"].items()
        if name == f"node_modules/{package}" or name.endswith(f"/node_modules/{package}")
    ]

    assert all(version not in SpecifierSet(affected) for _name, version in copies), copies
