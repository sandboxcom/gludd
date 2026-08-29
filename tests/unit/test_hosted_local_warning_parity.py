"""Hosted and local CI replicas must reject every runtime warning."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"


def _recipe(target: str) -> str:
    """Return one Make target recipe."""
    content = MAKEFILE.read_text()
    return content.split(f"{target}:", 1)[1].split("\n\n", 1)[0]


def test_local_named_shard_rejects_warnings() -> None:
    """The full local replica must promote warnings to failures."""
    assert "-W error" in _recipe("test-ci-shard")


def test_local_named_shard_slice_rejects_warnings() -> None:
    """Focused shard reproduction must use the hosted warning policy."""
    assert "-W error" in _recipe("test-ci-shard-slice")


def test_hosted_named_shards_reject_warnings() -> None:
    """The hosted canonical runner must promote warnings to failures."""
    workflow = WORKFLOW.read_text()
    assert "scripts/run_ci_shards_serial.py" in workflow
    assert '--pytest-args="-W error"' in workflow
    runner = (ROOT / "scripts" / "run_ci_shards_serial.py").read_text()
    assert "*pytest_args" in runner
