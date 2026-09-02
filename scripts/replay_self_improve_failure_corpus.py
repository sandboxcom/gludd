#!/usr/bin/env python3
"""Replay observed local self-improvement failures without loading a model."""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from scripts import run_self_improve_e2e as runner_module

from general_ludd.self_improve.codex_comparison import (
    LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL,
    LocalProposalGateway,
    ProposalContract,
    ProposalManifest,
    bind_compact_focus_path,
    decode_proposal_batch,
    encode_proposal_batch,
    merge_proposal_manifests,
)

_PROTOCOL: Final = "self-improve-failure-corpus-v2"
_MAX_CORPUS_BYTES: Final = 65_536
_MAX_CASES: Final = 32
_MAX_TEXT_BYTES: Final = 8_192
_CASE_ID_RE: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TOKEN_RE: Final = re.compile(r"^[a-z][a-z0-9_]*$")
_FEEDBACK_RE: Final = re.compile(
    r"^protocol=(?P<protocol>[^ ]+) type=(?P<type>[^ ]+) "
    r"source=(?P<source>[^ ]+) detail=(?P<detail>.+)$"
)
_ROOT_FIELDS: Final = frozenset({"schema_version", "protocol", "cases"})
_CASE_FIELDS: Final = frozenset({"id", "kind", "input", "expected"})
_EXPECTED_FIELDS: Final = frozenset(
    {"type", "source", "detail", "forbidden_substrings"}
)
_INPUT_FIELDS: Final = {
    "acquisition_trace": frozenset({"events"}),
    "compact_decode": frozenset({"focus_path", "model_output"}),
    "completion_decode": frozenset(
        {"phase", "budget", "require_stop", "worker_response"}
    ),
    "parent_merge": frozenset(
        {"worker_path", "expected_path", "protocol_digest"}
    ),
    "retry_feedback": frozenset({"error"}),
}
_TRACE_EVENT_FIELDS: Final = frozenset({"phase", "cause"})
_TRACE_PHASES: Final = frozenset(
    {
        "download_completed",
        "eviction_completed",
        "eviction_planned",
        "eviction_refused",
        "lease_acquired",
        "lease_released",
        "next_attempt_empty",
        "proposal_error",
        "terminal_refusal",
    }
)
_ACQUISITION_CAUSE_DETAILS: Final = {
    "internal": "model acquisition failed internally",
    "interrupted": "model acquisition was interrupted",
    "io": "model acquisition failed during bounded I/O",
    "no_safe_reclaim": "model cache has no safe reclaim candidate",
    "timeout": "model acquisition exceeded its deadline",
    "validation": "model acquisition artifact validation failed",
}
_COMPLETED_ACQUISITION_TRACE: Final = (
    "eviction_planned",
    "eviction_completed",
    "download_completed",
    "lease_acquired",
    "lease_released",
)
_TERMINAL_REFUSAL_TRACE: Final = (
    "eviction_planned",
    "eviction_refused",
    "terminal_refusal",
)
_PHANTOM_PROPOSAL_TRACE: Final = (
    "eviction_planned",
    "eviction_refused",
    "proposal_error",
    "next_attempt_empty",
)
_CONTRACT: Final = ProposalContract(
    baseline_sha="0" * 40,
    task_id="S83.134",
    tests=("tests/unit/test_self_improve_failure_corpus.py",),
    make_commands=(
        "make test-self-improve-failure-corpus "
        "SELF_IMPROVE_FAILURE_CORPUS_FILE=config/self-improve/failure-corpus.json",
    ),
)
_CANARY_RESPONSE: Final = {
    "choices": [
        {
            "finish_reason": "stop",
            "message": {"content": '{"ok":true}'},
        }
    ],
    "usage": {
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "total_tokens": 2,
    },
}


@dataclass(frozen=True)
class ExpectedFeedback:
    """Exact safe output expected from one historical failure."""

    feedback_type: str
    source: str
    detail: str
    forbidden_substrings: tuple[str, ...]


@dataclass(frozen=True)
class FailureCase:
    """One strict, data-only replay case."""

    case_id: str
    kind: str
    inputs: Mapping[str, object]
    expected: ExpectedFeedback


