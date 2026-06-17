"""Regression: prepare_messages docstring must not claim a hard cap it cannot enforce."""
from __future__ import annotations

import inspect

from general_ludd.agents.capabilities import AgentCapabilities


def test_docstring_does_not_claim_bound_conversation() -> None:
    """The old "Bound the conversation to the token budget" claim was false."""
    doc = inspect.getdoc(AgentCapabilities.prepare_messages) or ""
    assert "Bound the conversation" not in doc


def test_docstring_documents_uncapped_passthrough() -> None:
    """The honest docstring must warn about the uncapped oversized-message edge case."""
    doc = inspect.getdoc(AgentCapabilities.prepare_messages) or ""
    assert "uncapped" in doc or "NOTE" in doc
