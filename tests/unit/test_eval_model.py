"""Tests for eval model — ModelEvaluator."""

from __future__ import annotations

from unittest.mock import MagicMock

from general_ludd.eval.model import ModelEvaluator
from general_ludd.eval.schema import EvalCase


class TestModelEvaluator:
    def test_dry_run_returns_prompt(self):
        gateway = MagicMock()
        evaluator = ModelEvaluator(gateway, dry_run=True)
        case = EvalCase(
            id="1",
            description="fix the bug",
            input_files={"main.py": "print(1)"},
            expected_patch="diff",
        )
        result = evaluator.generate_patch(case)
        assert "fix the bug" in result
        assert "main.py" in result
        assert "print(1)" in result
        assert "Generate a unified diff patch" in result
        gateway.call_model.assert_not_called()

    def test_calls_gateway_when_not_dry_run(self):
        gateway = MagicMock()
        gateway.call_model.return_value = MagicMock(content="+print(2)")
        evaluator = ModelEvaluator(gateway, profile_id="sonnet")
        case = EvalCase(
            id="1",
            description="fix the bug",
            input_files={"main.py": "print(1)"},
            expected_patch="diff",
        )
        result = evaluator.generate_patch(case)
        assert result == "+print(2)"
        gateway.call_model.assert_called_once()
        args, _kwargs = gateway.call_model.call_args
        assert args[0] == "sonnet"

    def test_build_prompt_includes_all_files(self):
        gateway = MagicMock()
        evaluator = ModelEvaluator(gateway, dry_run=True)
        case = EvalCase(
            id="1",
            description="multi-file task",
            input_files={"a.py": "content_a", "b.py": "content_b"},
            expected_patch="diff",
        )
        result = evaluator.generate_patch(case)
        assert "a.py" in result
        assert "content_a" in result
        assert "b.py" in result
        assert "content_b" in result

    def test_default_profile_id_is_sonnet(self):
        gateway = MagicMock()
        evaluator = ModelEvaluator(gateway)
        case = EvalCase(id="1", description="d", input_files={}, expected_patch="p")
        evaluator.generate_patch(case)
        gateway.call_model.assert_called_once()
        args, _kwargs = gateway.call_model.call_args
        assert args[0] == "sonnet"
