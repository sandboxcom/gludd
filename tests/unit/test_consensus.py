"""Tests for G11 multi-agent debate/consensus engine."""

from __future__ import annotations

import pytest

from general_ludd.review.consensus import ConsensusEngine


class TestConsensusEngine:
    def test_engine_is_constructable(self) -> None:
        """ConsensusEngine instantiates without error."""
        engine = ConsensusEngine()
        assert engine is not None

    def test_run_debate_is_not_implemented(self) -> None:
        """run_debate raises NotImplementedError (stub)."""
        engine = ConsensusEngine()
        with pytest.raises(NotImplementedError):
            engine.run_debate("Is this a test question?")
