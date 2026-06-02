"""Schemas for prompt scoring and adaptive routing."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class TaskType(StrEnum):
    BUG_FIX = "bug_fix"
    FEATURE = "feature"
    REFACTOR = "refactor"
    TEST_WRITE = "test_write"
    CODE_REVIEW = "code_review"
    DOCUMENTATION = "documentation"
    DEBUGGING = "debugging"
    OPTIMIZATION = "optimization"
    SECURITY_FIX = "security_fix"
    INTEGRATION = "integration"


class PromptProfile(BaseModel):
    id: str
    name: str
    source: str
    source_url: str = ""
    prompt_text: str
    task_types: list[TaskType] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    version: str = "latest"
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BenchmarkScores(BaseModel):
    completion_score: float = Field(ge=0.0, le=1.0)
    code_quality_score: float = Field(ge=0.0, le=1.0)
    instruction_adherence_score: float = Field(ge=0.0, le=1.0)
    token_efficiency_score: float = Field(ge=0.0, le=1.0)

    @property
    def composite_score(self) -> float:
        weights = {
            "completion": 0.35,
            "code_quality": 0.25,
            "instruction": 0.25,
            "token_efficiency": 0.15,
        }
        return (
            self.completion_score * weights["completion"]
            + self.code_quality_score * weights["code_quality"]
            + self.instruction_adherence_score * weights["instruction"]
            + self.token_efficiency_score * weights["token_efficiency"]
        )


class BenchmarkResult(BaseModel):
    id: int | None = None
    prompt_profile_id: str | None = None
    model_profile_id: str
    task_type: TaskType
    task_description: str = ""
    scores: BenchmarkScores
    time_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    success: bool = False
    error_message: str = ""
    raw_output: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RoutingCandidate(BaseModel):
    prompt_profile_id: str | None
    model_profile_id: str
    composite_score: float
    avg_cost_usd: float
    sample_count: int
    task_type: TaskType


class RoutingDecision(BaseModel):
    selected_prompt_profile_id: str | None
    selected_model_profile_id: str
    composite_score: float
    estimated_cost_usd: float
    sample_count: int
    fallback: bool = False
    reason: str = ""
