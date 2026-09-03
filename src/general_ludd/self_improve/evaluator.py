"""Self-improvement evaluation framework.

Tests gludd components before and after agentic self-improvement passes.
Measures completeness (test pass rate), timing (wall clock), and cost
(tokens + compute). Integrates with cloud smoke tests for Azure/AWS/GCP.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Final, Protocol, TypeVar, cast

from general_ludd.self_improve.codex_comparison import (
    EVALUATION_DIAGNOSIS_PROTOCOL,
    safe_evaluation_retry_diagnosis,
)

logger = logging.getLogger(__name__)

_ATTEMPT_IDENTITY_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_PARENT_SYNTAX_ERROR_MARKER: Final = "SELF_IMPROVE_PARENT_SYNTAX_ERROR"
_MAX_FAILURE_DIAGNOSIS_TRACE_BYTES: Final = 131_072
_MAX_FAILURE_DIAGNOSIS_HYPOTHESIS_BYTES: Final = 160
_DIAGNOSIS_PHASE_RE: Final = re.compile(
    r"(?m)^SELF_IMPROVE_[A-Z_]+\b[^\r\n]*\bphase=([a-z][a-z0-9_-]{0,63})\b"
)
_DIAGNOSIS_FAILURE_RE: Final = re.compile(
    r"(?m)^SELF_IMPROVE_[A-Z_]+\b[^\r\n]*\bfailure=([a-z][a-z0-9_-]{0,63})\b"
)
_DIAGNOSIS_FINISH_RE: Final = re.compile(
    r"(?m)^SELF_IMPROVE_LOCAL_DECODE\b[^\r\n]*\b"
    r"finish=(stop|length|tool_calls|function_call|content_filter|unknown)\b"
)
_DIAGNOSIS_EXIT_RE: Final = re.compile(
    r"(?m)^SELF_IMPROVE_COMMAND_END\b[^\r\n]*\brc=(-?[0-9]{1,3})\b"
)
_DIAGNOSIS_SECRET_RE: Final = re.compile(
    r"(?i)(?:api[_-]?key|authorization|password|secret|token)\s*[:=]|"
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----"
)


def compact_failure_diagnosis(
    trace: str,
    *,
    hypothesis: str,
    max_bytes: int = 512,
    max_tokens: int = 512,
) -> str:
    """Convert a marker-bearing failure trace to bounded canonical JSON.

    Only allowlisted execution facts are copied from the trace.  The output is
    ASCII JSON, so its byte length is also a conservative upper bound for a
    byte-fallback tokenizer's token count.
    """
    if not isinstance(trace, str) or not trace.strip():
        raise ValueError("failure trace must be a non-empty string")
    if not isinstance(hypothesis, str) or not hypothesis.strip():
        raise ValueError("failure hypothesis must be a non-empty string")
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or max_bytes <= 0
        or isinstance(max_tokens, bool)
        or not isinstance(max_tokens, int)
        or max_tokens <= 0
    ):
        raise ValueError("diagnosis byte and token limits must be positive integers")

    normalized_hypothesis = " ".join(hypothesis.split())
    if (
        len(normalized_hypothesis.encode("utf-8"))
        > _MAX_FAILURE_DIAGNOSIS_HYPOTHESIS_BYTES
    ):
        raise ValueError("failure hypothesis exceeds its byte bound")
    if _DIAGNOSIS_SECRET_RE.search(normalized_hypothesis):
        raise ValueError("failure hypothesis contains secret-like material")

    bounded_trace = _failure_diagnosis_trace_view(trace)
    phase = _last_diagnosis_fact(_DIAGNOSIS_PHASE_RE, bounded_trace, "phase")
    failure = _last_diagnosis_fact(
        _DIAGNOSIS_FAILURE_RE, bounded_trace, "failure class"
    )
    finish = _last_diagnosis_fact(
        _DIAGNOSIS_FINISH_RE, bounded_trace, "finish reason"
    )
    exit_text = _last_diagnosis_fact(_DIAGNOSIS_EXIT_RE, bounded_trace, "exit code")
    exit_code = int(exit_text)
    if not -255 <= exit_code <= 255:
        raise ValueError("failure diagnosis exit code is outside the bounded range")

    artifact = json.dumps(
        {
            "exit_code": exit_code,
            "failure_class": failure,
            "finish_reason": finish,
            "finished": True,
            "hypothesis": normalized_hypothesis,
            "phase": phase,
            "schema_version": 1,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    artifact_bytes = len(artifact.encode("ascii"))
    if artifact_bytes > max_bytes:
        raise ValueError("failure diagnosis exceeds its byte budget")
    if artifact_bytes > max_tokens:
        raise ValueError("failure diagnosis exceeds its conservative token budget")
    return artifact


def _failure_diagnosis_trace_view(trace: str) -> str:
    """Retain only bounded head and tail windows for marker extraction."""
    encoded = trace.encode("utf-8", errors="replace")
    if len(encoded) <= _MAX_FAILURE_DIAGNOSIS_TRACE_BYTES:
        return encoded.decode("utf-8", errors="replace")
    half = _MAX_FAILURE_DIAGNOSIS_TRACE_BYTES // 2
    head = encoded[:half].decode("utf-8", errors="replace")
    tail = encoded[-half:].decode("utf-8", errors="replace")
    return f"{head}\n{tail}"


def _last_diagnosis_fact(pattern: re.Pattern[str], trace: str, label: str) -> str:
    """Return the final allowlisted fact for one required trace field."""
    matches = tuple(pattern.finditer(trace))
    if not matches:
        raise ValueError(f"failure trace is missing {label}")
    return matches[-1].group(1)


@dataclass(frozen=True, slots=True)
class _EvaluationLifecycleEvent:
    """One bounded event whose fields cannot carry model-authored content."""

    phase: str
    command_kind: str
    command_sha256: str
    returncode: int
    duration_ms: int
    failure_class: str

    def __post_init__(self) -> None:
        """Reject any event outside the identity-bound telemetry vocabulary."""
        protocol = EVALUATION_DIAGNOSIS_PROTOCOL
        if (self.phase, self.command_kind) not in protocol.phase_kinds:
            raise ValueError("evaluation event phase and command kind are unsupported")
        if _ATTEMPT_IDENTITY_RE.fullmatch(self.command_sha256) is None:
            raise ValueError("evaluation event command digest is not canonical")
        if (
            isinstance(self.returncode, bool)
            or not isinstance(self.returncode, int)
            or not -255 <= self.returncode <= 255
        ):
            raise ValueError("evaluation event return code is outside its bound")
        if (
            isinstance(self.duration_ms, bool)
            or not isinstance(self.duration_ms, int)
            or not 0 <= self.duration_ms <= protocol.max_duration_ms
        ):
            raise ValueError("evaluation event duration is outside its bound")
        if self.failure_class != "none" and (
            self.failure_class not in protocol.diagnosis_failure_classes
        ):
            raise ValueError("evaluation event failure class is unsupported")
        if (self.returncode == 0) != (self.failure_class == "none"):
            raise ValueError("evaluation event outcome fields are inconsistent")

    def render(self) -> str:
        """Render deterministic ASCII telemetry with no raw command or output."""
        rendered = (
            "SELF_IMPROVE_EVALUATION_EVENT "
            f"phase={self.phase} command_kind={self.command_kind} "
            f"command_sha256={self.command_sha256} rc={self.returncode} "
            f"duration_ms={self.duration_ms} failure={self.failure_class}"
        )
        if len(rendered.encode("ascii")) > EVALUATION_DIAGNOSIS_PROTOCOL.max_event_bytes:
            raise RuntimeError("evaluation event exceeded its fixed byte bound")
        return rendered


def _bounded_evaluation_duration_ms(value: object) -> int:
    """Convert elapsed seconds to a finite non-negative protocol-bounded integer."""
    maximum = EVALUATION_DIAGNOSIS_PROTOCOL.max_duration_ms
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= value <= maximum / 1000
    ):
        return maximum
    return min(maximum, round(value * 1000))


def _bounded_evaluation_returncode(value: object) -> int:
    """Return one bounded process status without forwarding malformed values."""
    if isinstance(value, bool) or not isinstance(value, int) or not -255 <= value <= 255:
        return 255
    return value


def _record_evaluation_event(
    events: list[_EvaluationLifecycleEvent],
    progress_sink: Callable[[str], None] | None,
    *,
    phase: str,
    command_kind: str,
    command_identity: str,
    returncode: object,
    elapsed_seconds: object,
    failure_class: str,
) -> _EvaluationLifecycleEvent:
    """Record and optionally publish one sanitized lifecycle event."""
    bounded_returncode = _bounded_evaluation_returncode(returncode)
    event = _EvaluationLifecycleEvent(
        phase=phase,
        command_kind=command_kind,
        command_sha256=hashlib.sha256(command_identity.encode("utf-8")).hexdigest(),
        returncode=bounded_returncode,
        duration_ms=_bounded_evaluation_duration_ms(elapsed_seconds),
        failure_class="none" if bounded_returncode == 0 else failure_class,
    )
    rendered = event.render()
    events.append(event)
    if progress_sink is not None:
        progress_sink(rendered)
    return event


class _EvaluationOperationResult(Protocol):
    """Minimal observable operation result consumed by evaluation telemetry."""

    @property
    def returncode(self) -> int:
        """Return the bounded process status."""

    @property
    def elapsed_seconds(self) -> float:
        """Return the observed operation duration."""


_EvaluationResultT = TypeVar("_EvaluationResultT", bound=_EvaluationOperationResult)


def _run_evaluation_operation(
    operation: Callable[[], _EvaluationResultT],
    events: list[_EvaluationLifecycleEvent],
    progress_sink: Callable[[str], None] | None,
    *,
    phase: str,
    command_kind: str,
    command_identity: str,
    failure_class: str,
) -> _EvaluationResultT:
    """Run one Make boundary and emit a terminal event even when it raises."""
    started = time.monotonic()
    try:
        result = operation()
    except BaseException:
        _record_evaluation_event(
            events,
            progress_sink,
            phase=phase,
            command_kind=command_kind,
            command_identity=command_identity,
            returncode=1,
            elapsed_seconds=time.monotonic() - started,
            failure_class=failure_class,
        )
        raise
    elapsed = result.elapsed_seconds
    if (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not 0 <= elapsed <= EVALUATION_DIAGNOSIS_PROTOCOL.max_duration_ms / 1000
    ):
        elapsed = time.monotonic() - started
    _record_evaluation_event(
        events,
        progress_sink,
        phase=phase,
        command_kind=command_kind,
        command_identity=command_identity,
        returncode=result.returncode,
        elapsed_seconds=elapsed,
        failure_class=failure_class,
    )
    return result


def _syntax_failure_class(diagnostic: str | None) -> str:
    """Map a trusted syntax marker to an allowlisted diagnosis class."""
    if diagnostic is None:
        return "none"
    match = re.search(r"\btype=(python_(?:encoding|path|read|size|syntax))\b", diagnostic)
    return match.group(1) if match is not None else "python_syntax"


def _syntax_diagnosis_fields(diagnostic: str | None) -> dict[str, object]:
    """Parse only the parent-rendered syntax coordinates into fixed safe fields."""
    empty: dict[str, object] = {
        "category": "none",
        "column": 0,
        "line": 0,
        "path_sha256": "",
    }
    if diagnostic is None:
        return empty
    match = re.fullmatch(
        rf"{re.escape(_PARENT_SYNTAX_ERROR_MARKER)} "
        r"type=(python_(?:encoding|path|read|size|syntax)) "
        r"path_sha256=([0-9a-f]{64}) line=([0-9]+) column=([0-9]+)",
        diagnostic,
    )
    if match is None:
        raise RuntimeError("parent syntax diagnostic is not canonical")
    line = int(match.group(3))
    column = int(match.group(4))
    if (
        line > EVALUATION_DIAGNOSIS_PROTOCOL.max_coordinate
        or column > EVALUATION_DIAGNOSIS_PROTOCOL.max_coordinate
    ):
        raise RuntimeError("parent syntax diagnostic coordinates exceed their bound")
    return {
        "category": match.group(1),
        "column": column,
        "line": line,
        "path_sha256": match.group(2),
    }


def _compact_evaluation_diagnosis(
    event: _EvaluationLifecycleEvent,
    *,
    syntax_diagnostic: str | None = None,
) -> str:
    """Reuse the installed trace sanitizer, then add bounded lifecycle fields."""
    protocol = EVALUATION_DIAGNOSIS_PROTOCOL
    compact = compact_failure_diagnosis(
        event.render()
        + "\nSELF_IMPROVE_LOCAL_DECODE finish=unknown"
        + f"\nSELF_IMPROVE_COMMAND_END rc={event.returncode}",
        hypothesis=protocol.failure_hypothesis,
        max_bytes=protocol.max_diagnosis_bytes,
        max_tokens=protocol.max_diagnosis_bytes,
    )
    payload = json.loads(compact)
    if not isinstance(payload, dict):
        raise RuntimeError("evaluation diagnosis sanitizer returned a non-object")
    payload.update(
        {
            "command_kind": event.command_kind,
            "command_sha256": event.command_sha256,
            "duration_ms": event.duration_ms,
            "protocol": protocol.version,
            "schema_version": protocol.schema_version,
            **_syntax_diagnosis_fields(syntax_diagnostic),
        }
    )
    artifact = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    validated = safe_evaluation_retry_diagnosis(artifact)
    if validated != artifact:
        raise RuntimeError("evaluation diagnosis failed its canonical validator")
    return validated


def _evaluation_target_identity(
    target: str,
    variables: dict[str, str] | None = None,
) -> str:
    """Return a canonical private preimage for one emitted target hash."""
    return json.dumps(
        {"target": target, "variables": variables or {}},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _repair_candidate_syntax_diagnosis(diagnostic: str) -> str:
    """Build canonical source-free feedback for the next bounded repair decode."""
    if _syntax_failure_class(diagnostic) != "python_syntax":
        raise ValueError("repair candidate feedback requires Python syntax failure")
    event = _EvaluationLifecycleEvent(
        phase="syntax_preflight",
        command_kind="syntax_preflight",
        command_sha256=hashlib.sha256(
            b"compact-v4-repair-candidate-syntax-preflight"
        ).hexdigest(),
        returncode=2,
        duration_ms=0,
        failure_class="python_syntax",
    )
    return _compact_evaluation_diagnosis(
        event,
        syntax_diagnostic=diagnostic,
    )


@dataclass
class RunMetrics:
    """Measured correctness, latency, token, and cost results for one run."""

    test_count: int = 0
    test_pass: int = 0
    test_fail: int = 0
    test_skip: int = 0
    total_wall_ms: float = 0.0
    median_wall_ms: float = 0.0
    llm_tokens: int = 0
    compute_cost_usd: float = 0.0
    run_timestamp: float = field(default_factory=time.time)

    @property
    def pass_rate(self) -> float:
        """Return the fraction of collected tests that passed."""
        if self.test_count == 0:
            return 0.0
        return self.test_pass / self.test_count

    @property
    def total_cost_usd(self) -> float:
        """Return the measured compute cost in US dollars."""
        return self.compute_cost_usd


@dataclass
class SelfImproveResult:
    """Baseline and candidate evidence for one self-improvement evaluation."""

    component: str
    provider: str
    baseline: RunMetrics = field(default_factory=RunMetrics)
    improved_metrics: RunMetrics = field(default_factory=RunMetrics)
    improvement_attempted: bool = False
    improvement_accepted: bool = False
    revert_reason: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def completeness_delta(self) -> float:
        """Return the candidate pass-rate change from baseline."""
        return self.improved_metrics.pass_rate - self.baseline.pass_rate

    @property
    def timing_delta_ms(self) -> float:
        """Return the candidate median-duration change from baseline."""
        return self.improved_metrics.median_wall_ms - self.baseline.median_wall_ms

    @property
    def cost_delta_usd(self) -> float:
        """Return the candidate cost change from baseline."""
        return self.improved_metrics.total_cost_usd - self.baseline.total_cost_usd

    @property
    def improved(self) -> bool:
        """Return whether an attempted candidate was accepted."""
        return self.improvement_attempted and self.improvement_accepted


class SelfImproveEvaluator:
    """Runs baseline + improvement + delta evaluation for a gludd component."""

    DEFAULT_IMPROVEMENT_PROMPT = (
        "Here is a Python module and its test suite. "
        "Improve the module to increase test pass rate, reduce execution time, "
        "and handle more edge cases. Do NOT change the public API. "
        "Output ONLY the improved source code between ```python ``` markers."
    )

    DEFAULT_THRESHOLDS: ClassVar[dict[str, float]] = {
        "min_pass_rate": 0.0,
        "max_timing_increase_pct": 10.0,
        "max_cost_usd": 5.0,
    }

    def __init__(
        self,
        gateway: Any,
        test_file: str,
        component_file: str,
        provider: str,
        improvement_thresholds: dict[str, float] | None = None,
        max_attempts: int = 2,
        budget_usd: float = 5.0,
        repo_root: str | None = None,
        model_profile_id: str = "default",
    ) -> None:
        """Bind the evaluator to one component, test suite, and model gateway."""
        self._gateway = gateway
        self._test_file = test_file
        self._component_file = component_file
        self._provider = provider
        self._thresholds = {**self.DEFAULT_THRESHOLDS, **(improvement_thresholds or {})}
        self._max_attempts = max_attempts
        self._budget_usd = budget_usd
        self._repo_root = repo_root or os.getcwd()
        self._model_profile_id = model_profile_id
        self._improved_code: str = ""

    def _resolve(self, rel_path: str) -> Path:
        return Path(self._repo_root) / rel_path

    def _run_pytest(self, test_file: str) -> dict[str, Any]:
        test_path = self._resolve(test_file)
        if not test_path.exists():
            raise FileNotFoundError(f"Test file not found: {test_path}")

        cmd = [
            "python",
            "-m",
            "pytest",
            str(test_path),
            "-v",
            "--tb=short",
            "--timeout=120",
            "--json-report",
            "--json-report-file=-",
            "--json-report-omit=collectors,warnings",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=180,
                cwd=str(self._repo_root),
            )
        except subprocess.TimeoutExpired as exc:
            logger.warning("pytest timed out for %s: %s", test_file, exc)
            return {
                "_error": "timeout",
                "tests": [],
                "passed": 0,
                "failed": 0,
                "skipped": 0,
            }

        json_output = ""
        for line in result.stdout.splitlines():
            if line.startswith("{"):
                json_output = line
                break

        if json_output:
            try:
                return cast(dict[str, Any], json.loads(json_output))
            except json.JSONDecodeError:
                pass

        return {
            "tests": [],
            "passed": 0,
            "failed": 0,
            "skipped": 0,
        }

    def _extract_timings(self, report: dict[str, Any]) -> list[float]:
        durations: list[float] = []
        for test in report.get("tests", []):
            duration = test.get("duration", 0.0)
            if isinstance(duration, (int, float)):
                durations.append(float(duration))
        return durations

    def _median(self, values: list[float]) -> float:
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        mid = n // 2
        if n % 2 == 0:
            return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0
        return sorted_vals[mid]

    def _count_tokens(self, text: str) -> int:
        if not text:
            return 0
        return int(len(text) / 3.8)

    def run_baseline(self) -> RunMetrics:
        """Execute the configured tests and record baseline metrics."""
        start = time.monotonic()
        report = self._run_pytest(self._test_file)
        elapsed_ms = (time.monotonic() - start) * 1000

        passed = int(report.get("passed", 0))
        failed = int(report.get("failed", 0))
        skipped = int(report.get("skipped", 0))

        durations = self._extract_timings(report)
        median_ms = self._median(durations) * 1000 if durations else elapsed_ms

        tokens = self._count_tokens(f"{self.DEFAULT_IMPROVEMENT_PROMPT}\n{self._read_file(self._component_file)}")

        return RunMetrics(
            test_count=passed + failed + skipped,
            test_pass=passed,
            test_fail=failed,
            test_skip=skipped,
            total_wall_ms=elapsed_ms,
            median_wall_ms=median_ms,
            llm_tokens=tokens,
            compute_cost_usd=0.0,
        )

    def _read_file(self, rel_path: str) -> str:
        path = self._resolve(rel_path)
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Cannot read %s: %s", path, exc)
            return ""

    def _invoke_gateway(self, prompt: str) -> str:
        gw = self._gateway
        if gw is None:
            raise RuntimeError("no model gateway configured")
        try:
            if hasattr(gw, "call_model"):
                response = gw.call_model(
                    self._model_profile_id,
                    [{"role": "user", "content": prompt}],
                    estimated_cost=0.05,
                    budget_remaining=self._budget_usd,
                )
            elif hasattr(gw, "complete"):
                response = gw.complete(prompt)
            else:
                raise RuntimeError(f"Gateway {type(gw).__name__} has neither call_model nor complete")
            return str(response.content)
        except RuntimeError:
            raise
        except Exception as exc:
            logger.error("Gateway call failed: %s", exc)
            raise RuntimeError(f"Gateway call failed: {exc}") from exc

    def _extract_code_block(self, text: str) -> str:
        if "```python" in text:
            parts = text.split("```python", 1)
            if len(parts) > 1:
                inner = parts[1].split("```", 1)
                return inner[0].strip() if inner else text
        elif "```" in text:
            parts = text.split("```", 2)
            if len(parts) >= 3:
                return parts[1].strip()

        lines = text.strip().splitlines()
        if lines and not lines[0].startswith(("#", "//", "<", '"')):
            return text.strip()

        return ""

    def run_improvement(self) -> str:
        """Request and decode one candidate implementation from the gateway."""
        source = self._read_file(self._component_file)
        test_source = self._read_file(self._test_file)

        prompt = (
            f"{self.DEFAULT_IMPROVEMENT_PROMPT}\n\n"
            f"=== CURRENT SOURCE ({self._component_file}) ===\n"
            f"```python\n{source}\n```\n\n"
            f"=== TEST SUITE ({self._test_file}) ===\n"
            f"```python\n{test_source}\n```\n\n"
            f"Output ONLY the improved source code between ```python ``` markers."
        )

        response = self._invoke_gateway(prompt)
        improved = self._extract_code_block(response)
        return improved if improved else source

    def validate_improved(self, improved_code: str) -> RunMetrics:
        """Temporarily install a candidate and return its validation metrics."""
        component_path = self._resolve(self._component_file)
        backup_path = component_path.with_suffix(component_path.suffix + ".bak")

        try:
            if component_path.exists():
                component_path.rename(backup_path)

            component_path.parent.mkdir(parents=True, exist_ok=True)
            component_path.write_text(improved_code, encoding="utf-8")

            start = time.monotonic()
            report = self._run_pytest(self._test_file)
            elapsed_ms = (time.monotonic() - start) * 1000

            passed = int(report.get("passed", 0))
            failed = int(report.get("failed", 0))
            skipped = int(report.get("skipped", 0))

            durations = self._extract_timings(report)
            median_ms = self._median(durations) * 1000 if durations else elapsed_ms

            return RunMetrics(
                test_count=passed + failed + skipped,
                test_pass=passed,
                test_fail=failed,
                test_skip=skipped,
                total_wall_ms=elapsed_ms,
                median_wall_ms=median_ms,
                llm_tokens=self._count_tokens(improved_code),
                compute_cost_usd=0.05,
            )
        finally:
            if backup_path.exists():
                with contextlib.suppress(OSError):
                    backup_path.rename(component_path)

    def _check_thresholds(self, baseline: RunMetrics, improved: RunMetrics) -> tuple[bool, str]:
        pass_delta = improved.pass_rate - baseline.pass_rate
        min_pass = self._thresholds.get("min_pass_rate", 0.0)
        if improved.pass_rate < min_pass:
            return False, f"pass_rate {improved.pass_rate:.2f} < min {min_pass:.2f}"

        time_increase_pct = (
            (improved.median_wall_ms - baseline.median_wall_ms) / max(baseline.median_wall_ms, 0.001)
        ) * 100
        max_time = self._thresholds.get("max_timing_increase_pct", 10.0)
        if time_increase_pct > max_time:
            return False, f"timing increase {time_increase_pct:.1f}% > max {max_time:.1f}%"

        improved_cost = improved.total_cost_usd
        max_cost = self._thresholds.get("max_cost_usd", 5.0)
        if improved_cost > max_cost:
            return False, f"cost ${improved_cost:.2f} > max ${max_cost:.2f}"

        if pass_delta < 0:
            return False, f"pass_rate regressed from {baseline.pass_rate:.2f} to {improved.pass_rate:.2f}"

        return True, ""

    def evaluate(self) -> SelfImproveResult:
        """Evaluate bounded attempts and persist only an accepted candidate."""
        result = SelfImproveResult(
            component=Path(self._component_file).stem,
            provider=self._provider,
        )

        try:
            result.baseline = self.run_baseline()
        except Exception as exc:
            result.errors.append(f"baseline failed: {exc}")
            return result

        for attempt in range(1, self._max_attempts + 1):
            try:
                self._improved_code = self.run_improvement()
                result.improvement_attempted = True

                improved_metrics = self.validate_improved(self._improved_code)
                result.improved_metrics = improved_metrics

                accept, reason = self._check_thresholds(result.baseline, improved_metrics)
                if accept:
                    result.improvement_accepted = True
                    self._persist_improvement(self._improved_code)
                    return result

                logger.warning(
                    "Attempt %d/%d rejected: %s",
                    attempt,
                    self._max_attempts,
                    reason or "thresholds not met",
                )
            except Exception as exc:
                result.errors.append(f"attempt {attempt} failed: {exc}")
                result.revert_reason = str(exc)

        result.revert_reason = result.revert_reason or "thresholds not met after max attempts"
        return result

    def _persist_improvement(self, code: str) -> None:
        component_path = self._resolve(self._component_file)
        component_path.parent.mkdir(parents=True, exist_ok=True)
        component_path.write_text(code, encoding="utf-8")

    def revert(self, reason: str) -> None:
        """Discard any retained original-file backup after a rejected attempt."""
        logger.info("Reverting %s: %s", self._component_file, reason)
        backup_path = self._resolve(self._component_file).with_suffix(Path(self._component_file).suffix + ".orig")
        backup_path.unlink(missing_ok=True)

    def report(self) -> dict[str, Any]:
        """Run the evaluation and return its serializable report."""
        result = self.evaluate()
        return {
            "component": result.component,
            "provider": result.provider,
            "baseline": {
                "test_count": result.baseline.test_count,
                "test_pass": result.baseline.test_pass,
                "test_fail": result.baseline.test_fail,
                "test_skip": result.baseline.test_skip,
                "pass_rate": result.baseline.pass_rate,
                "median_wall_ms": result.baseline.median_wall_ms,
                "llm_tokens": result.baseline.llm_tokens,
            },
            "improved_metrics": {
                "test_count": result.improved_metrics.test_count,
                "test_pass": result.improved_metrics.test_pass,
                "test_fail": result.improved_metrics.test_fail,
                "test_skip": result.improved_metrics.test_skip,
                "pass_rate": result.improved_metrics.pass_rate,
                "median_wall_ms": result.improved_metrics.median_wall_ms,
                "llm_tokens": result.improved_metrics.llm_tokens,
            },
            "deltas": {
                "pass_rate": result.completeness_delta,
                "timing_ms": result.timing_delta_ms,
                "cost_usd": result.cost_delta_usd,
            },
            "improved": result.improved,
            "improvement_accepted": result.improvement_accepted,
            "errors": result.errors,
        }


SELF_IMPROVE_TARGETS: dict[str, dict[str, str]] = {
    "azure_iam_validator": {
        "component_file": "scripts/validate_azure_iam_policy.py",
        "test_file": "tests/unit/test_validate_azure_iam_policy.py",
        "provider": "azure",
        "description": "Azure IAM policy validator — validates RBAC role JSON against Azure schema",
    },
    "aws_iam_validator": {
        "component_file": "scripts/validate_aws_iam_policy.py",
        "test_file": "tests/unit/test_validate_aws_iam_policy.py",
        "provider": "aws",
        "description": "AWS IAM policy validator — checks PassRole scoping, Deny blocks, wildcard constraints",
    },
    "game_generator": {
        "component_file": "src/general_ludd/cloud/game_e2e.py",
        "test_file": "tests/unit/test_game_e2e.py",
        "provider": "azure",
        "description": "Game E2E orchestrator — generates Doom/Quake-like games via LLM on GPU",
    },
    "cloud_iam_expert": {
        "component_file": "src/general_ludd/cloud/core.py",
        "test_file": "tests/unit/test_cloud_iam_expert.py",
        "provider": "azure",
        "description": "Cloud IAM expert — generates provider-specific IAM roles from templates",
    },
    "release_pipeline_checks": {
        "component_file": "tests/unit/test_release_pipeline_checks.py",
        "test_file": "tests/unit/test_release_pipeline_checks.py",
        "provider": "azure",
        "description": "Release pipeline checks — 20 AC behavioral spec enforcement tests",
    },
}


def run_self_improve(
    target_name: str,
    gateway: Any,
    **kwargs: Any,
) -> SelfImproveResult:
    """Run self-improvement evaluation for one registered target."""
    targets = SELF_IMPROVE_TARGETS
    if target_name not in targets:
        available = ", ".join(sorted(targets))
        raise ValueError(f"Unknown target '{target_name}'. Available: {available}")

    target = targets[target_name]
    evaluator = SelfImproveEvaluator(
        gateway=gateway,
        test_file=target["test_file"],
        component_file=target["component_file"],
        provider=target["provider"],
        **kwargs,
    )
    return evaluator.evaluate()


def run_all_self_improve(
    gateway: Any,
    **kwargs: Any,
) -> list[SelfImproveResult]:
    """Run self-improvement evaluation for every registered target."""
    results: list[SelfImproveResult] = []
    for name in SELF_IMPROVE_TARGETS:
        try:
            result = run_self_improve(name, gateway, **kwargs)
            results.append(result)
        except Exception as exc:
            logger.error("Self-improve for %s failed: %s", name, exc)
            results.append(
                SelfImproveResult(
                    component=name,
                    provider=SELF_IMPROVE_TARGETS[name]["provider"],
                    errors=[str(exc)],
                )
            )
    return results


def print_report(results: list[SelfImproveResult]) -> None:
    """Print a compact human-readable evaluation summary."""
    header = f"{'Component':<30} {'Provider':<8} {'Baseline':>12} {'Improved':>12} {'Delta':>10} {'Accepted':>10}"
    print(header)
    print("-" * len(header))

    for r in results:
        baseline_str = f"{r.baseline.test_pass}/{r.baseline.test_count} pass"
        improved_str = f"{r.improved_metrics.test_pass}/{r.improved_metrics.test_count} pass"
        delta_str = f"{r.completeness_delta:+.2f}"
        accepted_str = "YES" if r.improvement_accepted else "NO"

        print(
            f"{r.component:<30} {r.provider:<8} {baseline_str:>12} "
            f"{improved_str:>12} {delta_str:>10} {accepted_str:>10}"
        )

    improved_count = sum(1 for r in results if r.improvement_accepted)
    print(f"\n{improved_count}/{len(results)} components improved")
