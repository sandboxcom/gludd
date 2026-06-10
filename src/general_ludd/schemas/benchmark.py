"""Schemas for prompt scoring and adaptive routing."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


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

    @field_validator("id", "name", "prompt_text", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("field must not be empty")
        return v


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

    @field_validator("model_profile_id", mode="before")
    @classmethod
    def _strip_id(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        return v

    @field_validator("time_seconds", "cost_usd")
    @classmethod
    def _non_negative_float(cls, v: float) -> float:
        if v < 0:
            raise ValueError("must be non-negative")
        return v

    @field_validator("input_tokens", "output_tokens")
    @classmethod
    def _non_negative_int(cls, v: int) -> int:
        if v < 0:
            raise ValueError("must be non-negative")
        return v


class RoutingCandidate(BaseModel):
    prompt_profile_id: str | None
    model_profile_id: str
    composite_score: float
    avg_cost_usd: float
    sample_count: int
    task_type: TaskType

    @field_validator("composite_score")
    @classmethod
    def _score_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("composite_score must be between 0.0 and 1.0")
        return v

    @field_validator("avg_cost_usd")
    @classmethod
    def _cost_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("avg_cost_usd must be non-negative")
        return v

    @field_validator("sample_count")
    @classmethod
    def _count_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("sample_count must be at least 1")
        return v


class RoutingDecision(BaseModel):
    selected_prompt_profile_id: str | None
    selected_model_profile_id: str
    composite_score: float
    estimated_cost_usd: float
    sample_count: int
    fallback: bool = False
    reason: str = ""

    @field_validator("composite_score")
    @classmethod
    def _score_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("composite_score must be between 0.0 and 1.0")
        return v

    @field_validator("estimated_cost_usd")
    @classmethod
    def _cost_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("estimated_cost_usd must be non-negative")
        return v

    @field_validator("sample_count")
    @classmethod
    def _count_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("sample_count must be at least 1")
        return v
