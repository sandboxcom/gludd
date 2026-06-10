from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


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

    @field_validator("name", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v
