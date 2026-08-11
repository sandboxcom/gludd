"""Tests for the ModelGateway — importability and key public API."""

from __future__ import annotations


class TestModelGatewayImports:
    def test_module_importable(self) -> None:
        from general_ludd.models import gateway

        assert gateway is not None

    def test_model_gateway_class_exists(self) -> None:
        from general_ludd.models.gateway import ModelGateway

        assert ModelGateway is not None

    def test_model_profile_exists(self) -> None:
        from general_ludd.models.gateway import ModelProfile

        assert ModelProfile is not None

    def test_model_response_exists(self) -> None:
        from general_ludd.models.gateway import ModelResponse

        assert ModelResponse is not None


class TestGatewayExceptionHierarchy:
    def test_budget_exceeded_is_value_error(self) -> None:
        from general_ludd.models.gateway import BudgetExceededError

        assert issubclass(BudgetExceededError, ValueError)

    def test_ssrf_rejection_is_value_error(self) -> None:
        from general_ludd.models.gateway import SSRFRejectionError

        assert issubclass(SSRFRejectionError, ValueError)

    def test_model_paused_is_exception(self) -> None:
        from general_ludd.models.gateway import ModelPausedError

        assert issubclass(ModelPausedError, Exception)

    def test_circuit_breaker_open_is_exception(self) -> None:
        from general_ludd.models.gateway import CircuitBreakerOpenError

        assert issubclass(CircuitBreakerOpenError, Exception)

    def test_payload_limit_is_exception(self) -> None:
        from general_ludd.models.gateway import PayloadLimitError

        assert issubclass(PayloadLimitError, Exception)

    def test_cumulative_payload_limit_extends_payload_limit(self) -> None:
        from general_ludd.models.gateway import CumulativePayloadLimitError, PayloadLimitError

        assert issubclass(CumulativePayloadLimitError, PayloadLimitError)

    def test_stream_limit_extends_payload_limit(self) -> None:
        from general_ludd.models.gateway import PayloadLimitError, StreamLimitError

        assert issubclass(StreamLimitError, PayloadLimitError)

    def test_call_cancelled_is_exception(self) -> None:
        from general_ludd.models.gateway import CallCancelledError

        assert issubclass(CallCancelledError, Exception)


class TestGatewayHelpers:
    def test_positive_profile_limit_default_fallback(self) -> None:
        from general_ludd.models.gateway import _positive_profile_limit

        class StubProfile:
            pass

        result = _positive_profile_limit(StubProfile(), "max_tokens", 4096)
        assert result == 4096

    def test_positive_profile_limit_valid_int(self) -> None:
        from general_ludd.models.gateway import _positive_profile_limit

        class StubProfile:
            max_tokens = 8192

        result = _positive_profile_limit(StubProfile(), "max_tokens", 4096)
        assert result == 8192

    def test_positive_profile_limit_zero_falls_back(self) -> None:
        from general_ludd.models.gateway import _positive_profile_limit

        class StubProfile:
            max_tokens = 0

        result = _positive_profile_limit(StubProfile(), "max_tokens", 4096)
        assert result == 4096

    def test_positive_profile_limit_negative_falls_back(self) -> None:
        from general_ludd.models.gateway import _positive_profile_limit

        class StubProfile:
            max_tokens = -100

        result = _positive_profile_limit(StubProfile(), "max_tokens", 4096)
        assert result == 4096

    def test_positive_profile_limit_non_int_falls_back(self) -> None:
        from general_ludd.models.gateway import _positive_profile_limit

        class StubProfile:
            max_tokens = "1024"

        result = _positive_profile_limit(StubProfile(), "max_tokens", 4096)
        assert result == 4096

    def test_extract_tool_calls_none(self) -> None:
        from general_ludd.models.gateway import _extract_tool_calls

        result = _extract_tool_calls(None)
        assert result is None

    def test_extract_tool_calls_empty_dict(self) -> None:
        from general_ludd.models.gateway import _extract_tool_calls

        result = _extract_tool_calls({})
        assert result is None

    def test_attach_correlation_id(self) -> None:
        from general_ludd.models.gateway import ModelResponse, _attach_correlation_id

        resp = ModelResponse(content="hello")
        result = _attach_correlation_id(resp, "corr-001")
        assert result is resp
        assert result.correlation_id == "corr-001"


class TestGatewayDefaults:
    def test_default_response_cache_ttl(self) -> None:
        from general_ludd.models.gateway import DEFAULT_RESPONSE_CACHE_TTL_SECONDS

        assert DEFAULT_RESPONSE_CACHE_TTL_SECONDS == 3600

    def test_payload_defaults_positive(self) -> None:
        from general_ludd.models.gateway import (
            DEFAULT_MAX_INPUT_TOKENS,
            DEFAULT_MAX_OUTPUT_TOKENS,
            DEFAULT_MAX_REQUEST_BYTES,
            DEFAULT_MAX_RESPONSE_BYTES,
            DEFAULT_MAX_TOOL_CALLS,
        )

        assert DEFAULT_MAX_INPUT_TOKENS > 0
        assert DEFAULT_MAX_OUTPUT_TOKENS > 0
        assert DEFAULT_MAX_REQUEST_BYTES > 0
        assert DEFAULT_MAX_RESPONSE_BYTES > 0
        assert DEFAULT_MAX_TOOL_CALLS > 0
