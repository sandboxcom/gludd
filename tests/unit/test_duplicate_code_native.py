"""Native snapshot metadata and full-corpus changed-file clone admission."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from scripts import check_duplicate_code as guard
from scripts import staged_snapshot as snapshot

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / ".opencode/node_modules/.bin/jscpd"


def _git(root: Path, *arguments: str) -> str:
    """Run bounded native Git only in this test's owned repository."""
    result = subprocess.run(
        ["git", "-C", str(root), *arguments], text=True, capture_output=True,
        check=True, timeout=30,
    )
    return result.stdout.rstrip("\r\n")


def _repository(root: Path) -> tuple[Path, str]:
    """Seed real committed code and an unchanged canonical clone policy."""
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "clone@example.invalid")
    _git(root, "config", "user.name", "Clone Test")
    source = root / "src"
    source.mkdir()
    (root / "scripts").mkdir()
    (root / "scripts/control.py").write_text("CONTROL = True\n", encoding="utf-8")
    policy = json.loads((ROOT / "config/duplicate_code.json").read_text())
    policy["path"] = ["src", "scripts"]
    config = root / "duplicate_code.json"
    config.write_text(json.dumps(policy), encoding="utf-8")
    lines = ["def calculate(value):", "    result = value"]
    for number in range(16):
        lines.append(f"    result = (result * {number + 3}) + {number + 1}")
    lines.append("    return result")
    (source / "original.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "seed")
    return config, _git(root, "rev-parse", "HEAD")


def test_comparison_repository_keeps_exact_base_and_staged_bytes(tmp_path: Path) -> None:
    """The detector gets its own HEAD/index while the real checkout is untouched."""
    _config, base = _repository(tmp_path)
    original = tmp_path / "src/original.py"
    original.write_text("STAGED = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "src/original.py")
    original.write_text("UNSTAGED = 3\n", encoding="utf-8")
    before = (_git(tmp_path, "rev-parse", "HEAD"), (tmp_path / ".git/index").read_bytes(),
              _git(tmp_path, "status", "--porcelain=v1"))
    destination = tmp_path / "comparison"
    snapshot.materialize_index(tmp_path, destination, ("src",))
    snapshot.prepare_comparison_repository(tmp_path, base, destination)
    assert _git(destination, "rev-parse", "HEAD") == base
    assert _git(destination, "show", ":src/original.py").startswith("def calculate(")
    assert (destination / "src/original.py").read_text() == "STAGED = 2\n"
    assert " M src/original.py" in _git(destination, "status", "--porcelain=v1").splitlines()
    assert (tmp_path / ".git/index").read_bytes() == before[1]
    assert _git(tmp_path, "rev-parse", "HEAD") == before[0]
    assert _git(tmp_path, "status", "--porcelain=v1", "--untracked-files=no") == before[2]


def test_comparison_repository_rejects_existing_git_metadata(tmp_path: Path) -> None:
    """Never overwrite an existing index, refs or object ownership."""
    _config, base = _repository(tmp_path)
    with pytest.raises(snapshot.SnapshotError, match="metadata already exists"):
        snapshot.prepare_comparison_repository(tmp_path, base, tmp_path)


@pytest.mark.parametrize("case", ["unrelated", "exact", "renamed", "third", "staged", "committed"])
def test_locked_engine_keeps_new_copy_detection_and_snapshot_isolation(
    tmp_path: Path, case: str,
) -> None:
    """Native changed-file filtering still compares new code with unchanged code."""
    config, base = _repository(tmp_path)
    original = (tmp_path / "src/original.py").read_text()
    copied = tmp_path / "src/copied.py"
    if case == "third":
        copied.write_text(original, encoding="utf-8")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-qm", "accepted second occurrence")
        base = _git(tmp_path, "rev-parse", "HEAD")
        (tmp_path / "src/third.py").write_text(original, encoding="utf-8")
    elif case == "unrelated":
        (tmp_path / "src/changed.py").write_text("DIFFERENT = 42\n", encoding="utf-8")
    else:
        copied.write_text(
            original.replace("calculate", "evaluate").replace("result", "output") if case == "renamed" else original,
            encoding="utf-8",
        )
    _git(tmp_path, "add", ".")
    if case == "staged":
        copied.write_text("UNSTAGED = 'cannot conceal the staged copy'\n", encoding="utf-8")
    source: guard.Source = "staged"
    current = "HEAD"
    if case == "committed":
        _git(tmp_path, "commit", "-qm", "candidate")
        current = _git(tmp_path, "rev-parse", "HEAD")
        copied.write_text("UNSTAGED = 'cannot conceal the committed copy'\n", encoding="utf-8")
        source = "committed"
    result = guard.run_guard(root=tmp_path, config_path=config, engine=ENGINE,
                             source=source, base_ref=base, current_ref=current)
    assert (result == 0) is (case == "unrelated")


def test_locked_engine_supplies_native_changed_file_filter() -> None:
    """The pinned upstream binary must own this behavior, without a custom filter."""
    result = subprocess.run([str(ENGINE), "--help"], text=True, capture_output=True,
                            check=True, timeout=30, env=os.environ.copy())
    assert "--changed" in result.stdout


def test_hosted_shards_install_locked_native_quality_tools_before_testing() -> None:
    """The native clone contract must execute in hosted shards as well as locally."""
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/build.yml").read_text())
    steps = workflow["jobs"]["test-shard"]["steps"]
    installs = [index for index, step in enumerate(steps)
                if "make node-deps-sync" in str(step.get("run", ""))]
    test_index = next(index for index, step in enumerate(steps) if step.get("id") == "test-run")
    assert len(installs) == 1 and installs[0] < test_index
    assert "if" not in steps[installs[0]]
    assert "NODE_DEPS_VALIDATE_ONLY=0" in steps[installs[0]]["run"]
