"""Eval data schemas for G2 offline benchmark harness."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EvalCase:
    id: str
    description: str
    input_files: dict[str, str]
    expected_patch: str
    task_type: str = ""


@dataclass
class EvalResult:
    case_id: str
    passed: bool
    actual_patch: str
    score: float = 0.0
    tokens_used: int = 0
    duration_ms: int = 0
    errors: list[str] = field(default_factory=list)
