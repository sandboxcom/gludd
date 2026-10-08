"""Bounded operator CLI for the decision-codification lifecycle.

Remote analysis remains read-only. Local commands compose the existing signed
replay, immutable approval, and atomic rollout services while accepting only
identifiers, digests, timestamps, and closed enums. Raw evidence and key
material are neither command inputs nor output fields.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, NoReturn, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from general_ludd.approval.gate import ApprovalDecision, ApprovalGate
from general_ludd.config.user_config import UserConfig
from general_ludd.decision_codification.approval import DecisionApprovalService
from general_ludd.decision_codification.artifact_store import (
    ArtifactAlreadyExists,
    DecisionArtifactStore,
)
from general_ludd.decision_codification.configuration import (
    DecisionCodificationConfigurationError,
    build_configured_components,
)
from general_ludd.decision_codification.observability import (
    DecisionReuseObservability,
    DecisionReuseStatusReceipt,
)
from general_ludd.decision_codification.rollout import (
    GenerationPointer,
    RolloutController,
)
from general_ludd.decision_codification.schema import (
    ApprovalReceiptV1,
    DecisionKind,
    DecisionRuleBundleV1,
    EvaluationReportV1,
    LifecycleState,
    ReceiptType,
    RolloutStage,
)
from general_ludd.decision_codification.service import (
    DecisionAnalysis,
    DecisionAnalysisError,
    DecisionCodificationAdapter,
)
from general_ludd.replay.schema import (
    ReplaySchemaError,
    SafeRunId,
    decode_replay_json_object,
)
from general_ludd.replay.store import RunBundleStore
from general_ludd.routers.decision_codification import (
    MAX_API_ANALYSIS_CANDIDATES,
    DecisionAnalysisRequest,
    DecisionAnalysisResponse,
    DecisionCandidateSummary,
    DecisionRejectionSummary,
)
from general_ludd.schemas.execution_identity import BoundedIdentifier, Sha256Digest

MAX_ANALYSIS_RESPONSE_BYTES = 128 * 1024
MAX_OPERATOR_CONFIG_BYTES = 128 * 1024
_ANALYSIS_PATH = "/api/v1/decision-codification/analyze"
_REQUEST_TIMEOUT_SECONDS = 30.0
_APPROVER_IDENTITY_ENV = "GLUDD_DECISION_APPROVER_ID"
_DIGEST_ADAPTER = TypeAdapter(Sha256Digest, config=ConfigDict(strict=True))
_TerminalCaptureStatus = Literal["completed", "failed", "cancelled"]


class _StrictOperatorOutput(BaseModel):
    """Closed JSON output that cannot carry raw evidence or exception text."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DecisionCaptureSummary(_StrictOperatorOutput):
    """Content-free verification summary for one captured signed bundle."""

    run_id: SafeRunId
    project_id: BoundedIdentifier
    status: _TerminalCaptureStatus
    event_count: int = Field(ge=0, le=100_000)
    events_sha256: Sha256Digest
    integrity: Literal["signed"]


class DecisionLifecycleSummary(_StrictOperatorOutput):
    """Digest-only result of one immutable lifecycle mutation."""

    action: Literal["approval", "activation", "rollback"]
    operation_receipt_digest: Sha256Digest
    current_candidate_digest: Sha256Digest | None
    current_receipt_digest: Sha256Digest | None
    stage: Literal["shadow", "canary", "canary_10", "canary_50", "active", "disabled"]
    epoch: int | None = Field(default=None, ge=1)


class _AnalysisCLIError(Exception):
    """Base class for errors with fixed, content-free CLI diagnostics."""


