"""Tests: ModelProfile rejects non-finite cost rates (NaN / inf)."""

import math

import pytest
from pydantic import ValidationError

from general_ludd.models.gateway import ModelProfile


def _base_kwargs(**overrides):
    kwargs = dict(
        model_profile_id="test-model",
        provider="openai",
        model_name="gpt-4o",
        cost_per_input_token=0.000001,
        cost_per_output_token=0.000003,
    )
    kwargs.update(overrides)
    return kwargs


# ---------------------------------------------------------------------------
# Finite / valid cases
# ---------------------------------------------------------------------------

def test_finite_positive_cost_accepted():
    profile = ModelProfile(**_base_kwargs())
    assert math.isfinite(profile.cost_per_input_token)
    assert math.isfinite(profile.cost_per_output_token)


def test_zero_cost_accepted():
    profile = ModelProfile(**_base_kwargs(cost_per_input_token=0.0, cost_per_output_token=0.0))
    assert profile.cost_per_input_token == 0.0
    assert profile.cost_per_output_token == 0.0


# ---------------------------------------------------------------------------
# inf — cost_per_input_token
# ---------------------------------------------------------------------------

def test_inf_cost_per_input_token_raises():
    with pytest.raises(ValidationError, match="finite non-negative"):
        ModelProfile(**_base_kwargs(cost_per_input_token=float("inf")))


def test_neg_inf_cost_per_input_token_raises():
    with pytest.raises(ValidationError, match="finite non-negative"):
        ModelProfile(**_base_kwargs(cost_per_input_token=float("-inf")))


# ---------------------------------------------------------------------------
# NaN — cost_per_input_token
# ---------------------------------------------------------------------------

def test_nan_cost_per_input_token_raises():
    with pytest.raises(ValidationError, match="finite non-negative"):
        ModelProfile(**_base_kwargs(cost_per_input_token=float("nan")))


# ---------------------------------------------------------------------------
# inf / nan — cost_per_output_token
# ---------------------------------------------------------------------------

def test_inf_cost_per_output_token_raises():
    with pytest.raises(ValidationError, match="finite non-negative"):
        ModelProfile(**_base_kwargs(cost_per_output_token=float("inf")))


def test_nan_cost_per_output_token_raises():
    with pytest.raises(ValidationError, match="finite non-negative"):
        ModelProfile(**_base_kwargs(cost_per_output_token=float("nan")))


# ---------------------------------------------------------------------------
# Negative values still rejected (pre-existing behaviour, now with new message)
# ---------------------------------------------------------------------------

def test_negative_cost_per_input_token_raises():
    with pytest.raises(ValidationError, match="finite non-negative"):
        ModelProfile(**_base_kwargs(cost_per_input_token=-0.001))


def test_negative_cost_per_output_token_raises():
    with pytest.raises(ValidationError, match="finite non-negative"):
        ModelProfile(**_base_kwargs(cost_per_output_token=-1.0))


# ---------------------------------------------------------------------------
# inf / nan — run_budget_usd
# ---------------------------------------------------------------------------

def test_inf_run_budget_usd_raises():
    with pytest.raises(ValidationError, match="finite non-negative"):
        ModelProfile(**_base_kwargs(run_budget_usd=float("inf")))


def test_nan_run_budget_usd_raises():
    with pytest.raises(ValidationError, match="finite non-negative"):
        ModelProfile(**_base_kwargs(run_budget_usd=float("nan")))


def test_negative_run_budget_usd_raises():
    with pytest.raises(ValidationError, match="finite non-negative"):
        ModelProfile(**_base_kwargs(run_budget_usd=-5.0))
