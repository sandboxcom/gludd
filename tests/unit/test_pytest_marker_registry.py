"""Keep project-owned pytest markers registered and warning-free."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_e2e_marker_is_registered() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    markers = config["tool"]["pytest"]["ini_options"]["markers"]
    assert any(marker.startswith("e2e:") for marker in markers)
