"""Behavioral contract for quiet, fail-closed locked Node dependency targets."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
CONTRACT = json.loads(
    (ROOT / "config" / "make_target_contract.json").read_text(encoding="utf-8")
)
TARGETS = ("node-deps-sync", "node-deps-relock", "node-deps-audit")


def _target_recipe(target: str) -> str:
    """Return one target recipe without accidentally matching adjacent targets."""
    start = MAKEFILE.index(f"{target}:")
    end = MAKEFILE.find("\n\n", start)
    return MAKEFILE[start:] if end == -1 else MAKEFILE[start:end]


def _fake_npm(tmp_path: Path) -> tuple[Path, Path]:
    """Create a deterministic npm double that records config and then fails."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "npm.json"
    executable = bin_dir / "npm"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["FAKE_NPM_LOG"], "w", encoding="utf-8") as stream:
    json.dump(
        {
            "args": sys.argv[1:],
            "update_notifier": os.environ.get("NPM_CONFIG_UPDATE_NOTIFIER"),
        },
        stream,
    )
print("FAKE_NPM_FAILURE", file=sys.stderr)
raise SystemExit(23)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return bin_dir, log_path


def test_update_notifier_is_disabled_by_default_for_every_locked_target() -> None:
    """Every npm subprocess must inherit the shared quiet-by-default setting."""
    assert "NODE_DEPS_NPM_UPDATE_NOTIFIER ?= false" in MAKEFILE
    for target in TARGETS:
        assert (
            'NPM_CONFIG_UPDATE_NOTIFIER="$(NODE_DEPS_NPM_UPDATE_NOTIFIER)"'
            in _target_recipe(target)
        )


def test_notifier_setting_is_documented_in_make_contract() -> None:
    """Operators must see and explicitly exercise the supported override."""
    entries = {entry["name"]: entry for entry in CONTRACT["targets"]}
    for target in TARGETS:
        entry = entries[target]
        assert "NODE_DEPS_NPM_UPDATE_NOTIFIER" in entry["make_variables"]
        assert "NODE_DEPS_NPM_UPDATE_NOTIFIER=false" in entry["behavior"]

    help_text = MAKEFILE[MAKEFILE.index("help:") : MAKEFILE.index("setup-dirs:")]
    assert help_text.count("NODE_DEPS_NPM_UPDATE_NOTIFIER") >= len(TARGETS)


@pytest.mark.parametrize("target", TARGETS)
def test_default_suppresses_notifier_without_masking_npm_failure(
    target: str, tmp_path: Path
) -> None:
    """A quiet notifier must not turn a failed install, relock, or audit green."""
    bin_dir, log_path = _fake_npm(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["FAKE_NPM_LOG"] = str(log_path)

    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            target,
            "NODE_DEPS_VALIDATE_ONLY=0",
            "NODE_DEPS_NPM_USERCONFIG=/dev/null",
            f"NODE_DEPS_NPM_CACHE={tmp_path / 'cache'}",
            "NODE_DEPS_NPM_REGISTRY=https://registry.invalid",
            "NODE_DEPS_AUDIT_LEVEL=moderate",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "FAKE_NPM_FAILURE" in result.stderr
    invocation = json.loads(log_path.read_text(encoding="utf-8"))
    assert invocation["update_notifier"] == "false"


def test_notifier_can_be_explicitly_reenabled_without_masking_failure(tmp_path: Path) -> None:
    """The default remains an operator-overridable npm boolean configuration."""
    bin_dir, log_path = _fake_npm(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["FAKE_NPM_LOG"] = str(log_path)

    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "node-deps-sync",
            "NODE_DEPS_VALIDATE_ONLY=0",
            "NODE_DEPS_NPM_UPDATE_NOTIFIER=true",
            "NODE_DEPS_NPM_USERCONFIG=/dev/null",
            f"NODE_DEPS_NPM_CACHE={tmp_path / 'cache'}",
            "NODE_DEPS_NPM_REGISTRY=https://registry.invalid",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "FAKE_NPM_FAILURE" in result.stderr
    invocation = json.loads(log_path.read_text(encoding="utf-8"))
    assert invocation["update_notifier"] == "true"
