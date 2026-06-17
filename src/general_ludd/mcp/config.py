from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from general_ludd.mcp._validators import strip_and_require_str


class MCPServerConfig(BaseModel):
    server_id: str
    command: list[str] | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    env_aliases: dict[str, str] = Field(default_factory=dict)
    optional_env_aliases: set[str] = Field(default_factory=set)
    url: str | None = None
    timeout_seconds: float = 30.0
    enabled: bool = True
    project_id: str | None = None

    _validate_server_id = strip_and_require_str("server_id")

    @field_validator("timeout_seconds")
    @classmethod
    def _timeout_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("timeout_seconds must be positive")
        return v

    @model_validator(mode="after")
    def _validate_transport(self) -> MCPServerConfig:
        if self.command is None and self.url is None:
            raise ValueError("MCPServerConfig must have either command or url")
        return self

    def is_stdio(self) -> bool:
        return self.command is not None

    def is_http(self) -> bool:
        return self.url is not None
