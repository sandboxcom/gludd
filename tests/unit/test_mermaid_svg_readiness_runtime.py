"""Exercise the production SVG readiness loop without a browser allocation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

RUNTIME = Path(__file__).resolve().parents[2] / "docs/presentation/deck/presentation.js"


def _readiness(
    *, ready_after: int = 0, metadata: bool = True, geometry: str = "ok",
    deadline: int = 5,
) -> dict[str, object]:
    source = RUNTIME.read_text(encoding="utf-8")
    signature = "  async function awaitStableSvg(container, deadline) {"
    function = signature + source.split(signature, 1)[1].split(
        "\n  function initializeMermaid()", 1,
    )[0]
    settings = json.dumps({
        "readyAfter": ready_after, "metadata": metadata,
        "geometry": geometry, "deadline": deadline,
    })
    harness = """
const assert = require("node:assert/strict");
let frames = 0;
let metadataChecks = 0;
let geometryChecks = 0;
const svg = {};
const container = { querySelector(selector) {
  assert.equal(selector, "svg");
  return svg;
} };
const window = { performance: { now: () => frames } };
async function animationFrame() { frames += 1; }
function validSvgMetadata(value) {
  assert.equal(value, container);
  metadataChecks += 1;
  return settings.metadata && frames >= settings.readyAfter;
}
function svgGeometryStatus(value) {
  assert.equal(value, svg);
  geometryChecks += 1;
  return settings.geometry;
}
"""
    execution = """
awaitStableSvg(container, settings.deadline).then((ready) => {
  process.stdout.write(JSON.stringify({ ready, frames, metadataChecks, geometryChecks }));
}).catch((error) => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        ["node", "-"], input=f"const settings = {settings};\n" + harness + function + execution,
        text=True, capture_output=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
    value: object = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("ready_after", [0, 1, 2])
def test_valid_svg_uses_only_frames_actually_needed_for_geometry(ready_after: int) -> None:
    """Already measured Mermaid output must not pay two idle frames per chart."""
    result = _readiness(ready_after=ready_after)
    assert result["ready"] is True
    assert result["frames"] == ready_after
    assert result["geometryChecks"] == 1


@pytest.mark.parametrize("metadata,geometry", [(False, "ok"), (True, "invalid-svg")])
def test_invalid_metadata_or_geometry_remains_bounded_and_failed(
    metadata: bool, geometry: str,
) -> None:
    result = _readiness(metadata=metadata, geometry=geometry, deadline=3)
    assert result["ready"] is False
    assert result["frames"] == 3


@pytest.mark.parametrize("deadline", [0, -1])
def test_expired_deadline_never_checks_or_waits_for_a_valid_svg(deadline: int) -> None:
    result = _readiness(deadline=deadline)
    assert result == {"ready": False, "frames": 0, "metadataChecks": 0, "geometryChecks": 0}


def test_geometry_that_arrives_at_deadline_does_not_become_success() -> None:
    result = _readiness(ready_after=3, deadline=3)
    assert result["ready"] is False
    assert result["frames"] == 3
