from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Skill(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str
    description: str = ""
    model_profile: str | None = None
    tools: list[str] = []
    trigger_patterns: list[str] = []
    tags: list[str] = []
    body: str = ""
    source_path: str | None = None
