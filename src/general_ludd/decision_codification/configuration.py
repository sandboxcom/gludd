"""Typed opt-in construction for durable live decision codification."""

from __future__ import annotations

import os
from collections.abc import Mapping

from general_ludd.config.decision_codification import DecisionCodificationConfig
from general_ludd.decision_codification.artifact_store import DecisionArtifactStore
from general_ludd.decision_codification.durable import DurableGenerationStore
from general_ludd.decision_codification.rollout import RolloutController
from general_ludd.decision_codification.runtime import DecisionRuntime
from general_ludd.decision_codification.service import DecisionCodificationAdapter
from general_ludd.replay.store import RunBundleStore


class DecisionCodificationConfigurationError(RuntimeError):
    """Raised when enabled live codification cannot be built safely."""


def _secret_key(environment_name: str, environ: Mapping[str, str]) -> bytes:
    value = environ.get(environment_name)
    if value is None or len(value.encode("utf-8")) < 16:
        raise DecisionCodificationConfigurationError(
            "decision codification key material is unavailable"
        )
    return value.encode("utf-8")


def build_configured_adapter(
    config: DecisionCodificationConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> DecisionCodificationAdapter | None:
    """Build one durable adapter or preserve the default-disabled path."""
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
        artifacts = DecisionArtifactStore(
            str(config.artifact_root),
            key=artifact_key,
        )
        pointers = DurableGenerationStore(
            config.state_path,
            busy_timeout_seconds=config.busy_timeout_seconds,
        )
        rollout = RolloutController(
            artifacts,
            pointers,
            rollout_key=rollout_key,
        )
        runtime = DecisionRuntime(artifacts, rollout)
        return DecisionCodificationAdapter(
            bundle_reader=replay,
            runtime=runtime,
            project_id=config.project_id,
            policy_digest=config.policy_digest,
        )
    except DecisionCodificationConfigurationError:
        raise
    except Exception as exc:
        raise DecisionCodificationConfigurationError(
            "decision codification durable configuration failed"
        ) from exc


__all__ = [
    "DecisionCodificationConfig",
    "DecisionCodificationConfigurationError",
    "build_configured_adapter",
]
