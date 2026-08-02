"""OpenBao secrets configuration."""

from __future__ import annotations

from typing import Self

from pydantic import (
    BaseModel,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from general_ludd.secrets.openbao_scope import validate_openbao_mount


class OpenBaoConfig(BaseModel):
    mode: str = Field(default="auto", pattern="^(auto|external|disabled)$")
    backend: str = Field(default="openbao", pattern="^(openbao|vault)$")
    binary_path: str | None = None
    external_url: str | None = None
    external_token: str | None = Field(default=None, repr=False)
    # Security: TLS verification for the external OpenBao client. True (default)
    # verifies against the system CA bundle; a str is treated as a path to a CA
    # bundle / cert. Disabling (False) is permitted but discouraged.
    external_tls_verify: bool | str = True
    local_image: str = "ghcr.io/openbao/openbao"
    local_image_digest_pin: str | None = None
    local_container_runtime: str = "podman_preferred"
    kv_mount: str = "secret"
    auth_method: str = "approle"
    approle_role_name: str = "agentic-harness"
    # D-15: every per-agent AppRole credential is finite.  Pydantic bounds are
    # deliberately hard ceilings; operators can tighten them without being
    # able to configure an unlimited (zero-use/zero-TTL) credential.
    approle_secret_id_ttl_seconds: int = Field(default=600, ge=30, le=86_400)
    approle_token_ttl_seconds: int = Field(default=3_600, ge=30, le=86_400)
    approle_token_max_ttl_seconds: int = Field(default=3_600, ge=30, le=86_400)
    approle_secret_id_num_uses: int = Field(default=1, ge=1, le=100)
    approle_token_num_uses: int = Field(default=128, ge=1, le=100_000)
    weekly_image_update_scan: bool = True
    weekly_image_update_creates_manual_hold: bool = True

    @field_serializer("external_token")
    def _mask_external_token(self, v: str | None) -> str | None:
        return None if v is None else "**REDACTED**"

    @field_validator("kv_mount", "auth_method", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("field must not be empty")
        return v

    @field_validator("kv_mount")
    @classmethod
    def _validate_kv_mount(cls, value: str) -> str:
        return validate_openbao_mount(value)

    @model_validator(mode="after")
    def _validate_approle_ttl_order(self) -> Self:
        if self.approle_token_ttl_seconds > self.approle_token_max_ttl_seconds:
            raise ValueError(
                "AppRole token TTL must not exceed its explicit maximum TTL"
            )
        return self
