"""Native release-runbook admission must bind active version and real Make owners."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import check_runbook_currency as currency
else:
    from scripts import check_runbook_currency as currency


@pytest.fixture
def runbook_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "make").mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.2"\n', encoding="utf-8")
    (tmp_path / "Makefile").write_text("include make/release.mk\n", encoding="utf-8")
    (tmp_path / "make/release.mk").write_text("release-promote:\n\t@echo native-owner\n", encoding="utf-8")
    monkeypatch.setattr(currency, "__file__", str(tmp_path / "scripts/check_runbook_currency.py"))
    monkeypatch.setattr(sys, "argv", ["check_runbook_currency.py"])
    monkeypatch.delenv("TAG", raising=False)
    return tmp_path


def _invoke() -> int:
    with pytest.raises(SystemExit) as result:
        currency.main()
    assert isinstance(result.value.code, int)
    return result.value.code


def _write_runbook(root: Path, content: str) -> None:
    (root / "docs/RELEASE_RUNBOOK.md").write_text(content, encoding="utf-8")


def test_no_tag_uses_project_version_and_refuses_stale_runbook(runbook_root: Path) -> None:
    _write_runbook(runbook_root, "Release target: `v0.1.1`.\n")
    assert _invoke() == 1


def test_historical_current_version_does_not_validate_stale_active_target(
    runbook_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_runbook(runbook_root, "Release target: `v0.1.1`.\nHistory: v0.1.2 was discussed.\n")
    monkeypatch.setattr(sys, "argv", ["check_runbook_currency.py", "v0.1.2"])
    assert _invoke() == 1


@pytest.mark.parametrize("carrier", ["default", "argument", "environment"])
def test_current_target_resolves_fragment_owner(
    runbook_root: Path, monkeypatch: pytest.MonkeyPatch, carrier: str,
) -> None:
    _write_runbook(runbook_root, "Release target: `v0.1.2`.\nUse `make release-promote`.\n")
    if carrier == "argument":
        monkeypatch.setattr(sys, "argv", ["check_runbook_currency.py", "v0.1.2"])
    elif carrier == "environment":
        monkeypatch.setenv("TAG", "v0.1.2")
    assert _invoke() == 0


@pytest.mark.parametrize("command", [
    "`make missing-release-owner TAG=v0.1.2`",
    "```bash\nmake missing-release-owner TAG=v0.1.2\n```",
    "```text\nmake missing-release-owner\n```",
])
def test_unknown_argument_or_fenced_command_refuses_before_success(
    runbook_root: Path, command: str,
) -> None:
    _write_runbook(runbook_root, f"Release target: `v0.1.2`.\n{command}\n")
    assert _invoke() == 1


@pytest.mark.parametrize("content", [
    "Historical version 0.1.2 only.\n",
    "Release target: `v0.1.2`.\nRelease target: `v0.1.2`.\n",
])
def test_missing_or_ambiguous_active_target_is_not_current(runbook_root: Path, content: str) -> None:
    _write_runbook(runbook_root, content)
    assert _invoke() == 1


@pytest.mark.parametrize("metadata", ['[project]\n', '[project]\nversion = 12\n', '[project'])
def test_unreadable_project_version_fails_closed(runbook_root: Path, metadata: str) -> None:
    _write_runbook(runbook_root, "Release target: `v0.1.2`.\n")
    (runbook_root / "pyproject.toml").write_text(metadata, encoding="utf-8")
    assert _invoke() == 1


def test_missing_fragment_fails_closed(runbook_root: Path) -> None:
    _write_runbook(runbook_root, "Release target: `v0.1.2`.\n")
    (runbook_root / "make/release.mk").unlink()
    assert _invoke() == 1


@pytest.mark.parametrize("carrier", ["argument", "environment"])
def test_requested_tag_must_match_project_metadata(
    runbook_root: Path, monkeypatch: pytest.MonkeyPatch, carrier: str,
) -> None:
    _write_runbook(runbook_root, "Release target: `v0.1.2`.\n")
    if carrier == "argument":
        monkeypatch.setattr(sys, "argv", ["check_runbook_currency.py", "v0.1.1"])
    else:
        monkeypatch.setenv("TAG", "v0.1.1")
    assert _invoke() == 1


@pytest.mark.parametrize("metadata", ['version = "0.1.2"\n', '[project]\nversion = ""\n'])
def test_missing_table_or_empty_version_is_not_release_authority(
    runbook_root: Path, metadata: str,
) -> None:
    _write_runbook(runbook_root, "Release target: `v0.1.2`.\n")
    (runbook_root / "pyproject.toml").write_text(metadata, encoding="utf-8")
    assert _invoke() == 1


@pytest.mark.parametrize("missing", ["pyproject.toml", "docs/RELEASE_RUNBOOK.md"])
def test_missing_native_input_refuses(runbook_root: Path, missing: str) -> None:
    _write_runbook(runbook_root, "Release target: `v0.1.2`.\n")
    (runbook_root / missing).unlink()
    assert _invoke() == 1
