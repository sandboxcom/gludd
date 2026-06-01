from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class ConversationMessage(BaseModel):
    model_config = ConfigDict(strict=True)

    role: str
    content: str
    token_count: int = 0
    timestamp: float = Field(default_factory=time.monotonic)
    metadata: dict[str, Any] = {}


class Conversation(BaseModel):
    model_config = ConfigDict(strict=True)

    conversation_id: str = Field(default_factory=lambda: f"conv-{uuid4().hex[:8]}")
    todo_id: str = ""
    return_id: str = ""
    project_id: str | None = None
    messages: list[ConversationMessage] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def add_message(self, role: str, content: str) -> None:
        token_count = self._estimate_tokens(content)
        msg = ConversationMessage(role=role, content=content, token_count=token_count)
        self.messages.append(msg)

    def get_context(self, max_tokens: int = 100000) -> list[ConversationMessage]:
        if not self.messages:
            return []
        result: list[ConversationMessage] = []
        total = 0
        for msg in reversed(self.messages):
            if total + msg.token_count > max_tokens:
                break
            result.append(msg)
            total += msg.token_count
        result.reverse()
        return result

    def total_tokens(self) -> int:
        return sum(m.token_count for m in self.messages)

    def message_count(self) -> int:
        return len(self.messages)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Conversation:
        messages_data = data.pop("messages", [])
        messages = [ConversationMessage(**m) for m in messages_data]
        if "created_at" in data and isinstance(data["created_at"], str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        return cls(messages=messages, **data)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, len(text.split()))
