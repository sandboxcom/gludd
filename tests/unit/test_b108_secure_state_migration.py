"""Regression tests for owner-only migration of Bandit B108 call sites."""

from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path

import pytest
from fastapi import FastAPI


def _assert_private_descendant(path: Path, configured_root: Path) -> None:
    assert path.is_relative_to(configured_root)
    assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_runtime_defaults_share_configurable_project_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.execution.engine import ExecutionEngine
    from general_ludd.infra.deployment import DeploymentManager
    from general_ludd.projects.workspace import ProjectWorkspace
    from general_ludd.sandbox.image_cache import ImageCache

    configured = tmp_path / "runtime"
    monkeypatch.setenv("GLUDD_STATE_DIR", str(configured))
    monkeypatch.chdir(tmp_path)

    engine = ExecutionEngine()
    deployment = DeploymentManager()
    workspace = ProjectWorkspace("alpha")
    workspace.ensure_dirs()
    cache = ImageCache()

    _assert_private_descendant(Path(engine.workspace_path), configured)
    _assert_private_descendant(Path(deployment._working_dir), configured)
    _assert_private_descendant(workspace.root, configured)
    _assert_private_descendant(cache.cache_dir, configured)


def test_role_cloner_default_root_is_private_and_project_namespaced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.stream import RoleCloner

    configured = tmp_path / "runtime"
    collection = tmp_path / "collection"
    (collection / "roles" / "demo").mkdir(parents=True)
    monkeypatch.setenv("GLUDD_STATE_DIR", str(configured))
    cloner = RoleCloner(collection)
    clone = cloner.clone("demo", {})

    _assert_private_descendant(cloner.work_root, configured)
    assert clone.parent == cloner.work_root


def test_integrity_scan_does_not_allow_the_global_temp_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.routers.integrity import _scan_roots

    configured = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("GLUDD_STATE_DIR", str(configured))
    monkeypatch.chdir(project)
    app = FastAPI()

    roots = {Path(item).resolve() for item in _scan_roots(app)}
    assert Path(os.path.realpath(os.getenv("TMPDIR", "/tmp"))) not in roots
    assert any(root.is_relative_to(configured) for root in roots)


def test_physics_output_defaults_are_private(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from general_ludd.cli_physics import add_physics_subparser

    configured = tmp_path / "runtime"
    monkeypatch.setenv("GLUDD_STATE_DIR", str(configured))
    monkeypatch.chdir(tmp_path)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    add_physics_subparser(sub)

    args = parser.parse_args(["physics", "quantum"])
    output = Path(args.output_dir)
    _assert_private_descendant(output, configured)


def test_all_reported_b108_modules_avoid_hardcoded_host_temp_paths() -> None:
    files = (
        "ag2_lifecycle/hooks.py",
        "cli_audit_plugins.py",
        "cli_physics.py",
        "execution/engine.py",
        "execution/situation_store.py",
        "git_automation/ci_ops.py",
        "git_automation/repo.py",
        "git_automation/worktree.py",
        "infra/deployment.py",
        "ornith/sandbox.py",
        "projects/manager.py",
        "projects/workspace.py",
        "routers/integrity.py",
        "routers/reload.py",
        "sandbox/image_cache.py",
        "security/sandboxes/linux_bubblewrap.py",
        "security/sandboxes/macos_seatbelt.py",
        "self_improve/harness.py",
        "self_update/grinding_detector.py",
        "stream/__init__.py",
    )
    source_root = Path(__file__).parents[2] / "src" / "general_ludd"
    for relative in files:
        assert '"/tmp' not in (source_root / relative).read_text(), relative


def test_grinding_detector_safely_reads_legacy_plugin_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import json

    import general_ludd.self_update.grinding_detector as detector

    state_root = tmp_path / "state"
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    legacy = legacy_root / "gludd-floor-streak.json"
    legacy.write_text(json.dumps({"streak": 12, "timestamp": 1.0}))
    monkeypatch.setenv("GLUDD_STATE_DIR", str(state_root))
    monkeypatch.setattr(detector, "_LEGACY_ENFORCEMENT_STATE_ROOT", legacy_root)

    selected = detector._enforcement_state_file(None, "floor-streak.json")
    assert Path(selected) == legacy
    assert stat.S_IMODE(legacy.stat().st_mode) == 0o600
