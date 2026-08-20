"""Behavioral regressions for the depth-enforcement contract checker."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_depth_limit.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_depth_limit_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_plugin(path: Path, *, subagent_bypass: bool) -> None:
    bypass = "if (isSubagent()) return" if subagent_bypass else ""
    path.write_text(
        f'''const MAX_DEPTH = parseInt(process.env.GLUDD_MAX_DEPTH || "4", 10)
export default async () => ({{
  "tool.execute.before": async (input: {{ tool?: string }}) => {{
    {bypass}
    const depth = parseInt(process.env.OPENCODE_DEPTH || "0", 10)
    if (input.tool === "task" && depth >= MAX_DEPTH) {{
      return {{ permissionDecision: "deny" }}
    }}
  }},
}})
'''
    )


def test_subagent_bypass_is_a_contract_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = tmp_path / "enforce-depth.ts"
    _write_plugin(plugin, subagent_bypass=True)
    module = _load_module()
    monkeypatch.setattr(module, "PLUGIN_PATH", str(plugin))

    assert module.check_subagent_depth_only() == 1


def test_depth_only_plugin_passes_contract_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = tmp_path / "enforce-depth.ts"
    _write_plugin(plugin, subagent_bypass=False)
    module = _load_module()
    monkeypatch.setattr(module, "PLUGIN_PATH", str(plugin))

    assert module.check_subagent_depth_only() == 0


def test_main_propagates_subagent_bypass_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = tmp_path / "enforce-depth.ts"
    _write_plugin(plugin, subagent_bypass=True)
    module = _load_module()
    monkeypatch.setattr(module, "PLUGIN_PATH", str(plugin))
    monkeypatch.setattr(module, "EXPECTED_MAX", 3)

    assert module.main() == 1


def test_missing_depth_declaration_is_reported(tmp_path: Path) -> None:
    plugin = tmp_path / "enforce-depth.ts"
    plugin.write_text("export default async () => ({})\n")
    module = _load_module()

    effective, message = module.read_depth_config(str(plugin))

    assert effective == 0
    assert message.startswith("MISSING:")


def test_environment_override_is_effective(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = tmp_path / "enforce-depth.ts"
    _write_plugin(plugin, subagent_bypass=False)
    module = _load_module()
    monkeypatch.setenv("GLUDD_MAX_DEPTH", "6")

    effective, message = module.read_depth_config(str(plugin))

    assert effective == 6
    assert "env GLUDD_MAX_DEPTH=6" in message


def test_main_rejects_depth_below_required_minimum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = tmp_path / "enforce-depth.ts"
    plugin.write_text(
        'const MAX_DEPTH = parseInt(process.env.GLUDD_MAX_DEPTH || "2", 10)\n'
    )
    module = _load_module()
    monkeypatch.setattr(module, "PLUGIN_PATH", str(plugin))
    monkeypatch.setattr(module, "EXPECTED_MAX", 3)

    assert module.main() == 1
