#!/usr/bin/env python3
"""Run a bounded native Safari smoke without changing macOS automation settings."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

build_deck = importlib.import_module(
    f"{__package__}.build_deck" if __package__ else "build_deck"
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DRIVER = Path("/usr/bin/safaridriver")
DEFAULT_OUTPUT = Path("/tmp/gludd-presentation-safari")
DRIVER_READY_TIMEOUT_SECONDS = 10.0
DRIVER_POLL_SECONDS = 0.1
DRIVER_HEARTBEAT_SECONDS = 1.0
REMOTE_AUTOMATION_GUIDANCE = (
    "Native Safari acceptance could not start because Remote Automation is disabled. "
    "In Safari, open Safari > Settings > Advanced and enable the developer-features menu "
    "(called 'Show features for web developers' or 'Show Develop menu in menu bar'), then "
    "choose Develop > Allow Remote Automation and rerun presentation-safari-test. "
    "Gludd did not enable or prompt for this setting."
)


class WebDriverFailure(RuntimeError):
    """A bounded native WebDriver operation failed."""


class RemoteAutomationUnavailable(WebDriverFailure):
    """Safari rejected the session because the operator setting is disabled."""


@dataclass(frozen=True)
class SafariPlan:
    """Serializable native Safari execution plan."""

    browser: str
    driver_path: str
    output_root: str
    timeout_seconds: int


def _safe_output(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.as_posix().startswith(("/tmp/gludd-", "/private/tmp/gludd-")):
        raise ValueError("Safari output must be below an explicit /tmp/gludd-* path")
    return resolved


def build_plan(*, driver_path: Path, output_root: Path, timeout_seconds: int) -> SafariPlan:
    """Build a fail-closed native Safari plan without launching anything."""
    if not driver_path.is_absolute():
        raise ValueError("safaridriver path must be absolute")
    if not 20 <= timeout_seconds <= 300:
        raise ValueError("Safari timeout must be between 20 and 300 seconds")
    return SafariPlan(
        browser="native-safari",
        driver_path=str(driver_path),
        output_root=str(_safe_output(output_root)),
        timeout_seconds=timeout_seconds,
    )


def validate_plan(plan: SafariPlan) -> None:
    """Validate static prerequisites without launching Safari or writing output."""
    if sys.platform != "darwin":
        raise RuntimeError("native Safari acceptance requires macOS")
    if not Path(plan.driver_path).is_file():
        raise RuntimeError(f"native Safari driver is not available at {plan.driver_path}")
    for required in (
        ROOT / "docs" / "presentation" / "deck" / "index.html",
        ROOT / "docs" / "presentation" / "deck" / "presentation.js",
    ):
        if not required.is_file():
            raise RuntimeError(f"presentation input is missing: {required.relative_to(ROOT)}")


def session_id(payload: dict[str, Any]) -> str:
    """Extract either W3C or legacy WebDriver session identifiers."""
    value = payload.get("value")
    candidate = value.get("sessionId") if isinstance(value, dict) else None
    candidate = candidate or payload.get("sessionId")
    if not isinstance(candidate, str) or not candidate:
        raise WebDriverFailure("Safari WebDriver response is missing a session id")
    return candidate


def webdriver_value(payload: dict[str, Any]) -> Any:
    """Unwrap one W3C WebDriver value without accepting ambiguous responses."""
    if "value" not in payload:
        raise WebDriverFailure("Safari WebDriver response is missing value")
    return payload["value"]


def is_remote_automation_disabled(message: str) -> bool:
    """Recognize Safari's stable disabled-automation diagnostics."""
    normalized = message.lower()
    return "remote automation" in normalized and (
        "disabled" in normalized or "allow remote automation" in normalized
    )


def write_report(output_root: Path, report: dict[str, Any]) -> Path:
    """Atomically retain only content-free native-browser evidence."""
    output_root.mkdir(parents=True, exist_ok=True)
    target = output_root / "last-report.json"
    temporary = output_root / ".last-report.json.tmp"
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return target


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        value = payload.get("value")
        nested_message = value.get("message") if isinstance(value, dict) else None
        if isinstance(nested_message, str):
            return nested_message
        message = payload.get("message")
        if isinstance(message, str):
            return message
    return "Safari WebDriver request failed"