@dataclass(frozen=True)
class ReplayResult:
    """Bounded result from replaying one failure through current code."""

    case_id: str
    kind: str
    passed: bool
    feedback: str
    feedback_type: str
    source: str
    detail: str
    feedback_bytes: int
    worker_succeeded: bool
    parent_stage: str


@dataclass(frozen=True)
class AcquisitionTraceVerdict:
    """Bounded decision for one synthetic acquisition lifecycle trace."""

    accepted: bool
    outcome: str
    feedback: str


class CorpusMismatch(ValueError):
    """A replay no longer matches its pinned typed expectation."""

    def __init__(self, case_id: str, reason: str) -> None:
        super().__init__(reason)
        self.case_id = case_id
        self.reason = reason


class _FixtureChatModel:
    """Small in-memory stand-in for the llama.cpp chat boundary."""

    def __init__(self, responses: tuple[Mapping[str, object], ...]) -> None:
        self._responses = iter(responses)

    def __call__(
        self,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        echo: bool,
    ) -> object:
        """Reject the legacy completion path, which the replay never selects."""
        del prompt, max_tokens, temperature, echo
        raise AssertionError("offline fixture requires chat completion")

    def create_chat_completion(self, **_kwargs: object) -> Mapping[str, object]:
        """Return the next pinned response without executing inference."""
        return next(self._responses)


def _required_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise ValueError(f"{label} must be a JSON object")
    return cast("dict[str, object]", value)


def _required_string(
    value: object,
    label: str,
    *,
    max_bytes: int = _MAX_TEXT_BYTES,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > max_bytes
    ):
        raise ValueError(f"{label} must contain 1..{max_bytes} UTF-8 bytes")
    return value


def _parse_expected(value: object, case_id: str) -> ExpectedFeedback:
    expected = _required_object(value, f"{case_id} expected")
    if set(expected) != _EXPECTED_FIELDS:
        raise ValueError(f"{case_id} expected fields drifted")
    feedback_type = _required_string(expected["type"], f"{case_id} expected type")
    source = _required_string(expected["source"], f"{case_id} expected source")
    detail = _required_string(expected["detail"], f"{case_id} expected detail")
    if _TOKEN_RE.fullmatch(feedback_type) is None:
        raise ValueError(f"{case_id} expected type is not canonical")
    if _TOKEN_RE.fullmatch(source) is None:
        raise ValueError(f"{case_id} expected source is not canonical")
    forbidden_raw = expected["forbidden_substrings"]
    if not isinstance(forbidden_raw, list) or len(forbidden_raw) > 16:
        raise ValueError(f"{case_id} forbidden substrings must be a bounded list")
    forbidden = tuple(
        _required_string(item, f"{case_id} forbidden substring", max_bytes=256)
        for item in forbidden_raw
    )
    if len(set(forbidden)) != len(forbidden):
        raise ValueError(f"{case_id} forbidden substrings must be unique")
    return ExpectedFeedback(feedback_type, source, detail, forbidden)


def _parse_case(value: object) -> FailureCase:
    raw = _required_object(value, "failure case")
    if set(raw) != _CASE_FIELDS:
        raise ValueError("failure case fields drifted")
    case_id = _required_string(raw["id"], "failure case id", max_bytes=96)
    if _CASE_ID_RE.fullmatch(case_id) is None:
        raise ValueError("failure case id is not canonical")
    kind = _required_string(raw["kind"], f"{case_id} kind", max_bytes=64)
    if kind not in _INPUT_FIELDS:
        raise ValueError(f"{case_id} kind is unsupported")
    inputs = _required_object(raw["input"], f"{case_id} input")
    if set(inputs) != _INPUT_FIELDS[kind]:
        raise ValueError(f"{case_id} input fields drifted")
    _validate_inputs(case_id, kind, inputs)
    return FailureCase(
        case_id=case_id,
        kind=kind,
        inputs=inputs,
        expected=_parse_expected(raw["expected"], case_id),
    )


