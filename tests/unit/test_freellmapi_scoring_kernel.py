"""Contracts for the bounded in-process FreeLLMAPI scoring kernel."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

import general_ludd.models.freellmapi_scoring_kernel as scoring_kernel
from general_ludd.models.freellmapi_scoring_kernel import (
    FREELLMAPI_SCORING_BUNDLE_DIGEST,
    FreeLLMScoringFactors,
    FreeLLMScoringFault,
    FreeLLMScoringInput,
    FreeLLMScoringKernel,
    FreeLLMScoringSource,
)

_DIGEST = "a" * 64
_OTHER_DIGEST = "b" * 64
_ROOT = Path(__file__).resolve().parents[2]
_BUNDLE = (
    _ROOT
    / "src/general_ludd/models/vendor/freellmapi/scoring_kernel.js"
)
_MANIFEST = (
    _ROOT
    / "src/general_ludd/models/vendor/freellmapi/scoring_kernel.json"
)


def _candidate(**overrides: object) -> FreeLLMScoringInput:
    values: dict[str, object] = {
        "candidate_identity_digest": _DIGEST,
        "successes": 3.0,
        "failures": 1.0,
        "community_successes": 1.0,
        "community_failures": 1.0,
        "tokens_per_second": 60.0,
        "ttfb_ms": 300.0,
        "used_tokens": 850.0,
        "budget_tokens": 1_000.0,
        "rate_window_used_fraction": 0.9,
        "rate_limit_penalty": 5.0,
    }
    values.update(overrides)
    return FreeLLMScoringInput(**values)  # type: ignore[arg-type]


def _fallback(digest: str = _DIGEST) -> FreeLLMScoringFactors:
    return FreeLLMScoringFactors(
        candidate_identity_digest=digest,
        reliability_alpha=1.0,
        reliability_beta=1.0,
        expected_reliability=0.5,
        speed=0.5,
        headroom=1.0,
        rate_window_headroom=1.0,
        rate_limit=1.0,
    )


def test_real_quickjs_kernel_matches_pinned_upstream_golden_vector() -> None:
    result = FreeLLMScoringKernel(enabled=True).factor_batch(
        (_candidate(),),
        fallback=(_fallback(),),
    )

    assert result.source is FreeLLMScoringSource.FREELLMAPI_SHADOW
    assert result.fault is None
    factors = result.factors[0]
    assert factors.candidate_identity_digest == _DIGEST
    assert factors.reliability_alpha == 5.0
    assert factors.reliability_beta == 3.0
    assert factors.expected_reliability == pytest.approx(0.625)
    assert factors.speed == pytest.approx(0.7792723353)
    assert factors.headroom == pytest.approx(0.775)
    assert factors.rate_window_headroom == pytest.approx(0.55)
    assert factors.rate_limit == pytest.approx(0.7)


def test_kernel_is_default_off_and_does_not_construct_an_engine() -> None:
    constructed = False

    def factory() -> _FailingContext:
        nonlocal constructed
        constructed = True
        raise AssertionError("disabled kernel constructed an engine")

    result = FreeLLMScoringKernel(context_factory=factory).factor_batch(
        (_candidate(),),
        fallback=(_fallback(),),
    )

    assert result.source is FreeLLMScoringSource.GLUDD_FALLBACK
    assert result.factors == (_fallback(),)
    assert result.fault is FreeLLMScoringFault.DISABLED
    assert constructed is False


class _FailingContext:
    def set_memory_limit(self, _limit: int) -> None:
        pass

    def set_time_limit(self, _limit: float) -> None:
        pass

    def set_max_stack_size(self, _limit: int) -> None:
        pass

    def eval(self, _source: str) -> object:
        raise RuntimeError("provider-secret-that-must-not-escape")


def test_engine_fault_trips_circuit_and_returns_exact_gludd_fallback() -> None:
    contexts = 0

    def factory() -> _FailingContext:
        nonlocal contexts
        contexts += 1
        return _FailingContext()

    traces: list[object] = []
    kernel = FreeLLMScoringKernel(
        enabled=True,
        context_factory=factory,
        trace_sink=traces.append,
    )
    first = kernel.factor_batch((_candidate(),), fallback=(_fallback(),))
    second = kernel.factor_batch((_candidate(),), fallback=(_fallback(),))

    assert first.source is FreeLLMScoringSource.GLUDD_FALLBACK
    assert first.fault is FreeLLMScoringFault.ENGINE_FAILURE
    assert second.fault is FreeLLMScoringFault.CIRCUIT_OPEN
    assert first.factors == second.factors == (_fallback(),)
    assert contexts == 1
    assert "provider-secret" not in repr(first)
    assert "provider-secret" not in repr(traces)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("successes", math.nan),
        ("failures", math.inf),
        ("tokens_per_second", -1.0),
        ("ttfb_ms", -1.0),
        ("budget_tokens", -1.0),
        ("rate_window_used_fraction", 1.01),
        ("rate_limit_penalty", -0.01),
        ("candidate_identity_digest", "raw-model-name"),
    ],
)
def test_candidate_schema_rejects_nonfinite_unbounded_and_raw_identity(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError):
        _candidate(**{field: value})


def test_batch_rejects_identity_mismatch_and_more_than_256_candidates() -> None:
    kernel = FreeLLMScoringKernel()
    with pytest.raises(ValueError, match="identity"):
        kernel.factor_batch(
            (_candidate(),),
            fallback=(_fallback(_OTHER_DIGEST),),
        )
    with pytest.raises(ValueError, match="256"):
        kernel.factor_batch(
            tuple(_candidate() for _ in range(257)),
            fallback=tuple(_fallback() for _ in range(257)),
        )


def test_upstream_bundle_and_manifest_are_exact_allowlisted_artifacts() -> None:
    source = _BUNDLE.read_bytes()
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    source_digest = hashlib.sha256(source).hexdigest()

    assert source_digest == FREELLMAPI_SCORING_BUNDLE_DIGEST
    assert manifest["bundle_sha256"] == source_digest
    assert manifest["upstream_commit"] == (
        "780a7d8d6dcbc818eb10ec17da210635b569ae22"
    )
    assert manifest["license"] == "MIT"
    assert manifest["exports"] == [
        "reliabilityPosterior",
        "expectedReliability",
        "speedScore",
        "headroomFactor",
        "rateWindowHeadroomFactor",
        "rateLimitFactor",
    ]
    text = source.decode("utf-8")
    for forbidden in (
        "Math.random",
        "Date(",
        "eval(",
        "Function(",
        "require(",
        "fetch(",
        "process.",
        "WebSocket",
        "XMLHttpRequest",
    ):
        assert forbidden not in text


def test_scoring_is_monotonic_and_candidate_order_is_preserved() -> None:
    candidates = (
        _candidate(candidate_identity_digest=_DIGEST, successes=1.0),
        _candidate(
            candidate_identity_digest=_OTHER_DIGEST,
            successes=10.0,
            tokens_per_second=120.0,
            rate_window_used_fraction=0.1,
            rate_limit_penalty=0.0,
        ),
    )
    result = FreeLLMScoringKernel(enabled=True).factor_batch(
        candidates,
        fallback=(_fallback(_DIGEST), _fallback(_OTHER_DIGEST)),
    )

    first, second = result.factors
    assert [item.candidate_identity_digest for item in result.factors] == [
        _DIGEST,
        _OTHER_DIGEST,
    ]
    assert second.expected_reliability > first.expected_reliability
    assert second.speed > first.speed
    assert second.rate_window_headroom > first.rate_window_headroom
    assert second.rate_limit > first.rate_limit


class _ResultContext:
    def __init__(self, result: object) -> None:
        self._result = result
        self._eval_count = 0
        self.limits: list[tuple[str, int | float]] = []

    def set_memory_limit(self, limit: int) -> None:
        self.limits.append(("memory", limit))

    def set_time_limit(self, limit: float) -> None:
        self.limits.append(("time", limit))

    def set_max_stack_size(self, limit: int) -> None:
        self.limits.append(("stack", limit))

    def eval(self, _source: str) -> object:
        self._eval_count += 1
        return None if self._eval_count == 1 else self._result


def _raw_factor(**overrides: object) -> str:
    factor: dict[str, object] = {
        "candidate_identity_digest": _DIGEST,
        "reliability_alpha": 5.0,
        "reliability_beta": 3.0,
        "expected_reliability": 0.625,
        "speed": 0.75,
        "headroom": 0.5,
        "rate_window_headroom": 0.5,
        "rate_limit": 0.7,
    }
    factor.update(overrides)
    return json.dumps([factor], separators=(",", ":"))


def test_missing_engine_is_typed_content_free_and_opens_circuit() -> None:
    def missing_engine() -> _FailingContext:
        raise ModuleNotFoundError("credential-that-must-not-escape")

    kernel = FreeLLMScoringKernel(enabled=True, context_factory=missing_engine)
    first = kernel.factor_batch((_candidate(),), fallback=(_fallback(),))
    second = kernel.factor_batch((_candidate(),), fallback=(_fallback(),))

    assert first.fault is FreeLLMScoringFault.ENGINE_UNAVAILABLE
    assert second.fault is FreeLLMScoringFault.CIRCUIT_OPEN
    assert "credential-that" not in repr((first, second))


@pytest.mark.parametrize(
    "raw_result",
    [
        None,
        "{}",
        "[]",
        "[{}]",
        '[{"candidate_identity_digest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}]',
        "[NaN]",
        _raw_factor(candidate_identity_digest=7),
        _raw_factor(candidate_identity_digest=_OTHER_DIGEST),
        _raw_factor(speed="not-a-number"),
        _raw_factor(speed=math.inf).replace("Infinity", "1e999"),
        json.dumps(["x" * (256 * 1024)]),
    ],
    ids=(
        "non-text",
        "non-list",
        "count-drift",
        "schema-drift",
        "missing-fields",
        "non-json-number",
        "identity-non-text",
        "identity-drift",
        "factor-non-numeric",
        "factor-nonfinite",
        "oversized",
    ),
)
def test_invalid_engine_results_fail_closed_without_changing_fallback(
    raw_result: object,
) -> None:
    context = _ResultContext(raw_result)
    result = FreeLLMScoringKernel(
        enabled=True,
        context_factory=lambda: context,
    ).factor_batch((_candidate(),), fallback=(_fallback(),))

    assert result.fault is FreeLLMScoringFault.INVALID_RESULT
    assert result.factors == (_fallback(),)


def test_context_limits_are_applied_before_an_allowlisted_result() -> None:
    raw_result = _raw_factor()
    context = _ResultContext(raw_result)

    result = FreeLLMScoringKernel(
        enabled=True,
        context_factory=lambda: context,
    ).factor_batch((_candidate(),), fallback=(_fallback(),))

    assert result.source is FreeLLMScoringSource.FREELLMAPI_SHADOW
    assert context.limits == [
        ("memory", 16 * 1024 * 1024),
        ("time", 0.05),
        ("stack", 256 * 1024),
    ]


def test_telemetry_failure_cannot_change_fallback() -> None:
    def broken_sink(_trace: object) -> None:
        raise RuntimeError("telemetry-secret")

    result = FreeLLMScoringKernel(trace_sink=broken_sink).factor_batch(
        (_candidate(),),
        fallback=(_fallback(),),
    )

    assert result.fault is FreeLLMScoringFault.DISABLED
    assert result.factors == (_fallback(),)


def test_nullable_upstream_inputs_preserve_neutral_factors() -> None:
    result = FreeLLMScoringKernel(enabled=True).factor_batch(
        (
            _candidate(
                ttfb_ms=None,
                budget_tokens=0.0,
                rate_window_used_fraction=None,
            ),
        ),
        fallback=(_fallback(),),
    )

    assert result.source is FreeLLMScoringSource.FREELLMAPI_SHADOW
    assert result.factors[0].headroom == 1.0
    assert result.factors[0].rate_window_headroom == 1.0


@pytest.mark.parametrize(
    "manifest",
    [
        [],
        {"bundle_sha256": "0" * 64, "upstream_commit": "0" * 40},
        {
            "bundle_sha256": FREELLMAPI_SCORING_BUNDLE_DIGEST,
            "upstream_commit": "0" * 40,
        },
    ],
)
def test_manifest_tampering_fails_closed(
    manifest: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(scoring_kernel, "_MANIFEST_PATH", manifest_path)

    result = FreeLLMScoringKernel(enabled=True).factor_batch(
        (_candidate(),), fallback=(_fallback(),)
    )

    assert result.fault is FreeLLMScoringFault.INVALID_RESULT


@pytest.mark.parametrize("source", [b"", b"identity-drift"])
def test_bundle_tampering_fails_closed(
    source: bytes,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "kernel.js"
    bundle_path.write_bytes(source)
    monkeypatch.setattr(scoring_kernel, "_BUNDLE_PATH", bundle_path)

    result = FreeLLMScoringKernel(enabled=True).factor_batch(
        (_candidate(),), fallback=(_fallback(),)
    )

    assert result.fault is FreeLLMScoringFault.INVALID_RESULT


def test_batch_and_factor_boundaries_reject_ambiguous_values() -> None:
    kernel = FreeLLMScoringKernel()
    with pytest.raises(ValueError, match="at least one"):
        kernel.factor_batch((), fallback=())
    with pytest.raises(ValueError, match="counts"):
        kernel.factor_batch((_candidate(),), fallback=())
    with pytest.raises(TypeError, match="candidate"):
        kernel.factor_batch((object(),), fallback=(_fallback(),))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="fallback"):
        kernel.factor_batch((_candidate(),), fallback=(object(),))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at most 10"):
        _candidate(rate_limit_penalty=10.01)
    with pytest.raises(ValueError, match="numeric"):
        _candidate(successes=True)
    with pytest.raises(ValueError, match="positive"):
        FreeLLMScoringFactors(
            candidate_identity_digest=_DIGEST,
            reliability_alpha=0.0,
            reliability_beta=1.0,
            expected_reliability=0.5,
            speed=0.5,
            headroom=1.0,
            rate_window_headroom=1.0,
            rate_limit=1.0,
        )
