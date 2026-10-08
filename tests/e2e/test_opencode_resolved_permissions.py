"""Resolved-config checks for the installed OpenCode runtime.

Static JSON tests are insufficient because OpenCode merges global, project,
directory, agent, inline, and managed configuration layers. These tests use
``opencode debug config`` and inspect only the permission fields so secrets or
provider configuration are never printed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
OPENCODE = shutil.which("opencode")
# The CI runner may resolve opencode to a toolcache path whose binary was
# never installed; only run when the resolved file actually exists and we
# are not in CI (live-loader coverage requires a local install).
_OPENCODE_READY = OPENCODE is not None and Path(OPENCODE).exists() and os.environ.get("CI") not in ("1", "true")
_opencode_skip = pytest.mark.skipif(
    not _OPENCODE_READY,
    reason="opencode binary not resolvable/usable in this environment",
)


def _resolved_config() -> dict:
    result = subprocess.run(
        [str(OPENCODE), "debug", "config"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        f"opencode debug config failed without exposing its potentially sensitive output; rc={result.returncode}"
    )
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, dict)
    return parsed


@_opencode_skip
@pytest.mark.xdist_group("opencode-live")
def test_resolved_config_retains_project_permission_rules() -> None:
    """The runtime—not merely the JSON file—must retain project rules."""
    config = _resolved_config()
    permission = config.get("permission")
    assert isinstance(permission, dict)

    bash = permission.get("bash")
    assert isinstance(bash, dict)
    assert list(bash.items())[:2] == [("*", "deny"), ("make *", "allow")]

    read = permission.get("read")
    assert isinstance(read, dict)
    assert next(iter(read.items())) == ("*", "allow")
    assert read["*.env"] == "deny"
    assert permission.get("edit") == "allow"
    assert permission.get("glob") == "allow"
    assert permission.get("grep") == "allow"
    assert "write" not in permission

    external = permission.get("external_directory")
    assert isinstance(external, dict)
    assert next(iter(external.items())) == ("*", "deny")


@_opencode_skip
@pytest.mark.xdist_group("opencode-live")
def test_build_agent_does_not_override_project_permission_rules() -> None:
    """The effective build agent must mirror the project safety rules."""
    config = _resolved_config()
    assert isinstance(config, dict)
    permission = config.get("permission", {})
    build = config.get("agent", {}).get("build", {})
    build_permission = build.get("permission", {})
    assert isinstance(permission, dict)
    assert isinstance(build_permission, dict)
    for tool in ("read", "edit", "glob", "grep", "bash", "external_directory"):
        assert build_permission.get(tool) == permission.get(tool), (
            f"build agent widens or changes {tool}: "
            f"global={permission.get(tool)!r}, "
            f"build={build_permission.get(tool)!r}"
        )
