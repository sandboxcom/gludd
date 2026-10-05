"""Typed data contracts for the chat subsystem.

ChatConfig — session configuration dataclass (immutable snapshot of CLI args).
ChatMessage — structured message record for history and export.

These contracts exist to provide a single source of truth for chat message
shape and configuration validation, consumed by ChatSession and formatters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, cast

ChatRole = Literal["system", "user", "assistant", "tool"]


def _optional_string(value: object) -> str | None:
    """Return optional CLI text while rejecting non-string namespace values."""
    return value if isinstance(value, str) else None


@dataclass(frozen=True)
class ChatMessage:
    """A single message in a chat conversation.

    Follows the OpenAI message shape: ``role`` is one of ``system``, ``user``,
    ``assistant``, ``tool``; ``content`` is the message body.  Additional
    metadata (timestamp, model) is optional and used for export/persistence.
    """

    role: ChatRole
    content: str
    timestamp: str | None = None
    model: str | None = None

    def as_api_message(self) -> dict[str, str]:
        """Return a dict suitable for sending to OpenAI-compatible APIs."""
        return {"role": self.role, "content": self.content}

    def as_persistent_record(self) -> dict[str, str]:
        """Return a dict suitable for JSON-lines persistence."""
        record: dict[str, str] = {"role": self.role, "content": self.content}
        if self.timestamp:
            record["timestamp"] = self.timestamp
        if self.model:
            record["model"] = self.model
        return record

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> ChatMessage:
        """Construct from a plain dict (e.g. loaded from history JSONL)."""
        role = data["role"]
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"unsupported chat role: {role!r}")
        return cls(
            role=cast(ChatRole, role),
            content=data["content"],
            timestamp=data.get("timestamp"),
            model=data.get("model"),
        )


@dataclass(frozen=True)
class ChatConfig:
    """Immutable snapshot of chat session configuration.

    Consolidates the individual parameters passed to ``ChatSession.__init__``
    into a single validated value object.  Use ``ChatConfig.from_cli_args(args)``
    to construct from an ``argparse.Namespace``.
    """

    model: str = "default"
    system_prompt: str | None = None
    eval_mode: bool = False
    api_base_url: str | None = None
    api_key: str | None = None
    project_dir: str | None = None
    history_file: str | None = None
    save_interval: int = 5
    resume: bool = False
    max_context: int | None = None
    stream: bool = field(default=True, compare=False)
    export_format: str | None = None
    export_output: str | None = None

    def __post_init__(self) -> None:
        """Validate the initialized instance."""
        if self.save_interval < 1:
            raise ValueError(f"save_interval must be >= 1, got {self.save_interval}")

    def to_session_kwargs(self) -> dict[str, object]:
        """Return a kwargs dict suitable for ``ChatSession.__init__``."""
        return {
            "model": self.model,
            "system_prompt": self.system_prompt,
            "eval_mode": self.eval_mode,
            "api_base_url": self.api_base_url,
            "api_key": self.api_key,
            "project_dir": self.project_dir,
            "history_file": self.history_file,
            "save_interval": self.save_interval,
            "resume": self.resume,
            "max_context": self.max_context,
        }

    @classmethod
    def from_cli_args(cls, args: object) -> ChatConfig:
        """Construct from ``argparse.Namespace`` attributes."""
        a: dict[str, object] = args.__dict__
        return cls(
            model=str(a.get("model", "default")),
            system_prompt=_optional_string(a.get("system_prompt")),
            eval_mode=bool(a.get("eval")),
            api_base_url=_optional_string(a.get("api_base")),
            api_key=_optional_string(a.get("api_key")),
            project_dir=_optional_string(a.get("project_dir")),
            history_file=_optional_string(a.get("history")),
            save_interval=int(str(a.get("save_interval"))) if a.get("save_interval") is not None else 5,
            resume=bool(a.get("resume")),
            max_context=int(str(a.get("max_context"))) if a.get("max_context") is not None else None,
            stream=bool(a.get("stream", True)),
            export_format=_optional_string(a.get("export")),
            export_output=_optional_string(a.get("export_output")),
        )


__all__ = [
    "ChatConfig",
    "ChatMessage",
]
