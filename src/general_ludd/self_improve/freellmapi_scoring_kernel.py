"""Bounded in-process execution of FreeLLMAPI's pure scoring factors.

The kernel is deliberately shadow-only. Gludd remains the outer router and
supplies an already-computed fallback for every candidate. A missing or
faulting JavaScript engine therefore cannot change model selection.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import NoReturn, Protocol, cast

FREELLMAPI_SCORING_UPSTREAM_COMMIT = "780a7d8d6dcbc818eb10ec17da210635b569ae22"
FREELLMAPI_SCORING_BUNDLE_DIGEST = (
    "d3078364c02f482909681e21895c4e86dc11cc66c1da7ae2007ad35b096ddf7d"
)

_MAX_CANDIDATES = 256
_MAX_PAYLOAD_BYTES = 256 * 1024
_MAX_BUNDLE_BYTES = 64 * 1024
_MEMORY_LIMIT_BYTES = 16 * 1024 * 1024
_TIME_LIMIT_SECONDS = 0.05
_STACK_LIMIT_BYTES = 256 * 1024
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_VENDOR_ROOT = Path(__file__).resolve().parent / "vendor" / "freellmapi"
_BUNDLE_PATH = _VENDOR_ROOT / "scoring_kernel.js"
_MANIFEST_PATH = _VENDOR_ROOT / "scoring_kernel.json"
_EXPECTED_FACTOR_KEYS = frozenset(
    {
        "candidate_identity_digest",
        "reliability_alpha",
        "reliability_beta",
        "expected_reliability",
        "speed",
        "headroom",
        "rate_window_headroom",
        "rate_limit",
    }
)


class _QuickJSContext(Protocol):
    """Small capability boundary exposed to the scoring kernel."""

    def set_memory_limit(self, limit: int) -> None: ...

    def set_time_limit(self, limit: float) -> None: ...

    def set_max_stack_size(self, limit: int) -> None: ...

    def eval(self, source: str) -> object: ...


class FreeLLMScoringSource(StrEnum):
    """Source of a returned factor batch."""

    GLUDD_FALLBACK = "gludd_fallback"
    FREELLMAPI_SHADOW = "freellmapi_shadow"


class FreeLLMScoringFault(StrEnum):
    """Content-free reason that the exact Gludd fallback was used."""

    DISABLED = "disabled"
    CIRCUIT_OPEN = "circuit_open"
    ENGINE_UNAVAILABLE = "engine_unavailable"
    ENGINE_FAILURE = "engine_failure"
    INVALID_RESULT = "invalid_result"


@dataclass(frozen=True, slots=True)
class FreeLLMScoringInput:
    """One identity-only candidate input accepted by the vendored kernel."""

    candidate_identity_digest: str
    successes: float
    failures: float
    community_successes: float
    community_failures: float
    tokens_per_second: float
    ttfb_ms: float | None
    used_tokens: float
    budget_tokens: float
    rate_window_used_fraction: float | None
    rate_limit_penalty: float

    def __post_init__(self) -> None:
        """Reject raw identities and unbounded or non-finite measurements."""
        _require_digest(self.candidate_identity_digest)
        for name in (
            "successes",
            "failures",
            "community_successes",
            "community_failures",
            "tokens_per_second",
            "used_tokens",
            "budget_tokens",
            "rate_limit_penalty",
        ):
            _require_finite_nonnegative(name, getattr(self, name))
        if self.ttfb_ms is not None:
            _require_finite_nonnegative("ttfb_ms", self.ttfb_ms)
        if self.rate_window_used_fraction is not None:
            _require_unit_interval(
                "rate_window_used_fraction", self.rate_window_used_fraction
            )
        if self.rate_limit_penalty > 10.0:
            raise ValueError("rate_limit_penalty must be at most 10")


@dataclass(frozen=True, slots=True)
class FreeLLMScoringFactors:
    """Pure factors returned by either Gludd or the shadow kernel."""

    candidate_identity_digest: str
    reliability_alpha: float
    reliability_beta: float
    expected_reliability: float
    speed: float
    headroom: float
    rate_window_headroom: float
    rate_limit: float

    def __post_init__(self) -> None:
        """Reject factors that cannot safely participate in routing."""
        _require_digest(self.candidate_identity_digest)
        _require_finite_positive("reliability_alpha", self.reliability_alpha)
        _require_finite_positive("reliability_beta", self.reliability_beta)
        for name in (
            "expected_reliability",
            "speed",
            "headroom",
            "rate_window_headroom",
            "rate_limit",
        ):
            _require_unit_interval(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class FreeLLMScoringBatchResult:
    """Bounded scoring result with no raw model, prompt, or error content."""

    factors: tuple[FreeLLMScoringFactors, ...]
    source: FreeLLMScoringSource
    fault: FreeLLMScoringFault | None
    bundle_digest: str
    candidate_count: int


@dataclass(frozen=True, slots=True)
class FreeLLMScoringTrace:
    """Content-free operational trace emitted for every scoring attempt."""

    source: FreeLLMScoringSource
    fault: FreeLLMScoringFault | None
    bundle_digest: str
    candidate_count: int


class FreeLLMScoringKernel:
    """Execute the allowlisted pure kernel in a fresh constrained context."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        context_factory: Callable[[], _QuickJSContext] | None = None,
        trace_sink: Callable[[FreeLLMScoringTrace], None] | None = None,
    ) -> None:
        """Configure an opt-in shadow kernel and content-free trace sink.

        Args:
            enabled: Whether the pinned shadow kernel may execute.
            context_factory: Optional constrained-engine factory for testing.
            trace_sink: Optional receiver for content-free operation traces.
        """
        self._enabled = enabled
        self._context_factory = context_factory or _default_context_factory
        self._trace_sink = trace_sink
        self._circuit_open = False

    def factor_batch(
        self,
        candidates: Sequence[FreeLLMScoringInput],
        *,
        fallback: Sequence[FreeLLMScoringFactors],
    ) -> FreeLLMScoringBatchResult:
        """Return shadow factors or the caller's exact validated fallback."""
        candidate_batch, fallback_batch = _validate_batch(candidates, fallback)
        if not self._enabled:
            return self._fallback(fallback_batch, FreeLLMScoringFault.DISABLED)
        if self._circuit_open:
            return self._fallback(fallback_batch, FreeLLMScoringFault.CIRCUIT_OPEN)

        payload = _encode_payload(candidate_batch)
        try:
            source = _load_verified_bundle()
            context = self._context_factory()
            _constrain_context(context)
            context.eval(source)
            invocation = (
                "__gludd_freellmapi_factor_batch("
                + json.dumps(payload, ensure_ascii=True)
                + ")"
            )
            raw_result = context.eval(invocation)
            factors = _decode_factors(raw_result, candidate_batch)
        except ModuleNotFoundError:
            self._circuit_open = True
            return self._fallback(
                fallback_batch, FreeLLMScoringFault.ENGINE_UNAVAILABLE
            )
        except (KeyError, TypeError, ValueError):
            self._circuit_open = True
            return self._fallback(fallback_batch, FreeLLMScoringFault.INVALID_RESULT)
        except Exception:
            self._circuit_open = True
            return self._fallback(fallback_batch, FreeLLMScoringFault.ENGINE_FAILURE)

        result = FreeLLMScoringBatchResult(
            factors=factors,
            source=FreeLLMScoringSource.FREELLMAPI_SHADOW,
            fault=None,
            bundle_digest=FREELLMAPI_SCORING_BUNDLE_DIGEST,
            candidate_count=len(factors),
        )
        self._emit(result)
        return result

    def _fallback(
        self,
        factors: tuple[FreeLLMScoringFactors, ...],
        fault: FreeLLMScoringFault,
    ) -> FreeLLMScoringBatchResult:
        result = FreeLLMScoringBatchResult(
            factors=factors,
            source=FreeLLMScoringSource.GLUDD_FALLBACK,
            fault=fault,
            bundle_digest=FREELLMAPI_SCORING_BUNDLE_DIGEST,
            candidate_count=len(factors),
        )
        self._emit(result)
        return result

    def _emit(self, result: FreeLLMScoringBatchResult) -> None:
        if self._trace_sink is None:
            return
        trace = FreeLLMScoringTrace(
            source=result.source,
            fault=result.fault,
            bundle_digest=result.bundle_digest,
            candidate_count=result.candidate_count,
        )
        try:
            self._trace_sink(trace)
        except Exception:
            return


