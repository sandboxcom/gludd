"""Regression coverage for the hosted hot-reload build runtime."""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
SETUP_NODE = "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020"
ESBUILD_INSTALL = "npm install --no-save --no-package-lock esbuild@0.28.1"


def _gate_steps() -> list[dict[str, Any]]:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    steps = workflow["jobs"]["gate"]["steps"]
    assert isinstance(steps, list)
    return steps


def _hot_build_index(steps: list[dict[str, Any]]) -> int:
    return next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Build hot-reload modules"
    )


def test_gate_pins_supported_node_before_hot_reload_build() -> None:
    """Both Python matrix legs must use the supported Node runtime."""
    steps = _gate_steps()
    build_index = _hot_build_index(steps)
    setup = [
        (index, step)
        for index, step in enumerate(steps)
        if step.get("uses") == SETUP_NODE
    ]

    assert setup, "gate job relies on the hosted runner's ambient Node version"
    setup_index, setup_step = setup[0]
    assert setup_index < build_index
    assert setup_step.get("with", {}).get("node-version") == "22"


def test_gate_installs_pinned_esbuild_before_hot_reload_build() -> None:
    """The builder must use esbuild instead of its emergency regex fallback."""
    steps = _gate_steps()
    build_index = _hot_build_index(steps)
    installs = [
        index
        for index, step in enumerate(steps)
        if ESBUILD_INSTALL in str(step.get("run", ""))
    ]

    assert installs, "gate job does not provision the mature TypeScript transpiler"
    assert installs[0] < build_index
