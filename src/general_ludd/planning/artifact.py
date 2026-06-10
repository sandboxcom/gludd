from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PlanArtifact(BaseModel):
    model_config = ConfigDict(strict=True)

    todo_id: str
    title: str = ""
    description: str = ""
    target_files: list[str] = []
    contracts: list[str] = []
    dependencies: list[str] = []
    notes: str = ""
    content: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("todo_id", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("todo_id must not be empty")
        return v

    def to_markdown(self) -> str:
        lines: list[str] = []
        heading = self.title if self.title else self.todo_id
        lines.append(f"## Plan: {heading}")
        lines.append("")
        lines.append(f"**Todo ID:** {self.todo_id}")
        if self.description:
            lines.append(f"**Description:** {self.description}")
        if self.target_files:
            lines.append("")
            lines.append("### Target Files")
            for f in self.target_files:
                lines.append(f"- `{f}`")
        if self.contracts:
            lines.append("")
            lines.append("### Contracts")
            for c in self.contracts:
                lines.append(f"- `{c}`")
        if self.dependencies:
            lines.append("")
            lines.append("### Dependencies")
            for d in self.dependencies:
                lines.append(f"- {d}")
        if self.notes:
            lines.append("")
            lines.append(f"**Notes:** {self.notes}")
        if self.content:
            lines.append("")
            lines.append(self.content)
        lines.append("")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanArtifact:
        if "created_at" in data and isinstance(data["created_at"], str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        return cls(**data)

    @classmethod
    def from_todo(cls, todo: Any) -> PlanArtifact:
        notes_parts: list[str] = []
        if getattr(todo, "tags", None):
            notes_parts.append("Tags: " + ", ".join(todo.tags))
        if getattr(todo, "test_commands", None):
            notes_parts.append("Test commands: " + "; ".join(todo.test_commands))
        notes = " | ".join(notes_parts) if notes_parts else ""
        return cls(
            todo_id=todo.todo_id,
            title=todo.title,
            description=todo.description,
            notes=notes,
        )
