"""Load the exact, non-promoting FreeLLMAPI v0.11.1 IIFE candidate.

The checked-in bundle is an esbuild product of the signed candidate source.
Only a JSON-in/JSON-out reliability function is exposed to a constrained
QuickJS context.  The module never changes the admitted v0.9.9 artifact.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import NoReturn, Protocol, cast

from general_ludd.models.freellmapi_frozen_delta_validation import (
    validate_candidate,
)
from general_ludd.models.freellmapi_scoring_contracts import FreeLLMScoringInput

_UPSTREAM_REPOSITORY = "tashfeenahmed/freellmapi"
_UPSTREAM_TAG = "v0.11.1"
_UPSTREAM_COMMIT = "4191d8e7abef39fcd93fab009123467036f39750"
_SOURCE_PATH = "server/src/services/scoring.ts"
_SOURCE_SHA256 = "475557a97ff5a69150a4d30ad194edf05cb45b4d5d3b132d018b55344f3642f1"
_ADAPTER_SHA256 = "90e3eb734553989f00fbadbaafd0ec14df2369e8b9eeade1d0176b3939cfaa28"
_BUNDLE_SHA256 = "e9e5d87a0d1e9697e0681b52afb719b7fd1fcdd601bd7fcbaf430cc502114845"
_SELECTED_EXPORT = "expectedReliability"
_INVOCATION = "__gludd_freellmapi_candidate_batch"
_GLOBAL_NAME = "FreeLLMAPICandidateV0111"
_MAX_BATCH = 256
_MAX_BUNDLE_BYTES = 64 * 1024
_MAX_PAYLOAD_BYTES = 256 * 1024
_MEMORY_LIMIT_BYTES = 16 * 1024 * 1024
_TIME_LIMIT_SECONDS = 0.025
_STACK_LIMIT_BYTES = 256 * 1024
_VENDOR_ROOT = Path(__file__).resolve().parents[1] / "vendor" / "freellmapi"
_BUNDLE_PATH = _VENDOR_ROOT / "candidate_v0_11_1.iife.js"
_MANIFEST_PATH = _VENDOR_ROOT / "candidate_v0_11_1.json"
_FORBIDDEN_SOURCE_MARKERS = (
    "fetch(",
    "require(",
    "process.",
    "XMLHttpRequest",
    "WebSocket",
    "child_process",
    "Deno.",
)
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "candidate_id",
        "upstream_repository",
        "upstream_tag",
        "upstream_commit",
        "source_path",
        "source_sha256",
        "adapter_sha256",
        "bundle_sha256",
        "format",
        "global_name",
        "selected_export",
        "invocation",
        "builder",
        "host_capabilities",
        "runtime_admitted",
    }
)
_BUILDER_KEYS = frozenset({"name", "version", "argv"})
_BUILDER_ARGV = [
    "esbuild",
    "candidate_v0_11_1_entry.ts",
    "--bundle",
    "--format=iife",
    f"--global-name={_GLOBAL_NAME}",
    "--platform=neutral",
    "--target=es2020",
    "--tree-shaking=true",
    "--legal-comments=none",
    "--charset=utf8",
    "--log-level=warning",
]


class CandidateBundleFault(StrEnum):
    """Stable, content-free candidate-bundle failure categories."""

    CANDIDATE_INVALID = "candidate_invalid"
    MANIFEST_INVALID = "manifest_invalid"
    BUNDLE_INVALID = "bundle_invalid"
    INPUT_INVALID = "input_invalid"
    ENGINE_UNAVAILABLE = "engine_unavailable"
    ENGINE_FAILURE = "engine_failure"
    RESULT_INVALID = "result_invalid"


class CandidateBundleError(RuntimeError):
    """Fail-closed candidate-bundle error without rejected content."""

    def __init__(self, fault: CandidateBundleFault) -> None:
        """Create an error containing only the stable fault category."""
        self.fault = fault
        super().__init__(fault.value)


@dataclass(frozen=True, slots=True)
class CandidateBundleResult:
    """Validated candidate probabilities with immutable provenance."""

    probabilities: tuple[float, ...]
    bundle_sha256: str
    upstream_commit: str
    runtime_admitted: bool = False


@dataclass(frozen=True, slots=True)
class _ValidatedCandidateBundle:
    bundle_source: str
    bundle_sha256: str
    candidate_id: str


class _QuickJSContext(Protocol):
    """Capability-minimal embedded JavaScript context."""

    def set_memory_limit(self, limit: int) -> None: ...

    def set_time_limit(self, limit: float) -> None: ...

    def set_max_stack_size(self, limit: int) -> None: ...

    def eval(self, source: str) -> object: ...


def _fail(fault: CandidateBundleFault) -> NoReturn:
    raise CandidateBundleError(fault)


def _mapping(value: object, fault: CandidateBundleFault) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _fail(fault)
    return value


def _candidate_identity(candidate_lock: Mapping[str, object]) -> str:
    try:
        candidate_id, symbols = validate_candidate(candidate_lock)
        upstream = _mapping(
            candidate_lock.get("upstream"), CandidateBundleFault.CANDIDATE_INVALID
        )
        sources = _mapping(
            candidate_lock.get("sources"), CandidateBundleFault.CANDIDATE_INVALID
        )
        scoring = _mapping(
            sources.get("scoring"), CandidateBundleFault.CANDIDATE_INVALID
        )
        if (
            upstream.get("repository") != _UPSTREAM_REPOSITORY
            or upstream.get("tag") != _UPSTREAM_TAG
            or upstream.get("commit") != _UPSTREAM_COMMIT
            or scoring.get("path") != _SOURCE_PATH
            or scoring.get("sha256") != _SOURCE_SHA256
            or _SELECTED_EXPORT not in symbols
        ):
            _fail(CandidateBundleFault.CANDIDATE_INVALID)
    except CandidateBundleError:
        raise
    except Exception:
        _fail(CandidateBundleFault.CANDIDATE_INVALID)
    return candidate_id


def _manifest(manifest_bytes: bytes) -> Mapping[str, object]:
    try:
        value: object = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail(CandidateBundleFault.MANIFEST_INVALID)
    manifest = _mapping(value, CandidateBundleFault.MANIFEST_INVALID)
    if set(manifest) != _MANIFEST_KEYS:
        _fail(CandidateBundleFault.MANIFEST_INVALID)
    builder = _mapping(
        manifest.get("builder"), CandidateBundleFault.MANIFEST_INVALID
    )
    argv = builder.get("argv")
    if (
        set(builder) != _BUILDER_KEYS
        or builder.get("name") != "esbuild"
        or builder.get("version") != "0.28.1"
        or argv != _BUILDER_ARGV
    ):
        _fail(CandidateBundleFault.MANIFEST_INVALID)
    return manifest


def validate_candidate_bundle(
    *,
    candidate_lock: Mapping[str, object],
    bundle_bytes: bytes,
    manifest_bytes: bytes,
) -> _ValidatedCandidateBundle:
    """Validate exact candidate provenance and return the executable source."""
    candidate_id = _candidate_identity(candidate_lock)
    manifest = _manifest(manifest_bytes)
    digest = hashlib.sha256(bundle_bytes).hexdigest()
    expected = {
        "schema_version": 1,
        "candidate_id": candidate_id,
        "upstream_repository": _UPSTREAM_REPOSITORY,
        "upstream_tag": _UPSTREAM_TAG,
        "upstream_commit": _UPSTREAM_COMMIT,
        "source_path": _SOURCE_PATH,
        "source_sha256": _SOURCE_SHA256,
        "bundle_sha256": _BUNDLE_SHA256,
        "format": "iife",
        "global_name": _GLOBAL_NAME,
        "selected_export": _SELECTED_EXPORT,
        "invocation": _INVOCATION,
        "host_capabilities": [],
        "runtime_admitted": False,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        _fail(CandidateBundleFault.MANIFEST_INVALID)
    adapter_digest = manifest.get("adapter_sha256")
    if (
        not isinstance(adapter_digest, str)
        or adapter_digest != _ADAPTER_SHA256
    ):
        _fail(CandidateBundleFault.MANIFEST_INVALID)
    if digest != _BUNDLE_SHA256:
        _fail(CandidateBundleFault.BUNDLE_INVALID)
    if not bundle_bytes or len(bundle_bytes) > _MAX_BUNDLE_BYTES:
        _fail(CandidateBundleFault.BUNDLE_INVALID)
    try:
        source = bundle_bytes.decode("utf-8")
    except UnicodeDecodeError:
        _fail(CandidateBundleFault.BUNDLE_INVALID)
    if (
        not source.startswith(f'"use strict";\nvar {_GLOBAL_NAME} = (() => {{')
        or _INVOCATION not in source
        or any(marker in source for marker in _FORBIDDEN_SOURCE_MARKERS)
    ):
        _fail(CandidateBundleFault.BUNDLE_INVALID)
    return _ValidatedCandidateBundle(
        bundle_source=source,
        bundle_sha256=digest,
        candidate_id=candidate_id,
    )


def _default_context_factory() -> _QuickJSContext:
    try:
        import quickjs
    except ImportError as exc:
        raise ModuleNotFoundError("QuickJS context unavailable") from exc
    factory = getattr(quickjs, "Context", None)
    if not callable(factory):
        raise ModuleNotFoundError("QuickJS context unavailable")
    return cast(_QuickJSContext, factory())


def _payload(inputs: Sequence[FreeLLMScoringInput]) -> tuple[str, int]:
    values = tuple(inputs)
    if not values or len(values) > _MAX_BATCH or any(
        not isinstance(value, FreeLLMScoringInput) for value in values
    ):
        _fail(CandidateBundleFault.INPUT_INVALID)
    encoded = json.dumps(
        [
            {
                "successes": value.successes,
                "failures": value.failures,
                "community_successes": value.community_successes,
                "community_failures": value.community_failures,
            }
            for value in values
        ],
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(encoded.encode("ascii")) > _MAX_PAYLOAD_BYTES:
        _fail(CandidateBundleFault.INPUT_INVALID)
    return encoded, len(values)


def _probabilities(raw: object, count: int) -> tuple[float, ...]:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        _fail(CandidateBundleFault.RESULT_INVALID)
    try:
        values: object = json.loads(raw, parse_constant=lambda _value: None)
    except json.JSONDecodeError:
        _fail(CandidateBundleFault.RESULT_INVALID)
    if not isinstance(values, list) or len(values) != count:
        _fail(CandidateBundleFault.RESULT_INVALID)
    parsed: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int | float):
            _fail(CandidateBundleFault.RESULT_INVALID)
        number = float(value)
        if not math.isfinite(number) or not 0.0 <= number <= 1.0:
            _fail(CandidateBundleFault.RESULT_INVALID)
        parsed.append(number)
    return tuple(parsed)


class FreeLLMAPICandidateBundle:
    """Execute one verified candidate bundle in a constrained fresh context."""

    def __init__(
        self,
        *,
        candidate_lock: Mapping[str, object],
        bundle_path: Path = _BUNDLE_PATH,
        manifest_path: Path = _MANIFEST_PATH,
        context_factory: Callable[[], _QuickJSContext] | None = None,
    ) -> None:
        """Validate the candidate and load immutable artifact bytes."""
        try:
            bundle_bytes = bundle_path.read_bytes()
        except OSError:
            _fail(CandidateBundleFault.BUNDLE_INVALID)
        try:
            manifest_bytes = manifest_path.read_bytes()
        except OSError:
            _fail(CandidateBundleFault.MANIFEST_INVALID)
        self._validated = validate_candidate_bundle(
            candidate_lock=candidate_lock,
            bundle_bytes=bundle_bytes,
            manifest_bytes=manifest_bytes,
        )
        self._context_factory = context_factory or _default_context_factory

    def score_batch(
        self, inputs: Sequence[FreeLLMScoringInput]
    ) -> CandidateBundleResult:
        """Return bounded probabilities without changing runtime admission."""
        payload, count = _payload(inputs)
        try:
            context = self._context_factory()
        except ModuleNotFoundError:
            _fail(CandidateBundleFault.ENGINE_UNAVAILABLE)
        except Exception:
            _fail(CandidateBundleFault.ENGINE_FAILURE)
        try:
            context.set_memory_limit(_MEMORY_LIMIT_BYTES)
            context.set_time_limit(_TIME_LIMIT_SECONDS)
            context.set_max_stack_size(_STACK_LIMIT_BYTES)
            context.eval(self._validated.bundle_source)
            invocation = f"{_INVOCATION}({json.dumps(payload, ensure_ascii=True)})"
            raw = context.eval(invocation)
        except Exception:
            _fail(CandidateBundleFault.ENGINE_FAILURE)
        return CandidateBundleResult(
            probabilities=_probabilities(raw, count),
            bundle_sha256=self._validated.bundle_sha256,
            upstream_commit=_UPSTREAM_COMMIT,
        )


__all__ = [
    "CandidateBundleError",
    "CandidateBundleFault",
    "CandidateBundleResult",
    "FreeLLMAPICandidateBundle",
    "validate_candidate_bundle",
]
