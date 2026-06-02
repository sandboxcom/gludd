"""Tests for new benchmark DB models."""

from __future__ import annotations

from general_ludd.db.models import BenchmarkResultModel, PromptProfileModel


class TestPromptProfileModel:
    def test_has_required_fields(self):
        assert hasattr(PromptProfileModel, "id")
        assert hasattr(PromptProfileModel, "name")
        assert hasattr(PromptProfileModel, "source")
        assert hasattr(PromptProfileModel, "source_url")
        assert hasattr(PromptProfileModel, "prompt_text")
        assert hasattr(PromptProfileModel, "task_types")
        assert hasattr(PromptProfileModel, "tags")
        assert hasattr(PromptProfileModel, "version")
        assert hasattr(PromptProfileModel, "collected_at")

    def test_tablename(self):
        assert PromptProfileModel.__tablename__ == "prompt_profiles"

    def test_column_defaults_in_schema(self):
        col = PromptProfileModel.__table__.c
        assert col.task_types.default.arg == "[]"
        assert col.tags.default.arg == "[]"
        assert col.version.default.arg == "latest"


class TestBenchmarkResultModel:
    def test_has_required_fields(self):
        assert hasattr(BenchmarkResultModel, "id")
        assert hasattr(BenchmarkResultModel, "prompt_profile_id")
        assert hasattr(BenchmarkResultModel, "model_profile_id")
        assert hasattr(BenchmarkResultModel, "task_type")
        assert hasattr(BenchmarkResultModel, "completion_score")
        assert hasattr(BenchmarkResultModel, "code_quality_score")
        assert hasattr(BenchmarkResultModel, "instruction_adherence_score")
        assert hasattr(BenchmarkResultModel, "token_efficiency_score")
        assert hasattr(BenchmarkResultModel, "time_seconds")
        assert hasattr(BenchmarkResultModel, "input_tokens")
        assert hasattr(BenchmarkResultModel, "output_tokens")
        assert hasattr(BenchmarkResultModel, "cost_usd")
        assert hasattr(BenchmarkResultModel, "success")
        assert hasattr(BenchmarkResultModel, "error_message")
        assert hasattr(BenchmarkResultModel, "raw_output")
        assert hasattr(BenchmarkResultModel, "created_at")

    def test_tablename(self):
        assert BenchmarkResultModel.__tablename__ == "benchmark_results"

    def test_column_defaults_in_schema(self):
        col = BenchmarkResultModel.__table__.c
        assert col.completion_score.default.arg == 0.0
        assert col.success.default.arg in (False, 0)
        assert col.input_tokens.default.arg == 0
        assert col.output_tokens.default.arg == 0
        assert col.cost_usd.default.arg == 0.0