def _validate_inputs(
    case_id: str,
    kind: str,
    inputs: Mapping[str, object],
) -> None:
    if kind == "acquisition_trace":
        _parse_acquisition_trace(inputs["events"])
    elif kind == "compact_decode":
        _required_string(inputs["focus_path"], f"{case_id} focus path")
        _required_string(inputs["model_output"], f"{case_id} model output")
    elif kind == "completion_decode":
        _required_string(inputs["phase"], f"{case_id} phase", max_bytes=32)
        budget = inputs["budget"]
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
            raise ValueError(f"{case_id} budget must be a positive integer")
        if not isinstance(inputs["require_stop"], bool):
            raise ValueError(f"{case_id} require_stop must be boolean")
        _required_object(inputs["worker_response"], f"{case_id} worker response")
    elif kind == "parent_merge":
        _required_string(inputs["worker_path"], f"{case_id} worker path")
        _required_string(inputs["expected_path"], f"{case_id} expected path")
        digest = _required_string(
            inputs["protocol_digest"],
            f"{case_id} protocol digest",
            max_bytes=64,
        )
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError(f"{case_id} protocol digest is not canonical")
    else:
        _required_string(inputs["error"], f"{case_id} error")


def _parse_acquisition_trace(value: object) -> tuple[tuple[str, str | None], ...]:
    if (
        not isinstance(value, (list, tuple))
        or not 1 <= len(value) <= 16
    ):
        raise ValueError("acquisition trace must contain 1..16 events")
    parsed: list[tuple[str, str | None]] = []
    for index, item in enumerate(value):
        event = _required_object(item, f"acquisition trace event {index}")
        if set(event) != _TRACE_EVENT_FIELDS:
            raise ValueError("acquisition trace event fields drifted")
        phase = _required_string(
            event["phase"],
            f"acquisition trace event {index} phase",
            max_bytes=64,
        )
        if phase not in _TRACE_PHASES:
            raise ValueError("acquisition trace phase is unsupported")
        cause = event["cause"]
        if cause is not None and not isinstance(cause, str):
            raise ValueError("acquisition trace cause must be a string or null")
        parsed.append((phase, cause))
    return tuple(parsed)


def _typed_acquisition_feedback(cause: str) -> str:
    try:
        detail = _ACQUISITION_CAUSE_DETAILS[cause]
    except KeyError as exc:
        raise ValueError(
            "refusal requires a typed safe acquisition cause"
        ) from exc
    return (
        f"protocol={LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL.version} "
        "type=acquisition_refused source=acquisition_trace "
        f"detail={detail}"
    )


def check_acquisition_trace(
    events: Sequence[Mapping[str, object]],
) -> AcquisitionTraceVerdict:
    """Accept only a complete lease lifecycle or an explicit typed refusal."""
    parsed = _parse_acquisition_trace(events)
    phases = tuple(phase for phase, _cause in parsed)
    causes = tuple(cause for _phase, cause in parsed)
    if phases == _COMPLETED_ACQUISITION_TRACE:
        if any(cause is not None for cause in causes):
            raise ValueError("completed acquisition trace cannot contain a cause")
        return AcquisitionTraceVerdict(True, "completed", "")
    if phases not in {_TERMINAL_REFUSAL_TRACE, _PHANTOM_PROPOSAL_TRACE}:
        raise ValueError("acquisition trace has an unsupported transition")
    refusal_cause = causes[1]
    if not isinstance(refusal_cause, str):
        raise ValueError("refusal requires a typed safe acquisition cause")
    feedback = _typed_acquisition_feedback(refusal_cause)
    if phases == _TERMINAL_REFUSAL_TRACE:
        if causes != (None, refusal_cause, refusal_cause):
            raise ValueError("refusal requires one matching typed safe acquisition cause")
        return AcquisitionTraceVerdict(True, "refused", feedback)
    if causes != (None, refusal_cause, None, None):
        raise ValueError("refusal requires one matching typed safe acquisition cause")
    return AcquisitionTraceVerdict(False, "refused", feedback)


