"""S83.139 structural ratchet for near-threshold self-improvement services."""

import runpy
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src" / "general_ludd"
_TARGETS = (
    _SRC / "self_update" / "apply.py",
    _SRC / "self_update" / "applier.py",
    _SRC / "memory" / "cross_conversation.py",
    _SRC / "security" / "ssrf.py",
    _SRC / "writer" / "supervisor.py",
    _SRC / "connectors" / "local_files.py",
)


class _Metrics(Protocol):
    """Subset of the deep-complexity result used by this structural ratchet."""

    path: Path
    maintainability_index: float


_analyze_file = cast(
    Callable[[Path], _Metrics],
    runpy.run_path(str(Path(__file__).with_name("test_code_complexity_deep.py")))["_analyze_file"],
)
_TARGET_METRICS = tuple(_analyze_file(path) for path in _TARGETS)


def _metric_id(metric: _Metrics) -> str:
    """Expose exact MI evidence in each collected case's node id."""
    return f"{metric.path.relative_to(_SRC)}-mi-{metric.maintainability_index:.2f}"


@pytest.mark.parametrize("metric", _TARGET_METRICS, ids=_metric_id)
def test_s83_139_target_services_have_mi_at_least_20(metric: _Metrics) -> None:
    """Keep every selected service above the project's low-MI boundary."""
    assert metric.maintainability_index >= 20.0, (
        f"{metric.path.relative_to(_SRC)}: MI={metric.maintainability_index:.2f}"
    )
