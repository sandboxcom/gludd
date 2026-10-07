"""Durable multiworker state and live outcome feedback regressions."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

import general_ludd.decision_codification.durable_feedback as durable_feedback_module
from general_ludd.config.user_config import UserConfig
from general_ludd.decision_codification.artifact_store import DecisionArtifactStore
from general_ludd.decision_codification.configuration import (
    DecisionCaptureIdentityConfig,
    DecisionCodificationConfig,
    DecisionCodificationConfigurationError,
    build_configured_adapter,
)
from general_ludd.decision_codification.durable import (
    DurableGenerationStore,
    DurableGenerationStoreError,
)
from general_ludd.decision_codification.rollout import (
    GenerationPointer,
    RolloutController,
    RolloutError,
)
from general_ludd.decision_codification.schema import (
    DecisionApplicationOutcomeV1,
    DecisionKind,
    RolloutStage,
    VerifiedOutcome,
)

SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64
NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)


def test_split_durable_facades_preserve_the_generation_state_chain() -> None:
    """Keep public-store behavior routed through both cohesive split modules."""
    assert issubclass(
        DurableGenerationStore,
        durable_feedback_module._DurableFeedbackStore,
    )
    assert (
        durable_feedback_module._DurableFeedbackStore.__mro__[1].__module__
        == "general_ludd.decision_codification.durable_generation"
    )


def _pointer(candidate_digest: str, receipt_digest: str) -> GenerationPointer:
    return GenerationPointer(
        project_id="project-1",
        decision_kind=DecisionKind.REVIEW,
        candidate_digest=candidate_digest,
        receipt_digest=receipt_digest,
        stage=RolloutStage.ACTIVE,
        epoch=0,
    )


def _outcome(
    *,
    candidate_digest: str = SHA_A,
    application_id: str = SHA_C,
    outcome: VerifiedOutcome = VerifiedOutcome.SUCCESS,
) -> DecisionApplicationOutcomeV1:
    return DecisionApplicationOutcomeV1(
        schema="gludd.decision-application-outcome/v1",
        project_id="project-1",
        decision_kind=DecisionKind.REVIEW,
        candidate_digest=candidate_digest,
        application_id=application_id,
        rollout_stage=RolloutStage.ACTIVE,
        outcome=outcome,
        occurred_at=NOW,
        terminal_event_id=application_id,
        evidence_digest=SHA_B,
    )


def test_durable_store_serializes_cross_worker_cas_and_use_limits(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "decision-state.sqlite3"
    first = DurableGenerationStore(state_path)
    second = DurableGenerationStore(state_path)
    contenders = (
        _pointer(SHA_A, SHA_B),
        _pointer(SHA_B, SHA_C),
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            pool.map(
                lambda pointer: (
                    DurableGenerationStore(state_path).compare_and_swap(
                        pointer,
                        expected_candidate_digest=None,
                    )
                ),
                contenders,
            )
        )

    winners = tuple(result for result in results if result is not None)
    assert len(winners) == 1
    assert first.current("project-1", DecisionKind.REVIEW) == winners[0]
    assert second.current("project-1", DecisionKind.REVIEW) == winners[0]
    assert first.reserve_use(winners[0].candidate_digest, SHA_A, 1)
    assert second.reserve_use(winners[0].candidate_digest, SHA_A, 1)
    assert not second.reserve_use(winners[0].candidate_digest, SHA_B, 1)
    assert first.use_count(winners[0].candidate_digest) == 1


def test_durable_lifecycle_supports_rollback_revocation_and_tombstones(
    tmp_path: Path,
) -> None:
    store = DurableGenerationStore(tmp_path / "decision-state.sqlite3")
    assert store.path == (tmp_path / "decision-state.sqlite3").absolute()
    assert store.current("project-1", DecisionKind.REVIEW) is None
    assert store.inactive_reason("project-1", DecisionKind.REVIEW) is None
    assert not store.is_drift_held(SHA_A)
    assert not store.is_revoked(SHA_A)
    assert not store.has_application(SHA_A, SHA_C)

    first = store.compare_and_swap(_pointer(SHA_A, SHA_B), expected_candidate_digest=None)
    assert first is not None
    second = store.compare_and_swap(_pointer(SHA_B, SHA_C), expected_candidate_digest=SHA_A)
    assert second is not None
    restored = store.rollback(
        "project-1",
        DecisionKind.REVIEW,
        SHA_B,
        lambda pointer: pointer.candidate_digest == SHA_A,
    )
    assert restored is not None
    assert restored.candidate_digest == SHA_A
    assert store.current("project-1", DecisionKind.REVIEW) == restored

    assert store.compare_and_swap(
        _pointer(SHA_B, SHA_C), expected_candidate_digest=SHA_A
    ) is not None
    store.mark_drift_hold(SHA_A, "failure_rate")
    assert store.is_drift_held(SHA_A)
    assert (
        store.rollback(
            "project-1", DecisionKind.REVIEW, SHA_B, lambda _pointer: True
        )
        is None
    )
    assert store.current("project-1", DecisionKind.REVIEW) is None

    assert store.compare_and_swap(_pointer(SHA_C, SHA_B), expected_candidate_digest=None)
    assert not store.revoke(
        "project-1", DecisionKind.REVIEW, SHA_A, remove_pointer=False
    )
    assert store.revoke(
        "project-1", DecisionKind.REVIEW, SHA_C, remove_pointer=False
    )
    assert store.current("project-1", DecisionKind.REVIEW) is not None
    assert store.is_revoked(SHA_C)
    assert store.revoke(
        "project-1", DecisionKind.REVIEW, SHA_C, remove_pointer=True
    )
    assert store.current("project-1", DecisionKind.REVIEW) is None
    assert store.inactive_reason("project-1", DecisionKind.REVIEW) == "revoked"
    store.force_revoke(SHA_B)
    assert store.is_revoked(SHA_B)


def test_durable_rollback_rejects_missing_or_stale_expectations(tmp_path: Path) -> None:
    store = DurableGenerationStore(tmp_path / "decision-state.sqlite3")
    with pytest.raises(RolloutError, match="compare-and-swap"):
        store.rollback("project-1", DecisionKind.REVIEW, SHA_A, lambda _pointer: True)

    assert store.compare_and_swap(_pointer(SHA_A, SHA_B), expected_candidate_digest=None)
    with pytest.raises(RolloutError, match="compare-and-swap"):
        store.rollback("project-1", DecisionKind.REVIEW, SHA_B, lambda _pointer: True)


@pytest.mark.parametrize("timeout", [True, "ten", 0, 61])
def test_durable_configuration_rejects_unsafe_paths_and_timeouts(
    tmp_path: Path,
    timeout: object,
) -> None:
    with pytest.raises(ValueError, match="busy timeout"):
        DurableGenerationStore(
            tmp_path / "state.sqlite3",
            busy_timeout_seconds=cast(Any, timeout),
        )


def test_durable_configuration_rejects_symlinks_and_unusable_files(
    tmp_path: Path,
) -> None:
    target = tmp_path / "real.sqlite3"
    target.touch()
    link = tmp_path / "linked.sqlite3"
    link.symlink_to(target)
    with pytest.raises(DurableGenerationStoreError, match="symlink"):
        DurableGenerationStore(link)

    directory = tmp_path / "directory.sqlite3"
    directory.mkdir()
    with pytest.raises(DurableGenerationStoreError, match="initialization failed"):
        DurableGenerationStore(directory)


def test_durable_state_fails_closed_on_schema_and_row_corruption(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "decision-state.sqlite3"
    store = DurableGenerationStore(state_path)
    assert store.compare_and_swap(_pointer(SHA_A, SHA_B), expected_candidate_digest=None)
    connection = sqlite3.connect(state_path)
    try:
        connection.execute("UPDATE generation_pointers SET stage = 'invalid'")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(DurableGenerationStoreError, match="pointer is invalid"):
        store.current("project-1", DecisionKind.REVIEW)

    version_path = tmp_path / "version.sqlite3"
    DurableGenerationStore(version_path)
    connection = sqlite3.connect(version_path)
    try:
        connection.execute(
            "UPDATE state_metadata SET value = '999' WHERE key = 'schema_version'"
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(DurableGenerationStoreError, match="version is unsupported"):
        DurableGenerationStore(version_path)


def test_durable_validation_is_closed_before_state_mutation(tmp_path: Path) -> None:
    store = DurableGenerationStore(tmp_path / "decision-state.sqlite3")
    with pytest.raises(DurableGenerationStoreError, match="project scope"):
        store.current("bad project", DecisionKind.REVIEW)
    with pytest.raises(DurableGenerationStoreError, match="decision kind"):
        store.current("project-1", cast(Any, "review"))
    with pytest.raises(DurableGenerationStoreError, match="digest"):
        store.is_revoked("not-a-digest")
    with pytest.raises(DurableGenerationStoreError, match="drift reason"):
        store.mark_drift_hold(SHA_A, "unsafe reason")
    with pytest.raises(DurableGenerationStoreError, match="use count"):
        store.reserve_use(SHA_A, SHA_B, cast(Any, True))
    with pytest.raises(DurableGenerationStoreError, match="use count"):
        store.reserve_use(SHA_A, SHA_B, 0)
    with pytest.raises(DurableGenerationStoreError, match="timestamp"):
        store.recent_outcomes(SHA_A, now=datetime(2026, 10, 7))

    invalid_stage = GenerationPointer(
        project_id="project-1",
        decision_kind=DecisionKind.REVIEW,
        candidate_digest=SHA_A,
        receipt_digest=SHA_B,
        stage=cast(Any, "active"),
        epoch=0,
    )
    with pytest.raises(DurableGenerationStoreError, match="stage"):
        store.compare_and_swap(invalid_stage, expected_candidate_digest=None)
    invalid_epoch = GenerationPointer(
        project_id="project-1",
        decision_kind=DecisionKind.REVIEW,
        candidate_digest=SHA_A,
        receipt_digest=SHA_B,
        stage=RolloutStage.ACTIVE,
        epoch=-1,
    )
    with pytest.raises(DurableGenerationStoreError, match="epoch"):
        store.compare_and_swap(invalid_epoch, expected_candidate_digest=None)


def test_durable_outcome_feedback_is_idempotent_and_holds_every_worker(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "decision-state.sqlite3"
    first_store = DurableGenerationStore(state_path)
    second_store = DurableGenerationStore(state_path)
    artifacts = DecisionArtifactStore(str(tmp_path / "artifacts"), key=b"artifact-key")
    first = RolloutController(artifacts, first_store, rollout_key=b"rollout-key")
    second = RolloutController(artifacts, second_store, rollout_key=b"rollout-key")
    assert first_store.compare_and_swap(
        _pointer(SHA_A, SHA_B), expected_candidate_digest=None
    ) is not None
    assert first_store.reserve_use(SHA_A, SHA_C, 10)

    feedback = first.record_application_outcome(
        _outcome(outcome=VerifiedOutcome.FAILURE)
    )
    duplicate = second.record_application_outcome(
        _outcome(outcome=VerifiedOutcome.FAILURE)
    )

    assert feedback.recorded is True
    assert feedback.drift_reason == "failure_rate"
    assert duplicate.recorded is False
    assert second.is_drift_held(SHA_A)
    assert second_store.recent_outcomes(SHA_A, now=NOW) == (
        VerifiedOutcome.FAILURE,
    )

    with pytest.raises(RolloutError, match="conflicting outcome"):
        second.record_application_outcome(
            _outcome(outcome=VerifiedOutcome.SUCCESS)
        )

    assert first_store.reserve_use(SHA_B, SHA_A, 10)
    unsafe = first.record_application_outcome(
        _outcome(
            candidate_digest=SHA_B,
            application_id=SHA_A,
            outcome=VerifiedOutcome.UNSAFE,
        )
    )
    assert unsafe.recorded is True
    assert unsafe.drift_reason == "safety_violation"
    assert second_store.is_drift_held(SHA_B)


def test_outcome_requires_an_issued_application_and_terminal_evidence(
    tmp_path: Path,
) -> None:
    store = DurableGenerationStore(tmp_path / "decision-state.sqlite3")
    artifacts = DecisionArtifactStore(str(tmp_path / "artifacts"), key=b"artifact-key")
    controller = RolloutController(artifacts, store, rollout_key=b"rollout-key")

    with pytest.raises(RolloutError, match="issued application"):
        controller.record_application_outcome(_outcome())
    with pytest.raises(ValidationError, match="terminal evidence"):
        DecisionApplicationOutcomeV1(
            schema="gludd.decision-application-outcome/v1",
            project_id="project-1",
            decision_kind=DecisionKind.REVIEW,
            candidate_digest=SHA_A,
            application_id=SHA_C,
            rollout_stage=RolloutStage.ACTIVE,
            outcome=VerifiedOutcome.FAILURE,
            occurred_at=NOW,
            terminal_event_id=None,
            evidence_digest=None,
        )


def test_durable_outcome_corruption_fails_closed(tmp_path: Path) -> None:
    state_path = tmp_path / "decision-state.sqlite3"
    store = DurableGenerationStore(state_path)
    assert store.reserve_use(SHA_A, SHA_C, 10)
    assert store.record_outcome(_outcome())
    connection = sqlite3.connect(state_path)
    try:
        connection.execute("UPDATE application_outcomes SET outcome = 'invalid'")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DurableGenerationStoreError, match="outcome is invalid"):
        store.record_outcome(_outcome())
    with pytest.raises(DurableGenerationStoreError, match="outcome is invalid"):
        store.recent_outcomes(SHA_A, now=NOW)


def _enabled_config(tmp_path: Path) -> DecisionCodificationConfig:
    return DecisionCodificationConfig(
        enabled=True,
        project_id="project-1",
        policy_digest="sha256:" + "e" * 64,
        replay_root=tmp_path / "replays",
        artifact_root=tmp_path / "artifacts",
        state_path=tmp_path / "decision-state.sqlite3",
        replay_key_id="primary",
        replay_key_envs={"primary": "TEST_REPLAY_KEY"},
        artifact_key_env="TEST_ARTIFACT_KEY",
        rollout_key_env="TEST_ROLLOUT_KEY",
    )


def test_configuration_is_default_off_secret_indirect_and_durable(
    tmp_path: Path,
) -> None:
    assert build_configured_adapter(DecisionCodificationConfig(), environ={}) is None
    with pytest.raises(ValidationError, match="project_id"):
        DecisionCodificationConfig(enabled=True)

    config = _enabled_config(tmp_path)
    environment = {
        "TEST_REPLAY_KEY": "replay-key-material-123",
        "TEST_ARTIFACT_KEY": "artifact-key-material-123",
        "TEST_ROLLOUT_KEY": "rollout-key-material-123",
    }
    first = build_configured_adapter(config, environ=environment)
    second = build_configured_adapter(config, environ=environment)

    assert first is not None
    assert second is not None
    assert first.project_id == second.project_id == "project-1"
    assert first.policy_digest == second.policy_digest
    assert config.model_dump(mode="json").get("artifact_key") is None
    assert config.state_path is not None
    assert config.state_path.is_file()

    with pytest.raises(DecisionCodificationConfigurationError, match="unavailable"):
        build_configured_adapter(config, environ={})
    with pytest.raises(DecisionCodificationConfigurationError, match="unavailable"):
        build_configured_adapter(
            config,
            environ={
                "TEST_REPLAY_KEY": "short",
                "TEST_ARTIFACT_KEY": "artifact-key-material-123",
                "TEST_ROLLOUT_KEY": "rollout-key-material-123",
            },
        )


def test_configured_adapter_automatically_signs_agent_outcome_capture(
    tmp_path: Path,
) -> None:
    from general_ludd.replay.store import RunBundleStore

    identity = DecisionCaptureIdentityConfig.model_validate(
        {
            "source": {
                "repository_url_sha256": "sha256:" + "1" * 64,
                "commit_sha": "2" * 40,
                "tree_sha": "3" * 40,
                "branch": "development",
                "dirty": False,
            },
            "runtime": {
                "gludd_version": "0.1.1",
                "python_version": "3.14.0",
                "os": "darwin",
                "architecture": "arm64",
                "config_sha256": "sha256:" + "4" * 64,
                "feature_flags": {"decision_codification": True},
            },
            "model": {
                "provider": "openai",
                "profile": "review",
                "model": "gpt-6",
                "request_parameters": {},
                "provider_revision": None,
            },
        }
    )
    config = _enabled_config(tmp_path).model_copy(
        update={
            "capture_identity": identity,
            "capture_retention_days": 14,
            "capture_max_total_bytes": 1_048_576,
            "capture_scan_limit": 100,
        }
    )
    environment = {
        "TEST_REPLAY_KEY": "replay-key-material-123",
        "TEST_ARTIFACT_KEY": "artifact-key-material-123",
        "TEST_ROLLOUT_KEY": "rollout-key-material-123",
    }
    adapter = build_configured_adapter(config, environ=environment)
    assert adapter is not None

    receipt = adapter.record_agent_decision_outcome(
        project_id="project-1",
        decision_kind=DecisionKind.REVIEW,
        features={"risk_band": "low", "operation_class": "review"},
        decision="approve",
        capture_id="return-review:RET-CONFIG-001",
        root_task_id="TODO-CONFIG-001",
        outcome=VerifiedOutcome.SUCCESS,
        occurred_at=NOW,
    )

    assert receipt is not None
    store = RunBundleStore(
        tmp_path / "replays",
        verification_keys={"primary": b"replay-key-material-123"},
        active_key_id="primary",
    )
    bundle = store.read_verified(receipt.run_id)
    assert [event.type for event in bundle.events] == [
        "review.decided",
        "decision.outcome",
    ]


def test_user_configuration_parses_the_typed_default_off_contract(
    tmp_path: Path,
) -> None:
    assert UserConfig().decision_codification == DecisionCodificationConfig()
    nested = UserConfig.model_validate(
        {"decision_codification": _enabled_config(tmp_path).model_dump(mode="json")}
    )
    assert nested.decision_codification.enabled is True
    assert nested.decision_codification.project_id == "project-1"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"project_id": "bad project"}, "project_id is invalid"),
        ({"policy_digest": "bad"}, "policy_digest is invalid"),
        ({"replay_key_id": "bad key"}, "replay_key_id is invalid"),
        ({"replay_key_envs": {"secondary": "TEST_KEY"}}, "no configured"),
        (
            {
                "replay_key_envs": {
                    "primary": "TEST_REPLAY_KEY",
                    "bad key": "OTHER_KEY",
                }
            },
            "identifier is invalid",
        ),
        ({"replay_key_envs": {"primary": "lowercase"}}, "variable name"),
        ({"artifact_key_env": "lowercase"}, "variable name"),
        ({"rollout_key_env": "lowercase"}, "variable name"),
        ({"state_path": Path(".")}, "database file"),
    ],
)
def test_enabled_configuration_rejects_unsafe_scope_and_secret_bindings(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    payload = _enabled_config(tmp_path).model_dump(mode="python")
    payload.update(changes)
    with pytest.raises(ValidationError, match=message):
        DecisionCodificationConfig.model_validate(payload)


def test_configured_adapter_rejects_wrong_type_and_wraps_storage_failure(
    tmp_path: Path,
) -> None:
    with pytest.raises(DecisionCodificationConfigurationError, match="invalid"):
        build_configured_adapter(cast(Any, object()), environ={})

    state_directory = tmp_path / "state-directory"
    state_directory.mkdir()
    config = _enabled_config(tmp_path).model_copy(
        update={"state_path": state_directory}
    )
    environment = {
        "TEST_REPLAY_KEY": "replay-key-material-123",
        "TEST_ARTIFACT_KEY": "artifact-key-material-123",
        "TEST_ROLLOUT_KEY": "rollout-key-material-123",
    }
    with pytest.raises(
        DecisionCodificationConfigurationError,
        match="durable configuration failed",
    ):
        build_configured_adapter(config, environ=environment)


def test_daemon_automatically_builds_the_enabled_durable_adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import general_ludd.daemon as daemon_module

    config = _enabled_config(tmp_path)
    monkeypatch.setenv("TEST_REPLAY_KEY", "replay-key-material-123")
    monkeypatch.setenv("TEST_ARTIFACT_KEY", "artifact-key-material-123")
    monkeypatch.setenv("TEST_ROLLOUT_KEY", "rollout-key-material-123")
    monkeypatch.setattr(
        daemon_module,
        "load_startup_config",
        lambda _config_dir=None: {
            "user_config": SimpleNamespace(decision_codification=config),
            "project_gludd_dir": None,
        },
    )

    app = daemon_module.create_daemon_app(tick_interval=300.0)

    assert app.state.decision_codification is not None
    assert app.state.decision_codification.project_id == "project-1"
    assert app.state.decision_codification.policy_digest == config.policy_digest
