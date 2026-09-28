"""Keep the six v0.1.1 exact-gate repairs atomic."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _implementation_lines(path: Path) -> int:
    """Count plugin implementation lines using the budget gate's rules."""
    count = 0
    in_block_comment = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if in_block_comment:
            if "*/" in line:
                in_block_comment = False
            continue
        if line.startswith("/*"):
            if "*/" not in line:
                in_block_comment = True
            continue
        if line.startswith(("*", "//")):
            continue
        count += 1
    return count


def _yaml(path: Path) -> dict[str, Any]:
    """Load a mapping-shaped YAML contract."""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{path} must contain a YAML mapping"
    return loaded


def test_all_six_exact_gate_repairs_remain_green_together() -> None:
    """Fail with the complete deterministic set of drifted repair contracts."""
    config = json.loads((ROOT / "opencode.json").read_text(encoding="utf-8"))
    plugin_paths = config["plugin"]
    plugin_names = [Path(path).name for path in plugin_paths]
    registry = (ROOT / "docs" / "ENFORCEMENT_PLUGIN_REGISTRY.md").read_text(
        encoding="utf-8"
    )
    registry_rows = [
        name
        for _number, name in re.findall(
            r"^\|\s*(\d+)\s*\|\s*`([^`]+)`\s*\|", registry, re.MULTILINE
        )
    ]
    advertised_counts = re.findall(
        r"Total:\s*(\d+)\s+active\s+plugins", registry, re.IGNORECASE
    )

    runner = (ROOT / "scripts" / "run_ci_shards_serial.py").read_text(
        encoding="utf-8"
    )
    beta3_tokens = {
        "SHARD-EMPTY",
        "SHARD-COVERAGE-MISSING",
        "failures[shard] = 2",
        "coverage_file.stat().st_size == 0",
    }

    precommit = _yaml(ROOT / ".pre-commit-config.yaml")
    local_hooks = [
        hook
        for repo in precommit["repos"]
        if repo.get("repo") == "local"
        for hook in repo.get("hooks", [])
        if hook.get("id") == "check-disk"
    ]

    workflow = _yaml(ROOT / ".github" / "workflows" / "build.yml")
    release = workflow["jobs"]["release"]
    release_condition = str(release.get("if", ""))
    smoke_steps = [
        step
        for step in release.get("steps", [])
        if "smoke" in str(step.get("name", "")).lower()
    ]

    plugin_total = sum(
        _implementation_lines(path)
        for path in (ROOT / ".opencode" / "plugin").glob("enforce-*.ts")
    )
    contracts = {
        "registry-entry-and-count": (
            registry_rows == plugin_names
            and advertised_counts == [str(len(plugin_names))]
            and "enforce-pipeline-kickoff.ts" in plugin_names
        ),
        "beta3-empty-and-missing-coverage": all(
            token in runner for token in beta3_tokens
        ),
        "loop-line-count": len(
            (ROOT / "src" / "general_ludd" / "event_loop" / "loop.py")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        <= 5500,
        "precommit-entry-point": (
            len(local_hooks) == 1
            and local_hooks[0].get("entry")
            == "make check-disk CHECK_DISK_VALIDATE_ONLY=0"
        ),
        "tag-only-post-deploy-smoke": (
            bool(smoke_steps)
            and "startsWith(github.ref, 'refs/tags/v')" in release_condition
        ),
        "plugin-implementation-budget": plugin_total < 6000,
    }
    failures = sorted(name for name, passed in contracts.items() if not passed)
    assert not failures, f"v0.1.1 exact-gate repair drift: {failures}"
