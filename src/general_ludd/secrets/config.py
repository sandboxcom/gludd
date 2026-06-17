"""OpenBao secrets configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


def _strip_and_require(v: object, field_name: str = "field") -> str:
    """Strip whitespace and reject empty values for string config fields.

    Shared by all ``@field_validator`` decorators in this module that enforce
    the same strip-and-require contract, avoiding copy-paste of the same two
    lines across multiple validator methods.

    ``field_name`` is used only in the ``ValueError`` message so operators
    know which field was invalid.
    """
    if isinstance(v, str):
        v = v.strip()
    if not v:
        raise ValueError(f"{field_name} must not be empty")
    return str(v)


class OpenBaoConfig(BaseModel):
    mode: str = Field(default="auto", pattern="^(auto|external|disabled)$")
    backend: str = Field(default="openbao", pattern="^(openbao|vault)$")
    binary_path: str | None = None
    external_url: str | None = None
    external_token: str | None = None
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
    weekly_image_update_scan: bool = True
    weekly_image_update_creates_manual_hold: bool = True

    @field_validator("kv_mount", "auth_method", mode="before")
    @classmethod
    def _validate_strip_and_require(cls, v: object) -> str:
        return _strip_and_require(v)
