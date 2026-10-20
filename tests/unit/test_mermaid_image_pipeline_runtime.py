"""Exercise serialized Mermaid layout with independent native image completion."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

RUNTIME = Path(__file__).resolve().parents[2] / "docs/presentation/deck/presentation.js"


def _pipeline(*, count: int = 3, failure: str = "", refresh: bool = False) -> dict[str, object]:
    source = RUNTIME.read_text(encoding="utf-8")
    boundaries = (
        ("  function setState(", "\n  function sourceElement("),
        ("  function sourceElement(", "\n  function failureElement("),
        ("  function failureElement(", "\n  function prepareDiagrams("),
        ("  function failDiagram(", "\n  function animationFrame("),
        ("  async function validateVisibleGeometry(", "\n  async function awaitStableSvg("),
        ("  async function renderDiagram(", "\n  function requestRender("),
        ("  function health(", "\n  function registerRevealEvents("),
    )
    functions = "\n".join(
        start + source.split(start, 1)[1].split(end, 1)[0]
        for start, end in boundaries
    )
    settings = json.dumps({"count": count, "failure": failure, "refresh": refresh})
    harness = r"""
const assert = require("node:assert/strict");
const VALID_STATES = new Set(["pending", "rendering", "rendered", "failed"]);
const DIAGRAM_DEADLINE_MS = 10000;
const BOOT_DIAGNOSTIC = "Preparation is incomplete.";
const sourceByDiagram = new WeakMap();
const failureReported = new WeakSet();
const errors = [];
const console = { error: (message) => errors.push(message) };
const status = { hidden: false, textContent: BOOT_DIAGNOSTIC };
const renderCalls = [];
const svgDeadlines = [];
const imageDeadlines = [];
const geometryChecks = [];
const scratchNodes = [];
const waiting = new Map();
const releases = [];
const snapshots = [];
let clock = 0;
let renderSequence = 0;
let activeRenders = 0;
let peakRenders = 0;
let stagesRemoved = 0;
let frames = 0;
let summaryCalls = 0;
const window = { performance: { now: () => clock++ } };
const document = {
  fonts: { ready: Promise.resolve() },
  getElementById(id) { assert.equal(id, "presentation-render-status"); return status; },
};
const nodes = Array.from({length: settings.count}, (_, index) => {
  const fallback = { hidden: false, textContent: "chart " + index };
  const notice = { hidden: true, textContent: "" };
  const host = {
    dataset: {mermaidState: "pending"},
    querySelector(selector) {
      if (selector === ".mermaid-source") return fallback;
      assert.equal(selector, ".mermaid-failure");
      return notice;
    },
  };
  const classes = new Set();
  const diagram = {
    index, host, fallback, notice, image: null,
    dataset: {mermaidState: "pending"},
    classList: {
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
      contains: (name) => classes.has(name),
    },
    closest(selector) { assert.equal(selector, ".mermaid-host"); return host; },
    replaceChildren(image = null) { this.image = image; },
  };
  sourceByDiagram.set(diagram, fallback.textContent);
  return diagram;
});
function diagramNodes() { return nodes; }
function validRenderedVisual(diagram) { return Boolean(diagram.image?.complete); }
function isMeasurable() { return true; }
function positiveGeometry(diagram) {
  geometryChecks.push(diagram.index);
  return validRenderedVisual(diagram);
}
async function animationFrame() { frames += 1; }
function initializeMermaid() {}
function createRenderStage() { return {remove() { stagesRemoved += 1; }}; }
function createRenderScratch() {
  const index = scratchNodes.length;
  const scratch = {
    index, removed: false, innerHTML: "",
    querySelector(selector) {
      assert.equal(selector, "svg");
      return {outerHTML: "svg-" + index};
    },
    remove() { this.removed = true; },
  };
  scratchNodes.push(scratch);
  return scratch;
}
const mermaidApi = {
  async render(id, source, scratch) {
    const index = Number(source.slice(6));
    assert.equal(scratch.index, index);
    assert.equal(window.gluddPresentationActiveDiagram, index);
    assert.equal(id, "gludd-mermaid-" + (index + 1));
    renderCalls.push(index);
    activeRenders += 1;
    peakRenders = Math.max(peakRenders, activeRenders);
    try {
      await Promise.resolve();
      if (settings.failure === "source" && index === 1) throw new Error("invalid syntax");
      return {svg: "<svg/>"};
    } finally {
      activeRenders -= 1;
    }
  },
};
function stabilizeSvgViewport() { return true; }
async function awaitStableSvg(scratch, deadline) {
  svgDeadlines.push([scratch.index, deadline]);
  return true;
}
function viewBoxDimensions() { return {width: 10, height: 20}; }
function createSvgImage(markup) {
  return {index: Number(markup.slice(4)), complete: false};
}
function awaitImageReady(image, deadline) {
  imageDeadlines.push([image.index, deadline]);
  return new Promise((resolve) => { waiting.set(image.index, {image, deadline, resolve}); });
}
function stabilizeImageViewport(_diagram, image) {
  return !(settings.failure === "image-geometry" && image.index === 1);
}
const flush = () => new Promise((resolve) => setImmediate(resolve));
function snapshot() {
  return {
    renderCalls: [...renderCalls],
    states: nodes.map((node) => node.dataset.mermaidState),
    sourcesHidden: nodes.map((node) => node.fallback.hidden),
    noticesVisible: nodes.map((node) => !node.notice.hidden),
    summaryHidden: status.hidden,
    stagesRemoved,
  };
}
"""
    execution = r"""
