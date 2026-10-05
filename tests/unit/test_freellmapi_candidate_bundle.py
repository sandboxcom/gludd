"""Contracts for the exact FreeLLMAPI v0.11.1 candidate bundle."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

import pytest

import general_ludd.models.freellmapi.candidate_bundle as candidate_bundle
from general_ludd.models.freellmapi.candidate_bundle import (
    CandidateBundleError,
    CandidateBundleFault,
    FreeLLMAPICandidateBundle,
)
from general_ludd.models.freellmapi_scoring_contracts import FreeLLMScoringInput

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "config/freellmapi/upstream_candidate.json"
BUNDLE = ROOT / "src/general_ludd/models/vendor/freellmapi/candidate_v0_11_1.iife.js"
MANIFEST = ROOT / "src/general_ludd/models/vendor/freellmapi/candidate_v0_11_1.json"
COMMIT = "4191d8e7abef39fcd93fab009123467036f39750"
SOURCE_DIGEST = "475557a97ff5a69150a4d30ad194edf05cb45b4d5d3b132d018b55344f3642f1"


def _candidate() -> dict[str, object]:
    value: object = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _input() -> FreeLLMScoringInput:
    return FreeLLMScoringInput(
        candidate_identity_digest="a" * 64,
        successes=1.0,
        failures=5.0,
        community_successes=20.0,
        community_failures=0.0,
        tokens_per_second=60.0,
        ttfb_ms=300.0,
        used_tokens=10.0,
        budget_tokens=100.0,
        rate_window_used_fraction=0.1,
        rate_limit_penalty=0.0,
    )


def test_exact_candidate_iife_is_provenance_bound_and_executes_in_quickjs() -> None:
    runner = FreeLLMAPICandidateBundle(candidate_lock=_candidate())

    result = runner.score_batch((_input(),))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert result.probabilities == pytest.approx((22.0 / 28.0,))
    assert result.bundle_sha256 == manifest["bundle_sha256"]
    assert result.upstream_commit == COMMIT
    assert result.runtime_admitted is False
    assert manifest["source_sha256"] == SOURCE_DIGEST
    assert manifest["source_path"] == "server/src/services/scoring.ts"
    assert manifest["format"] == "iife"
    assert manifest["selected_export"] == "expectedReliability"
    assert manifest["host_capabilities"] == []
    assert manifest["runtime_admitted"] is False
    assert BUNDLE.read_text(encoding="utf-8").startswith('"use strict";')


def test_candidate_bundle_is_deterministic_and_has_no_ambient_authority() -> None:
    source = BUNDLE.read_bytes()
    manifest = MANIFEST.read_bytes()

    first = candidate_bundle.validate_candidate_bundle(
        candidate_lock=_candidate(),
        bundle_bytes=source,
        manifest_bytes=manifest,
    )
    second = candidate_bundle.validate_candidate_bundle(
        candidate_lock=_candidate(),
        bundle_bytes=source,
        manifest_bytes=manifest,
    )

    assert first == second
    text = source.decode("utf-8")
    for forbidden in (
        "fetch(",
        "require(",
        "process.",
        "XMLHttpRequest",
        "WebSocket",
        "child_process",
        "Deno.",
    ):
        assert forbidden not in text


class _Context:
    def __init__(self, result: object) -> None:
        self.result = result
        self.evaluations = 0
        self.limits: list[tuple[str, int | float]] = []

    def set_memory_limit(self, value: int) -> None:
        self.limits.append(("memory", value))

    def set_time_limit(self, value: float) -> None:
        self.limits.append(("time", value))

    def set_max_stack_size(self, value: int) -> None:
        self.limits.append(("stack", value))

    def eval(self, _source: str) -> object:
        self.evaluations += 1
        return None if self.evaluations == 1 else self.result


def test_context_limits_are_applied_before_invocation() -> None:
    context = _Context("[0.75]")
    runner = FreeLLMAPICandidateBundle(
        candidate_lock=_candidate(),
        context_factory=lambda: context,
    )

    assert runner.score_batch((_input(),)).probabilities == (0.75,)
    assert context.limits == [
        ("memory", 16 * 1024 * 1024),
        ("time", 0.025),
        ("stack", 256 * 1024),
    ]


@pytest.mark.parametrize(
    ("result", "fault"),
    [
        (None, CandidateBundleFault.RESULT_INVALID),
        ("{}", CandidateBundleFault.RESULT_INVALID),
        ("[]", CandidateBundleFault.RESULT_INVALID),
        ("[true]", CandidateBundleFault.RESULT_INVALID),
        ("[1.1]", CandidateBundleFault.RESULT_INVALID),
        ("[NaN]", CandidateBundleFault.RESULT_INVALID),
        (json.dumps([math.inf]).replace("Infinity", "1e999"), CandidateBundleFault.RESULT_INVALID),
    ],
)
def test_invalid_candidate_results_are_typed_and_content_free(
    result: object,
    fault: CandidateBundleFault,
) -> None:
    runner = FreeLLMAPICandidateBundle(
        candidate_lock=_candidate(),
        context_factory=lambda: _Context(result),
    )

    with pytest.raises(CandidateBundleError) as caught:
        runner.score_batch((_input(),))

    assert caught.value.fault is fault
    assert str(caught.value) == fault.value


def test_engine_failures_are_typed_without_reflecting_secret_details() -> None:
    def unavailable() -> _Context:
        raise ModuleNotFoundError("provider-secret")

    unavailable_runner = FreeLLMAPICandidateBundle(
        candidate_lock=_candidate(),
        context_factory=unavailable,
    )
    with pytest.raises(CandidateBundleError) as missing:
        unavailable_runner.score_batch((_input(),))
    assert missing.value.fault is CandidateBundleFault.ENGINE_UNAVAILABLE
    assert "provider-secret" not in repr(missing.value)

    class _Broken(_Context):
        def eval(self, _source: str) -> object:
            raise RuntimeError("private-prompt")

    broken_runner = FreeLLMAPICandidateBundle(
        candidate_lock=_candidate(),
        context_factory=lambda: _Broken(None),
    )
    with pytest.raises(CandidateBundleError) as broken:
        broken_runner.score_batch((_input(),))
    assert broken.value.fault is CandidateBundleFault.ENGINE_FAILURE
    assert "private-prompt" not in repr(broken.value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest: manifest.__setitem__("bundle_sha256", "0" * 64),
        lambda manifest: manifest.__setitem__("source_sha256", "0" * 64),
        lambda manifest: manifest.__setitem__("upstream_commit", "0" * 40),
        lambda manifest: manifest.__setitem__("upstream_tag", "v0.12.0"),
        lambda manifest: manifest.__setitem__("host_capabilities", ["network"]),
        lambda manifest: manifest.__setitem__("runtime_admitted", True),
        lambda manifest: manifest.__setitem__("unexpected", "field"),
    ],
)
def test_manifest_drift_fails_with_one_content_free_fault(mutation: object) -> None:
    manifest: object = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(manifest, dict)
    mutation(manifest)  # type: ignore[operator]

    with pytest.raises(CandidateBundleError) as caught:
        candidate_bundle.validate_candidate_bundle(
            candidate_lock=_candidate(),
            bundle_bytes=BUNDLE.read_bytes(),
            manifest_bytes=json.dumps(manifest).encode(),
        )

    assert caught.value.fault is CandidateBundleFault.MANIFEST_INVALID
    assert str(caught.value) == "manifest_invalid"


def test_candidate_and_bundle_tampering_fail_closed() -> None:
    changed_candidate = copy.deepcopy(_candidate())
    upstream = changed_candidate["upstream"]
    assert isinstance(upstream, dict)
    upstream["commit"] = "0" * 40
    with pytest.raises(CandidateBundleError) as candidate_error:
        candidate_bundle.validate_candidate_bundle(
            candidate_lock=changed_candidate,
            bundle_bytes=BUNDLE.read_bytes(),
            manifest_bytes=MANIFEST.read_bytes(),
        )
    assert candidate_error.value.fault is CandidateBundleFault.CANDIDATE_INVALID

    with pytest.raises(CandidateBundleError) as bundle_error:
        candidate_bundle.validate_candidate_bundle(
            candidate_lock=_candidate(),
            bundle_bytes=BUNDLE.read_bytes() + b"\n// drift",
            manifest_bytes=MANIFEST.read_bytes(),
        )
    assert bundle_error.value.fault is CandidateBundleFault.BUNDLE_INVALID


def test_batch_boundary_rejects_empty_oversized_and_non_contract_inputs() -> None:
    runner = FreeLLMAPICandidateBundle(candidate_lock=_candidate())

    for values in ((), tuple(_input() for _ in range(257)), (object(),)):
        with pytest.raises(CandidateBundleError) as caught:
            runner.score_batch(values)  # type: ignore[arg-type]
        assert caught.value.fault is CandidateBundleFault.INPUT_INVALID


def test_candidate_bundle_public_surface_stays_universal_and_small() -> None:
    assert candidate_bundle.__all__ == [
        "CandidateBundleError",
        "CandidateBundleFault",
        "CandidateBundleResult",
        "FreeLLMAPICandidateBundle",
        "validate_candidate_bundle",
    ]
    assert "self_improve" not in Path(candidate_bundle.__file__).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "manifest_bytes",
    [
        b"not-json",
        b"[]",
    ],
)
def test_malformed_manifest_shapes_fail_closed(manifest_bytes: bytes) -> None:
    with pytest.raises(CandidateBundleError) as caught:
        candidate_bundle.validate_candidate_bundle(
            candidate_lock=_candidate(),
            bundle_bytes=BUNDLE.read_bytes(),
            manifest_bytes=manifest_bytes,
        )
    assert caught.value.fault is CandidateBundleFault.MANIFEST_INVALID


def test_invalid_builder_and_adapter_identity_fail_closed() -> None:
    for mutation in (
        lambda value: value["builder"].__setitem__("version", "latest"),
        lambda value: value["builder"]["argv"].append("--minify"),
        lambda value: value.__setitem__("adapter_sha256", 1),
    ):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        mutation(manifest)
        with pytest.raises(CandidateBundleError) as caught:
            candidate_bundle.validate_candidate_bundle(
                candidate_lock=_candidate(),
                bundle_bytes=BUNDLE.read_bytes(),
                manifest_bytes=json.dumps(manifest).encode(),
            )
        assert caught.value.fault is CandidateBundleFault.MANIFEST_INVALID


@pytest.mark.parametrize(
    "bundle_bytes",
    [
        b"",
        b"\xff",
        b'"use strict";\nvar Wrong = (() => {})();',
        b'"use strict";\nvar FreeLLMAPICandidateV0111 = (() => { fetch(); })();',
    ],
)
def test_structurally_invalid_digest_pinned_bundles_are_rejected(
    bundle_bytes: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = hashlib.sha256(bundle_bytes).hexdigest()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["bundle_sha256"] = digest
    monkeypatch.setattr(candidate_bundle, "_BUNDLE_SHA256", digest)

    with pytest.raises(CandidateBundleError) as caught:
        candidate_bundle.validate_candidate_bundle(
            candidate_lock=_candidate(),
            bundle_bytes=bundle_bytes,
            manifest_bytes=json.dumps(manifest).encode(),
        )
    assert caught.value.fault is CandidateBundleFault.BUNDLE_INVALID


def test_missing_artifacts_and_malformed_candidate_have_typed_faults(
    tmp_path: Path,
) -> None:
    with pytest.raises(CandidateBundleError) as missing_bundle:
        FreeLLMAPICandidateBundle(
            candidate_lock=_candidate(),
            bundle_path=tmp_path / "missing.js",
        )
    assert missing_bundle.value.fault is CandidateBundleFault.BUNDLE_INVALID

    with pytest.raises(CandidateBundleError) as malformed_candidate:
        candidate_bundle.validate_candidate_bundle(
            candidate_lock={},
            bundle_bytes=BUNDLE.read_bytes(),
            manifest_bytes=MANIFEST.read_bytes(),
        )
    assert malformed_candidate.value.fault is CandidateBundleFault.CANDIDATE_INVALID
