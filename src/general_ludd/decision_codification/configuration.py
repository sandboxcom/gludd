"""Typed opt-in construction for durable live decision codification."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import create_engine

from general_ludd.config.decision_codification import (
    DecisionCaptureIdentityConfig,
    DecisionCodificationConfig,
)
from general_ludd.decision_codification.artifact_store import DecisionArtifactStore
from general_ludd.decision_codification.capture import DecisionOutcomeRecorder
from general_ludd.decision_codification.durable import DurableGenerationStore
from general_ludd.decision_codification.observability import DecisionReuseObservability
from general_ludd.decision_codification.rollout import GenerationStore, RolloutController
from general_ludd.decision_codification.runtime import DecisionRuntime
from general_ludd.decision_codification.service import DecisionCodificationAdapter
from general_ludd.decision_codification.shared_generation import (
    PostgresGenerationStore,
)
from general_ludd.decision_codification.telemetry import DecisionCodificationTelemetry
from general_ludd.replay.store import RunBundleStore


class DecisionCodificationConfigurationError(RuntimeError):
    """Raised when enabled live codification cannot be built safely."""


@dataclass(frozen=True, slots=True)
class DecisionCodificationComponents:
    """Durable capabilities shared by the daemon and the operator CLI."""

    adapter: DecisionCodificationAdapter
    replay: RunBundleStore
    artifacts: DecisionArtifactStore
    pointers: GenerationStore
    rollout: RolloutController
    observability: DecisionReuseObservability


def _secret_key(environment_name: str, environ: Mapping[str, str]) -> bytes:
    value = environ.get(environment_name)
    if value is None or len(value.encode("utf-8")) < 16:
        raise DecisionCodificationConfigurationError(
            "decision codification key material is unavailable"
        )
    return value.encode("utf-8")


def _generation_database_url(
    environment_name: str,
    environ: Mapping[str, str],
) -> str:
    value = environ.get(environment_name)
    if not value:
        raise DecisionCodificationConfigurationError(
            "decision generation database URL is unavailable"
        )
    if not value.startswith("postgresql+psycopg://"):
        raise DecisionCodificationConfigurationError(
            "decision generation database URL must use PostgreSQL psycopg"
        )
    return value


def build_configured_components(
    config: DecisionCodificationConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> DecisionCodificationComponents | None:
    """Build one durable capability set or preserve the default-disabled path."""
    if not isinstance(config, DecisionCodificationConfig):
        raise DecisionCodificationConfigurationError(
            "decision codification configuration is invalid"
        )
    if not config.enabled:
        return None
    environment = os.environ if environ is None else environ
    assert config.project_id is not None
    assert config.policy_digest is not None
    assert config.replay_root is not None
    assert config.artifact_root is not None
    assert config.state_path is not None
    assert config.replay_key_id is not None
    assert config.artifact_key_env is not None
    assert config.rollout_key_env is not None
    try:
        verification_keys = {
            key_id: _secret_key(environment_name, environment)
            for key_id, environment_name in sorted(config.replay_key_envs.items())
        }
        artifact_key = _secret_key(config.artifact_key_env, environment)
        rollout_key = _secret_key(config.rollout_key_env, environment)
        replay = RunBundleStore(
            config.replay_root,
            verification_keys=verification_keys,
            active_key_id=config.replay_key_id,
            lock_timeout=config.busy_timeout_seconds,
        )
        capture_identity = config.capture_identity
        decision_recorder = (
            None
            if capture_identity is None
            else DecisionOutcomeRecorder(
                replay,
                project_id=config.project_id,
                policy_digest=config.policy_digest,
                correlation_key=verification_keys[config.replay_key_id],
                source=capture_identity.source,
                runtime=capture_identity.runtime,
                model=capture_identity.model,
                retention_days=config.capture_retention_days,
                max_total_bytes=config.capture_max_total_bytes,
                scan_limit=config.capture_scan_limit,
            )
        )
        artifacts = DecisionArtifactStore(
            str(config.artifact_root),
            key=artifact_key,
        )
        local_state = DurableGenerationStore(
            config.state_path,
            busy_timeout_seconds=config.busy_timeout_seconds,
        )
        if config.generation_database_url_env is None:
            pointers: GenerationStore = local_state
        else:
            database_url = _generation_database_url(
                config.generation_database_url_env,
                environment,
            )
            engine = create_engine(
                database_url,
                pool_pre_ping=True,
                connect_args={
                    "connect_timeout": max(
                        1,
                        int(config.busy_timeout_seconds),
                    )
                },
            )
            shared_pointers = PostgresGenerationStore(
                engine,
                artifacts,
                lock_timeout_seconds=config.busy_timeout_seconds,
            )
            shared_pointers.verify_ready(config.project_id)
            pointers = shared_pointers
        rollout = RolloutController(
            artifacts,
            pointers,
            rollout_key=rollout_key,
        )
        telemetry = DecisionCodificationTelemetry()
        observability = DecisionReuseObservability(
            local_state,
            rollout,
            artifacts,
            project_id=config.project_id,
            policy_digest=config.policy_digest,
            telemetry=telemetry,
        )
        runtime = DecisionRuntime(artifacts, rollout, telemetry=telemetry)
        adapter = DecisionCodificationAdapter(
            bundle_reader=replay,
            runtime=runtime,
            project_id=config.project_id,
            policy_digest=config.policy_digest,
            decision_recorder=decision_recorder,
            observability=observability,
        )
        return DecisionCodificationComponents(
            adapter=adapter,
            replay=replay,
            artifacts=artifacts,
            pointers=pointers,
            rollout=rollout,
            observability=observability,
        )
    except DecisionCodificationConfigurationError:
        raise
    except Exception as exc:
        raise DecisionCodificationConfigurationError(
            "decision codification durable configuration failed"
        ) from exc


def build_configured_adapter(
    config: DecisionCodificationConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> DecisionCodificationAdapter | None:
    """Build one durable adapter or preserve the default-disabled path."""
    components = build_configured_components(config, environ=environ)
    return None if components is None else components.adapter


__all__ = [
    "DecisionCaptureIdentityConfig",
    "DecisionCodificationComponents",
    "DecisionCodificationConfig",
    "DecisionCodificationConfigurationError",
    "build_configured_adapter",
    "build_configured_components",
]