const nativeSummary = updateFailureSummary;
updateFailureSummary = () => { summaryCalls += 1; nativeSummary(); };
(async () => {
  const running = renderPendingDiagrams();
  await flush();
  const launched = snapshot();
  for (let step = 0; step < settings.count + 1; step += 1) {
    const pending = Array.from(waiting.keys()).sort((left, right) => right - left);
    if (!pending.length) break;
    const index = pending[0];
    const entry = waiting.get(index);
    waiting.delete(index);
    const ready = !(settings.failure === "image" && index === 1);
    entry.image.complete = ready;
    entry.resolve(ready);
    releases.push(index);
    await flush();
    snapshots.push(snapshot());
  }
  await running;
  if (settings.refresh) await renderPendingDiagrams();
  process.stdout.write(JSON.stringify({
    launched, snapshots, releases, renderCalls, peakRenders,
    svgDeadlines, imageDeadlines, geometryChecks, summaryCalls, frames,
    finished: snapshot(), health: health(), errors,
    scratchRemoved: scratchNodes.every((scratch) => scratch.removed),
    activeReleased: !Object.hasOwn(window, "gluddPresentationActiveDiagram"),
  }));
})().catch((error) => { process.stderr.write(error.stack); process.exitCode = 1; });
"""
    completed = subprocess.run(
        ["node", "-"], input=f"const settings = {settings};\n" + harness + functions + execution,
        text=True, capture_output=True, timeout=10, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result: object = json.loads(completed.stdout)
    assert isinstance(result, dict)
    return result


@pytest.mark.parametrize("count", [3, 28])
def test_serialized_layout_does_not_wait_for_previous_image(count: int) -> None:
    """All actual Mermaid calls proceed while decoded images remain independently pending."""
    result = _pipeline(count=count)
    assert result["launched"] == {
        "renderCalls": list(range(count)), "states": ["rendering"] * count,
        "sourcesHidden": [False] * count, "noticesVisible": [False] * count,
        "summaryHidden": False, "stagesRemoved": 0,
    }
    assert result["releases"] == list(reversed(range(count)))
    assert result["peakRenders"] == 1
    assert result["summaryCalls"] == 1
    assert result["finished"] == {
        "renderCalls": list(range(count)), "states": ["rendered"] * count,
        "sourcesHidden": [True] * count, "noticesVisible": [False] * count,
        "summaryHidden": True, "stagesRemoved": 1,
    }
    assert result["geometryChecks"] == list(range(count))
    assert result["health"] == {
        "pending": 0, "rendering": 0, "rendered": count, "failed": 0,
        "unrendered": 0, "errors": {},
    }
    assert result["scratchRemoved"] is True
    assert result["activeReleased"] is True
    assert result["errors"] == []


@pytest.mark.parametrize("failure,category", [
    ("source", "invalid-source"), ("image", "render-timeout"), ("image-geometry", "invalid-geometry"),
])
def test_pipeline_failure_is_visible_while_other_images_are_pending(failure: str, category: str) -> None:
    """A failed phase retains source immediately without waiting for unrelated images."""
    result = _pipeline(failure=failure)
    snapshots = result["snapshots"]
    assert isinstance(snapshots, list)
    failed = [
        snapshot for snapshot in [result["launched"], *snapshots]
        if isinstance(snapshot, dict) and snapshot["states"][1] == "failed"
    ]
    assert failed
    first = failed[0]
    assert first["states"][0] == "rendering"
    assert first["sourcesHidden"][1] is False
    assert first["noticesVisible"][1] is True
    assert first["summaryHidden"] is False
    assert result["renderCalls"] == [0, 1, 2]
    assert result["peakRenders"] == 1
    assert result["health"] == {
        "pending": 0, "rendering": 0, "rendered": 2, "failed": 1,
        "unrendered": 1, "errors": {category: 1},
    }
    assert result["errors"] == [f"[gludd-presentation] Diagram render failed: {category}"]
    assert result["scratchRemoved"] is True
    assert result["activeReleased"] is True


def test_image_completion_retains_each_original_svg_deadline() -> None:
    result = _pipeline()
    assert result["svgDeadlines"] == result["imageDeadlines"]
    deadlines = result["imageDeadlines"]
    assert isinstance(deadlines, list)
    assert [entry[0] for entry in deadlines] == [0, 1, 2]
    assert all(entry[1] >= 10000 for entry in deadlines)
    assert len({entry[1] for entry in deadlines}) == 3
    assert result["peakRenders"] == 1


def test_pipeline_refresh_retains_every_chart_and_revalidates_geometry() -> None:
    result = _pipeline(refresh=True)
    assert result["renderCalls"] == [0, 1, 2]
    assert result["geometryChecks"] == [0, 1, 2, 0, 1, 2]
    assert result["finished"] == {
        "renderCalls": [0, 1, 2], "states": ["rendered"] * 3,
        "sourcesHidden": [True] * 3, "noticesVisible": [False] * 3,
        "summaryHidden": True, "stagesRemoved": 1,
    }
    assert result["errors"] == []
