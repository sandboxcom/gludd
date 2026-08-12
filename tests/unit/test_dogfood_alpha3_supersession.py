"""Focused evidence for ancestry-only reconciliation of dogfood alpha.3."""

from __future__ import annotations

from general_ludd.secrets import EnvSecretsManager
from tests.e2e.dogfood import test_dogfood_todo_site


def test_current_zai_alias_resolution_is_fail_closed(monkeypatch) -> None:
    """The old alias fix survives alongside the newer ambient-secret guard."""
    monkeypatch.setenv("ZAI_BASE_URL", "https://example.invalid/v4")
    monkeypatch.setenv("GLUDD_PSK", "must-not-leak")

    manager = EnvSecretsManager()

    assert manager.resolve("zai_api_base") == "https://example.invalid/v4"
    assert manager.resolve("GLUDD_PSK") is None


def test_live_dogfood_gap_is_visible_as_a_non_strict_xfail() -> None:
    """The current scaffold tracks E9 explicitly instead of hiding a TODO skip."""
    marks = getattr(test_dogfood_todo_site.test_todo_website_live_scenario, "pytestmark", [])
    xfail_marks = [mark for mark in marks if mark.name == "xfail"]

    assert len(xfail_marks) == 1
    assert xfail_marks[0].kwargs["strict"] is False
    assert "E9" in xfail_marks[0].kwargs["reason"]
    assert test_dogfood_todo_site.pytestmark.name == "e2e"
