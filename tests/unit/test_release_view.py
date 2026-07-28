"""Fail-closed tests for the human-readable GitHub release view."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release_view.py"
MAKEFILE = ROOT / "Makefile"


def _load_module() -> ModuleType:
    assert SCRIPT.is_file(), "scripts/release_view.py must implement release-view"
    spec = importlib.util.spec_from_file_location("release_view", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_release_fails_without_traceback(monkeypatch, capsys):
    module = _load_module()
    monkeypatch.setattr(
        module,
        "_run",
        lambda _cmd: (1, "", "release not found"),
    )

    result = module.view_release("v0.1.0-beta.3", "sandboxcom/gludd")

    output = capsys.readouterr()
    assert result == 1
    assert "release not found" in output.err
    assert "Traceback" not in output.out + output.err


def test_invalid_json_fails_without_traceback(monkeypatch, capsys):
    module = _load_module()
    monkeypatch.setattr(module, "_run", lambda _cmd: (0, "not-json", ""))

    result = module.view_release("v0.1.0-beta.3", "sandboxcom/gludd")

    output = capsys.readouterr()
    assert result == 1
    assert "invalid JSON" in output.err
    assert "Traceback" not in output.out + output.err


def test_valid_release_prints_summary_and_assets(monkeypatch, capsys):
    module = _load_module()
    monkeypatch.setattr(
        module,
        "_run",
        lambda _cmd: (
            0,
            (
                '{"tagName":"v0.1.0-beta.3","name":"beta.3","isDraft":false,'
                '"isPrerelease":true,"publishedAt":"2026-07-28T00:00:00Z",'
                '"url":"https://github.com/sandboxcom/gludd/releases/tag/v0.1.0-beta.3",'
                '"assets":[{"name":"gludd-linux","size":42}]}'
            ),
            "",
        ),
    )

    result = module.view_release("v0.1.0-beta.3", "sandboxcom/gludd")

    output = capsys.readouterr()
    assert result == 0
    assert "RELEASE: v0.1.0-beta.3" in output.out
    assert "gludd-linux 42 bytes" in output.out
    assert output.err == ""


def test_make_target_delegates_to_fail_closed_script():
    content = MAKEFILE.read_text(encoding="utf-8")
    recipe = content.split("\nrelease-view:", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]

    assert 'scripts/release_view.py "$(TAG)"' in recipe
    assert "|| echo" not in recipe