def load_corpus(path: Path) -> tuple[FailureCase, ...]:
    """Load a strict, bounded, versioned offline failure corpus."""
    if not path.is_file():
        raise FileNotFoundError(f"failure corpus is not readable: {path}")
    if path.stat().st_size > _MAX_CORPUS_BYTES:
        raise ValueError(f"failure corpus exceeds {_MAX_CORPUS_BYTES} bytes")
    try:
        decoded = cast(
            "object",
            json.loads(path.read_text(encoding="utf-8")),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("failure corpus is not valid UTF-8 JSON") from exc
    root = _required_object(decoded, "failure corpus")
    if set(root) != _ROOT_FIELDS:
        raise ValueError("failure corpus fields drifted")
    if root["schema_version"] != 2 or root["protocol"] != _PROTOCOL:
        raise ValueError("failure corpus protocol is unsupported")
    raw_cases = root["cases"]
    if not isinstance(raw_cases, list) or not 1 <= len(raw_cases) <= _MAX_CASES:
        raise ValueError(f"failure corpus must contain 1..{_MAX_CASES} cases")
    cases = tuple(_parse_case(item) for item in raw_cases)
    identities = tuple(case.case_id for case in cases)
    if len(set(identities)) != len(identities):
        raise ValueError("failure corpus case ids must be unique")
    return cases


def _fixture_gateway(
    proposal_response: Mapping[str, object],
    operation: Callable[[LocalProposalGateway], ProposalManifest],
) -> ProposalManifest:
    model = _FixtureChatModel((_CANARY_RESPONSE, proposal_response))

    def factory(
        *,
        model_path: str,
        n_ctx: int,
        verbose: bool,
        n_gpu_layers: int = 0,
    ) -> _FixtureChatModel:
        del model_path, n_ctx, verbose, n_gpu_layers
        return model

    with tempfile.TemporaryDirectory(prefix="gludd-failure-corpus-") as raw_dir:
        model_path = Path(raw_dir) / "offline-fixture.gguf"
        model_path.write_bytes(b"offline fixture only")
        gateway = LocalProposalGateway(model_path, model_factory=factory)
        with redirect_stdout(io.StringIO()):
            return operation(gateway)


def _compact_response(model_output: str) -> Mapping[str, object]:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": model_output},
            }
        ],
        "usage": {
            "prompt_tokens": 64,
            "completion_tokens": 32,
            "total_tokens": 96,
        },
    }


def _replay_gateway_failure(case: FailureCase) -> str:
    inputs = case.inputs
    focus_path = cast("str", inputs.get("focus_path", "src/offline.py"))
    if case.kind == "compact_decode":
        output = _required_string(inputs["model_output"], "model output")
        response = _compact_response(output)
    else:
        response = cast(
            "Mapping[str, object]",
            _required_object(inputs["worker_response"], "worker response"),
        )
    prompt = bind_compact_focus_path("Apply the exact offline fixture.", focus_path)
    try:
        _fixture_gateway(
            response,
            lambda gateway: gateway.propose(prompt, contract=_CONTRACT),
        )
    except (RuntimeError, ValueError) as exc:
        return runner_module._validation_retry_feedback(str(exc))
    raise CorpusMismatch(case.case_id, "expected_rejection_missing")


def _replay_parent_merge(case: FailureCase) -> str:
    worker_path = _required_string(case.inputs["worker_path"], "worker path")
    expected_path = _required_string(case.inputs["expected_path"], "expected path")
    digest = _required_string(case.inputs["protocol_digest"], "protocol digest")
    prompt = bind_compact_focus_path("Apply the exact offline fixture.", worker_path)
    manifest = _fixture_gateway(
        _compact_response('{"e":[{"a":"old","z":"new"}]}'),
        lambda gateway: gateway.propose(prompt, contract=_CONTRACT),
    )
    encoded = encode_proposal_batch((manifest,), protocol_digest=digest)
    decoded = decode_proposal_batch(
        encoded,
        expected_protocol_digest=digest,
        expected_count=1,
    )
    try:
        merge_proposal_manifests(
            decoded,
            expected_path_groups=((expected_path,),),
            expected_baseline_sha=_CONTRACT.baseline_sha,
            expected_task_id=_CONTRACT.task_id,
            expected_tests=_CONTRACT.tests,
            expected_make_commands=_CONTRACT.make_commands,
        )
    except (RuntimeError, ValueError) as exc:
        return runner_module._validation_retry_feedback(str(exc))
    raise CorpusMismatch(case.case_id, "expected_parent_rejection_missing")


