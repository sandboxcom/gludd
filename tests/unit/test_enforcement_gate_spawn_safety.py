"""Regression guards for enforcement-hook process isolation."""

from __future__ import annotations

from pathlib import Path

PLUGIN_ROOT = Path(".opencode/plugin")


def test_enforcement_plugins_never_spawn_a_detached_gate() -> None:
    """Loading any hook must not launch an untracked repository gate."""

    offenders = []
    for plugin in PLUGIN_ROOT.rglob("*.ts"):
        source = plugin.read_text(encoding="utf-8")
        if "spawnGateRefresh" in source or '["gate-refresh"]' in source:
            offenders.append(str(plugin))

    assert offenders == []
