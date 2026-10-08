"""Tests for the bounded decision-codification operator CLI."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest

from general_ludd.cli_decision_codification import (
    MAX_ANALYSIS_RESPONSE_BYTES,
    DecisionCaptureSummary,
    DecisionLifecycleSummary,
    _AnalysisUnavailable,
    _cmd_activate,
    _cmd_analyze,
    _cmd_approve,
    _cmd_capture,
    _cmd_mine,
    _cmd_rollback,
    _cmd_status,
    _DecisionOperator,
    _load_operator,
    _OperatorCLIError,
    _post_analysis,
    _request_payload,
    _utc_timestamp,
    add_decision_codification_subparser,
)
from general_ludd.decision_codification.artifact_store import DecisionArtifactStore
from general_ludd.decision_codification.durable import DurableGenerationStore
from general_ludd.decision_codification.observability import DecisionReuseObservability
from general_ludd.decision_codification.rollout import (
    AtomicGenerationStore,
    RolloutController,
)
from general_ludd.decision_codification.schema import (
    DecisionRuleBundleV1,
    DecisionRuleLeafV1,
    DecisionRuleNodeV1,
    EvaluationReportV1,
    LeafEvaluationV1,
    OutcomeCountsV1,
)
from general_ludd.decision_codification.service import (
    DecisionAnalysis,
    DecisionCandidate,
)
from general_ludd.routers.decision_codification import DecisionAnalysisResponse

_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64
_DIGEST_C = "sha256:" + "c" * 64
_DIGEST_D = "sha256:" + "d" * 64
_DIGEST_E = "sha256:" + "e" * 64
_DIGEST_F = "sha256:" + "f" * 64
_NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "project": "project-alpha",
        "run_ids": ["run-1", "run_2"],
        "training_recipe_digest": _DIGEST_A,
        "dependency_lock_digest": _DIGEST_B,
        "created_at": "2026-01-01T00:00:00Z",
        "expires_at": "2026-02-01T00:00:00Z",
        "maximum_use_count": 25,
        "estimated_tokens_per_call": 2048,
        "daemon_url": "http://localhost:8000",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _response_payload() -> dict[str, object]:
    return {
        "bundles_read": 2,
        "events_seen": 9,
        "events_eligible": 4,
        "candidate_count": 1,
        "candidates": [
            {
                "cluster_digest": _DIGEST_A,
                "evidence_count": 2,
                "candidate_digest": _DIGEST_C,
                "validation_report_digest": _DIGEST_D,
                "holdout_report_digest": _DIGEST_E,
            }
        ],
        "rejection_counts": [{"reason": "unsigned_bundle", "count": 1}],
    }


@dataclass
class _StreamResponse:
    status_code: int = 200
    body: bytes = field(default_factory=lambda: json.dumps(_response_payload()).encode())
    headers: dict[str, str] = field(default_factory=dict)
    chunks: list[bytes] | None = None

    def __enter__(self) -> _StreamResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def iter_bytes(self) -> Iterator[bytes]:
        yield from self.chunks if self.chunks is not None else [self.body]


@dataclass
class _Client:
    response: _StreamResponse
    calls: list[dict[str, object]] = field(default_factory=list)

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def stream(self, method: str, url: str, **kwargs: object) -> _StreamResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.response


def _install_client(monkeypatch: pytest.MonkeyPatch, response: _StreamResponse) -> _Client:
    client = _Client(response)
    monkeypatch.setattr(
        "general_ludd.cli_decision_codification.httpx.Client",
        lambda **_kwargs: client,
    )
    return client


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    raise AssertionError("parser has no subcommands")


def test_parser_registers_analysis_and_bounded_operator_commands() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    add_decision_codification_subparser(subparsers)

    namespace = parser.parse_args(
        [
            "decision-codification",
            "analyze",
            "--project",
            "project-alpha",
            "--run-id",
            "run-1",
            "--run-id",
            "run-2",
            "--training-recipe-digest",
            _DIGEST_A,
            "--dependency-lock-digest",
            _DIGEST_B,
            "--created-at",
            "2026-01-01T00:00:00Z",
            "--expires-at",
            "2026-02-01T00:00:00Z",
            "--maximum-use-count",
            "25",
        ]
    )

    command_parser = subparsers.choices["decision-codification"]
    assert set(_subcommands(command_parser)) == {
        "activate",
        "analyze",
        "approve",
        "capture",
        "mine",
        "rollback",
        "status",
    }
    assert namespace.func is _cmd_analyze
    assert namespace.project == "project-alpha"
    assert namespace.run_ids == ["run-1", "run-2"]
    assert namespace.estimated_tokens_per_call == 0
    assert namespace.daemon_url == "http://localhost:8000"


def test_unified_parser_registers_decision_codification() -> None:
    from general_ludd.cli import build_parser

    parser, subcommand_map = build_parser()
    namespace = parser.parse_args(["decision-codification"])

    assert namespace.command == "decision-codification"
    assert "decision-codification" in subcommand_map


def test_request_payload_preserves_only_bounded_api_fields() -> None:
    payload = _request_payload(_args())

    assert payload == {
        "project_id": "project-alpha",
        "run_ids": ["run-1", "run_2"],
        "training_recipe_digest": _DIGEST_A,
        "dependency_lock_digest": _DIGEST_B,
        "created_at": "2026-01-01T00:00:00Z",
        "expires_at": "2026-02-01T00:00:00Z",
        "maximum_use_count": 25,
        "estimated_tokens_per_call": 2048,
    }


@pytest.mark.parametrize(
    ("overrides", "sensitive_value"),
    [
        ({"project": "../project"}, "../project"),
        ({"run_ids": ["run-1", "run-1"]}, "run-1"),
        ({"run_ids": ["../unsafe"]}, "../unsafe"),
        ({"run_ids": [f"run-{number}" for number in range(257)]}, "run-256"),
        ({"training_recipe_digest": "raw-key-material"}, "raw-key-material"),
        ({"created_at": "2026-01-01T00:00:00"}, "2026-01-01T00:00:00"),
        ({"expires_at": "2028-01-01T00:00:00Z"}, "2028-01-01T00:00:00Z"),
        ({"maximum_use_count": 1_000_001}, "1000001"),
        ({"estimated_tokens_per_call": 10_000_001}, "10000001"),
    ],
)
def test_invalid_requests_fail_before_http_without_reflection(
    overrides: dict[str, object],
    sensitive_value: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        _cmd_analyze(_args(**overrides))

    captured = capsys.readouterr()
    assert raised.value.code == 2
    assert captured.out == ""
    assert captured.err == "Error: invalid decision analysis request\n"
    assert sensitive_value not in captured.err


def test_success_posts_project_scoped_request_and_prints_only_safe_projection(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _install_client(monkeypatch, _StreamResponse())
    monkeypatch.setenv("GLUDD_AUTH_PSK", "super-secret-psk")

    _cmd_analyze(_args(daemon_url="http://daemon.internal:8000/"))

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert captured.err == ""
    assert output == _response_payload()
    assert "super-secret-psk" not in captured.out
    assert client.calls == [
        {
            "method": "POST",
            "url": "http://daemon.internal:8000/api/v1/decision-codification/analyze",
            "headers": {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer project-alpha:super-secret-psk",
            },
            "json": _request_payload(_args()),
        }
    ]


def test_auth_header_is_omitted_when_psk_is_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _install_client(monkeypatch, _StreamResponse())
    monkeypatch.delenv("GLUDD_AUTH_PSK", raising=False)

    _cmd_analyze(_args())

    capsys.readouterr()
    headers = client.calls[0]["headers"]
    assert isinstance(headers, dict)
    assert "Authorization" not in headers


def test_valid_declared_response_length_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    body = json.dumps(_response_payload()).encode()
    _install_client(
        monkeypatch,
        _StreamResponse(body=body, headers={"content-length": str(len(body))}),
    )

    _cmd_analyze(_args())

    assert json.loads(capsys.readouterr().out) == _response_payload()


@pytest.mark.parametrize("status_code", [300, 400, 401, 404, 422, 500, 503])
def test_non_2xx_errors_never_reflect_backend_body(
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "backend-secret-and-request-content"
    _install_client(
        monkeypatch,
        _StreamResponse(status_code=status_code, body=secret.encode()),
    )

    with pytest.raises(SystemExit) as raised:
        _cmd_analyze(_args())

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.out == ""
    assert captured.err == f"Error: decision analysis request failed (HTTP {status_code})\n"
    assert secret not in captured.err


@pytest.mark.parametrize("content_length", ["not-an-integer", "-1"])
def test_invalid_content_length_fails_closed(
    content_length: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_client(
        monkeypatch,
        _StreamResponse(headers={"content-length": content_length}),
    )

    with pytest.raises(SystemExit) as raised:
        _cmd_analyze(_args())

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.err == "Error: invalid decision analysis response\n"
    assert content_length not in captured.err


@pytest.mark.parametrize("status_code", [99, 600, cast(int, "200")])
def test_invalid_status_value_fails_closed(
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_client(monkeypatch, _StreamResponse(status_code=status_code))

    with pytest.raises(SystemExit) as raised:
        _cmd_analyze(_args())

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.err == "Error: invalid decision analysis response\n"


def test_non_text_internal_project_scope_fails_before_transport() -> None:
    with pytest.raises(_AnalysisUnavailable):
        _post_analysis({"project_id": 7}, daemon_url="http://localhost:8000")


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"[]",
        b'{"bundles_read":1,"bundles_read":2}',
        json.dumps({**_response_payload(), "private_evidence": "do-not-print"}).encode(),
        json.dumps({**_response_payload(), "candidate_count": 2}).encode(),
        json.dumps(
            {
                **_response_payload(),
                "rejection_counts": [{"reason": "backend_free_text", "count": 1}],
            }
        ).encode(),
    ],
)
def test_malformed_or_unsafe_response_fails_closed_without_reflection(
    body: bytes,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_client(monkeypatch, _StreamResponse(body=body))

    with pytest.raises(SystemExit) as raised:
        _cmd_analyze(_args())

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.out == ""
    assert captured.err == "Error: invalid decision analysis response\n"
    assert "do-not-print" not in captured.err
    assert "backend_free_text" not in captured.err


def test_declared_oversize_response_fails_before_reading_body(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    response = _StreamResponse(
        headers={"content-length": str(MAX_ANALYSIS_RESPONSE_BYTES + 1)},
        chunks=[b"must-not-be-read"],
    )
    _install_client(monkeypatch, response)

    with pytest.raises(SystemExit) as raised:
        _cmd_analyze(_args())

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.err == "Error: decision analysis response too large\n"


def test_streamed_oversize_response_fails_without_printing_content(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = b"stream-secret"
    response = _StreamResponse(
        chunks=[b"x" * MAX_ANALYSIS_RESPONSE_BYTES, secret],
    )
    _install_client(monkeypatch, response)

    with pytest.raises(SystemExit) as raised:
        _cmd_analyze(_args())

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.out == ""
    assert captured.err == "Error: decision analysis response too large\n"
    assert secret.decode() not in captured.err


def test_transport_error_is_bounded_and_does_not_reflect_exception(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = httpx.Request("POST", "http://localhost:8000")

    class _FailingClient(_Client):
        def stream(self, method: str, url: str, **kwargs: object) -> _StreamResponse:
            raise httpx.ConnectError("credential=transport-secret", request=request)

    monkeypatch.setattr(
        "general_ludd.cli_decision_codification.httpx.Client",
        lambda **_kwargs: _FailingClient(_StreamResponse()),
    )

    with pytest.raises(SystemExit) as raised:
        _cmd_analyze(_args())

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.out == ""
    assert captured.err == "Error: decision analysis request unavailable\n"
    assert "transport-secret" not in captured.err


def test_parser_exposes_no_key_or_raw_content_inputs() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    add_decision_codification_subparser(subparsers)
    commands = _subcommands(subparsers.choices["decision-codification"])

    destinations = {
        action.dest for command in commands.values() for action in command._actions
    }
    forbidden = {
        "artifact",
        "key",
        "key_material",
        "payload",
        "private_key",
        "prompt",
        "public_key",
        "raw_content",
        "secret",
    }
    assert destinations.isdisjoint(forbidden)


def _candidate() -> tuple[
    DecisionRuleBundleV1,
    EvaluationReportV1,
    EvaluationReportV1,
]:
    bundle = DecisionRuleBundleV1.create(
        schema="gludd.decision-rule-bundle/v1",
        project_id="project-alpha",
        decision_kind="review",
        feature_schema=_DIGEST_D,
        policy_compatibility=(_DIGEST_E,),
        risk_scope="low",
        root_id="node",
        default_leaf_id="abstain",
        nodes=(
            DecisionRuleNodeV1(
                node_id="node",
                feature_id="work_type",
                operator="eq",
                value="code",
                match_id="approve",
                miss_id="abstain",
            ),
        ),
        leaves=(
            DecisionRuleLeafV1(
                leaf_id="abstain",
                decision=None,
                support=0,
                confidence=0.0,
                outcome_counts=OutcomeCountsV1(
                    success=0,
                    failure=0,
                    reverted=0,
                    unknown=0,
                    unsafe=0,
                ),
                abstain=True,
            ),
            DecisionRuleLeafV1(
                leaf_id="approve",
                decision="approve",
                support=20,
                confidence=1.0,
                outcome_counts=OutcomeCountsV1(
                    success=20,
                    failure=0,
                    reverted=0,
                    unknown=0,
                    unsafe=0,
                ),
                abstain=False,
            ),
        ),
        corpus_digest=_DIGEST_A,
        training_recipe_digest=_DIGEST_B,
        dependency_lock_digest=_DIGEST_C,
        observed_context_digests=(_DIGEST_F,),
        created_at=_NOW,
        expires_at=_NOW + timedelta(days=30),
        maximum_use_count=100,
    )

    def report(exact_match_count: int) -> EvaluationReportV1:
        return EvaluationReportV1.create(
            schema="gludd.decision-evaluation-report/v1",
            candidate_digest=bundle.candidate_digest,
            corpus_digest=bundle.corpus_digest,
            project_id=bundle.project_id,
            decision_kind=bundle.decision_kind,
            created_at=_NOW,
            support_count=20,
            root_task_count=8,
            utc_day_count=3,
            source_agent_count=2,
            exact_match_count=exact_match_count,
            abstention_count=20 - exact_match_count,
            precision=1.0,
            leaf_results=(
                LeafEvaluationV1(
                    leaf_id="approve",
                    support=exact_match_count,
                    confidence=1.0,
                ),
            ),
            false_automation_count=0,
            conflict_count=0,
            unknown_feature_count=0,
            policy_mismatch_count=0,
            verified_failure_count=0,
            rollback_count=0,
            safety_violation_count=0,
            estimated_agent_calls_avoided=exact_match_count,
            estimated_tokens_avoided=exact_match_count * 100,
            historical_policy_digest=_DIGEST_E,
            current_policy_digest=_DIGEST_E,
            deterministic_replay_runs=2,
        )

    return bundle, report(19), report(20)


@dataclass
class _ReplayReader:
    bundle: object

    def read_verified(self, _run_id: str) -> object:
        return self.bundle


@dataclass
class _Analyzer:
    result: DecisionAnalysis

    def analyze(self, *_args: object, **_kwargs: object) -> DecisionAnalysis:
        return self.result


def _operator(tmp_path: Path) -> tuple[_DecisionOperator, DecisionRuleBundleV1]:
    bundle, validation, holdout = _candidate()
    analysis = DecisionAnalysis(
        bundles_read=1,
        events_seen=40,
        events_eligible=32,
        candidates=(
            DecisionCandidate(
                cluster_digest=_DIGEST_A,
                evidence_count=32,
                bundle=bundle,
                validation_report=validation,
                holdout_report=holdout,
            ),
        ),
        rejection_counts=(),
    )
    artifacts = DecisionArtifactStore(
        str(tmp_path / "artifacts"), key=b"artifact-key-1234"
    )
    rollout = RolloutController(
        artifacts,
        AtomicGenerationStore(),
        rollout_key=b"rollout-key-12345",
    )
    observability = DecisionReuseObservability(
        DurableGenerationStore(tmp_path / "observability.sqlite3"),
        rollout,
        artifacts,
        project_id="project-alpha",
        policy_digest=_DIGEST_E,
        clock=lambda: _NOW + timedelta(hours=1),
    )
    replay = _ReplayReader(
        SimpleNamespace(
            manifest=SimpleNamespace(
                run_id="run-1",
                project_id="project-alpha",
                integrity="signed",
                status="completed",
                event_count=2,
                events_sha256=_DIGEST_A,
            ),
            events=(object(), object()),
        )
    )
    operator = _DecisionOperator(
        adapter=cast("object", _Analyzer(analysis)),
        replay=cast("object", replay),
        artifacts=artifacts,
        rollout=rollout,
        project_id="project-alpha",
        policy_digest=_DIGEST_E,
        authorized_approver="human-1",
        observability=observability,
        clock=lambda: _NOW + timedelta(hours=1),
    )
    return operator, bundle


def test_operator_runs_mine_approval_activation_and_zdd_rollback(
    tmp_path: Path,
) -> None:
    operator, bundle = _operator(tmp_path)
    capture = operator.capture(
        project_id="project-alpha",
        policy_digest=_DIGEST_E,
        run_id="run-1",
    )
    mined = operator.mine(_request_payload(_args()))
    assert operator.mine(_request_payload(_args())) == mined

    approval = operator.approve(
        project_id="project-alpha",
        policy_digest=_DIGEST_E,
        decision_kind="review",
        candidate_digest=bundle.candidate_digest,
        report_digest=mined.candidates[0].holdout_report_digest,
        approver_identity="human-1",
        authorization_evidence_digest=_DIGEST_C,
        source_code_digest=_DIGEST_B,
        expires_at=_NOW + timedelta(days=2),
        rollout_plan=("shadow", "active"),
        maximum_use_count=50,
    )
    activated = operator.activate(
        project_id="project-alpha",
        policy_digest=_DIGEST_E,
        decision_kind="review",
        candidate_digest=bundle.candidate_digest,
        expected_receipt_digest=approval.current_receipt_digest,
        target_stage="active",
        approver_identity="human-1",
        authorization_evidence_digest=_DIGEST_C,
    )
    rolled_back = operator.rollback(
        project_id="project-alpha",
        policy_digest=_DIGEST_E,
        decision_kind="review",
        candidate_digest=bundle.candidate_digest,
        expected_receipt_digest=activated.current_receipt_digest,
        approver_identity="human-1",
        authorization_evidence_digest=_DIGEST_C,
    )

    assert capture.model_dump(mode="json") == {
        "run_id": "run-1",
        "project_id": "project-alpha",
        "status": "completed",
        "event_count": 2,
        "events_sha256": _DIGEST_A,
        "integrity": "signed",
    }
    assert mined.candidate_count == 1
    assert approval.action == "approval"
    assert approval.stage == "shadow"
    assert activated.action == "activation"
    assert activated.stage == "active"
    assert rolled_back.action == "rollback"
    assert rolled_back.stage == "disabled"
    assert rolled_back.current_candidate_digest is None


@pytest.mark.parametrize(
    ("changes", "secret"),
    [
        ({"project_id": "other-project"}, "other-project"),
        ({"policy_digest": _DIGEST_D}, _DIGEST_D),
        ({"approver_identity": "intruder-secret"}, "intruder-secret"),
    ],
)
def test_operator_lifecycle_mismatch_fails_closed_without_reflecting_input(
    tmp_path: Path,
    changes: dict[str, object],
    secret: str,
) -> None:
    operator, bundle = _operator(tmp_path)
    mined = operator.mine(_request_payload(_args()))
    kwargs: dict[str, object] = {
        "project_id": "project-alpha",
        "policy_digest": _DIGEST_E,
        "decision_kind": "review",
        "candidate_digest": bundle.candidate_digest,
        "report_digest": mined.candidates[0].holdout_report_digest,
        "approver_identity": "human-1",
        "authorization_evidence_digest": _DIGEST_C,
        "source_code_digest": _DIGEST_B,
        "expires_at": _NOW + timedelta(days=2),
        "rollout_plan": ("shadow", "active"),
        "maximum_use_count": 50,
    }
    kwargs.update(changes)

    with pytest.raises(_OperatorCLIError) as raised:
        operator.approve(**kwargs)  # type: ignore[arg-type]

    assert secret not in str(raised.value)


@pytest.mark.parametrize(
    "value",
    [None, "not-a-timestamp", "2026-10-07T12:00:00"],
)
def test_operator_timestamp_rejects_unbounded_or_naive_values(value: object) -> None:
    with pytest.raises(_OperatorCLIError):
        _utc_timestamp(value)


def test_operator_rejects_unsigned_capture_shadow_skip_stale_head_and_bad_clock(
    tmp_path: Path,
) -> None:
    operator, bundle = _operator(tmp_path)
    operator._replay.bundle.manifest.integrity = "unsigned"  # type: ignore[attr-defined]
    with pytest.raises(_OperatorCLIError):
        operator.capture(
            project_id="project-alpha",
            policy_digest=_DIGEST_E,
            run_id="run-1",
        )
    operator._replay.bundle.manifest.integrity = "signed"  # type: ignore[attr-defined]
    mined = operator.mine(_request_payload(_args()))
    approval = operator.approve(
        project_id="project-alpha",
        policy_digest=_DIGEST_E,
        decision_kind="review",
        candidate_digest=bundle.candidate_digest,
        report_digest=mined.candidates[0].holdout_report_digest,
        approver_identity="human-1",
        authorization_evidence_digest=_DIGEST_C,
        source_code_digest=_DIGEST_B,
        expires_at=_NOW + timedelta(days=2),
        rollout_plan=("shadow", "active"),
        maximum_use_count=50,
    )
    base = {
        "project_id": "project-alpha",
        "policy_digest": _DIGEST_E,
        "decision_kind": "review",
        "candidate_digest": bundle.candidate_digest,
        "expected_receipt_digest": approval.current_receipt_digest,
        "approver_identity": "human-1",
        "authorization_evidence_digest": _DIGEST_C,
    }
    with pytest.raises(_OperatorCLIError):
        operator.activate(target_stage="shadow", **base)  # type: ignore[arg-type]
    with pytest.raises(_OperatorCLIError):
        operator.activate(
            target_stage="active",
            **{**base, "expected_receipt_digest": _DIGEST_A},  # type: ignore[arg-type]
        )

    operator._clock = lambda: datetime(2026, 10, 7, 12)
    with pytest.raises(_OperatorCLIError):
        operator.activate(target_stage="active", **base)  # type: ignore[arg-type]


def test_load_operator_accepts_only_bounded_enabled_exact_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "general-ludd.yml"
    config_path.write_text("decision_codification: {}\n", encoding="utf-8")
    config = SimpleNamespace(
        project_id="project-alpha",
        policy_digest=_DIGEST_E,
    )
    components = SimpleNamespace(
        adapter=object(),
        replay=object(),
        artifacts=object(),
        rollout=object(),
        observability=object(),
    )
    monkeypatch.setattr(
        "general_ludd.cli_decision_codification.UserConfig.from_yaml",
        lambda _path: SimpleNamespace(decision_codification=config),
    )
    monkeypatch.setattr(
        "general_ludd.cli_decision_codification.build_configured_components",
        lambda _config: components,
    )
    args = argparse.Namespace(
        config=str(config_path),
        project="project-alpha",
        policy_digest=_DIGEST_E,
    )

    assert isinstance(_load_operator(args), _DecisionOperator)

    monkeypatch.setattr(
        "general_ludd.cli_decision_codification.build_configured_components",
        lambda _config: None,
    )
    with pytest.raises(_OperatorCLIError):
        _load_operator(args)
    with pytest.raises(_OperatorCLIError):
        _load_operator(argparse.Namespace(config="", project="x", policy_digest="x"))


def test_status_command_prints_only_the_authenticated_bounded_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    operator, _bundle = _operator(tmp_path)
    monkeypatch.setattr(
        "general_ludd.cli_decision_codification._load_operator",
        lambda _args: operator,
    )

    _cmd_status(
        argparse.Namespace(
            project="project-alpha",
            policy_digest=_DIGEST_E,
        )
    )

    output = json.loads(capsys.readouterr().out)
    assert output["schema"] == "gludd.decision-reuse-status/v1"
    assert output["project_id"] == "project-alpha"
    assert output["policy_digest"] == _DIGEST_E
    assert output["total_observations"] == 0
    assert len(output["summaries"]) == 5
    assert output["receipt_digest"].startswith("sha256:")
    assert output["authentication_tag"].startswith("hmac-sha256:")
    assert set(output) == {
        "authentication_tag",
        "policy_digest",
        "project_id",
        "receipt_digest",
        "schema",
        "summaries",
        "total_observations",
    }


def test_local_commands_print_only_validated_safe_results(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    capture = DecisionCaptureSummary(
        run_id="run-1",
        project_id="project-alpha",
        status="completed",
        event_count=2,
        events_sha256=_DIGEST_A,
        integrity="signed",
    )
    analysis = DecisionAnalysisResponse.model_validate_json(
        json.dumps(_response_payload())
    )
    lifecycle = DecisionLifecycleSummary(
        action="activation",
        operation_receipt_digest=_DIGEST_A,
        current_candidate_digest=_DIGEST_B,
        current_receipt_digest=_DIGEST_C,
        stage="active",
        epoch=1,
    )

    class _SuccessfulOperator:
        def capture(self, **_kwargs: object) -> DecisionCaptureSummary:
            return capture

        def mine(self, *_args: object) -> DecisionAnalysisResponse:
            return analysis

        def approve(self, **_kwargs: object) -> DecisionLifecycleSummary:
            return lifecycle.model_copy(update={"action": "approval", "stage": "shadow"})

        def activate(self, **_kwargs: object) -> DecisionLifecycleSummary:
            return lifecycle

        def rollback(self, **_kwargs: object) -> DecisionLifecycleSummary:
            return lifecycle.model_copy(update={"action": "rollback", "stage": "disabled"})

        def status(self, **_kwargs: object) -> DecisionCaptureSummary:
            return capture

    monkeypatch.setattr(
        "general_ludd.cli_decision_codification._load_operator",
        lambda _args: _SuccessfulOperator(),
    )
    namespace = argparse.Namespace(
        project="project-alpha",
        policy_digest=_DIGEST_E,
        run_id="run-1",
        run_ids=["run-1"],
        training_recipe_digest=_DIGEST_A,
        dependency_lock_digest=_DIGEST_B,
        created_at=_NOW.isoformat(),
        expires_at=(_NOW + timedelta(days=2)).isoformat(),
        maximum_use_count=50,
        estimated_tokens_per_call=0,
        decision_kind="review",
        candidate_digest=_DIGEST_A,
        report_digest=_DIGEST_B,
        approver="human-1",
        authorization_evidence_digest=_DIGEST_C,
        source_code_digest=_DIGEST_D,
        rollout_plan=["shadow", "active"],
        expected_receipt_digest=_DIGEST_F,
        target_stage="active",
    )

    for command in (
        _cmd_capture,
        _cmd_mine,
        _cmd_approve,
        _cmd_activate,
        _cmd_rollback,
        _cmd_status,
    ):
        command(namespace)

    outputs = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(outputs) == 6
    assert outputs[0]["events_sha256"] == _DIGEST_A
    assert outputs[1]["candidate_count"] == 1
    assert [item.get("action") for item in outputs[2:]] == [
        "approval",
        "activation",
        "rollback",
        None,
    ]


@pytest.mark.parametrize(
    ("command", "expected_method"),
    [
        (_cmd_capture, "capture"),
        (_cmd_mine, "mine"),
        (_cmd_approve, "approve"),
        (_cmd_activate, "activate"),
        (_cmd_rollback, "rollback"),
        (_cmd_status, "status"),
    ],
)
def test_local_commands_collapse_internal_errors_without_reflection(
    command: object,
    expected_method: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "raw-secret-content"

    class _FailingOperator:
        def __getattr__(self, name: str) -> object:
            assert name == expected_method

            def fail(*_args: object, **_kwargs: object) -> None:
                raise RuntimeError(secret)

            return fail

    monkeypatch.setattr(
        "general_ludd.cli_decision_codification._load_operator",
        lambda _args: _FailingOperator(),
    )
    namespace = argparse.Namespace(
        project="project-alpha",
        policy_digest=_DIGEST_E,
        run_id="run-1",
        run_ids=["run-1"],
        training_recipe_digest=_DIGEST_A,
        dependency_lock_digest=_DIGEST_B,
        created_at=_NOW.isoformat(),
        expires_at=(_NOW + timedelta(days=2)).isoformat(),
        maximum_use_count=50,
        estimated_tokens_per_call=0,
        decision_kind="review",
        candidate_digest=_DIGEST_A,
        report_digest=_DIGEST_B,
        approver="human-1",
        authorization_evidence_digest=_DIGEST_C,
        source_code_digest=_DIGEST_D,
        rollout_plan=["shadow", "active"],
        expected_receipt_digest=_DIGEST_F,
        target_stage="active",
    )

    with pytest.raises(SystemExit) as raised:
        cast("object", command)(namespace)  # type: ignore[operator]

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.out == ""
    assert "failed closed" in captured.err
    assert secret not in captured.err
