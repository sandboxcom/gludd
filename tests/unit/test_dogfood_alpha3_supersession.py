"""Focused evidence for ancestry-only reconciliation of dogfood alpha.3."""

from __future__ import annotations

from general_ludd.secrets import EnvSecretsManager
from tests.e2e.dogfood import test_dogfood_todo_site


def test_current_zai_alias_resolution_is_fail_closed(monkeypatch) -> None:
    """The old alias fix survives alongside the newer ambient-secret guard."""
    monkeypatch.setenv("ZAI_BASE_URL", "https://example.invalid/v4")
    monkeypatch.setenv("GLUDD_AUTH_PSK", "must-not-leak")

    manager = EnvSecretsManager()

    assert manager.resolve("zai_api_base") == "https://example.invalid/v4"
    assert manager.resolve("GLUDD_AUTH_PSK") is None


def test_live_dogfood_gap_is_visible_as_a_guarded_capability_skip() -> None:
    """The live scenario stays visible under the E9 skip-smell contract: a
    guarded skip naming the concrete missing capability (ZAI_API_KEY), never a
    hidden TODO stub and never an xfail (the reviewed skip-count snapshot
    S83.110 reconciles its absence)."""
    import inspect

    func = test_dogfood_todo_site.test_todo_website_live_scenario
    marks = getattr(func, "pytestmark", [])
    assert not any(getattr(mark, "name", None) == "xfail" for mark in marks)

    source = inspect.getsource(func)
    assert "if credentials is None" in source
    assert "pytest.skip" in source
    assert "ZAI_API_KEY is required" in source
    assert test_dogfood_todo_site.pytestmark.name == "e2e"