def _actual_feedback(case: FailureCase) -> tuple[str, bool, str]:
    if case.kind == "acquisition_trace":
        raw_events = case.inputs["events"]
        if not isinstance(raw_events, list):
            raise ValueError("acquisition trace events must be a list")
        events = cast("list[Mapping[str, object]]", raw_events)
        verdict = check_acquisition_trace(events)
        if verdict.accepted:
            raise CorpusMismatch(
                case.case_id,
                "expected_acquisition_rejection_missing",
            )
        return verdict.feedback, False, "acquisition"
    if case.kind in {"compact_decode", "completion_decode"}:
        return _replay_gateway_failure(case), False, ""
    if case.kind == "parent_merge":
        return _replay_parent_merge(case), True, "merge"
    error = _required_string(case.inputs["error"], "retry error")
    return runner_module._validation_retry_feedback(error), False, ""


def replay_case(case: FailureCase) -> ReplayResult:
    """Replay one case and require exact typed, bounded, non-leaking feedback."""
    feedback, worker_succeeded, parent_stage = _actual_feedback(case)
    match = _FEEDBACK_RE.fullmatch(feedback)
    if match is None or match.group("protocol") != (
        LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL.version
    ):
        raise CorpusMismatch(case.case_id, "feedback_protocol_drift")
    feedback_type = match.group("type")
    source = match.group("source")
    detail = match.group("detail")
    expected = case.expected
    if (
        feedback_type != expected.feedback_type
        or source != expected.source
        or detail != expected.detail
    ):
        raise CorpusMismatch(case.case_id, "typed_feedback_drift")
    if any(item in feedback for item in expected.forbidden_substrings):
        raise CorpusMismatch(case.case_id, "forbidden_feedback_leakage")
    feedback_bytes = len(feedback.encode("utf-8"))
    if feedback_bytes > (
        LOCAL_PROPOSAL_VALIDATION_RETRY_PROTOCOL.max_feedback_bytes
    ):
        raise CorpusMismatch(case.case_id, "feedback_bound_exceeded")
    return ReplayResult(
        case_id=case.case_id,
        kind=case.kind,
        passed=True,
        feedback=feedback,
        feedback_type=feedback_type,
        source=source,
        detail=detail,
        feedback_bytes=feedback_bytes,
        worker_succeeded=worker_succeeded,
        parent_stage=parent_stage,
    )


def replay_corpus(cases: Sequence[FailureCase]) -> tuple[ReplayResult, ...]:
    """Replay every pinned case in declaration order."""
    return tuple(replay_case(case) for case in cases)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay typed self-improvement failures without a local model"
    )
    parser.add_argument("--corpus", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Replay the corpus and publish only bounded typed evidence."""
    args = _parser().parse_args(argv)
    try:
        cases = load_corpus(args.corpus)
        results = replay_corpus(cases)
    except CorpusMismatch as exc:
        print(
            "SELF_IMPROVE_FAILURE_CORPUS_MISMATCH "
            f"case={exc.case_id} reason={exc.reason}",
            file=sys.stderr,
        )
        return 1
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        print(
            "SELF_IMPROVE_FAILURE_CORPUS_ERROR type=fixture_validation",
            file=sys.stderr,
        )
        return 2
    for result in results:
        parent_stage = result.parent_stage or "none"
        print(
            "SELF_IMPROVE_FAILURE_CORPUS_CASE "
            f"id={result.case_id} kind={result.kind} result=pass "
            f"type={result.feedback_type} source={result.source} "
            f"feedback_bytes={result.feedback_bytes} "
            f"worker_succeeded={str(result.worker_succeeded).lower()} "
            f"parent_stage={parent_stage}"
        )
    summary = json.dumps(
        {
            "cases": len(results),
            "failed": 0,
            "passed": len(results),
            "protocol": _PROTOCOL,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    print(f"SELF_IMPROVE_FAILURE_CORPUS_SUMMARY {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