class _AnalysisHTTPStatusError(_AnalysisCLIError):
    """A bounded non-success HTTP status returned by the daemon."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__()


class _AnalysisResponseTooLarge(_AnalysisCLIError):
    """The daemon response exceeded the local output safety bound."""


class _InvalidAnalysisResponse(_AnalysisCLIError):
    """The daemon response did not match the digest-only wire contract."""


class _AnalysisUnavailable(_AnalysisCLIError):
    """The daemon request could not be completed safely."""


class DecisionOperatorError(RuntimeError):
    """A local operator request failed without exposing internal content.

    Library callers may catch this public exception while the command handlers
    continue to translate it into their existing fixed, content-free messages.
    """

    def __init__(self) -> None:
        super().__init__("decision operator request failed closed")


# Compatibility for callers that imported the original private name. New code
# should catch ``DecisionOperatorError``.
_OperatorCLIError = DecisionOperatorError


def _terminal_capture_status(value: object) -> _TerminalCaptureStatus:
    """Narrow a replay status to the terminal capture vocabulary."""
    if type(value) is not str:
        raise _OperatorCLIError
    if value == "completed":
        return "completed"
    if value == "failed":
        return "failed"
    if value == "cancelled":
        return "cancelled"
    raise _OperatorCLIError


class _DecisionOperator:
    """Thin composition over existing capture, mining, approval, and rollout APIs."""

    def __init__(
        self,
        *,
        adapter: DecisionCodificationAdapter,
        replay: RunBundleStore,
        artifacts: DecisionArtifactStore,
        rollout: RolloutController,
        project_id: str,
        policy_digest: str,
        authorized_approver: str | None,
        observability: DecisionReuseObservability | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._adapter = adapter
        self._replay = replay
        self._artifacts = artifacts
        self._rollout = rollout
        self._project_id = project_id
        self._policy_digest = policy_digest
        self._authorized_approver = authorized_approver
        self._observability = observability
        self._clock = clock or (lambda: datetime.now(UTC))

    def require_scope(self, project_id: str, policy_digest: str) -> None:
        """Require the command and configured project/policy to match exactly."""
        if (
            type(project_id) is not str
            or type(policy_digest) is not str
            or not hmac.compare_digest(project_id, self._project_id)
            or not hmac.compare_digest(policy_digest, self._policy_digest)
        ):
            raise _OperatorCLIError

    def capture(
        self,
        *,
        project_id: str,
        policy_digest: str,
        run_id: str,
    ) -> DecisionCaptureSummary:
        """Verify an automatic capture and return no event payload content."""
        self.require_scope(project_id, policy_digest)
        try:
            verified = self._replay.read_verified(run_id)
            manifest = verified.manifest
            status = _terminal_capture_status(manifest.status)
            if (
                manifest.run_id != run_id
                or manifest.project_id != self._project_id
                or manifest.integrity != "signed"
                or manifest.event_count != len(verified.events)
            ):
                raise _OperatorCLIError
            return DecisionCaptureSummary(
                run_id=manifest.run_id,
                project_id=manifest.project_id,
                status=status,
                event_count=manifest.event_count,
                events_sha256=manifest.events_sha256,
                integrity=manifest.integrity,
            )
        except _OperatorCLIError:
            raise
        except Exception:
            raise _OperatorCLIError from None

    def mine(self, payload: Mapping[str, object]) -> DecisionAnalysisResponse:
        """Mine signed bundles and persist only authenticated candidate artifacts."""
        try:
            request = DecisionAnalysisRequest.model_validate(payload, strict=True)
            self.require_scope(request.project_id, self._policy_digest)
            result = self._adapter.analyze(
                request.run_ids,
                training_recipe_digest=request.training_recipe_digest,
                dependency_lock_digest=request.dependency_lock_digest,
                created_at=request.created_at,
                expires_at=request.expires_at,
                maximum_use_count=request.maximum_use_count,
                estimated_tokens_per_call=request.estimated_tokens_per_call,
            )
            if len(result.candidates) > MAX_API_ANALYSIS_CANDIDATES:
                raise _OperatorCLIError
            for candidate in result.candidates:
                self._persist_rule(candidate.bundle)
                self._persist_report(candidate.validation_report)
                self._persist_report(candidate.holdout_report)
            return self._analysis_response(result)
        except _OperatorCLIError:
            raise
        except (DecisionAnalysisError, TypeError, ValueError, ValidationError):
            raise _OperatorCLIError from None
        except Exception:
            raise _OperatorCLIError from None

    def status(
        self,
        *,
        project_id: str,
        policy_digest: str,
    ) -> DecisionReuseStatusReceipt:
        """Return one exact-scope, digest-bound observability receipt."""
        self.require_scope(project_id, policy_digest)
        if self._observability is None:
            raise _OperatorCLIError
        try:
            return self._observability.status_receipt(
                project_id=project_id,
                policy_digest=policy_digest,
            )
        except Exception:
            raise _OperatorCLIError from None

    def approve(
        self,
        *,
        project_id: str,
        policy_digest: str,
        decision_kind: str,
        candidate_digest: str,
        report_digest: str,
        approver_identity: str,
        authorization_evidence_digest: str,
        source_code_digest: str,
        expires_at: datetime,
        rollout_plan: tuple[str, ...],
        maximum_use_count: int,
    ) -> DecisionLifecycleSummary:
        """Create one immutable approval and CAS-install its shadow generation."""
        try:
            self.require_scope(project_id, policy_digest)
            kind = DecisionKind(decision_kind)
            bundle = self._artifacts.read_rule_bundle(self._digest(candidate_digest))
            report = self._artifacts.read_evaluation_report(self._digest(report_digest))
            self._require_candidate_scope(bundle, kind)
            if report.current_policy_digest != self._policy_digest:
                raise _OperatorCLIError
            service = self._approval_service(kind)
            receipt = service.approve(
                bundle,
                report,
                decision=ApprovalDecision.APPROVED,
                approver_identity=approver_identity,
                authorization_evidence_digest=self._digest(
                    authorization_evidence_digest
                ),
                source_code_digest=self._digest(source_code_digest),
                policy_digest=self._policy_digest,
                now=self._now(),
                expires_at=expires_at,
                rollout_plan=rollout_plan,
                maximum_use_count=maximum_use_count,
            )
            pointer = self._rollout.install(
                bundle,
                receipt,
                expected_candidate_digest=None,
            )
            if pointer is None:
                raise _OperatorCLIError
            return self._lifecycle_summary("approval", receipt.receipt_digest, pointer)
        except _OperatorCLIError:
            raise
        except Exception:
            raise _OperatorCLIError from None

    def activate(
        self,
        *,
        project_id: str,
        policy_digest: str,
        decision_kind: str,
        candidate_digest: str,
        expected_receipt_digest: str,
        target_stage: str,
        approver_identity: str,
        authorization_evidence_digest: str,
    ) -> DecisionLifecycleSummary:
        """Append and atomically apply exactly one approved rollout-plan stage."""
        try:
            self.require_scope(project_id, policy_digest)
            kind = DecisionKind(decision_kind)
            expected_candidate = self._digest(candidate_digest)
            expected_receipt = self._digest(expected_receipt_digest)
            stage = RolloutStage(target_stage)
            if stage is RolloutStage.SHADOW:
                raise _OperatorCLIError
            current, bundle, previous = self._current_generation(
                kind,
                expected_candidate,
                expected_receipt,
            )
            service = self._approval_service(kind)
            receipt = service.append_lifecycle(
                previous,
                receipt_type=ReceiptType.PROMOTION,
                lifecycle_state=LifecycleState(stage.value),
                decision=ApprovalDecision.APPROVED,
                approver_identity=approver_identity,
                authorization_evidence_digest=self._digest(
                    authorization_evidence_digest
                ),
                now=self._now(),
            )
            pointer = self._rollout.promote(
                receipt,
                expected_candidate_digest=current.candidate_digest,
            )
            self._require_candidate_scope(bundle, kind)
            return self._lifecycle_summary("activation", receipt.receipt_digest, pointer)
        except _OperatorCLIError:
            raise
        except Exception:
            raise _OperatorCLIError from None

    def rollback(
        self,
        *,
        project_id: str,
        policy_digest: str,
        decision_kind: str,
        candidate_digest: str,
        expected_receipt_digest: str,
        approver_identity: str,
        authorization_evidence_digest: str,
    ) -> DecisionLifecycleSummary:
        """Append a rollback and atomically restore prior compatible state or off."""
        try:
            self.require_scope(project_id, policy_digest)
            kind = DecisionKind(decision_kind)
            current, _bundle, previous = self._current_generation(
                kind,
                self._digest(candidate_digest),
                self._digest(expected_receipt_digest),
            )
            now = self._now()
            service = self._approval_service(kind)
            receipt = service.append_lifecycle(
                previous,
                receipt_type=ReceiptType.ROLLBACK,
                lifecycle_state=LifecycleState.ROLLED_BACK,
                decision=ApprovalDecision.APPROVED,
                approver_identity=approver_identity,
                authorization_evidence_digest=self._digest(
                    authorization_evidence_digest
                ),
                now=now,
            )
            pointer = self._rollout.rollback(
                receipt,
                expected_candidate_digest=current.candidate_digest,
                now=now,
                policy_digest=self._policy_digest,
            )
            return self._lifecycle_summary("rollback", receipt.receipt_digest, pointer)
        except _OperatorCLIError:
            raise
        except Exception:
            raise _OperatorCLIError from None

    def _approval_service(self, kind: DecisionKind) -> DecisionApprovalService:
        identity = self._authorized_approver
        if identity is None or not 1 <= len(identity.encode("utf-8")) <= 256:
            raise _OperatorCLIError

        def authorized(candidate: str, project: str, candidate_kind: DecisionKind) -> bool:
            return (
                hmac.compare_digest(candidate, identity)
                and hmac.compare_digest(project, self._project_id)
                and candidate_kind is kind
            )

        return DecisionApprovalService(
            self._artifacts,
            ApprovalGate(),
            authorizer=authorized,
        )

    def _current_generation(
        self,
        kind: DecisionKind,
        expected_candidate: str,
        expected_receipt: str,
    ) -> tuple[GenerationPointer, DecisionRuleBundleV1, ApprovalReceiptV1]:
        current = self._rollout.current(self._project_id, kind)
        if (
            current is None
            or current.candidate_digest != expected_candidate
            or current.receipt_digest != expected_receipt
        ):
            raise _OperatorCLIError
        bundle, previous = self._rollout.verified_generation(current)
        self._require_candidate_scope(bundle, kind)
        if previous.policy_digest != self._policy_digest:
            raise _OperatorCLIError
        return current, bundle, previous

    def _require_candidate_scope(
        self,
        bundle: DecisionRuleBundleV1,
        kind: DecisionKind,
    ) -> None:
        if (
            bundle.project_id != self._project_id
            or bundle.decision_kind is not kind
            or self._policy_digest not in bundle.policy_compatibility
        ):
            raise _OperatorCLIError

    def _persist_rule(self, bundle: DecisionRuleBundleV1) -> None:
        try:
            self._artifacts.create_rule_bundle(bundle)
        except ArtifactAlreadyExists:
            if self._artifacts.read_rule_bundle(bundle.candidate_digest) != bundle:
                raise _OperatorCLIError from None

    def _persist_report(self, report: EvaluationReportV1) -> None:
        try:
            self._artifacts.create_evaluation_report(report)
        except ArtifactAlreadyExists:
            if self._artifacts.read_evaluation_report(report.report_digest) != report:
                raise _OperatorCLIError from None

    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise _OperatorCLIError
        return now.astimezone(UTC)

    @staticmethod
    def _digest(value: str) -> str:
        try:
            return _DIGEST_ADAPTER.validate_python(value, strict=True)
        except ValidationError:
            raise _OperatorCLIError from None

    @staticmethod
    def _analysis_response(result: DecisionAnalysis) -> DecisionAnalysisResponse:
        candidates = tuple(
            DecisionCandidateSummary(
                cluster_digest=candidate.cluster_digest,
                evidence_count=candidate.evidence_count,
                candidate_digest=candidate.bundle.candidate_digest,
                validation_report_digest=candidate.validation_report.report_digest,
                holdout_report_digest=candidate.holdout_report.report_digest,
            )
            for candidate in result.candidates
        )
        return DecisionAnalysisResponse(
            bundles_read=result.bundles_read,
            events_seen=result.events_seen,
            events_eligible=result.events_eligible,
            candidate_count=len(candidates),
            candidates=candidates,
            rejection_counts=tuple(
                DecisionRejectionSummary(reason=reason, count=count)
                for reason, count in result.rejection_counts
            ),
        )

    @staticmethod
    def _lifecycle_summary(
        action: Literal["approval", "activation", "rollback"],
        operation_receipt_digest: str,
        pointer: GenerationPointer | None,
    ) -> DecisionLifecycleSummary:
        return DecisionLifecycleSummary(
            action=action,
            operation_receipt_digest=operation_receipt_digest,
            current_candidate_digest=(
                None if pointer is None else pointer.candidate_digest
            ),
            current_receipt_digest=None if pointer is None else pointer.receipt_digest,
            stage="disabled" if pointer is None else pointer.stage.value,
            epoch=None if pointer is None else pointer.epoch,
        )


def _exit(message: str, *, code: int) -> NoReturn:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def _request_payload(args: argparse.Namespace) -> dict[str, object]:
    """Return the exact bounded request schema or raise ``ValidationError``."""
    request = DecisionAnalysisRequest.model_validate(
        {
            "project_id": args.project,
            "run_ids": args.run_ids,
            "training_recipe_digest": args.training_recipe_digest,
            "dependency_lock_digest": args.dependency_lock_digest,
            "created_at": args.created_at,
            "expires_at": args.expires_at,
            "maximum_use_count": args.maximum_use_count,
            "estimated_tokens_per_call": args.estimated_tokens_per_call,
        },
        strict=True,
    )
    return cast(dict[str, object], request.model_dump(mode="json"))


def _headers(project_id: str) -> dict[str, str]:
    """Build JSON headers, binding configured daemon auth to the exact project."""
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    psk = os.environ.get("GLUDD_AUTH_PSK", "").strip()
    if psk:
        headers["Authorization"] = f"Bearer {project_id}:{psk}"
    return headers


def _read_response(response: httpx.Response) -> bytes:
    """Read a response incrementally while enforcing a strict byte bound."""
    declared_length = response.headers.get("content-length")
    if declared_length is not None:
        try:
            parsed_length = int(declared_length)
        except ValueError:
            raise _InvalidAnalysisResponse from None
        if parsed_length < 0:
            raise _InvalidAnalysisResponse
        if parsed_length > MAX_ANALYSIS_RESPONSE_BYTES:
            raise _AnalysisResponseTooLarge

    chunks: list[bytes] = []
    received = 0
    for chunk in response.iter_bytes():
        received += len(chunk)
        if received > MAX_ANALYSIS_RESPONSE_BYTES:
            raise _AnalysisResponseTooLarge
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_response(body: bytes) -> DecisionAnalysisResponse:
    """Validate the response as the strict digest/count/enum projection."""
    try:
        decode_replay_json_object(body)
        return DecisionAnalysisResponse.model_validate_json(body, strict=True)
    except (ReplaySchemaError, TypeError, ValueError, ValidationError):
        raise _InvalidAnalysisResponse from None


def _post_analysis(
    payload: dict[str, object],
    *,
    daemon_url: str,
) -> DecisionAnalysisResponse:
    """POST one bounded request through the maintained httpx client."""
    endpoint = daemon_url.rstrip("/") + _ANALYSIS_PATH
    project_id = payload["project_id"]
    if not isinstance(project_id, str):
        raise _AnalysisUnavailable

    try:
        with httpx.Client(
            timeout=httpx.Timeout(_REQUEST_TIMEOUT_SECONDS),
            follow_redirects=False,
            trust_env=False,
        ) as client, client.stream(
            "POST",
            endpoint,
            headers=_headers(project_id),
            json=payload,
        ) as response:
            status_code = response.status_code
            if type(status_code) is not int or not 100 <= status_code <= 599:
                raise _InvalidAnalysisResponse
            if not 200 <= status_code < 300:
                raise _AnalysisHTTPStatusError(status_code)
            body = _read_response(response)
    except _AnalysisCLIError:
        raise
    except Exception:
        raise _AnalysisUnavailable from None

    return _validate_response(body)


def _cmd_analyze(args: argparse.Namespace) -> None:
    """Analyze verified run bundles without exposing evidence or lifecycle actions."""
    try:
        payload = _request_payload(args)
    except (AttributeError, TypeError, ValueError, ValidationError):
        _exit("invalid decision analysis request", code=2)

    try:
        response = _post_analysis(payload, daemon_url=args.daemon_url)
    except _AnalysisHTTPStatusError as exc:
        _exit(f"decision analysis request failed (HTTP {exc.status_code})", code=1)
    except _AnalysisResponseTooLarge:
        _exit("decision analysis response too large", code=1)
    except _InvalidAnalysisResponse:
        _exit("invalid decision analysis response", code=1)
    except _AnalysisUnavailable:
        _exit("decision analysis request unavailable", code=1)

    safe_output = response.model_dump(mode="json")
    print(json.dumps(safe_output, sort_keys=True, separators=(",", ":")))


def _load_operator(args: argparse.Namespace) -> _DecisionOperator:
    """Load one bounded, enabled, secret-indirect local operator composition."""
    try:
        raw_path = args.config
        if type(raw_path) is not str or not raw_path:
            raise _OperatorCLIError
        path = Path(raw_path)
        if path.is_symlink() or not path.is_file():
            raise _OperatorCLIError
        before = path.stat()
        if not 0 < before.st_size <= MAX_OPERATOR_CONFIG_BYTES:
            raise _OperatorCLIError
        config = UserConfig.from_yaml(path).decision_codification
        after = path.stat()
        if (
            path.is_symlink()
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or after.st_size > MAX_OPERATOR_CONFIG_BYTES
        ):
            raise _OperatorCLIError
        components = build_configured_components(config)
        if components is None or config.project_id is None or config.policy_digest is None:
            raise _OperatorCLIError
        authorized_approver = os.environ.get(_APPROVER_IDENTITY_ENV)
        operator = _DecisionOperator(
            adapter=components.adapter,
            replay=components.replay,
            artifacts=components.artifacts,
            rollout=components.rollout,
            project_id=config.project_id,
            policy_digest=config.policy_digest,
            authorized_approver=authorized_approver,
            observability=components.observability,
        )
        operator.require_scope(args.project, args.policy_digest)
        return operator
    except _OperatorCLIError:
        raise
    except (DecisionCodificationConfigurationError, OSError, TypeError, ValueError):
        raise _OperatorCLIError from None


def _utc_timestamp(value: object) -> datetime:
    """Parse one timezone-aware timestamp without reflecting rejected input."""
    if type(value) is not str:
        raise _OperatorCLIError
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _OperatorCLIError from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _OperatorCLIError
    return parsed.astimezone(UTC)


def _print_operator_output(value: BaseModel) -> None:
    """Print only a schema-validated safe projection."""
    print(
        json.dumps(
            value.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _cmd_capture(args: argparse.Namespace) -> None:
    """Verify one automatic signed capture without exposing event content."""
    try:
        result = _load_operator(args).capture(
            project_id=args.project,
            policy_digest=args.policy_digest,
            run_id=args.run_id,
        )
    except Exception:
        _exit("decision capture command failed closed", code=1)
    _print_operator_output(result)


def _cmd_mine(args: argparse.Namespace) -> None:
    """Mine and authenticate bounded candidates without approving them."""
    try:
        result = _load_operator(args).mine(_request_payload(args))
    except Exception:
        _exit("decision mining command failed closed", code=1)
    _print_operator_output(result)


def _cmd_status(args: argparse.Namespace) -> None:
    """Print one privacy-safe immutable decision-reuse status receipt."""
    try:
        result = _load_operator(args).status(
            project_id=args.project,
            policy_digest=args.policy_digest,
        )
    except Exception:
        _exit("decision observability status failed closed", code=1)
    _print_operator_output(result)


def _cmd_approve(args: argparse.Namespace) -> None:
    """Create one exact immutable human approval in shadow mode."""
    try:
        result = _load_operator(args).approve(
            project_id=args.project,
            policy_digest=args.policy_digest,
            decision_kind=args.decision_kind,
            candidate_digest=args.candidate_digest,
            report_digest=args.report_digest,
            approver_identity=args.approver,
            authorization_evidence_digest=args.authorization_evidence_digest,
            source_code_digest=args.source_code_digest,
            expires_at=_utc_timestamp(args.expires_at),
            rollout_plan=tuple(args.rollout_plan),
            maximum_use_count=args.maximum_use_count,
        )
    except Exception:
        _exit("decision approval command failed closed", code=1)
    _print_operator_output(result)


def _cmd_activate(args: argparse.Namespace) -> None:
    """Promote exactly one approved stage using a stale-head guard."""
    try:
        result = _load_operator(args).activate(
            project_id=args.project,
            policy_digest=args.policy_digest,
            decision_kind=args.decision_kind,
            candidate_digest=args.candidate_digest,
            expected_receipt_digest=args.expected_receipt_digest,
            target_stage=args.target_stage,
            approver_identity=args.approver,
            authorization_evidence_digest=args.authorization_evidence_digest,
        )
    except Exception:
        _exit("decision activation command failed closed", code=1)
    _print_operator_output(result)


def _cmd_rollback(args: argparse.Namespace) -> None:
    """Atomically restore a compatible generation or disable reuse."""
    try:
        result = _load_operator(args).rollback(
            project_id=args.project,
            policy_digest=args.policy_digest,
            decision_kind=args.decision_kind,
            candidate_digest=args.candidate_digest,
            expected_receipt_digest=args.expected_receipt_digest,
            approver_identity=args.approver,
            authorization_evidence_digest=args.authorization_evidence_digest,
        )
    except Exception:
        _exit("decision rollback command failed closed", code=1)
    _print_operator_output(result)


def _add_local_scope(parser: argparse.ArgumentParser) -> None:
    """Add the exact config/project/policy binding shared by local commands."""
    parser.add_argument("--config", required=True, help="Bounded Gludd YAML path")
    parser.add_argument("--project", required=True, help="Exact project identifier")
    parser.add_argument("--policy-digest", required=True, help="Exact sha256: policy")


def _add_approval_identity(parser: argparse.ArgumentParser) -> None:
    """Add content-free human authorization bindings; key material stays in env."""
    parser.add_argument("--approver", required=True, help="Authorized operator identity")
    parser.add_argument(
        "--authorization-evidence-digest",
        required=True,
        help="sha256: digest of external authorization evidence",
    )


def add_decision_codification_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register remote analysis and bounded local lifecycle commands."""
    parser = subparsers.add_parser(
        "decision-codification",
        help="Mine and operate digest-bound reusable decisions.",
    )
    parser.set_defaults(func=None)
    commands = parser.add_subparsers(dest="decision_codification_command")

    analyze = commands.add_parser(
        "analyze",
        help="Request bounded proposal analysis; does not approve or activate candidates.",
    )
    analyze.add_argument("--project", required=True, help="Exact project identifier")
    analyze.add_argument(
        "--run-id",
        dest="run_ids",
        action="append",
        required=True,
        help="Verified run ID (repeat 1-256 times)",
    )
    analyze.add_argument("--training-recipe-digest", required=True, help="sha256: digest")
    analyze.add_argument("--dependency-lock-digest", required=True, help="sha256: digest")
    analyze.add_argument("--created-at", required=True, help="Timezone-aware ISO-8601 timestamp")
    analyze.add_argument("--expires-at", required=True, help="Timezone-aware ISO-8601 timestamp")
    analyze.add_argument(
        "--maximum-use-count",
        required=True,
        type=int,
        help="Candidate use bound (1-1000000)",
    )
    analyze.add_argument(
        "--estimated-tokens-per-call",
        type=int,
        default=0,
        help="Estimated token bound per call (0-10000000)",
    )
    analyze.add_argument("--daemon-url", default="http://localhost:8000")
    analyze.set_defaults(func=_cmd_analyze)

    capture = commands.add_parser(
        "capture",
        help="Verify and summarize an automatically captured signed bundle.",
    )
    _add_local_scope(capture)
    capture.add_argument("--run-id", required=True, help="Exact signed run ID")
    capture.set_defaults(func=_cmd_capture)

    status = commands.add_parser(
        "status",
        help="Print bounded durable decision-reuse evidence for the exact scope.",
    )
    _add_local_scope(status)
    status.set_defaults(func=_cmd_status)

    mine = commands.add_parser(
        "mine",
        help="Mine signed captures into authenticated, unapproved artifacts.",
    )
    _add_local_scope(mine)
    mine.add_argument(
        "--run-id",
        dest="run_ids",
        action="append",
        required=True,
        help="Verified run ID (repeat 1-256 times)",
    )
    mine.add_argument("--training-recipe-digest", required=True, help="sha256: digest")
    mine.add_argument("--dependency-lock-digest", required=True, help="sha256: digest")
    mine.add_argument("--created-at", required=True, help="Timezone-aware ISO-8601 timestamp")
    mine.add_argument("--expires-at", required=True, help="Timezone-aware ISO-8601 timestamp")
    mine.add_argument(
        "--maximum-use-count",
        required=True,
        type=int,
        help="Candidate use bound (1-1000000)",
    )
    mine.add_argument(
        "--estimated-tokens-per-call",
        type=int,
        default=0,
        help="Estimated token bound per call (0-10000000)",
    )
    mine.set_defaults(func=_cmd_mine)

    approve = commands.add_parser(
        "approve",
        help="Approve an exact stored candidate and install it in shadow.",
    )
    _add_local_scope(approve)
    approve.add_argument(
        "--decision-kind",
        required=True,
        choices=[kind.value for kind in DecisionKind],
    )
    approve.add_argument("--candidate-digest", required=True, help="Exact candidate sha256:")
    approve.add_argument("--report-digest", required=True, help="Exact holdout sha256:")
    approve.add_argument("--source-code-digest", required=True, help="Exact source sha256:")
    _add_approval_identity(approve)
    approve.add_argument("--expires-at", required=True, help="Approval expiry in ISO-8601")
    approve.add_argument(
        "--stage",
        dest="rollout_plan",
        action="append",
        required=True,
        choices=[stage.value for stage in RolloutStage],
        help="Approved monotonic stage (repeat; begin with shadow)",
    )
    approve.add_argument(
        "--maximum-use-count",
        required=True,
        type=int,
        help="Approval use bound (1-1000000)",
    )
    approve.add_argument(
        "--confirm",
        required=True,
        choices=["approve-exact-digests"],
        help="Explicit human confirmation token",
    )
    approve.set_defaults(func=_cmd_approve)

    activate = commands.add_parser(
        "activate",
        help="Promote the current candidate exactly one approved stage.",
    )
    _add_local_scope(activate)
    activate.add_argument(
        "--decision-kind",
        required=True,
        choices=[kind.value for kind in DecisionKind],
    )
    activate.add_argument("--candidate-digest", required=True, help="Expected candidate sha256:")
    activate.add_argument(
        "--expected-receipt-digest",
        required=True,
        help="Expected current lifecycle receipt sha256:",
    )
    activate.add_argument(
        "--target-stage",
        required=True,
        choices=[stage.value for stage in RolloutStage if stage is not RolloutStage.SHADOW],
    )
    _add_approval_identity(activate)
    activate.add_argument(
        "--confirm",
        required=True,
        choices=["activate-next-stage"],
        help="Explicit human confirmation token",
    )
    activate.set_defaults(func=_cmd_activate)

    rollback = commands.add_parser(
        "rollback",
        help="Atomically restore the prior compatible generation or disable reuse.",
    )
    _add_local_scope(rollback)
    rollback.add_argument(
        "--decision-kind",
        required=True,
        choices=[kind.value for kind in DecisionKind],
    )
    rollback.add_argument("--candidate-digest", required=True, help="Expected candidate sha256:")
    rollback.add_argument(
        "--expected-receipt-digest",
        required=True,
        help="Expected current lifecycle receipt sha256:",
    )
    _add_approval_identity(rollback)
    rollback.add_argument(
        "--confirm",
        required=True,
        choices=["rollback-current-generation"],
        help="Explicit human confirmation token",
    )
    rollback.set_defaults(func=_cmd_rollback)


__all__ = [
    "MAX_ANALYSIS_RESPONSE_BYTES",
    "MAX_OPERATOR_CONFIG_BYTES",
    "add_decision_codification_subparser",
]
