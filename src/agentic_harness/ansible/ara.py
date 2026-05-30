"""ARA (Ansible Record Automation) configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator


class ARAConfig(BaseModel):
    enabled: bool = False
    backend: Literal["sqlite", "postgresql"] = "sqlite"
    connection_string: str = "sqlite:///tmp/ara-default.db"
    callback_plugin_path: str = "/usr/lib/python3/dist-packages/ara/plugins/callback"

    @model_validator(mode="after")
    def _validate_backend_connection(self) -> ARAConfig:
        if self.backend == "postgresql" and not self.connection_string.startswith(
            "postgresql://"
        ):
            self.connection_string = f"postgresql://{self.connection_string}"
        return self
