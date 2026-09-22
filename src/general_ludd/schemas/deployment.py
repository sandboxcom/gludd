"""Deployment registry schema (W2.3 / C5 / M2).

A DeploymentRecord pins, per instance_id, WHERE the deployment lives (its own
terraform working dir) and its lifecycle state, so destroy can never run against
an instance the manager never deployed (the money-leak the C5 fix closes).
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

from general_ludd.projects.identity import validate_project_id


class DeploymentRecord(BaseModel):
    """Persist one project/provider/instance deployment lifecycle record."""

    project_id: str = "default"
    instance_id: str
    working_dir: str
    provider: str = ""
    model_name: str = ""
    state: str = "running"
    ip_address: str | None = None
    endpoint_url: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    # Alias *names* only. Secret values remain in the configured resolver and
    # are resolved again for teardown after a daemon restart.
    provider_auth_aliases: dict[str, str] | None = None

    @field_validator("project_id")
    @classmethod
    def _validate_project_id(cls, value: str) -> str:
        return validate_project_id(value)
