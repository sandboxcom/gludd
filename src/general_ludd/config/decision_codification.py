"""Core, secret-indirect settings for optional decision codification."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from general_ludd.schemas.execution_identity import (
    ModelIdentityV1,
    RuntimeIdentityV1,
    SourceIdentityV1,
)

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


class DecisionCaptureIdentityConfig(BaseModel):
    """Exact, content-safe provenance attached to captured decision bundles."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: SourceIdentityV1
    runtime: RuntimeIdentityV1
    model: ModelIdentityV1

    @model_validator(mode="after")
    def _request_parameters_are_not_persisted(self) -> DecisionCaptureIdentityConfig:
        if self.model.request_parameters:
            raise ValueError("capture model request_parameters must be empty")
        return self


class DecisionCodificationConfig(BaseModel):
    """Secret-indirect, default-off durable runtime configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    project_id: str | None = None
    policy_digest: str | None = None
    replay_root: Path | None = None
    artifact_root: Path | None = None
    state_path: Path | None = None
    replay_key_id: str | None = None
    replay_key_envs: dict[str, str] = Field(default_factory=dict, max_length=16)
    artifact_key_env: str | None = None
    rollout_key_env: str | None = None
    busy_timeout_seconds: float = Field(default=10.0, gt=0.0, le=60.0)
    capture_identity: DecisionCaptureIdentityConfig | None = None
    capture_retention_days: int = Field(default=30, ge=1, le=366)
    capture_max_total_bytes: int = Field(
        default=256 * 1024 * 1024,
        ge=128 * 1024,
        le=10 * 1024 * 1024 * 1024,
    )
    capture_scan_limit: int = Field(default=1_000, ge=1, le=10_000)

    @model_validator(mode="after")
    def _enabled_configuration_is_complete(self) -> Self:
        if not self.enabled:
            return self
        missing = tuple(
            name
            for name in (
                "project_id",
                "policy_digest",
                "replay_root",
                "artifact_root",
                "state_path",
                "replay_key_id",
                "artifact_key_env",
                "rollout_key_env",
            )
            if getattr(self, name) is None
        )
        if missing:
            raise ValueError(
                "enabled decision codification requires " + ", ".join(missing)
            )
        assert self.project_id is not None
        assert self.policy_digest is not None
        assert self.replay_key_id is not None
        assert self.artifact_key_env is not None
        assert self.rollout_key_env is not None
        if _SAFE_IDENTIFIER.fullmatch(self.project_id) is None:
            raise ValueError("project_id is invalid")
        if _SHA256.fullmatch(self.policy_digest) is None:
            raise ValueError("policy_digest is invalid")
        if _SAFE_IDENTIFIER.fullmatch(self.replay_key_id) is None:
            raise ValueError("replay_key_id is invalid")
        if self.replay_key_id not in self.replay_key_envs:
            raise ValueError("replay_key_id has no configured environment binding")
        for key_id, environment_name in self.replay_key_envs.items():
            if _SAFE_IDENTIFIER.fullmatch(key_id) is None:
                raise ValueError("replay key identifier is invalid")
            self._validate_environment_name(environment_name)
        self._validate_environment_name(self.artifact_key_env)
        self._validate_environment_name(self.rollout_key_env)
        assert self.state_path is not None
        if self.state_path.name in {"", ".", ".."}:
            raise ValueError("state_path must identify a database file")
        return self

    @staticmethod
    def _validate_environment_name(value: str) -> None:
        if _ENV_NAME.fullmatch(value) is None:
            raise ValueError("key environment variable name is invalid")


__all__ = ["DecisionCaptureIdentityConfig", "DecisionCodificationConfig"]
