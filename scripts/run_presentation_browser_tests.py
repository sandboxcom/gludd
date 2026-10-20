#!/usr/bin/env python3
"""Run the mandatory presentation browser acceptance in a bounded process."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess as subprocess
import sys as sys
import tomllib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from importlib import import_module
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from general_ludd.util.owned_process import (
    OwnedProcessPolicy,
    OwnedProcessTimeout,
    run_owned_process,
)

if TYPE_CHECKING:
    import pytest

ROOT = Path(__file__).resolve().parent.parent
TEST_FILE = ROOT / "tests" / "browser" / "test_presentation.py"
DEFAULT_OUTPUT_ROOT = Path("/tmp/gludd-presentation-browser")
DEFAULT_BROWSER_ROOT = Path("/tmp/gludd-playwright-browsers")
SUPPORTED_BROWSERS = frozenset({"chromium", "webkit"})
RUN_LABELS = ("desktop-landscape", "compact-4x3")
VIEWPORT_SIZE_ENV = "GLUDD_PRESENTATION_VIEWPORT_SIZE"
JS_COVERAGE_DIR_ENV = "GLUDD_PRESENTATION_JS_COVERAGE_DIR"
JS_COVERAGE_MANIFEST_ENV = "GLUDD_PRESENTATION_JS_COVERAGE_MANIFEST"
CONTROLLER_RELATIVE = "docs/presentation/deck/presentation.js"
JS_COVERAGE_CONFIG = "config/presentation_javascript_coverage.json"
PROBE_TIMEOUT_SECONDS = 15
PROBE_ACTION_TIMEOUT_MILLISECONDS = 5_000
PROBE_TITLE = "gludd-presentation-browser-probe"
PROBE_URL = "data:text/html,%3Ctitle%3Egludd-presentation-browser-probe%3C/title%3E%3Ch1%3Eready%3C/h1%3E"
WEBKIT_MACOS_DEADLOCK_REFERENCE = "https://github.com/microsoft/playwright/issues/41870"
PROBE_PROCESS_POLICY = OwnedProcessPolicy(
    timeout_seconds=PROBE_TIMEOUT_SECONDS,
    shutdown_grace_seconds=2,
    poll_interval_seconds=0.1,
    heartbeat_interval_seconds=5,
)
LAYOUT_CONTAINMENT_TEST = (
    "tests/browser/test_presentation.py::test_every_slide_keeps_visible_content_inside_the_reveal_canvas"
)
PINNED_PRESENTATION_DEPENDENCIES = frozenset({"playwright==1.63.0", "pytest-playwright==0.9.0"})


@dataclass(frozen=True)
class ViewportRun:
    """One auditable viewport and its intentionally bounded pytest scope."""

    label: str
    viewport_width: int
    viewport_height: int
    scope: str
    output_root: str
    command: tuple[str, ...]
    enforce_viewport: bool


@dataclass(frozen=True)
class BrowserPlan:
    """Auditable, serializable description of one browser acceptance run."""

    browser: str
    browser_root: str
    output_root: str
    test_file: str
    timeout_seconds: int
    runs: tuple[ViewportRun, ...]
    js_coverage: bool = False

    @property
    def command(self) -> tuple[str, ...]:
        """Retain the historical primary-command view for callers and diagnostics."""
        return self.runs[0].command


@dataclass(frozen=True)
class BrowserProbeDiagnostic:
    """Stable machine-readable root-cause record for a failed browser probe."""

    action: str
    browser: str
    detail: str | None
    event: Literal["presentation-browser-root-cause"]
    failure_kind: Literal["timeout", "process-error"]
    phase: Literal["launch-page-probe"]
    platform: str
    reference: str | None
    root_cause: str
    schema_version: int
    timeout_seconds: int


@dataclass(frozen=True)
class BrowserStartupProbeEvidence:
    """Content-free result record for the explicit startup diagnostic mode."""

    browser: str
    event: Literal["presentation-browser-startup-probe"]
    exit_code: int
    failure_kind: Literal["timeout", "process-error"] | None
    phase: Literal["launch-page-probe"]
    platform: str
    reference: str | None
    schema_version: int
    status: Literal["passed", "failed"]
    timeout_seconds: int


def _safe_owned_tmp(path: Path, *, label: str) -> Path:
    """Accept only explicit project-namespaced temporary output roots."""
    resolved = path.resolve()
    prefixes = ("/tmp/gludd-", "/private/tmp/gludd-")
    if not resolved.as_posix().startswith(prefixes):
        raise ValueError(f"{label} must be below an explicit /tmp/gludd-* path")
    return resolved


def build_plan(
    *,
    browser: str,
    browser_root: Path,
    output_root: Path,
    timeout_seconds: int,
    js_coverage: bool = False,
) -> BrowserPlan:
    """Build a bounded pytest-playwright invocation without executing it."""
    if browser not in SUPPORTED_BROWSERS:
        raise ValueError("the presentation contract requires Chromium or WebKit")
    if not 30 <= timeout_seconds <= 900:
        raise ValueError("browser timeout must be between 30 and 900 seconds")
    if not isinstance(js_coverage, bool):
        raise ValueError("JavaScript coverage must be an explicit boolean")
    if js_coverage and browser != "chromium":
        raise ValueError("native JavaScript coverage requires Chromium; WebKit remains uninstrumented")
    safe_browser_root = _safe_owned_tmp(browser_root, label="browser root")
    safe_output_root = _safe_owned_tmp(output_root, label="output root")

    def viewport_run(
        *,
        label: str,
        viewport_width: int,
        viewport_height: int,
        scope: str,
        test_selector: str,
        enforce_viewport: bool = False,
    ) -> ViewportRun:
        run_output = safe_output_root / label
        plugin_arguments = ("-p", "scripts.run_presentation_browser_tests") if enforce_viewport else ()
        command = (
            sys.executable,
            "-m",
            "pytest",
            *plugin_arguments,
            test_selector,
            "-m",
            "presentation_browser",
            "-n",
            "0",
            "--browser",
            browser,
            "--tracing",
            "retain-on-failure",
            "--output",
            str(run_output),
            "--capture=tee-sys",
            "-W",
            "error",
        )
        return ViewportRun(
            label=label,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            scope=scope,
            output_root=str(run_output),
            command=command,
            enforce_viewport=enforce_viewport,
        )

    runs = (
        viewport_run(
            label="desktop-landscape",
            viewport_width=1280,
            viewport_height=720,
            scope="full-suite",
            test_selector=str(TEST_FILE.relative_to(ROOT)),
        ),
        viewport_run(
            label="compact-4x3",
            viewport_width=1024,
            viewport_height=768,
            scope="layout-containment",
            test_selector=LAYOUT_CONTAINMENT_TEST,
            enforce_viewport=True,
        ),
    )
    return BrowserPlan(
        browser=browser,
        browser_root=str(safe_browser_root),
        output_root=str(safe_output_root),
        test_file=str(TEST_FILE.relative_to(ROOT)),
        timeout_seconds=timeout_seconds,
        runs=runs,
        js_coverage=js_coverage,
    )


def _requested_viewport() -> tuple[int, int] | None:
    """Parse the CSS-only viewport override used by the focused pytest replay."""
    rendered = os.environ.get(VIEWPORT_SIZE_ENV)
    if rendered is None:
        return None
    width_text, separator, height_text = rendered.partition("x")
    try:
        width = int(width_text)
        height = int(height_text)
    except ValueError as exc:
        raise RuntimeError(f"invalid {VIEWPORT_SIZE_ENV}: {rendered}") from exc
    if separator != "x" or width <= 0 or height <= 0:
        raise RuntimeError(f"invalid {VIEWPORT_SIZE_ENV}: {rendered}")
    return width, height


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply an exact viewport marker when this module is loaded as a plugin."""
    viewport = _requested_viewport()
    if viewport is None:
        return
    import pytest

    width, height = viewport
    marker = pytest.mark.browser_context_args(viewport={"width": width, "height": height})
    for item in items:
        item.add_marker(marker)


