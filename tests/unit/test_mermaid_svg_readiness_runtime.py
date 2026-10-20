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


def _batch_status(
    *, count: int = 3, failure: str = "", refresh: bool = False,
    invalidate_after: bool = False,
) -> dict[str, object]:
    """Run actual batch/status functions with finite renderer and DOM boundaries."""
    source = RUNTIME.read_text(encoding="utf-8")
    boundaries = (
        ("  function setState(", "\n  function sourceElement("),
        ("  function sourceElement(", "\n  function failureElement("),
        ("  function failureElement(", "\n  function prepareDiagrams("),
        ("  function failDiagram(", "\n  function beforeRender("),
        ("  function beforeRender(", "\n  function afterRender("),
        ("  function afterRender(", "\n  function completeRender("),
        ("  function completeRender(", "\n  function classifyRenderFailure("),
        ("  function classifyRenderFailure(", "\n  function animationFrame("),
        ("  async function validateVisibleGeometry(", "\n  async function awaitStableSvg("),
        ("  async function renderPendingDiagrams(", "\n  function requestRender("),
        ("  function health(", "\n  function updateFailureSummary("),
        ("  function updateFailureSummary(", "\n  function registerRevealEvents("),
    )
    functions = "\n".join(
        start + source.split(start, 1)[1].split(end, 1)[0]
        for start, end in boundaries
    )
    settings = json.dumps({
        "count": count, "failure": failure, "refresh": refresh,
        "invalidateAfter": invalidate_after,
    })
    harness = r"""
const assert = require("node:assert/strict");
const VALID_STATES = new Set(["pending", "rendering", "rendered", "failed"]);
const DIAGRAM_DEADLINE_MS = 10000;
const BOOT_DIAGNOSTIC = "Boot diagnostic remains visible.";
const sourceByDiagram = new WeakMap();
const failureReported = new WeakSet();
const clientErrors = [];
const console = { error: (message) => clientErrors.push(message) };
const status = { hidden: false, textContent: BOOT_DIAGNOSTIC };
let clock = 0;
let frames = 0;
let summaryCalls = 0;
let summaryMetadataChecks = 0;
let summarizing = false;
let stagesRemoved = 0;
let failureSnapshot = null;
const renderCalls = [];
const geometryChecks = [];
const window = { performance: { now: () => clock++ } };
const document = {
  fonts: { ready: Promise.resolve() },
  getElementById(id) {
    assert.equal(id, "presentation-render-status");
    return status;
  },
};
const nodes = Array.from({ length: settings.count }, (_, index) => {
  const fallback = { hidden: false, textContent: "chart " + index };
  const notice = { hidden: true, textContent: "" };
  const host = {
    dataset: { mermaidState: "pending" },
    querySelector(selector) {
      if (selector === ".mermaid-source") return fallback;
      assert.equal(selector, ".mermaid-failure");
      return notice;
    },
  };
  const classes = new Set();
  const diagram = {
    index, host, fallback, notice, visualReady: false,
    dataset: { mermaidState: "pending" },
    classList: {
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
      contains: (name) => classes.has(name),
    },
    closest(selector) {
      assert.equal(selector, ".mermaid-host");
      return host;
    },
    replaceChildren() { this.visualReady = false; },
  };
  sourceByDiagram.set(diagram, fallback.textContent);
  return diagram;
});
function diagramNodes() { return nodes; }
function validRenderedVisual(diagram) {
  if (summarizing) summaryMetadataChecks += 1;
  return diagram.visualReady;
}
function isMeasurable() { return true; }
function positiveGeometry(diagram) {
  geometryChecks.push(diagram.index);
  return diagram.visualReady && !(settings.failure === "geometry" && diagram.index === 1);
}
async function animationFrame() { frames += 1; }
function createRenderStage() { return { remove() { stagesRemoved += 1; } }; }
const mermaidApi = { render() {} };
function initializeMermaid() {}
async function renderDiagram(diagram, stage, deadline) {
  assert.equal(beforeRender(diagram), true);
  assert.equal(typeof stage.remove, "function");
  assert.ok(deadline >= DIAGRAM_DEADLINE_MS);
  assert.equal(window.gluddPresentationActiveDiagram, diagram.index);
  renderCalls.push(diagram.index);
  if (settings.failure === "source" && diagram.index === 1) {
    throw new Error("invalid diagram syntax");
  }
  diagram.visualReady = true;
  afterRender(diagram);
  if (diagram.dataset.mermaidState === "rendering") completeRender(diagram);
}
"""
    execution = r"""
const nativeSummary = updateFailureSummary;
updateFailureSummary = () => {
  summaryCalls += 1;
  summarizing = true;
  try { nativeSummary(); } finally { summarizing = false; }
};
const nativeFailure = failDiagram;
failDiagram = (diagram, category) => {
  nativeFailure(diagram, category);
  if (!failureSnapshot) {
    failureSnapshot = {
      state: diagram.dataset.mermaidState,
      source: diagram.fallback.textContent,
      sourceVisible: !diagram.fallback.hidden,
      noticeVisible: !diagram.notice.hidden,
      summaryVisible: !status.hidden,
      summaryText: status.textContent,
      category,
    };
  }
};
(async () => {
  await renderPendingDiagrams();
  if (settings.refresh) await renderPendingDiagrams();
  if (settings.invalidateAfter) nodes[0].visualReady = false;
  process.stdout.write(JSON.stringify({
    renderCalls, summaryCalls, summaryMetadataChecks, geometryChecks, frames,
    stagesRemoved, failureSnapshot,
    activeReleased: !Object.hasOwn(window, "gluddPresentationActiveDiagram"),
    states: nodes.map((node) => node.dataset.mermaidState),
    hostStates: nodes.map((node) => node.host.dataset.mermaidState),
    sourcesHidden: nodes.map((node) => node.fallback.hidden),
    health: health(), clientErrors,
    status: { hidden: status.hidden, text: status.textContent },
  }));
})().catch((error) => { process.stderr.write(String(error.stack)); process.exitCode = 1; });
"""
    result = subprocess.run(
        ["node", "-"], input=f"const settings = {settings};\n" + harness + functions + execution,
        text=True, capture_output=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
    value: object = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("count", [3, 8])
def test_success_batch_scans_summary_metadata_once(count: int) -> None:
    """Publishing each success must not repeatedly inspect all completed charts."""
    result = _batch_status(count=count)
    assert result["renderCalls"] == list(range(count))
    assert result["states"] == ["rendered"] * count
    assert result["hostStates"] == ["rendered"] * count
    assert result["sourcesHidden"] == [True] * count
    assert result["summaryCalls"] == 1
    assert result["summaryMetadataChecks"] == count
    assert result["health"] == {
        "pending": 0, "rendering": 0, "rendered": count,
        "failed": 0, "unrendered": 0, "errors": {},
    }
    assert result["status"] == {"hidden": True, "text": ""}
    assert result["geometryChecks"] == list(range(count))
    assert result["stagesRemoved"] == 1
    assert result["activeReleased"] is True
    assert result["clientErrors"] == []


@pytest.mark.parametrize("failure,category", [
    ("source", "invalid-source"), ("geometry", "invalid-geometry"),
])
def test_batch_failure_remains_immediate_and_later_charts_finish(
    failure: str, category: str,
) -> None:
    """Source and late-geometry errors retain their immediate readable failure."""
    result = _batch_status(failure=failure)
    assert result["renderCalls"] == [0, 1, 2]
    assert result["states"] == ["rendered", "failed", "rendered"]
    assert result["hostStates"] == result["states"]
    assert result["sourcesHidden"] == [True, False, True]
    assert result["failureSnapshot"] == {
        "state": "failed", "source": "chart 1", "sourceVisible": True,
        "noticeVisible": True, "summaryVisible": True,
        "summaryText": "1 diagram(s) failed; readable source is shown.",
        "category": category,
    }
    assert result["health"] == {
        "pending": 0, "rendering": 0, "rendered": 2,
        "failed": 1, "unrendered": 1, "errors": {category: 1},
    }
    assert result["clientErrors"] == [f"[gludd-presentation] Diagram render failed: {category}"]
    assert result["stagesRemoved"] == 1
    assert result["activeReleased"] is True


def test_completed_batch_refresh_keeps_every_chart_and_revalidates_geometry() -> None:
    result = _batch_status(refresh=True)
    assert result["renderCalls"] == [0, 1, 2]
    assert result["geometryChecks"] == [0, 1, 2, 0, 1, 2]
    assert result["states"] == ["rendered"] * 3
    assert result["sourcesHidden"] == [True] * 3
    assert result["status"] == {"hidden": True, "text": ""}
    assert result["stagesRemoved"] == 1


def test_health_query_still_detects_invalid_visual_after_batch() -> None:
    result = _batch_status(invalidate_after=True)
    assert result["states"] == ["rendered"] * 3
    assert result["health"] == {
        "pending": 0, "rendering": 0, "rendered": 3,
        "failed": 0, "unrendered": 1, "errors": {},
    }