def _default_context_factory() -> _QuickJSContext:
    module = importlib.import_module("quickjs")
    factory = getattr(module, "Context", None)
    if not callable(factory):
        raise RuntimeError("QuickJS context unavailable")
    return cast(_QuickJSContext, factory())


def _constrain_context(context: _QuickJSContext) -> None:
    context.set_memory_limit(_MEMORY_LIMIT_BYTES)
    context.set_time_limit(_TIME_LIMIT_SECONDS)
    context.set_max_stack_size(_STACK_LIMIT_BYTES)


def _load_verified_bundle() -> str:
    source = _BUNDLE_PATH.read_bytes()
    if not source or len(source) > _MAX_BUNDLE_BYTES:
        raise ValueError("FreeLLMAPI scoring bundle has an invalid size")
    digest = hashlib.sha256(source).hexdigest()
    if digest != FREELLMAPI_SCORING_BUNDLE_DIGEST:
        raise ValueError("FreeLLMAPI scoring bundle identity drifted")
    manifest_object = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(manifest_object, dict):
        raise ValueError("FreeLLMAPI scoring manifest must be an object")
    if manifest_object.get("bundle_sha256") != digest:
        raise ValueError("FreeLLMAPI scoring manifest identity drifted")
    if manifest_object.get("upstream_commit") != FREELLMAPI_SCORING_UPSTREAM_COMMIT:
        raise ValueError("FreeLLMAPI scoring upstream identity drifted")
    return source.decode("utf-8")


