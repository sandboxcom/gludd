from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class MCPServerConfig(BaseModel):
    server_id: str
    command: list[str] | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    timeout_seconds: float = 30.0
    enabled: bool = True

    @model_validator(mode="after")
    def _validate_transport(self) -> MCPServerConfig:
        if self.command is None and self.url is None:
            raise ValueError("MCPServerConfig must have either command or url")
        return self

    def is_stdio(self) -> bool:
        return self.command is not None

    def is_http(self) -> bool:
        return self.url is not None
