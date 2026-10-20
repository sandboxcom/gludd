"""Exercise native duplicate-owner admission without treating Make variables as recipes."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import check_duplicate_targets as owner
else:
    from scripts import check_duplicate_targets as owner


@pytest.mark.parametrize("modifier", ["override", "export", "private"])
@pytest.mark.parametrize("operator", ["=", ":=", "?=", "+="])
def test_target_variable_modifiers_do_not_duplicate_recipe(
    tmp_path: Path, modifier: str, operator: str,
) -> None:
    entrypoint = tmp_path / "Makefile"
    entrypoint.write_text(
        f"proof: {modifier} UV {operator} selected-runner\nproof:\n\t@echo proof\n",
        encoding="utf-8",
    )
    assert owner.extract_targets(entrypoint) == {"proof": 1}


def test_variable_assignments_do_not_hide_real_duplicate_in_fragments(tmp_path: Path) -> None:
    (tmp_path / "make").mkdir()
    entrypoint = tmp_path / "Makefile"
    entrypoint.write_text("include make/proof.mk\n", encoding="utf-8")
    (tmp_path / "make/proof.mk").write_text(
        "# ignored: comment\n.PHONY: proof\nproof: override UV := selected-runner\n"
        "proof:\n\t@echo first\nproof:\n\t@echo duplicate\n",
        encoding="utf-8",
    )
    assert owner.extract_targets(entrypoint) == {"proof": 2}


@pytest.mark.parametrize("recipes,expected", [("proof:\n", 0), ("proof:\nproof:\n", 1)])
def test_cli_retains_clean_and_duplicate_exit_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    recipes: str, expected: int,
) -> None:
    entrypoint = tmp_path / "Makefile"
    entrypoint.write_text(recipes, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["check_duplicate_targets.py", str(entrypoint)])
    assert owner.main() == expected
    output = capsys.readouterr()
    if expected:
        assert "proof: declared 2 times" in output.err
    else:
        assert "0 duplicates" in output.out


def test_cli_missing_native_source_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["check_duplicate_targets.py", str(tmp_path / "absent")])
    assert owner.main() == 1
    assert "Makefile not found" in capsys.readouterr().err


def test_default_cli_resolves_native_entrypoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "Makefile").write_text("proof:\n", encoding="utf-8")
    monkeypatch.setattr(owner, "__file__", str(tmp_path / "scripts/check_duplicate_targets.py"))
    monkeypatch.setattr(sys, "argv", ["check_duplicate_targets.py"])
    assert owner.main() == 0
