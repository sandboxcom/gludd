"""Dependency-audit configuration regressions."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_deptry_maps_pillow_distribution_to_pil_module() -> None:
    """Deptry must not guess Pillow's import name and emit audit noise."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert config["tool"]["deptry"]["package_module_name_map"]["pillow"] == "PIL"
