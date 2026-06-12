"""Deployment registry schema (W2.3 / C5 / M2).

A DeploymentRecord pins, per instance_id, WHERE the deployment lives (its own
terraform working dir) and its lifecycle state, so destroy can never run against
an instance the manager never deployed (the money-leak the C5 fix closes).
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class DeploymentRecord(BaseModel):
    instance_id: str
    working_dir: str
    provider: str = ""
    model_name: str = ""
    state: str = "running"
    ip_address: str | None = None
    endpoint_url: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
