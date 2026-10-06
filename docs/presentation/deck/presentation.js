/* Gludd presentation runtime: resilient Mermaid lifecycle and local source viewer. */
(function () {
  "use strict";

  const VALID_STATES = new Set(["pending", "rendering", "rendered", "failed"]);
  const RETRYABLE = new Set(["invalid-geometry", "render-timeout"]);
  const RENDER_TIMEOUT_MS = 12000;
  const sourceByDiagram = new WeakMap();
  const failureReported = new WeakSet();
  const queuedReasons = new Set();
  const revealMermaid = typeof window.RevealMermaid === "function"
    ? window.RevealMermaid()
    : window.RevealMermaid;
  let renderQueue = Promise.resolve();
  let queueScheduled = false;
  let revealReady = false;
  let requestedDiagram = null;

  function diagramNodes() {
    return Array.from(document.querySelectorAll(".mermaid"));
  }

  // Every transition is reflected as the observable data-mermaid-state contract.
  function setState(diagram, state, category) {
    if (!VALID_STATES.has(state)) {
      throw new Error("invalid diagram state transition");
    }
    diagram.dataset.mermaidState = state;
    if (category) {
      diagram.dataset.mermaidError = category;
    } else {
      delete diagram.dataset.mermaidError;
    }
    const host = diagram.closest(".mermaid-host");
    if (host) {
      host.dataset.mermaidState = state;
    }
  }

  function sourceElement(diagram) {
    return diagram.closest(".mermaid-host")?.querySelector(".mermaid-source") || null;
  }

  function failureElement(diagram) {
    return diagram.closest(".mermaid-host")?.querySelector(".mermaid-failure") || null;
  }

  function prepareDiagrams() {
    for (const authored of diagramNodes()) {
      if (authored.closest(".mermaid-host")) {
        continue;
      }
      const source = authored.textContent.trim();
      const host = document.createElement("div");
      host.className = "mermaid-host";
      host.dataset.mermaidState = "pending";
      const fallback = document.createElement("pre");
      fallback.className = "mermaid-source";
      fallback.textContent = source;
      const failure = document.createElement("p");
      failure.className = "mermaid-failure";
      failure.setAttribute("role", "status");
      failure.setAttribute("aria-live", "polite");
      failure.hidden = true;
      authored.replaceWith(host);
      authored.textContent = source;
      host.append(fallback, authored, failure);
      sourceByDiagram.set(authored, source);
      setState(authored, "pending");
    }
  }

  function isMeasurable(diagram) {
    const slide = diagram.closest("section");
    const host = diagram.closest(".mermaid-host");
    if (!slide || !host || slide.offsetParent === null || host.offsetParent === null) {
      return false;
    }
    const rect = host.getBoundingClientRect();
    const style = window.getComputedStyle(slide);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }

  function validSvgMetadata(diagram) {
    const svg = diagram.querySelector("svg");
    if (!svg || diagram.querySelectorAll("svg").length !== 1) {
      return false;
    }
    const viewBox = (svg.getAttribute("viewBox") || "").trim().split(/[ ,]+/).map(Number);
    return viewBox.length === 4 && viewBox.every(Number.isFinite) && viewBox[2] > 0 && viewBox[3] > 0;
  }

  function positiveGeometry(diagram) {
    const svg = diagram.querySelector("svg");
    if (!svg || !validSvgMetadata(diagram)) {
      return false;
    }
    const rect = svg.getBoundingClientRect();
    return (
      Number.isFinite(rect.width) &&
      Number.isFinite(rect.height) &&
      rect.width > 0 &&
      rect.height > 0
    );
  }

  function failDiagram(diagram, category) {
    const source = sourceByDiagram.get(diagram) || sourceElement(diagram)?.textContent || "";
    diagram.replaceChildren();
    const fallback = sourceElement(diagram);
    if (fallback) {
      fallback.textContent = source;
      fallback.hidden = false;
    }
    const notice = failureElement(diagram);
    if (notice) {
      notice.textContent = `Diagram render failed (${category}). Source remains available.`;
      notice.hidden = false;
    }
    setState(diagram, "failed", category);
    if (!failureReported.has(diagram)) {
      failureReported.add(diagram);
      console.error(`[gludd-presentation] Diagram render failed: ${category}`);
    }
    updateFailureSummary();
  }

  function beforeRender(diagram) {
    if (diagram !== requestedDiagram) {
      return false;
    }
    const state = diagram.dataset.mermaidState || "pending";
    if (state === "rendered" || state === "rendering") {
      return false;
    }
    if (state === "failed") {
      const category = diagram.dataset.mermaidError || "";
      const attempts = Number(diagram.dataset.mermaidRetries || "0");
      if (!RETRYABLE.has(category) || attempts >= 1) {
        return false;
      }
      diagram.dataset.mermaidRetries = String(attempts + 1);
    }
    if (!isMeasurable(diagram)) {
      return false;
    }
    const source = sourceByDiagram.get(diagram) || sourceElement(diagram)?.textContent || "";
    diagram.textContent = source;
    setState(diagram, "rendering");
    return true;
  }

  function afterRender(diagram) {
    if (!positiveGeometry(diagram)) {
      failDiagram(diagram, "invalid-geometry");
      return;
    }
    const fallback = sourceElement(diagram);
    const notice = failureElement(diagram);
    if (fallback) {
      fallback.hidden = true;
    }
    if (notice) {
      notice.hidden = true;
      notice.textContent = "";
    }
    setState(diagram, "rendered");
    updateFailureSummary();
  }

  function classifyRenderFailure(error) {
    const name = String(error?.name || "").toLowerCase();
    const message = String(error?.message || "").toLowerCase();
    if (name.includes("parse") || message.includes("parse") || message.includes("syntax")) {
      return "invalid-source";
    }
    return "renderer-unavailable";
  }

  function animationFrame() {
    return new Promise((resolve) => window.requestAnimationFrame(() => resolve()));
  }

  function renderEligible(diagram) {
    const state = diagram.dataset.mermaidState || "pending";
    if (state === "pending") {
      return true;
    }
    if (state !== "failed") {
      return false;
    }
    const category = diagram.dataset.mermaidError || "";
    const attempts = Number(diagram.dataset.mermaidRetries || "0");
    return RETRYABLE.has(category) && attempts < 1;
  }

  async function awaitRenderOutcome(diagram) {
    const source = (sourceByDiagram.get(diagram) || "").trim();
    const deadline = window.performance.now() + RENDER_TIMEOUT_MS;
    while (window.performance.now() < deadline) {
      await animationFrame();
      const state = diagram.dataset.mermaidState;
      if (state === "rendered" || state === "failed") {
        return;
      }
      if (
        state === "rendering" &&
        !diagram.querySelector("svg") &&
        diagram.textContent.trim() !== source
      ) {
        failDiagram(diagram, "invalid-source");
        return;
      }
    }
    const error = new Error("bounded render timeout");
    error.name = "RenderTimeout";
    throw error;
  }

  async function renderOne(diagram) {
    requestedDiagram = diagram;
    const originalConsoleError = console.error;
    console.error = (...args) => {
      const detail = args[1];
      if (detail && typeof detail === "object" && "graphDefinition" in detail) {
        return;
      }
      originalConsoleError(...args);
    };
    try {
      revealMermaid.init(window.Reveal);
      await awaitRenderOutcome(diagram);
    } catch (error) {
      const category = error?.name === "RenderTimeout" ? "render-timeout" : classifyRenderFailure(error);
      failDiagram(diagram, category);
    } finally {
      requestedDiagram = null;
      console.error = originalConsoleError;
    }
  }

  async function renderVisibleDiagrams() {
    await animationFrame();
    if (!revealMermaid || typeof revealMermaid.init !== "function") {
      for (const diagram of diagramNodes()) {
        if (diagram.dataset.mermaidState !== "rendered") {
          failDiagram(diagram, "renderer-unavailable");
        }
      }
      return;
    }
    let candidate = diagramNodes().find(
      (diagram) => renderEligible(diagram) && isMeasurable(diagram),
    );
    while (candidate) {
      await renderOne(candidate);
      candidate = diagramNodes().find(
        (diagram) => renderEligible(diagram) && isMeasurable(diagram),
      );
    }
  }

  function requestRender(reason) {
    queuedReasons.add(reason);
    if (queueScheduled) {
      return renderQueue;
    }
    queueScheduled = true;
    renderQueue = renderQueue
      .catch(() => undefined)
      .then(async () => {
        queueScheduled = false;
        queuedReasons.clear();
        await renderVisibleDiagrams();
      });
    return renderQueue;
  }

  function health() {
    const summary = { pending: 0, rendering: 0, rendered: 0, failed: 0, unrendered: 0, errors: {} };
    for (const diagram of diagramNodes()) {
      const state = VALID_STATES.has(diagram.dataset.mermaidState) ? diagram.dataset.mermaidState : "pending";
      summary[state] += 1;
      if (state !== "rendered" || !validSvgMetadata(diagram)) {
        summary.unrendered += 1;
      }
      const category = diagram.dataset.mermaidError;
      if (category) {
        summary.errors[category] = (summary.errors[category] || 0) + 1;
      }
    }
    return summary;
  }

  function updateFailureSummary() {
    const status = document.getElementById("presentation-render-status");
    if (!status) {
      return;
    }
    const summary = health();
    status.hidden = summary.failed === 0;
    status.textContent = summary.failed ? `${summary.failed} diagram(s) failed; readable source is shown.` : "";
  }

  function registerRevealEvents() {
    const schedule = (event) => requestRender(event.type);
    for (const eventName of [
      "ready",
      "slidechanged",
      "slidetransitionend",
      "fragmentshown",
      "overviewhidden",
    ]) {
      window.Reveal.on(eventName, schedule);
    }
    window.addEventListener("resize", () => requestRender("resize"));
    window.addEventListener("beforeprint", () => requestRender("print"));
  }

  const modeByExtension = Object.freeze({
    ".css": "css",
    ".html": "html",
    ".js": "javascript",
    ".json": "json",
    ".md": "markdown",
    ".py": "python",
    ".sh": "sh",
    ".toml": "text",
    ".yaml": "yaml",
    ".yml": "yaml",
  });
  let aceEditor = null;
  let viewerTrigger = null;

  function localOrigin() {
    return ["localhost", "127.0.0.1", "::1", "[::1]"].includes(window.location.hostname);
  }

  function sourceMode(path) {
    const name = path.toLowerCase();
    const extension = Object.keys(modeByExtension).find((candidate) => name.endsWith(candidate));
    return extension ? modeByExtension[extension] : "text";
  }

  function ensureAce() {
    if (aceEditor) {
      return aceEditor;
    }
    if (!window.ace) {
      throw new Error("local code viewer unavailable");
    }
    window.ace.config.set("basePath", "./vendor/ace");
    aceEditor = window.ace.edit("source-viewer-editor", {
      readOnly: true,
      highlightActiveLine: true,
      showPrintMargin: false,
      theme: "ace/theme/tomorrow_night",
    });
    aceEditor.session.setUseWorker(false);
    return aceEditor;
  }

  function sourceRange(anchor, text) {
    const value = anchor.dataset.sourceLines || "1-1";
    const match = /^(\d+)-(\d+)$/.exec(value);
    const lineCount = text.split("\n").length;
    const start = match ? Number(match[1]) : 1;
    const end = match ? Number(match[2]) : start;
    if (start < 1 || end < start || end > lineCount) {
      throw new Error("invalid authored source range");
    }
    return { start, end };
  }

  async function openSourceViewer(anchor) {
    const path = anchor.dataset.sourcePath;
    if (!path || path.endsWith("/")) {
      window.location.assign(anchor.href);
      return;
    }
    const endpoint = new URL("/__gludd_source__", window.location.origin);
    endpoint.searchParams.set("path", path);
    let response;
    try {
      response = await window.fetch(endpoint, { credentials: "same-origin" });
    } catch (_error) {
      window.location.assign(anchor.href);
      return;
    }
    if (!response.ok) {
      window.location.assign(anchor.href);
      return;
    }
    const text = await response.text();
    const range = sourceRange(anchor, text);
    const dialog = document.getElementById("source-viewer-dialog");
    const title = document.getElementById("source-viewer-path");
    const github = document.getElementById("source-viewer-github");
    const editor = ensureAce();
    title.textContent = path;
    github.href = anchor.href;
    editor.session.setMode(`ace/mode/${sourceMode(path)}`);
    editor.setValue(text, -1);
    const Range = window.ace.require("ace/range").Range;
    const lastLine = text.split("\n")[range.end - 1] || "";
    const selection = new Range(range.start - 1, 0, range.end - 1, lastLine.length);
    editor.selection.setSelectionRange(selection);
    editor.gotoLine(range.start, 0, true);
    editor.selection.setSelectionRange(selection);
    viewerTrigger = anchor;
    dialog.showModal();
    editor.focus();
  }

  function closeSourceViewer() {
    const dialog = document.getElementById("source-viewer-dialog");
    if (dialog?.open) {
      dialog.close();
    }
    viewerTrigger?.focus();
    viewerTrigger = null;
  }

  function installSourceViewer() {
    if (!localOrigin()) {
      return;
    }
    document.addEventListener("click", (event) => {
      const anchor = event.target.closest?.("a.source-link[data-source-path]");
      if (
        !anchor ||
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey ||
        anchor.target === "_blank"
      ) {
        return;
      }
      event.preventDefault();
      openSourceViewer(anchor).catch(() => window.location.assign(anchor.href));
    });
    document.getElementById("source-viewer-close")?.addEventListener("click", closeSourceViewer);
    document.getElementById("source-viewer-dialog")?.addEventListener("cancel", (event) => {
      event.preventDefault();
      closeSourceViewer();
    });
  }

  prepareDiagrams();
  installSourceViewer();
  window.gluddPresentationHealth = health;
  window.gluddPresentationRenderVisible = () => requestRender("explicit");
  window.gluddPresentationRefresh = () => {
    prepareDiagrams();
    return requestRender("refresh");
  };

  const plugins = [window.RevealHighlight, window.RevealNotes].filter(Boolean);
  if (revealMermaid) {
    plugins.push(revealMermaid);
  }
  const mermaidPlugin = {
    beforeRender,
    afterRender,
  };
  window.Reveal.initialize({
    hash: true,
    center: true,
    width: 1150,
    height: 820,
    margin: 0.045,
    minScale: 0.2,
    maxScale: 1.8,
    overflow: "scroll",
    transition: "slide",
    transitionSpeed: "default",
    backgroundTransition: "fade",
    slideNumber: "c/t",
    mermaid: {
      startOnLoad: false,
      securityLevel: "strict",
      theme: "base",
      themeVariables: {
        primaryColor: "#161b22",
        primaryTextColor: "#c9d1d9",
        primaryBorderColor: "#58a6ff",
        lineColor: "#58a6ff",
        secondaryColor: "#1f6feb",
        tertiaryColor: "#238636",
        background: "#161b22",
        mainBkg: "#161b22",
        secondBkg: "#1f6feb",
        fontSize: "16px",
      },
    },
    mermaidPlugin,
    plugins,
  }).then(() => {
    revealReady = true;
    registerRevealEvents();
    requestRender("ready");
  }).catch(() => {
    for (const diagram of diagramNodes()) {
      failDiagram(diagram, revealMermaid ? "invalid-source" : "renderer-unavailable");
    }
  });

  Object.defineProperty(window, "gluddPresentationReady", {
    get() {
      return revealReady;
    },
  });
}());
