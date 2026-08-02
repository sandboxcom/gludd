"""D-30: Model gateway response size limit enforcement."""

from __future__ import annotations

import pytest

from general_ludd.models.gateway import (
    DEFAULT_MAX_RESPONSE_BYTES,
    PayloadLimitError,
    _RequestPayloadBudget,
)


class TestResponseSizeLimit:
    def test_default_limit_is_positive(self) -> None:
        assert DEFAULT_MAX_RESPONSE_BYTES > 0

    def test_budget_rejects_oversized_response(self) -> None:
        budget = _RequestPayloadBudget(
            max_request_bytes=1_000_000,
            max_input_tokens=100_000,
            max_response_bytes=1_000,
            max_output_tokens=100_000,
            max_tool_calls=100,
            max_provider_attempts=3,
        )
        budget.response_bytes = 1001
        with pytest.raises(PayloadLimitError) as exc:
            budget.check_budget("test-profile")
        assert exc.value.dimension == "bytes"
        assert exc.value.stage == "response"
        assert exc.value.limit == 1000

    def test_budget_allows_under_limit(self) -> None:
        budget = _RequestPayloadBudget(
            max_request_bytes=1_000_000,
            max_input_tokens=100_000,
            max_response_bytes=10_000,
            max_output_tokens=100_000,
            max_tool_calls=100,
            max_provider_attempts=3,
        )
        budget.response_bytes = 500
        budget.check_budget("test-profile")  # should not raise

    def test_max_response_bytes_stop_accumulation(self) -> None:
        budget = _RequestPayloadBudget(
            max_request_bytes=1_000_000,
            max_input_tokens=100_000,
            max_response_bytes=500,
            max_output_tokens=100_000,
            max_tool_calls=100,
            max_provider_attempts=3,
        )
        budget.response_bytes = 499
        budget.check_budget("p")
        budget.response_bytes = 501
        with pytest.raises(PayloadLimitError, match="response"):
            budget.check_budget("p")

    def test_budget_from_profile_uses_profile_limits(self) -> None:
        from general_ludd.models.gateway import ModelProfile

        profile = ModelProfile(
            model_profile_id="test-model",
            model_id="claude-haiku",
            endpoint="https://example.com/v1",
            credential_alias="test-creds",
            max_cumulative_response_bytes=2048,
        )
        budget = _RequestPayloadBudget.from_profile(profile)
        assert budget.max_response_bytes == 2048
