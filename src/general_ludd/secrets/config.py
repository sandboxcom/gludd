"""OpenBao secrets configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class OpenBaoConfig(BaseModel):
    mode: str = Field(default="auto", pattern="^(auto|external|disabled)$")
    backend: str = Field(default="openbao", pattern="^(openbao|vault)$")
    binary_path: str | None = None
    external_url: str | None = None
    external_token: str | None = None
    local_image: str = "ghcr.io/openbao/openbao"
    local_image_digest_pin: str | None = None
    local_container_runtime: str = "podman_preferred"
    kv_mount: str = "secret"
    auth_method: str = "approle"
    approle_role_name: str = "agentic-harness"
    weekly_image_update_scan: bool = True
    weekly_image_update_creates_manual_hold: bool = True

    @field_validator("kv_mount", "auth_method", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("field must not be empty")
        return v
