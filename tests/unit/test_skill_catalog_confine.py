"""Regression tests for curated skill download path confinement."""

from __future__ import annotations

from pathlib import Path

import pytest

from general_ludd.skills import catalog as catalog_module
from general_ludd.skills.catalog import CatalogSkillEntry, SkillCatalog


@pytest.mark.parametrize(
    "unsafe_name",
    ["../escape", "subdir/escape", "..\\escape", ".", "..", "foo..bar"],
)
def test_download_rejects_unsafe_catalog_keys_before_filesystem_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_name: str,
) -> None:
    """A compromised catalog key cannot select a path or create its target."""
    target = tmp_path / "catalog"
    monkeypatch.setitem(
        catalog_module._CURATED_SKILLS,
        unsafe_name,
        CatalogSkillEntry(name=unsafe_name, description="untrusted"),
    )

    result = SkillCatalog().download_skill(unsafe_name, str(target))

    assert result is None
    assert not target.exists()
    assert not (tmp_path / "escape.md").exists()


def test_download_rejects_existing_leaf_symlink_escape(tmp_path: Path) -> None:
    """Canonical confinement prevents an existing file link from escaping."""
    target = tmp_path / "catalog"
    target.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("keep", encoding="utf-8")
    (target / "tdd-discipline.md").symlink_to(outside)

    result = SkillCatalog().download_skill("tdd-discipline", str(target))

    assert result is None
    assert outside.read_text(encoding="utf-8") == "keep"


def test_download_keeps_valid_filename_directly_in_canonical_target(
    tmp_path: Path,
) -> None:
    """A valid curated name retains the public download contract."""
    result = SkillCatalog().download_skill("tdd-discipline", str(tmp_path))

    assert result == tmp_path / "tdd-discipline.md"
    assert result.resolve().parent == tmp_path.resolve()
