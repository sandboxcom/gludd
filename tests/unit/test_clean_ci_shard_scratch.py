from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "clean_ci_shard_scratch.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("clean_ci_shard_scratch_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _age_path(path: Path, seconds: int) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_recent_ci_shard_directory_is_not_removed(tmp_path: Path) -> None:
    module = _load_module()
    active = tmp_path / "gludd-ci-shard-other-123"
    active.mkdir()
    (active / "popen-gw0").mkdir()

    result = module.clean_ci_shard_scratch(tmp_root=tmp_path, min_age_seconds=3600)

    assert active.exists()
    assert (active / "popen-gw0").exists()
    assert result["removed"] == []
    assert f"{active}:recent" in result["skipped"]


def test_stale_ci_shard_directory_is_removed(tmp_path: Path) -> None:
    module = _load_module()
    stale = tmp_path / "gludd-ci-shard-unit-3-456"
    stale.mkdir()
    (stale / "node.log").write_text("old", encoding="utf-8")
    _age_path(stale, 7200)

    result = module.clean_ci_shard_scratch(tmp_root=tmp_path, min_age_seconds=3600)

    assert not stale.exists()
    assert str(stale) in result["removed"]


def test_stale_unit_shard_directory_is_removed(tmp_path: Path) -> None:
    module = _load_module()
    stale = tmp_path / "gludd-unit-shard-2-789"
    stale.mkdir()
    _age_path(stale, 7200)

    result = module.clean_ci_shard_scratch(tmp_root=tmp_path, min_age_seconds=3600)

    assert not stale.exists()
    assert str(stale) in result["removed"]


def test_dry_run_reports_stale_directory_without_removing_it(tmp_path: Path) -> None:
    module = _load_module()
    stale = tmp_path / "gludd-ci-shard-unit-2-111"
    stale.mkdir()
    _age_path(stale, 7200)

    result = module.clean_ci_shard_scratch(
        tmp_root=tmp_path,
        min_age_seconds=3600,
        dry_run=True,
    )

    assert stale.exists()
    assert str(stale) in result["removed"]


def test_non_shard_gludd_directory_is_ignored(tmp_path: Path) -> None:
    module = _load_module()
    unrelated = tmp_path / "gludd-worktrees"
    unrelated.mkdir()

    result = module.clean_ci_shard_scratch(tmp_root=tmp_path, min_age_seconds=0)

    assert unrelated.exists()
    assert result == {"removed": [], "skipped": []}
