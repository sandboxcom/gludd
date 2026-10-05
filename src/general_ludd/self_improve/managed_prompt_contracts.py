"""Bounded immutable prompt contracts for managed self-improvement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

_MAX_PROMPT_SHARD_BYTES: Final = 16_384


@dataclass(frozen=True, slots=True)
class PromptShard:
    """One bounded proposal prompt with an exact, disjoint edit focus."""

    focus_paths: tuple[str, ...]
    prompt: str
    editable_ranges: tuple[tuple[int, int], ...] = ()

    def __post_init__(self) -> None:
        """Reject empty, mutable, duplicate, or oversized shard state."""
        if (
            not isinstance(self.focus_paths, tuple)
            or not self.focus_paths
            or not all(isinstance(path, str) and path for path in self.focus_paths)
            or len(set(self.focus_paths)) != len(self.focus_paths)
        ):
            raise ValueError("prompt shard focus paths must be a non-empty unique tuple")
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("prompt shard must not be empty")
        if len(self.prompt.encode("utf-8")) > _MAX_PROMPT_SHARD_BYTES:
            raise ValueError(f"prompt shard exceeds {_MAX_PROMPT_SHARD_BYTES} bytes")
        if not isinstance(self.editable_ranges, tuple):
            raise ValueError("prompt shard editable ranges must be an immutable tuple")
        previous_end = 1
        for item in self.editable_ranges:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or any(
                    isinstance(value, bool) or not isinstance(value, int)
                    for value in item
                )
            ):
                raise ValueError(
                    "prompt shard editable ranges must contain integer pairs"
                )
            start, end = item
            if start < 1 or end <= start or start < previous_end:
                raise ValueError(
                    "prompt shard editable ranges must be ordered half-open ranges"
                )
            previous_end = end


__all__ = ["PromptShard"]