def _validate_batch(
    candidates: Sequence[FreeLLMScoringInput],
    fallback: Sequence[FreeLLMScoringFactors],
) -> tuple[
    tuple[FreeLLMScoringInput, ...], tuple[FreeLLMScoringFactors, ...]
]:
    candidate_batch = tuple(candidates)
    fallback_batch = tuple(fallback)
    if not candidate_batch:
        raise ValueError("scoring batch must contain at least one candidate")
    if len(candidate_batch) > _MAX_CANDIDATES:
        raise ValueError("scoring batch may contain at most 256 candidates")
    if len(candidate_batch) != len(fallback_batch):
        raise ValueError("candidate and fallback counts must match")
    for candidate, factor in zip(candidate_batch, fallback_batch, strict=True):
        if not isinstance(candidate, FreeLLMScoringInput):
            raise TypeError("candidate batch contains an invalid value")
        if not isinstance(factor, FreeLLMScoringFactors):
            raise TypeError("fallback batch contains an invalid value")
        if candidate.candidate_identity_digest != factor.candidate_identity_digest:
            raise ValueError("candidate and fallback identity must match")
    return candidate_batch, fallback_batch


def _encode_payload(candidates: tuple[FreeLLMScoringInput, ...]) -> str:
    objects = [
        {
            "candidate_identity_digest": candidate.candidate_identity_digest,
            "successes": candidate.successes,
            "failures": candidate.failures,
            "community_successes": candidate.community_successes,
            "community_failures": candidate.community_failures,
            "tokens_per_second": candidate.tokens_per_second,
            "ttfb_ms": candidate.ttfb_ms,
            "used_tokens": candidate.used_tokens,
            "budget_tokens": candidate.budget_tokens,
            "rate_window_used_fraction": candidate.rate_window_used_fraction,
            "rate_limit_penalty": candidate.rate_limit_penalty,
        }
        for candidate in candidates
    ]
    payload = json.dumps(objects, separators=(",", ":"), sort_keys=True)
    if len(payload.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError("scoring payload exceeds 256 KiB")
    return payload


def _decode_factors(
    raw_result: object,
    candidates: tuple[FreeLLMScoringInput, ...],
) -> tuple[FreeLLMScoringFactors, ...]:
    if not isinstance(raw_result, str):
        raise TypeError("scoring result must be JSON text")
    if len(raw_result.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError("scoring result exceeds 256 KiB")
    decoded = json.loads(raw_result, parse_constant=_reject_json_constant)
    if not isinstance(decoded, list) or len(decoded) != len(candidates):
        raise ValueError("scoring result count drifted")
    return tuple(
        _decode_factor(value, candidate)
        for value, candidate in zip(decoded, candidates, strict=True)
    )


def _decode_factor(
    value: object,
    candidate: FreeLLMScoringInput,
) -> FreeLLMScoringFactors:
    if not isinstance(value, Mapping) or set(value) != _EXPECTED_FACTOR_KEYS:
        raise ValueError("scoring factor schema drifted")
    identity = value["candidate_identity_digest"]
    if not isinstance(identity, str):
        raise TypeError("scoring factor identity must be text")
    if identity != candidate.candidate_identity_digest:
        raise ValueError("scoring factor identity drifted")
    return FreeLLMScoringFactors(
        candidate_identity_digest=identity,
        reliability_alpha=_json_number(value["reliability_alpha"]),
        reliability_beta=_json_number(value["reliability_beta"]),
        expected_reliability=_json_number(value["expected_reliability"]),
        speed=_json_number(value["speed"]),
        headroom=_json_number(value["headroom"]),
        rate_window_headroom=_json_number(value["rate_window_headroom"]),
        rate_limit=_json_number(value["rate_limit"]),
    )


def _json_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("scoring factor must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("scoring factor must be finite")
    return number


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"invalid JSON numeric constant: {value}")


def _require_digest(value: object) -> None:
    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        raise ValueError("candidate identity must be a lowercase SHA-256 digest")


def _require_finite_nonnegative(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return number


def _require_finite_positive(name: str, value: object) -> None:
    if _require_finite_nonnegative(name, value) <= 0.0:
        raise ValueError(f"{name} must be positive")


def _require_unit_interval(name: str, value: object) -> None:
    if _require_finite_nonnegative(name, value) > 1.0:
        raise ValueError(f"{name} must be at most 1")


__all__ = [
    "FREELLMAPI_SCORING_BUNDLE_DIGEST",
    "FREELLMAPI_SCORING_UPSTREAM_COMMIT",
    "FreeLLMScoringBatchResult",
    "FreeLLMScoringFactors",
    "FreeLLMScoringFault",
    "FreeLLMScoringInput",
    "FreeLLMScoringKernel",
    "FreeLLMScoringSource",
    "FreeLLMScoringTrace",
]