def _request(
    port: int,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 5,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = {}
        finally:
            exc.close()
        raise WebDriverFailure(_error_message(detail)) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise WebDriverFailure("Safari WebDriver endpoint is unavailable") from exc
    if not isinstance(result, dict):
        raise WebDriverFailure("Safari WebDriver returned a non-object response")
    return result


def _driver_output(process: subprocess.Popen[str]) -> str:
    try:
        stdout, stderr = process.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        return ""
    return "\n".join(part for part in (stdout, stderr) if part)


@contextmanager
def _running_driver(plan: SafariPlan, deadline: float) -> Iterator[int]:
    port = _free_port()
    print(f"presentation-safari phase=driver-start port={port}", flush=True)
    process = subprocess.Popen(
        (plan.driver_path, "--port", str(port)),
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        started_at = time.monotonic()
        readiness_deadline = min(
            deadline,
            started_at + DRIVER_READY_TIMEOUT_SECONDS,
        )
        next_heartbeat = started_at
        while time.monotonic() < readiness_deadline:
            if process.poll() is not None:
                output = _driver_output(process)
                if is_remote_automation_disabled(output):
                    raise RemoteAutomationUnavailable(REMOTE_AUTOMATION_GUIDANCE)
                raise WebDriverFailure("safaridriver exited before becoming ready")
            try:
                _request(port, "GET", "/status", timeout=1)
                break
            except WebDriverFailure:
                now = time.monotonic()
                if now >= next_heartbeat:
                    print(
                        "presentation-safari phase=driver-wait "
                        f"elapsed={max(0.0, now - started_at):.1f}s "
                        f"remaining={max(0.0, readiness_deadline - now):.1f}s",
                        flush=True,
                    )
                    next_heartbeat = now + DRIVER_HEARTBEAT_SECONDS
                time.sleep(
                    min(
                        DRIVER_POLL_SECONDS,
                        max(0.0, readiness_deadline - now),
                    )
                )
        else:
            raise WebDriverFailure("safaridriver readiness timed out")
        yield port
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        _driver_output(process)
        print("presentation-safari phase=driver-stop", flush=True)


@contextmanager
def _served_artifact(output_root: Path) -> Iterator[str]:
    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="artifact-", dir=output_root) as temporary:
        deck = Path(temporary) / "deck"
        build_deck.build_preview_copy(
            deck,
            data={
                "version": "0.1.2-native-safari",
                "git_sha": "b" * 7,
                "git_sha_full": "b" * 40,
                "test_count": 1,
                "role_count": 1,
                "features": [],
                "generated_at": "2026-10-06T00:00:00Z",
            },
        )
        allowlist = json.loads((deck / build_deck.SOURCE_ALLOWLIST).read_text(encoding="utf-8"))
        handler = build_deck.source_request_handler(
            serve_dir=deck,
            repo_root=build_deck.ROOT,
            allowlist=frozenset(allowlist["paths"]),
            url_prefix="/gludd/",
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(
            target=server.serve_forever,
            name="gludd-presentation-native-safari",
            daemon=True,
        )
        thread.start()
        host_value = server.server_address[0]
        host = host_value.decode() if isinstance(host_value, bytes) else host_value
        port = server.server_port
        try:
            yield f"http://{host}:{port}/gludd/"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


SAFARI_ACCEPTANCE_SCRIPT = r"""
const done = arguments[arguments.length - 1];
(async () => {
  const waitUntil = async (predicate, timeoutMs) => {
    const deadline = performance.now() + timeoutMs;
    while (performance.now() < deadline) {
      if (predicate()) return;
      await new Promise((resolve) => requestAnimationFrame(resolve));
    }
    throw new Error('readiness-timeout');
  };
  const started = performance.now();
  await waitUntil(() => window.gluddPresentationReady === true, 15000);
  await waitUntil(() => {
    const health = window.gluddPresentationHealth();
    return health.rendered > 0 && health.pending === 0 && health.rendering === 0 &&
      health.failed === 0 && health.unrendered === 0;
  }, 15000);
  const readinessMs = performance.now() - started;
  const failures = [];
  const slides = Reveal.getSlides();
  for (const [slideIndex, slide] of slides.entries()) {
    const indices = Reveal.getIndices(slide);
    Reveal.slide(indices.h, indices.v);
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    await window.gluddPresentationRenderVisible();
    for (const [chartIndex, chart] of Array.from(slide.querySelectorAll('.mermaid')).entries()) {
      const image = chart.querySelector('img.mermaid-image');
      const rect = image?.getBoundingClientRect() || {width: 0, height: 0};
      const sourceIsSvg = image?.src.startsWith('data:image/svg+xml;charset=utf-8,') || false;
      if (chart.dataset.mermaidState !== 'rendered' || !image || !image.complete ||
          image.naturalWidth <= 0 || image.naturalHeight <= 0 ||
          chart.querySelectorAll('svg').length !== 0 || !sourceIsSvg ||
          !Number.isFinite(rect.width) || !Number.isFinite(rect.height) ||
          rect.width <= 0 || rect.height <= 0) {
        failures.push({chartIndex, slideIndex});
      }
    }
  }
  const sourceLink = document.querySelector('a.source-link[data-source-lines]');
  sourceLink?.click();
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  const dialog = document.getElementById('source-viewer-dialog');
  const editor = window.ace?.edit('source-viewer-editor');
  const sourceViewer = Boolean(dialog?.open && editor?.getReadOnly());
  const health = window.gluddPresentationHealth();
  const clientErrors = typeof window.gluddPresentationClientErrors === 'function'
    ? window.gluddPresentationClientErrors()
    : ['missing-client-error-monitor'];
  done({
    chartCount: document.querySelectorAll('.mermaid').length,
    clientErrorCount: clientErrors.length,
    failed: health.failed,
    geometryFailureCount: failures.length,
    ok: failures.length === 0 && health.failed === 0 && sourceViewer && clientErrors.length === 0,
    readinessMs,
    rendered: health.rendered,
    slideCount: slides.length,
    sourceViewer,
  });
})().catch((error) => done({error: error?.name || 'Error', ok: false}));
"""


def _validate_acceptance(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("ok") is not True:
        category = result.get("error", "acceptance-failed") if isinstance(result, dict) else "invalid-result"
        raise WebDriverFailure(f"native Safari presentation acceptance failed: {category}")
    required = ("chartCount", "rendered", "slideCount", "readinessMs")
    if any(not isinstance(result.get(key), (int, float)) for key in required):
        raise WebDriverFailure("native Safari presentation evidence is incomplete")
    return result


def _run_native_smoke(plan: SafariPlan) -> dict[str, Any]:
    deadline = time.monotonic() + plan.timeout_seconds
    with _served_artifact(Path(plan.output_root)) as url, _running_driver(plan, deadline) as port:
        print("presentation-safari phase=session-create", flush=True)
        try:
            created = _request(
                port,
                "POST",
                "/session",
                payload={"capabilities": {"alwaysMatch": {"browserName": "safari"}}},
                timeout=min(10, max(1, deadline - time.monotonic())),
            )
        except WebDriverFailure as exc:
            if is_remote_automation_disabled(str(exc)):
                raise RemoteAutomationUnavailable(REMOTE_AUTOMATION_GUIDANCE) from exc
            raise
        identifier = session_id(created)
        value = created.get("value")
        capabilities = value.get("capabilities", {}) if isinstance(value, dict) else {}
        timings: dict[str, float] = {}
        last_result: dict[str, Any] = {}
        try:
            _request(
                port,
                "POST",
                f"/session/{identifier}/timeouts",
                payload={"pageLoad": 30_000, "script": 30_000},
            )
            for cache_state in ("cold", "cached"):
                if time.monotonic() >= deadline:
                    raise WebDriverFailure("native Safari smoke exceeded its total deadline")
                print(f"presentation-safari phase=load cache={cache_state}", flush=True)
                started = time.monotonic()
                _request(port, "POST", f"/session/{identifier}/url", payload={"url": url}, timeout=30)
                executed = _request(
                    port,
                    "POST",
                    f"/session/{identifier}/execute/async",
                    payload={"script": SAFARI_ACCEPTANCE_SCRIPT, "args": []},
                    timeout=min(35, max(1, deadline - time.monotonic())),
                )
                last_result = _validate_acceptance(webdriver_value(executed))
                timings[cache_state] = round(time.monotonic() - started, 3)
                print(
                    f"presentation-safari cache={cache_state} readiness_ms="
                    f"{float(last_result['readinessMs']):.1f} charts={int(last_result['rendered'])}",
                    flush=True,
                )
        finally:
            with suppress(WebDriverFailure):
                _request(port, "DELETE", f"/session/{identifier}", timeout=5)
        return {
            "browser": "native-safari",
            "browser_version": str(capabilities.get("browserVersion", "unknown")),
            "cached_seconds": timings["cached"],
            "chart_count": int(last_result["chartCount"]),
            "cold_seconds": timings["cold"],
            "geometry_failures": int(last_result["geometryFailureCount"]),
            "rendered": int(last_result["rendered"]),
            "slide_count": int(last_result["slideCount"]),
            "source_viewer": bool(last_result["sourceViewer"]),
            "status": "passed",
        }


def run_plan(plan: SafariPlan) -> int:
    """Run the native smoke once; unavailable automation is a visible failure."""
    validate_plan(plan)
    try:
        report = _run_native_smoke(plan)
    except RemoteAutomationUnavailable as exc:
        report_path = write_report(
            Path(plan.output_root),
            {
                "browser": "native-safari",
                "operator_action": "enable-remote-automation",
                "status": "remote-automation-disabled",
            },
        )
        print(str(exc), file=sys.stderr, flush=True)
        print(
            f"presentation-safari status=remote-automation-disabled report={report_path}",
            flush=True,
        )
        return 3
    except (WebDriverFailure, subprocess.SubprocessError) as exc:
        print(f"Native Safari acceptance failed: {exc}", file=sys.stderr, flush=True)
        return 1
    report_path = write_report(Path(plan.output_root), report)
    print(f"presentation-safari status=passed report={report_path}", flush=True)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--driver", type=Path, default=DEFAULT_DRIVER)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout-seconds", type=int, default=60)
    args = parser.parse_args()
    plan = build_plan(
        driver_path=args.driver,
        output_root=args.output_root,
        timeout_seconds=args.timeout_seconds,
    )
    if args.validate_only:
        validate_plan(plan)
        print(json.dumps(asdict(plan), indent=2, sort_keys=True))
        return
    raise SystemExit(run_plan(plan))


if __name__ == "__main__":
    main()
