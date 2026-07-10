"""tests/models/test_gateway.py — security batch 4 gateway tests."""
from __future__ import annotations


class TestSecurityBatch4Gateway:
    """Security batch 4: D-05 budget rejection + D-04 health-gate primary."""

    def _make_gw(self, *, health_tracker=None, profiles=None):
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        if profiles is None:
            p = ModelProfile(
                model_profile_id="primary",
                provider="openai",
                model_name="gpt-4",
                enabled=True,
                fallback_profiles=["fallback"],
            )
            p2 = ModelProfile(
                model_profile_id="fallback",
                provider="openai",
                model_name="gpt-3.5",
                enabled=True,
            )
            profiles = [p, p2]
        return ModelGateway(profiles=profiles, health_tracker=health_tracker)

    def test_tripped_circuit_skipped(self):
        """A tripped primary circuit is not called."""
        from unittest.mock import MagicMock, patch
        tracker = MagicMock()
        tracker.is_healthy.return_value = False
        gw = self._make_gw(health_tracker=tracker)
        resp = MagicMock()
        with (
            patch.object(gw, 'call_model', return_value=resp) as mock_call,
            patch.object(gw, '_walk_fallbacks', return_value=(resp, None, [])),
        ):
            result = gw.call_model_with_fallback("primary", [])
        # call_model should NOT have been called for primary (tripped)
        # _walk_fallbacks should have been called instead
        assert result is resp
        mock_call.assert_not_called()

    def test_all_circuits_open_raises_clear_error(self):
        """When primary AND all fallbacks tripped, raise CircuitBreakerOpenError."""
        from unittest.mock import MagicMock, patch

        from general_ludd.models.gateway import CircuitBreakerOpenError
        tracker = MagicMock()
        tracker.is_healthy.return_value = False
        gw = self._make_gw(health_tracker=tracker)
        with patch.object(gw, '_walk_fallbacks', return_value=(None, None, [])):
            import pytest
            with pytest.raises(CircuitBreakerOpenError, match=r"All circuits open for fallback chain"):
                gw.call_model_with_fallback("primary", [])

    def test_budget_rejection_not_swallowed(self):
        """BudgetExceededError propagates through _try_call_model."""
        from unittest.mock import patch

        from general_ludd.models.gateway import BudgetExceededError
        gw = self._make_gw()
        with patch.object(gw, 'call_model', side_effect=BudgetExceededError("over budget limit reached")):
            import pytest
            with pytest.raises(BudgetExceededError, match="over budget"):
                gw._try_call_model("primary", [])

    def test_budget_rejection_stops_fallback_walk(self):
        """BudgetExceededError propagates out of call_model_with_fallback."""
        from unittest.mock import patch

        from general_ludd.models.gateway import BudgetExceededError
        gw = self._make_gw()
        with patch.object(gw, 'call_model', side_effect=BudgetExceededError("over budget limit reached")):
            import pytest
            with pytest.raises(BudgetExceededError, match="over budget"):
                gw.call_model_with_fallback("primary", [])