def _json_object(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise RuntimeError(f"{label} must be a JSON object")
    return cast(dict[str, object], value)


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        return _json_object(json.loads(path.read_text(encoding="utf-8")), label=str(path))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid or missing JavaScript coverage input: {path}") from exc


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _verified_source(source_path: Path, expected_sha256: str) -> str:
    try:
        content = source_path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise RuntimeError("JavaScript coverage candidate source changed")
        return content.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("JavaScript coverage candidate source is unreadable") from exc


def _controller_records(payload: object) -> list[dict[str, object]]:
    result = _json_object(payload, label="V8 coverage").get("result")
    if not isinstance(result, list):
        raise RuntimeError("V8 coverage result must be a list")
    records: list[dict[str, object]] = []
    for item in result:
        record = _json_object(item, label="V8 script")
        url = record.get("url")
        if not isinstance(url, str):
            raise RuntimeError("V8 script URL is missing")
        try:
            path = unquote(urlsplit(url).path)
        except ValueError as exc:
            raise RuntimeError("V8 script URL is malformed") from exc
        if Path(path).name == "presentation.js":
            records.append(record)
    return records


def normalize_javascript_coverage(
    payload: object,
    *,
    source_path: Path,
    expected_sha256: str,
    sources: dict[str, str],
) -> dict[str, object]:
    """Validate provenance and retain V8 ranges unchanged for the mature converter."""
    source = _verified_source(source_path, expected_sha256)
    source_length = len(source.encode("utf-16-le")) // 2
    records = _controller_records(payload)
    if not records:
        raise RuntimeError("no executed complete presentation controller coverage")
    normalized: list[dict[str, object]] = []
    for record in records:
        url = record["url"]
        assert isinstance(url, str)
        parsed = urlsplit(url)
        local_http = (
            parsed.scheme == "http"
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            and parsed.username is None
            and parsed.password is None
        )
        local_file = (
            parsed.scheme == "file" and parsed.netloc in {"", "localhost"} and Path(unquote(parsed.path)).is_absolute()
        )
        if not (local_http or local_file):
            raise RuntimeError("controller coverage has an unknown source origin")
        script_id = record.get("scriptId")
        if not isinstance(script_id, str) or sources.get(script_id) != source:
            raise RuntimeError("controller coverage does not match the complete source")
        functions = record.get("functions")
        if not isinstance(functions, list) or not functions:
            raise RuntimeError("controller coverage functions are missing")
        complete_script = False
        for item in functions:
            function = _json_object(item, label="V8 function")
            detailed = function.get("isBlockCoverage")
            if not isinstance(detailed, bool):
                raise RuntimeError("controller coverage must declare V8 block granularity")
            if not isinstance(function.get("functionName"), str):
                raise RuntimeError("controller coverage function name is missing")
            ranges = function.get("ranges")
            if not isinstance(ranges, list) or not ranges:
                raise RuntimeError("controller coverage ranges are missing")
            for item_range in ranges:
                coverage_range = _json_object(item_range, label="V8 range")
                start, end, count = (
                    coverage_range.get("startOffset"),
                    coverage_range.get("endOffset"),
                    coverage_range.get("count"),
                )
                if not all(isinstance(value, int) and not isinstance(value, bool) for value in (start, end, count)):
                    raise RuntimeError("controller coverage ranges must contain integers")
                assert isinstance(start, int) and isinstance(end, int) and isinstance(count, int)
                if not 0 <= start < end <= source_length or count < 0:
                    raise RuntimeError("controller coverage range is outside the exact source")
                complete_script |= start == 0 and end == source_length
            if not detailed and len(ranges) != 1:
                raise RuntimeError("function-only controller coverage has malformed non-block ranges")
        if not complete_script:
            raise RuntimeError("complete controller script range is missing")
        normalized.append({**record, "url": str(source_path.resolve())})
    return {"result": normalized}


class CoverageSession(Protocol):
    """The public CDP surface used by the existing Playwright fixture."""

    def send(
        self,
        method: str,
        params: dict[str, object] | None = None,
    ) -> dict[str, object]: ...


class CoveragePage(Protocol):
    @property
    def context(self) -> object: ...


class CoverageSnapshots(Protocol):
    def snapshot(self, reason: str) -> None: ...


class JavaScriptCoverageCollector:
    """Retain native snapshots; c8 alone merges ranges and calculates coverage."""

    def __init__(
        self,
        session: CoverageSession,
        *,
        source_path: Path,
        expected_sha256: str,
        output_root: Path,
        test_id: str,
    ) -> None:
        self.session = session
        self.source_path = source_path
        self.expected_sha256 = expected_sha256
        self.output_root = output_root
        self.test_id = test_id
        self.identity = uuid4().hex
        self.sequence = 0
        self.started = False
        self.finished = False

    def start(self) -> None:
        _verified_source(self.source_path, self.expected_sha256)
        for directory in ("raw", "normalized"):
            (self.output_root / directory).mkdir(parents=True, exist_ok=True)
        self.session.send("Debugger.enable")
        self.session.send("Profiler.enable")
        self.session.send(
            "Profiler.startPreciseCoverage",
            {"callCount": True, "detailed": True},
        )
        self.started = True

    def snapshot(self, reason: str) -> None:
        if self.finished:
            if reason.endswith("close"):
                return
            raise RuntimeError("JavaScript coverage session already finished")
        if not self.started:
            raise RuntimeError("JavaScript coverage session was not started")
        self.sequence += 1
        stem = f"{self.identity}-{self.sequence:04d}"
        payload = self.session.send("Profiler.takePreciseCoverage")
        raw_path = self.output_root / "raw" / f"{stem}.json"
        raw = {
            **payload,
            "sources": {},
            "source_sha256": self.expected_sha256,
            "test_id": self.test_id,
            "collector_id": self.identity,
            "reason": reason,
        }
        _write_json(raw_path, raw)
        _verified_source(self.source_path, self.expected_sha256)
        sources: dict[str, str] = {}
        records = _controller_records(payload)
        for record in records:
            script_id = record.get("scriptId")
            if not isinstance(script_id, str):
                raise RuntimeError("controller coverage script identifier is missing")
            response = self.session.send("Debugger.getScriptSource", {"scriptId": script_id})
            body = response.get("scriptSource")
            if not isinstance(body, str):
                raise RuntimeError("controller coverage script source is missing")
            sources[script_id] = body
        raw["sources"] = sources
        _write_json(raw_path, raw)
        # A deliberately blocked-CSP test can have no controller. The whole fresh
        # invocation still requires real controller records before native reporting.
        if records:
            normalized = normalize_javascript_coverage(
                payload,
                source_path=self.source_path,
                expected_sha256=self.expected_sha256,
                sources=sources,
            )
            _write_json(self.output_root / "normalized" / f"{stem}.json", normalized)
        if reason.endswith("close"):
            self.session.send("Profiler.stopPreciseCoverage")
            self.finished = True

    def finish(self) -> None:
        if self.finished:
            return
        try:
            self.snapshot("teardown")
        finally:
            self.session.send("Profiler.stopPreciseCoverage")
            self.finished = True


@contextmanager
def coverage_boundaries(page: CoveragePage, collector: CoverageSnapshots) -> Iterator[None]:
    """Snapshot before the actual public calls can destroy a document/isolate."""
    originals: list[tuple[object, str, Callable[..., object]]] = []

    def before(original: Callable[..., object], reason: str) -> Callable[..., object]:
        def invoke(*args: object, **kwargs: object) -> object:
            collector.snapshot(reason)
            return original(*args, **kwargs)

        return invoke

    try:
        for owner, method, reason in (
            (page, "goto", "goto"),
            (page, "reload", "reload"),
            (page, "close", "page.close"),
            (page.context, "close", "context.close"),
        ):
            original = getattr(owner, method)
            if not callable(original):
                raise RuntimeError(f"coverage boundary is not callable: {reason}")
            saved = cast(Callable[..., object], original)
            originals.append((owner, method, saved))
            setattr(owner, method, before(saved, reason))
        yield
    finally:
        for owner, method, original in reversed(originals):
            setattr(owner, method, original)


def _validate_javascript_coverage_tool(*, require_installed: bool) -> str:
    config = _read_json_object(ROOT / JS_COVERAGE_CONFIG)
    required: dict[str, object] = {
        "all": True,
        "src": ["docs/presentation/deck"],
        "include": [CONTROLLER_RELATIVE],
        "exclude": [],
        "extension": [".js"],
        "lines": 85,
        "branches": 85,
        "per-file": False,
        "skip-full": False,
    }
    if set(config) != {*required, "reporter"} or any(
        type(config.get(key)) is not type(value) or config.get(key) != value for key, value in required.items()
    ):
        raise RuntimeError("JavaScript coverage config must select the complete controller and floors")
    reporters = config.get("reporter")
    if (
        not isinstance(reporters, list)
        or not all(isinstance(reporter, str) for reporter in reporters)
        or set(reporters) != {"json", "json-summary", "lcov", "text"}
    ):
        raise RuntimeError("JavaScript coverage native reporters are incomplete")
    manifest = _read_json_object(ROOT / ".opencode" / "package.json")
    dependencies = _json_object(manifest.get("devDependencies"), label="Node devDependencies")
    version = dependencies.get("c8")
    if not isinstance(version, str) or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        raise RuntimeError("c8 must have an exact release pin")
    lock = _read_json_object(ROOT / ".opencode" / "package-lock.json")
    packages = _json_object(lock.get("packages"), label="Node locked packages")
    root_package = _json_object(packages.get(""), label="Node locked root")
    locked_dependencies = _json_object(root_package.get("devDependencies"), label="Node locked devDependencies")
    locked_c8 = _json_object(packages.get("node_modules/c8"), label="Node locked c8")
    if locked_dependencies.get("c8") != version or locked_c8.get("version") != version:
        raise RuntimeError("c8 manifest and lock pins differ")
    if not isinstance(locked_c8.get("integrity"), str) or not locked_c8["integrity"]:
        raise RuntimeError("c8 lock integrity is missing")
    if require_installed:
        package_root = ROOT / ".opencode" / "node_modules" / "c8"
        installed = _read_json_object(package_root / "package.json")
        if installed.get("name") != "c8" or installed.get("version") != version:
            raise RuntimeError("installed c8 does not match the exact lock pin")
        executable = ROOT / ".opencode" / "node_modules" / ".bin" / "c8"
        if (
            not executable.is_file()
            or not executable.resolve().is_relative_to(package_root.resolve())
            or not os.access(executable, os.X_OK)
        ):
            raise RuntimeError("locked c8 executable is absent or outside its package")
    return version


class _NativeGitOwner(Protocol):
    def git(self, root: Path, *args: str, optional: bool = False) -> str: ...


def _native_source_ref() -> dict[str, object]:
    """Reuse the public native Git owner, with package/flat-script imports."""
    owner = cast(
        _NativeGitOwner,
        import_module("scripts.owned_git_checkpoint" if __package__ else "owned_git_checkpoint"),
    )
    commit = owner.git(ROOT, "rev-parse", "--verify", "HEAD^{commit}").strip()
    tree = owner.git(ROOT, "rev-parse", "--verify", f"{commit}^{{tree}}").strip()
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit) is None:
        raise RuntimeError("native JavaScript coverage source ref is invalid")
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", tree) is None:
        raise RuntimeError("native JavaScript coverage source tree is invalid")
    return {
        "commit": commit,
        "tree": tree,
        "branch": owner.git(ROOT, "symbolic-ref", "--quiet", "--short", "HEAD", optional=True).strip(),
        "tracked_status": owner.git(ROOT, "status", "--porcelain=v1", "-z", "--untracked-files=no"),
    }


