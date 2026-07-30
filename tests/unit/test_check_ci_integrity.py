import importlib
from pathlib import Path


def test_script_exists(monkeypatch):
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "check_ci_integrity.py"
    )
    assert script.is_file()

    monkeypatch.syspath_prepend(str(script.parent))
    m = importlib.import_module("check_ci_integrity")
    assert hasattr(m, "CI_CRITICAL")
    assert len(m.CI_CRITICAL) >= 1

    assert "require_ci_green.py" in str(m.CI_CRITICAL)

    assert hasattr(m, "main")
    assert callable(m.main)
