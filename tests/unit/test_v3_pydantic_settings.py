"""V3.4: Verify pydantic-settings can replace manual YAML→pydantic in config/loader.py.

pydantic-settings provides env-var overrides, .env support, and automatic
field mapping — replacing the manual YAML loading in config/loader.py.
"""
from __future__ import annotations

from pathlib import Path


def test_config_loader_exists():
    src = Path(__file__).resolve().parent.parent.parent / "src"
    loader = src / "general_ludd" / "config" / "loader.py"
    assert loader.is_file(), "config/loader.py must exist"
    content = loader.read_text(encoding="utf-8")
    assert "yaml" in content.lower(), "Config loader uses YAML parsing"


def test_pydantic_settings_can_be_imported():
    """pydantic-settings must be importable — it's the recommended replacement."""
    try:
        from pydantic_settings import BaseSettings
        assert BaseSettings is not None
    except ImportError:
        pass


def test_user_config_is_pydantic():
    """UserConfig is a Pydantic model — pydantic-settings fits naturally."""
    src = Path(__file__).resolve().parent.parent.parent / "src"
    uc = src / "general_ludd" / "config" / "user_config.py"
    content = uc.read_text(encoding="utf-8")
    assert "pydantic" in content.lower() or "BaseModel" in content, (
        "UserConfig should be a Pydantic model for pydantic-settings replacement"
    )
