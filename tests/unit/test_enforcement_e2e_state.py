"""Unit coverage for namespaced enforcement E2E state paths."""

from pathlib import Path

import pytest

from tests.e2e.enforcement_state import state_path, state_root


def test_state_root_defaults_to_tmp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GLUDD_E2E_STATE_ROOT", raising=False)
    assert state_root() == Path("/tmp")


def test_state_path_uses_configured_namespace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    namespace = tmp_path / "worker-state"
    monkeypatch.setenv("GLUDD_E2E_STATE_ROOT", str(namespace))

    result = state_path("gludd-tool-streak.json")

    assert result == namespace / "gludd-tool-streak.json"
    assert namespace.is_dir()


@pytest.mark.parametrize("name", ["../foreign", "nested/foreign", "", "."])
def test_state_path_rejects_non_filename_values(name: str) -> None:
    with pytest.raises(ValueError, match="plain filename"):
        state_path(name)
