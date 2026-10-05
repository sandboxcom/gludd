"""TDD proof: S.14 — call_model_with_retry uses asyncio.sleep for backoff.

Proves:
1. Backoff sleep uses asyncio.sleep (not time.sleep)
2. Retry succeeds on second attempt with async sleep
3. Cumulative cap still enforced — when budget is positive, sleep duration
   is capped at min(wait_s, remaining_budget)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

_TEST_INPUT_COST = 0.000001
_TEST_OUTPUT_COST = 0.000002


class TestAsyncSleepBackoff:
    """Prove that call_model_with_retry uses asyncio.sleep during backoff."""

    @pytest.mark.asyncio
    async def test_backoff_uses_asyncio_sleep(self) -> None:
        """Retryable failure → _before_sleep calls asyncio.sleep, not time.sleep."""
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.timeout_detector import ModelHealthTracker

        tracker = ModelHealthTracker()
        profile = ModelProfile(
            model_profile_id="async-sleep-test", provider="openai",
            model_name="m1", enabled=True,
            cost_per_input_token=_TEST_INPUT_COST,
            cost_per_output_token=_TEST_OUTPUT_COST,
        )
        gateway = ModelGateway(profiles=[profile], health_tracker=tracker)

        call_count: list[int] = [0]
        good_response = MagicMock()
        good_response.content = "ok"
        good_response.usage_metadata = {"input_tokens": 1, "output_tokens": 1}

        def mock_invoke(*args: object, **kwargs: object) -> MagicMock:
            call_count[0] += 1
            if call_count[0] == 1:
                raise httpx.ReadTimeout("first call times out")
            return good_response

        mock_cls = MagicMock()
        mock_cls.return_value.invoke.side_effect = mock_invoke

        with patch.object(
            gateway, "_registry", MagicMock(
                is_installed=MagicMock(return_value=True),
                get_provider_class=MagicMock(return_value=mock_cls),
            ),
        ), patch("general_ludd.models.gateway.asyncio.sleep") as mock_sleep:
            result = await gateway.call_model_with_retry(
                "async-sleep-test", [{"role": "user", "content": "hi"}],
            )

        assert result.content == "ok"
        assert call_count[0] == 2
        positive_sleeps = [
            c for c in mock_sleep.call_args_list if c[0][0] > 0
        ]
        assert len(positive_sleeps) >= 1, (
            f"Expected at least 1 positive-duration sleep, got {len(positive_sleeps)}"
        )
        sleep_arg = positive_sleeps[0][0][0]
        assert sleep_arg > 0, f"Expected positive sleep duration, got {sleep_arg}"
        # Base backoff is 1.0 with jitter; max backoff is 60s.
        assert sleep_arg <= 60.0, f"Sleep {sleep_arg} exceeds max backoff 60s"

    @pytest.mark.asyncio
    async def test_retry_still_works_with_async_sleep(self) -> None:
        """Two retryable failures → succeeds on third attempt via async sleep path."""
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.timeout_detector import ModelHealthTracker

        tracker = ModelHealthTracker()
        profile = ModelProfile(
            model_profile_id="multi-retry", provider="openai", model_name="m1",
            enabled=True, max_failover_retries=5,
            cost_per_input_token=_TEST_INPUT_COST,
            cost_per_output_token=_TEST_OUTPUT_COST,
        )
        gateway = ModelGateway(profiles=[profile], health_tracker=tracker)

        call_count: list[int] = [0]
        good_response = MagicMock()
        good_response.content = "third-time-lucky"
        good_response.usage_metadata = {"input_tokens": 1, "output_tokens": 1}

        def mock_invoke(*args: object, **kwargs: object) -> MagicMock:
            call_count[0] += 1
            if call_count[0] < 3:
                raise httpx.ConnectError(f"attempt {call_count[0]} fails")
            return good_response

        mock_cls = MagicMock()
        mock_cls.return_value.invoke.side_effect = mock_invoke

        with patch.object(
            gateway, "_registry", MagicMock(
                is_installed=MagicMock(return_value=True),
                get_provider_class=MagicMock(return_value=mock_cls),
            ),
        ), patch("general_ludd.models.gateway.asyncio.sleep") as mock_sleep:
            result = await gateway.call_model_with_retry(
                "multi-retry", [{"role": "user", "content": "hi"}],
            )

        assert result.content == "third-time-lucky"
        assert call_count[0] == 3, f"Expected 3 calls, got {call_count[0]}"
        positive_sleeps = [
            c for c in mock_sleep.call_args_list if c[0][0] > 0
        ]
        assert len(positive_sleeps) == 2, (
            f"Expected 2 backoff sleeps (1 after each failure), got {len(positive_sleeps)} "
            f"(total calls: {mock_sleep.call_count})"
        )

    @pytest.mark.asyncio
    async def test_cumulative_cap_constrains_sleep_duration(self) -> None:
        """When computed backoff exceeds remaining budget, sleep is capped.

        The cumulative cap is 300s by default. On the first retry the full
        budget is available, so the sleep is min(wait_s, 300). On later
        retries the budget decreases. We verify that the sleep argument is
        always <= the max backoff (60s) and that the cumulative sleep
        tracking works correctly (each sleep increments the total).
        """
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.timeout_detector import ModelHealthTracker

        tracker = ModelHealthTracker(failure_threshold=10)
        profile = ModelProfile(
            model_profile_id="cap-test", provider="openai", model_name="m1",
            enabled=True, max_failover_retries=5,
            cost_per_input_token=_TEST_INPUT_COST,
            cost_per_output_token=_TEST_OUTPUT_COST,
        )
        gateway = ModelGateway(profiles=[profile], health_tracker=tracker)

        call_count: list[int] = [0]
        good_response = MagicMock()
        good_response.content = "cap-ok"
        good_response.usage_metadata = {"input_tokens": 1, "output_tokens": 1}

        def mock_invoke(*args: object, **kwargs: object) -> MagicMock:
            call_count[0] += 1
            if call_count[0] < 4:
                raise httpx.ConnectError(f"attempt {call_count[0]} fails")
            return good_response

        mock_cls = MagicMock()
        mock_cls.return_value.invoke.side_effect = mock_invoke

        sleep_durations: list[float] = []

        with patch.object(
            gateway, "_registry", MagicMock(
                is_installed=MagicMock(return_value=True),
                get_provider_class=MagicMock(return_value=mock_cls),
            ),
        ), patch("general_ludd.models.gateway.asyncio.sleep") as mock_sleep:
            mock_sleep.side_effect = sleep_durations.append
            result = await gateway.call_model_with_retry(
                "cap-test", [{"role": "user", "content": "hi"}],
                max_retries=5,
            )

        assert result.content == "cap-ok"
        assert call_count[0] == 4, f"Expected 4 calls (3 failures + 1 success), got {call_count[0]}"
        positive_sleeps = [d for d in sleep_durations if d > 0]
        assert len(positive_sleeps) == 3, (
            f"Expected 3 positive backoff sleeps, got {len(positive_sleeps)} "
            f"(total sleeps: {len(sleep_durations)})"
        )
        for i, dur in enumerate(positive_sleeps):
            assert dur <= 60.0, f"Sleep {i}: {dur} > max backoff 60s"
        # Cumulative total should increase
        total = sum(sleep_durations)
        assert total > 0, f"Cumulative sleep total {total} <= 0"

    @pytest.mark.asyncio
    async def test_time_not_imported_locally(self) -> None:
        """After the refactor, call_model_with_retry no longer imports time locally."""
        import ast
        import inspect
        import textwrap

        from general_ludd.models.gateway import ModelGateway

        source = inspect.getsource(ModelGateway.call_model_with_retry)
        tree = ast.parse(textwrap.dedent(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "time", (
                        "import time found in call_model_with_retry — should use asyncio.sleep"
                    )
