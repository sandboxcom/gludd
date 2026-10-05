"""Exercise the one-shot drift fixers without writing to the checkout.

The fixers normally run via ``make fix-init-drift`` / ``make fix-docs-drift``.
Their tests must use disposable roots: pytest is validation, never an alternate
way to apply a formatter to tracked project files.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]


class _InitDriftModule(Protocol):
    SRC_ROOT: Path

    def main(self, argv: list[str] | None = None) -> int: ...


class _DocsDriftModule(Protocol):
    ROOT: Path
    DOCS: Path
    EXCLUDE_PREFIXES: tuple[str, str]

    def main(self, argv: list[str] | None = None) -> int: ...


def _load_script(name: str) -> ModuleType:
    """Load a fixer without retaining a repository path or module globally."""
    spec = importlib.util.spec_from_file_location(f"_gludd_test_{name}", ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_apply_init_drift_fixes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fix_init_drift = cast(_InitDriftModule, _load_script("fix_init_drift"))

    source_root = tmp_path / "src" / "general_ludd"
    fixture = source_root / "algorithms" / "__init__.py"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("", encoding="utf-8")
    sentinel = ROOT / "src" / "general_ludd" / "__init__.py"
    sentinel_before = sentinel.read_bytes()

    monkeypatch.setattr(fix_init_drift, "SRC_ROOT", source_root)

    rc = fix_init_drift.main([])

    assert rc == 0
    assert fixture.read_text(encoding="utf-8").startswith('"""Algorithm library')
    assert "__all__: list[str] = []" in fixture.read_text(encoding="utf-8")
    assert sentinel.read_bytes() == sentinel_before


def test_apply_docs_drift_fixes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fix_docs_drift = cast(_DocsDriftModule, _load_script("fix_docs_drift"))

    docs = tmp_path / "docs"
    docs.mkdir()
    fixture = docs / "fixture.md"
    fixture.write_text(
        "# Fixture  \n\n```\ndef sample():\n    return 1\n```\n",
        encoding="utf-8",
    )
    archive = docs / "archive" / "historic.md"
    archive.parent.mkdir()
    archive.write_text("# Historic  \n", encoding="utf-8")
    root_doc = tmp_path / "README.md"
    root_doc.write_text("# Root  \n", encoding="utf-8")
    sentinel = ROOT / "docs" / "features" / "OPENCODE_DEPTH_ENFORCEMENT.md"
    sentinel_before = sentinel.read_bytes()

    monkeypatch.setattr(fix_docs_drift, "ROOT", tmp_path)
    monkeypatch.setattr(fix_docs_drift, "DOCS", docs)
    monkeypatch.setattr(
        fix_docs_drift,
        "EXCLUDE_PREFIXES",
        (str(tmp_path / "external"), str(docs / "archive")),
    )

    rc = fix_docs_drift.main([])

    assert rc == 0
    assert fixture.read_text(encoding="utf-8") == (
        "# Fixture\n\n```python\ndef sample():\n    return 1\n```\n"
    )
    assert root_doc.read_text(encoding="utf-8") == "# Root\n"
    assert archive.read_text(encoding="utf-8") == "# Historic  \n"
    assert sentinel.read_bytes() == sentinel_before


def test_gate_status_is_ignored_runtime_state() -> None:
    """A gate may persist status, but that artifact must never dirty Git."""
    ignored = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert ".gate-status" in ignored
