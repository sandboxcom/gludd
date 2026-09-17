"""Regression contract for the S83.139 maintainability refactor."""

import runpy
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast


class _Metrics(Protocol):
    maintainability_index: float


_METRIC_NAMESPACE = runpy.run_path(str(Path(__file__).with_name("test_code_complexity_deep.py")))
SRC_ROOT = cast(Path, _METRIC_NAMESPACE["SRC_ROOT"])
_analyze_file = cast(Callable[[Path], _Metrics], _METRIC_NAMESPACE["_analyze_file"])


TARGET_NAMES = {
    "entropy_codec.py",
    "quadtree.py",
    "semver_v2.py",
    "two_phase_commit.py",
    "unicode_data.py",
}


def test_reported_algorithm_modules_clear_mi_floor() -> None:
    """Keep the reported near-threshold modules out of the low-MI inventory."""
    target_paths = [path for path in SRC_ROOT.rglob("*.py") if path.name in TARGET_NAMES]
    assert {path.name for path in target_paths} == TARGET_NAMES

    results: dict[Path, float] = {
        path.relative_to(SRC_ROOT): _analyze_file(path).maintainability_index for path in target_paths
    }
    violations = {path: mi for path, mi in results.items() if mi < 20.0}

    assert len(violations) <= 1, "At least four targeted modules must reach MI >= 20:\n" + "\n".join(
        f"{path}: MI={mi:.3f}" for path, mi in sorted(violations.items())
    )
