"""Tests: ModelProfile rejects zero costs for enabled+metered profiles."""

import pytest
from pydantic import ValidationError

from general_ludd.models.gateway import ModelProfile


class TestZeroCostRejection:
    """Enabled + api_metered profiles must have non-zero costs."""

    def test_enabled_metered_zero_costs_raises(self):
        with pytest.raises(ValidationError, match="zero cost"):
            ModelProfile(
                model_profile_id="bad-profile",
                enabled=True,
                api_metered=True,
                cost_per_input_token=0.0,
                cost_per_output_token=0.0,
            )

    def test_enabled_metered_input_zero_raises(self):
        with pytest.raises(ValidationError, match="zero cost"):
            ModelProfile(
                model_profile_id="bad-profile",
                enabled=True,
                api_metered=True,
                cost_per_input_token=0.0,
                cost_per_output_token=0.003,
            )

    def test_enabled_metered_output_zero_raises(self):
        with pytest.raises(ValidationError, match="zero cost"):
            ModelProfile(
                model_profile_id="bad-profile",
                enabled=True,
                api_metered=True,
                cost_per_input_token=0.001,
                cost_per_output_token=0.0,
            )

    def test_enabled_metered_nonzero_costs_accepted(self):
        profile = ModelProfile(
            model_profile_id="good-profile",
            enabled=True,
            api_metered=True,
            cost_per_input_token=0.001,
            cost_per_output_token=0.003,
        )
        assert profile.cost_per_input_token == 0.001
        assert profile.cost_per_output_token == 0.003

    def test_enabled_not_metered_zero_costs_accepted(self):
        profile = ModelProfile(
            model_profile_id="free-local",
            enabled=True,
            api_metered=False,
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
        )
        assert profile.cost_per_input_token == 0.0

    def test_disabled_metered_zero_costs_accepted(self):
        profile = ModelProfile(
            model_profile_id="disabled-paid",
            enabled=False,
            api_metered=True,
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
        )
        assert profile.cost_per_input_token == 0.0

    def test_default_disabled_zero_costs_accepted(self):
        profile = ModelProfile(
            model_profile_id="defaults",
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
        )
        assert profile.enabled is False
        assert profile.cost_per_input_token == 0.0

    def test_default_enabled_zero_costs_raises(self):
        with pytest.raises(ValidationError, match="zero cost"):
            ModelProfile(
                model_profile_id="defaults-metered",
                enabled=True,
                cost_per_input_token=0.0,
                cost_per_output_token=0.0,
            )

    def test_negative_cost_still_raises(self):
        with pytest.raises(ValidationError, match="finite non-negative"):
            ModelProfile(
                model_profile_id="neg",
                enabled=True,
                api_metered=True,
                cost_per_input_token=-0.001,
                cost_per_output_token=0.003,
            )

    def test_inf_cost_still_raises(self):
        with pytest.raises(ValidationError, match="finite non-negative"):
            ModelProfile(
                model_profile_id="inf",
                enabled=True,
                api_metered=True,
                cost_per_input_token=float("inf"),
                cost_per_output_token=0.003,
            )
