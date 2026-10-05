"""Regression tests for extracted provider credit parsing contracts."""

from __future__ import annotations

from general_ludd.budget import credit_providers
from general_ludd.budget.credit_tracker import (
    _PARSERS,
    SUPPORTED_SERVICES,
    _parse_deepseek,
    _parse_openai,
    _parse_openrouter,
    _parse_zai,
)


def test_credit_tracker_reexports_canonical_provider_contracts() -> None:
    """Provider parsing keeps one implementation and stable import identities."""
    assert SUPPORTED_SERVICES is credit_providers.SUPPORTED_SERVICES
    assert _PARSERS is credit_providers.PARSERS
    assert _parse_deepseek is credit_providers.parse_deepseek
    assert _parse_openai is credit_providers.parse_openai
    assert _parse_openrouter is credit_providers.parse_openrouter
    assert _parse_zai is credit_providers.parse_zai


def test_openai_usage_parser_sums_only_present_line_items() -> None:
    """OpenAI's usage-only response handles both values and absent items."""
    balance, currency = _parse_openai(
        {"data": [{"line_item": "1.25"}, {"line_item": None}, {}]}
    )
    assert (balance, currency) == (1.25, "USD")
    assert _parse_openai(None) == (0.0, "USD")