def _javascript_tool_versions() -> dict[str, object]:
    """Bind the selected runtime, without downloading or guessing tool versions."""
    versions: dict[str, object] = {"python_version": sys.version.split()[0]}
    for package, expected in (
        ("playwright", "1.63.0"),
        ("pytest-playwright", "0.9.0"),
    ):
        try:
            actual = package_version(package)
        except PackageNotFoundError as exc:
            raise RuntimeError(f"missing pinned presentation runtime: {package}") from exc
        if actual != expected:
            raise RuntimeError(f"presentation runtime differs from its exact pin: {package}")
        versions[package] = actual
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("locked c8 requires the prepared native Node runtime")
    try:
        completed = subprocess.run(
            (node, "--version"),
            cwd=ROOT,
            timeout=30,
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("native Node version identity is unavailable") from exc
    actual_node = completed.stdout.strip()
    if completed.returncode or re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", actual_node) is None:
        raise RuntimeError("native Node version identity is invalid")
    versions.update(
        {
            "node_executable": str(Path(node).resolve()),
            "node_version": actual_node,
        }
    )
    return versions


def _require_javascript_source_unchanged(manifest_path: Path) -> None:
    manifest = _read_json_object(manifest_path)
    if manifest.get("source_ref") != _native_source_ref():
        raise RuntimeError("JavaScript coverage native source ref changed")
    if manifest.get("tool_versions") != _javascript_tool_versions():
        raise RuntimeError("JavaScript coverage selected tool runtime changed")
    version = _validate_javascript_coverage_tool(require_installed=True)
    if manifest.get("c8_version") != version:
        raise RuntimeError("JavaScript coverage installed tool changed")


def _prepare_javascript_coverage(plan: BrowserPlan) -> tuple[Path, Path]:
    version = _validate_javascript_coverage_tool(require_installed=True)
    output = Path(plan.output_root) / "javascript-coverage"
    if output.exists():
        raise RuntimeError("JavaScript coverage output already exists; refuse stale evidence")
    output.mkdir(parents=True)
    source_path = (ROOT / CONTROLLER_RELATIVE).resolve()
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    manifest_path = output / "source.json"
    _write_json(
        manifest_path,
        {
            "schema_version": 1,
            "source_path": str(source_path),
            "source_relative_path": CONTROLLER_RELATIVE,
            "source_sha256": source_sha256,
            "c8_version": version,
            "browser": plan.browser,
            "source_ref": _native_source_ref(),
            "tool_versions": _javascript_tool_versions(),
            "c8_executable": str((ROOT / ".opencode" / "node_modules" / ".bin" / "c8").resolve()),
            "source_authority": ("controller SHA-256; native HEAD and tracked modifications recorded separately"),
        },
    )
    return output, manifest_path


def _require_executed_javascript_coverage(output: Path, manifest_path: Path) -> Path:
    manifest = _read_json_object(manifest_path)
    source_path = (ROOT / CONTROLLER_RELATIVE).resolve()
    digest = manifest.get("source_sha256")
    if manifest.get("source_path") != str(source_path) or not isinstance(digest, str):
        raise RuntimeError("JavaScript coverage source manifest is invalid")
    _verified_source(source_path, digest)
    files = sorted((output / "normalized").glob("*.json"))
    if not files:
        raise RuntimeError("no executed complete controller coverage was retained")
    executed = False
    browser_versions: set[str] = set()
    # Detailed authority must come from another retained ScriptCov, not a coarse-only merge.
    coarse_roots: dict[tuple[str, str, int, int], set[tuple[str, int]]] = {}
    detailed_roots: dict[tuple[str, str, int, int], set[tuple[str, int]]] = {}
    for path in files:
        raw = _read_json_object(output / "raw" / path.name)
        bodies = _json_object(raw.get("sources"), label="retained controller sources")
        if not all(isinstance(body, str) for body in bodies.values()):
            raise RuntimeError("retained controller source is invalid")
        if raw.get("source_sha256") != digest:
            raise RuntimeError("retained coverage has a stale source identity")
        expected = normalize_javascript_coverage(
            raw,
            source_path=source_path,
            expected_sha256=digest,
            sources=cast(dict[str, str], bodies),
        )
        if _read_json_object(path) != expected:
            raise RuntimeError("normalized coverage differs from retained native ranges")
        for ordinal, record in enumerate(_controller_records(expected)):
            canonical_source = record["url"]
            assert isinstance(canonical_source, str)
            native_record = (path.name, ordinal)
            functions = record["functions"]
            assert isinstance(functions, list)
            for function in functions:
                verified_function = _json_object(function, label="verified function")
                ranges = verified_function["ranges"]
                assert isinstance(ranges, list)
                root = _json_object(ranges[0], label="verified function root")
                if cast(int, root["count"]) > 0:
                    span = (
                        canonical_source,
                        digest,
                        cast(int, root["startOffset"]),
                        cast(int, root["endOffset"]),
                    )
                    roots = detailed_roots if verified_function["isBlockCoverage"] is True else coarse_roots
                    roots.setdefault(span, set()).add(native_record)
                executed |= any(cast(int, _json_object(item, label="verified range")["count"]) > 0 for item in ranges)
        collector_id = raw.get("collector_id")
        if not isinstance(collector_id, str) or re.fullmatch(r"[0-9a-f]{32}", collector_id) is None:
            raise RuntimeError("controller coverage collector identity is missing")
        runtime = _read_json_object(output / f"runtime-{collector_id}.json")
        browser_version = runtime.get("browser_version")
        if (
            runtime.get("browser") != "chromium"
            or runtime.get("source_sha256") != digest
            or runtime.get("source_manifest_sha256") != hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            or not isinstance(browser_version, str)
            or not browser_version
        ):
            raise RuntimeError("controller coverage browser/runtime binding is missing")
        browser_versions.add(browser_version)
    if not executed:
        raise RuntimeError("no executed controller in the whole fresh coverage invocation")
    if len(browser_versions) != 1:
        raise RuntimeError("controller coverage changed its browser runtime")
    missing = [
        span
        for span, records in coarse_roots.items()
        if any(not (detailed_roots.get(span, set()) - {record}) for record in records)
    ]
    if missing:
        spans = ", ".join(f"[{span[2]},{span[3]})" for span in sorted(missing))
        raise RuntimeError(f"controller coverage lacks an executed detailed witness for coarse roots: {spans}")
    _write_json(
        output / "runtime.json",
        {
            "browser": "chromium",
            "browser_version": next(iter(browser_versions)),
            "source_ref": manifest.get("source_ref"),
            "tool_versions": manifest.get("tool_versions"),
        },
    )
    return source_path


def report_javascript_coverage(output: Path, *, timeout_seconds: int) -> int:
    """Use the locked native tool for conversion and both independent floors."""
    executable = str(ROOT / ".opencode" / "node_modules" / ".bin" / "c8")
    reports = output / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    common = (
        "--merge-async=false",
        "--config",
        str(ROOT / JS_COVERAGE_CONFIG),
        "--temp-directory",
        str(output / "normalized"),
        "--reports-dir",
        str(reports),
    )
    phases = (
        ("report", (executable, "report", *common)),
        (
            "aggregate",
            (
                executable,
                "check-coverage",
                *common,
                "--lines",
                "85",
                "--branches",
                "85",
            ),
        ),
        (
            "per-file",
            (
                executable,
                "check-coverage",
                *common,
                "--lines",
                "75",
                "--branches",
                "75",
                "--per-file",
            ),
        ),
    )
    for phase, command in phases:
        print(
            f"presentation-browser phase=javascript-coverage-{phase} status=starting",
            flush=True,
        )
        status: dict[str, object] = {
            "phase": phase,
            "command": command,
            "cwd": str(ROOT),
            "timeout_seconds": timeout_seconds,
            "stdout": "inherited native observer/CI log",
        }
        try:
            # Inherit native stdout/stderr so the existing run-watched/CI owner
            # streams and retains them. Do not introduce another runner or tee.
            completed = subprocess.run(command, cwd=ROOT, timeout=timeout_seconds, check=False)
            code = completed.returncode
        except subprocess.TimeoutExpired:
            code = 124
        except OSError as exc:
            status["error"] = str(exc)
            code = 127
        status["exit_code"] = code
        _write_json(output / f"c8-{phase}.json", status)
        print(
            f"presentation-browser phase=javascript-coverage-{phase} status=finished exit={code}",
            flush=True,
        )
        if code:
            return code
    return 0


def require_javascript_coverage_report(report_path: Path, *, source_path: Path) -> None:
    """Require native metric presence; the c8 checks own percentage arithmetic."""
    report = _read_json_object(report_path)
    if set(report) != {"total", str(source_path.resolve())}:
        raise RuntimeError("native coverage report must select only the complete controller")
    for key in ("total", str(source_path.resolve())):
        entry = _json_object(report.get(key), label=f"coverage report {key}")
        for metric in ("lines", "branches"):
            values = _json_object(entry.get(metric), label=f"coverage report {metric}")
            for field in ("total", "covered", "skipped"):
                value = values.get(field)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise RuntimeError(f"native coverage {metric}.{field} is missing")
            percent = values.get("pct")
            if (
                not isinstance(percent, (int, float))
                or isinstance(percent, bool)
                or not math.isfinite(percent)
                or not 0 <= percent <= 100
            ):
                raise RuntimeError(f"native coverage {metric}.pct is missing")


def validate_plan(plan: BrowserPlan) -> None:
    """Verify static prerequisites without importing or launching a browser."""
    if not TEST_FILE.is_file():
        raise RuntimeError("presentation browser test file is missing")
    profile = ROOT / "requirements" / "profiles" / "presentation-test" / "pyproject.toml"
    if not profile.is_file():
        raise RuntimeError("presentation dependency profile is missing")
    try:
        profile_data = tomllib.loads(profile.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError("presentation dependency profile is invalid") from exc
    dependencies = profile_data.get("project", {}).get("dependencies")
    if not isinstance(dependencies, list) or not all(isinstance(dependency, str) for dependency in dependencies):
        raise RuntimeError("presentation dependency profile has invalid dependencies")
    for requirement in sorted(PINNED_PRESENTATION_DEPENDENCIES):
        if requirement not in dependencies:
            raise RuntimeError(f"missing pinned presentation dependency: {requirement}")
    if not plan.runs:
        raise RuntimeError("presentation viewport matrix is empty")
    for run in plan.runs:
        if run.command[0] != sys.executable or "shell" in run.command:
            raise RuntimeError("presentation browser command is not a direct bounded argv")
    if plan.js_coverage:
        _validate_javascript_coverage_tool(require_installed=False)


def _browser_label(browser: str) -> str:
    """Return the user-facing name for one allowlisted engine."""
    return {"chromium": "Chromium", "webkit": "WebKit"}[browser]


def _require_browser_executable(plan: BrowserPlan) -> Path:
    """Fail with the owned install target when the requested engine is absent."""
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = plan.browser_root
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed; run make sync") from exc
    with sync_playwright() as playwright:
        executable = Path(getattr(playwright, plan.browser).executable_path)
    if not executable.is_file():
        label = _browser_label(plan.browser)
        raise RuntimeError(f"{label} is not installed; run make presentation-browser-install")
    return executable


def _require_browser_launchable(plan: BrowserPlan) -> None:
    """Launch one engine and prove that its first page can navigate."""
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = plan.browser_root
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed; run make sync") from exc
    try:
        with sync_playwright() as playwright:
            runtime = getattr(playwright, plan.browser).launch(headless=True)
            try:
                page = runtime.new_page()
                page.goto(
                    PROBE_URL,
                    wait_until="domcontentloaded",
                    timeout=PROBE_ACTION_TIMEOUT_MILLISECONDS,
                )
                if page.title() != PROBE_TITLE:
                    raise RuntimeError("browser probe returned the wrong document")
            finally:
                runtime.close()
    except PlaywrightError as exc:
        label = _browser_label(plan.browser)
        raise RuntimeError(f"{label} cannot launch and navigate the probe page") from exc


def _emit_probe_supervisor_event(event: str) -> None:
    """Surface lifecycle progress from the shared owned-process supervisor."""
    print(f"presentation-browser supervisor={event}", flush=True)


def _run_probe_process(plan: BrowserPlan) -> None:
    """Run the browser/page handshake under Gludd's owned process policy."""
    run_owned_process(
        _require_browser_launchable,
        (plan,),
        policy=PROBE_PROCESS_POLICY,
        process_name=f"gludd-presentation-{plan.browser}-probe",
        event_sink=_emit_probe_supervisor_event,
    )


def _probe_failure_diagnostic(
    plan: BrowserPlan,
    *,
    failure_kind: Literal["timeout", "process-error"],
    detail: str | None,
) -> BrowserProbeDiagnostic:
    """Classify one probe failure without exposing a misleading test timeout."""
    is_macos_webkit = sys.platform == "darwin" and plan.browser == "webkit"
    if failure_kind == "timeout" and is_macos_webkit:
        root_cause = (
            "Playwright WebKit did not complete first-page creation and navigation "
            "before the external deadline on macOS; this matches the "
            "NetworkProcess deadlock signature tracked in issue #41870."
        )
        action = (
            "Keep the WebKit acceptance red; compare system Safari with "
            "make presentation-safari-test PRESENTATION_SAFARI_VALIDATE_ONLY=0 "
            "PRESENTATION_SAFARI_DRIVER=/usr/bin/safaridriver "
            "PRESENTATION_SAFARI_OUTPUT=/tmp/gludd-presentation-safari "
            "PRESENTATION_SAFARI_TIMEOUT=60; "
            "then rerun after the macOS or pinned Playwright runtime changes."
        )
        reference: str | None = WEBKIT_MACOS_DEADLOCK_REFERENCE
    else:
        outcome = "timed out" if failure_kind == "timeout" else "raised an error"
        root_cause = (
            f"Playwright {_browser_label(plan.browser)} could not complete the "
            f"isolated launch/page/navigation handshake: probe {outcome}."
        )
        action = (
            "Keep browser acceptance red; verify the pinned cache with "
            "make presentation-browser-install "
            "PRESENTATION_BROWSER_INSTALL_VALIDATE_ONLY=1 "
            "PRESENTATION_BROWSER_ENGINES='chromium webkit' "
            "PRESENTATION_BROWSER_ROOT=/tmp/gludd-playwright-browsers "
            "PRESENTATION_BROWSER_OUTPUT=/tmp/gludd-presentation-browser "
            "PRESENTATION_BROWSER_INSTALL_TIMEOUT=600; repair the runtime before "
            "rerunning acceptance."
        )
        reference = None
    return BrowserProbeDiagnostic(
        action=action,
        browser=plan.browser,
        detail=detail,
        event="presentation-browser-root-cause",
        failure_kind=failure_kind,
        phase="launch-page-probe",
        platform=sys.platform,
        reference=reference,
        root_cause=root_cause,
        schema_version=1,
        timeout_seconds=PROBE_TIMEOUT_SECONDS,
    )


def probe_browser_runtime(plan: BrowserPlan) -> int:
    """Prove launch plus first-page readiness without letting Playwright hang."""
    print(
        f"presentation-browser browser={plan.browser} phase=launch-page-probe status=starting",
        flush=True,
    )
    try:
        _run_probe_process(plan)
    except OwnedProcessTimeout:
        failure_kind: Literal["timeout", "process-error"] = "timeout"
        detail = None
        status = 124
    except Exception as exc:
        failure_kind = "process-error"
        rendered_detail = str(exc).strip()
        detail = rendered_detail.splitlines()[-1][-500:] if rendered_detail else None
        status = 1
    else:
        print(
            f"presentation-browser browser={plan.browser} phase=launch-page-probe status=finished exit=0",
            flush=True,
        )
        return 0
    diagnostic = _probe_failure_diagnostic(
        plan,
        failure_kind=failure_kind,
        detail=detail,
    )
    print(json.dumps(asdict(diagnostic), sort_keys=True), file=sys.stderr, flush=True)
    return status


def run_startup_probe_only(plan: BrowserPlan) -> int:
    """Emit a bounded, content-free readiness result without running pytest."""
    try:
        validate_plan(plan)
        _require_browser_executable(plan)
        _run_probe_process(plan)
    except OwnedProcessTimeout:
        failure_kind: Literal["timeout", "process-error"] | None = "timeout"
        exit_code = 124
    except Exception:
        failure_kind = "process-error"
        exit_code = 1
    else:
        failure_kind = None
        exit_code = 0
    evidence = BrowserStartupProbeEvidence(
        browser=plan.browser,
        event="presentation-browser-startup-probe",
        exit_code=exit_code,
        failure_kind=failure_kind,
        phase="launch-page-probe",
        platform=sys.platform,
        reference=(
            WEBKIT_MACOS_DEADLOCK_REFERENCE
            if failure_kind == "timeout" and sys.platform == "darwin" and plan.browser == "webkit"
            else None
        ),
        schema_version=1,
        status="passed" if exit_code == 0 else "failed",
        timeout_seconds=PROBE_TIMEOUT_SECONDS,
    )
    print(json.dumps(asdict(evidence), sort_keys=True), flush=True)
    return exit_code


def install_browser(plan: BrowserPlan, *, with_dependencies: bool = False) -> int:
    """Install one pinned engine and optionally its host-library contract."""
    validate_plan(plan)
    Path(plan.browser_root).mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PLAYWRIGHT_BROWSERS_PATH"] = plan.browser_root
    dependency_arguments = ("--with-deps",) if with_dependencies else ()
    command = (
        sys.executable,
        "-m",
        "playwright",
        "install",
        *dependency_arguments,
        plan.browser,
    )
    print(
        f"presentation-browser browser={plan.browser} phase=install status=starting",
        flush=True,
    )
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            timeout=plan.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(
            f"presentation-browser browser={plan.browser} phase=install status=timeout",
            flush=True,
        )
        return 124
    status = completed.returncode
    if status == 0:
        _require_browser_executable(plan)
        if with_dependencies:
            status = probe_browser_runtime(plan)
    print(
        f"presentation-browser browser={plan.browser} phase=install status=finished exit={status}",
        flush=True,
    )
    return status


def _execute_viewport_command(plan: BrowserPlan, run: ViewportRun) -> int:
    """Execute one pytest lane inside its already isolated process session."""
    Path(run.output_root).mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PLAYWRIGHT_BROWSERS_PATH"] = plan.browser_root
    environment["GLUDD_PRESENTATION_BROWSER_OUTPUT"] = run.output_root
    environment.pop(JS_COVERAGE_DIR_ENV, None)
    environment.pop(JS_COVERAGE_MANIFEST_ENV, None)
    if plan.js_coverage:
        coverage_dir = Path(plan.output_root).parent / "javascript-coverage"
        environment[JS_COVERAGE_DIR_ENV] = str(coverage_dir)
        environment[JS_COVERAGE_MANIFEST_ENV] = str(coverage_dir / "source.json")
    if run.enforce_viewport:
        environment[VIEWPORT_SIZE_ENV] = f"{run.viewport_width}x{run.viewport_height}"
    else:
        environment.pop(VIEWPORT_SIZE_ENV, None)
    completed = subprocess.run(
        run.command,
        cwd=ROOT,
        env=environment,
        timeout=plan.timeout_seconds + 10,
        check=False,
    )
    return completed.returncode


def _viewport_process_policy(plan: BrowserPlan) -> OwnedProcessPolicy:
    """Return the external deadline that owns pytest and browser descendants."""
    return OwnedProcessPolicy(
        timeout_seconds=plan.timeout_seconds,
        shutdown_grace_seconds=5,
        poll_interval_seconds=0.25,
        heartbeat_interval_seconds=15,
    )


def _run_viewport_process(plan: BrowserPlan, run: ViewportRun) -> int:
    """Run one acceptance lane in a killable, joined process group."""
    return run_owned_process(
        _execute_viewport_command,
        (plan, run),
        policy=_viewport_process_policy(plan),
        process_name=f"gludd-presentation-{plan.browser}-{run.label}",
        event_sink=_emit_probe_supervisor_event,
    )


def run_plan(plan: BrowserPlan, *, run_label: str | None = None) -> int:
    """Run each viewport in an independently bounded owned process group."""
    validate_plan(plan)
    selected_runs = tuple(run for run in plan.runs if run_label is None or run.label == run_label)
    if not selected_runs:
        raise ValueError(f"unknown presentation run label: {run_label}")
    _require_browser_executable(plan)
    probe_status = probe_browser_runtime(plan)
    if probe_status != 0:
        return probe_status
    coverage = _prepare_javascript_coverage(plan) if plan.js_coverage else None
    for run in selected_runs:
        print(
            f"presentation-browser browser={plan.browser} run={run.label} "
            f"viewport={run.viewport_width}x{run.viewport_height} "
            f"scope={run.scope} phase=pytest status=starting",
            flush=True,
        )
        try:
            status = _run_viewport_process(plan, run)
        except (OwnedProcessTimeout, subprocess.TimeoutExpired):
            print(
                f"presentation-browser browser={plan.browser} run={run.label} phase=pytest status=timeout",
                flush=True,
            )
            return 124
        print(
            f"presentation-browser browser={plan.browser} run={run.label} phase=pytest status=finished exit={status}",
            flush=True,
        )
        if status != 0:
            return status
    if coverage is not None:
        output, manifest_path = coverage
        source_path = _require_executed_javascript_coverage(output, manifest_path)
        code = report_javascript_coverage(output, timeout_seconds=plan.timeout_seconds)
        if code:
            return code
        require_javascript_coverage_report(output / "reports" / "coverage-summary.json", source_path=source_path)
        manifest = _read_json_object(manifest_path)
        digest = manifest.get("source_sha256")
        if not isinstance(digest, str):
            raise RuntimeError("JavaScript coverage source digest is missing")
        _verified_source(source_path, digest)
        _require_javascript_source_unchanged(manifest_path)
    return 0


def main() -> None:
    """Validate the execution plan or run it after explicit opt-in."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true", help="print and validate plan without side effects")
    mode.add_argument("--run", action="store_true", help="launch the pinned browser acceptance")
    mode.add_argument("--check-browser", action="store_true", help="verify the pinned browser without writing")
    mode.add_argument(
        "--startup-probe-only",
        action="store_true",
        help="run only the bounded launch/page readiness diagnostic",
    )
    mode.add_argument("--install-browser", action="store_true", help="install the pinned browser into the owned cache")
    mode.add_argument(
        "--install-browser-with-deps",
        action="store_true",
        help="install the pinned browser plus host libraries, then launch-probe it",
    )
    parser.add_argument("--js-coverage", choices=("0", "1"), default="0")
    parser.add_argument("--browser", default="chromium")
    parser.add_argument("--browser-root", type=Path, default=DEFAULT_BROWSER_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument(
        "--run-label",
        choices=RUN_LABELS,
        help="run one named viewport lane; default acceptance still runs both",
    )
    args = parser.parse_args()
    plan = build_plan(
        browser=args.browser,
        browser_root=args.browser_root,
        output_root=args.output_root,
        timeout_seconds=args.timeout_seconds,
        js_coverage=args.js_coverage == "1",
    )
    if args.validate_only:
        validate_plan(plan)
        print(json.dumps(asdict(plan), indent=2, sort_keys=True))
        return
    if args.check_browser:
        validate_plan(plan)
        executable = _require_browser_executable(plan)
        print(json.dumps({"browser": plan.browser, "executable": str(executable), "status": "available"}))
        return
    if args.startup_probe_only:
        raise SystemExit(run_startup_probe_only(plan))
    if args.install_browser:
        raise SystemExit(install_browser(plan))
    if args.install_browser_with_deps:
        raise SystemExit(install_browser(plan, with_dependencies=True))
    raise SystemExit(run_plan(plan, run_label=args.run_label))


if __name__ == "__main__":
    main()
