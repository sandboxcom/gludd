"""Architecture contracts for the split Codex-comparison implementation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import general_ludd.self_improve.codex_comparison as comparison
from general_ludd.self_improve import codex_evaluation, codex_gateway


def test_codex_comparison_modules_stay_within_source_line_limit() -> None:
    """Keep each cohesive implementation below the repository's hard ceiling."""
    source_root = Path(__file__).resolve().parents[2] / "src/general_ludd/self_improve"
    modules = (
        "codex_comparison.py",
        "codex_protocol.py",
        "codex_evaluation.py",
        "codex_gateway.py",
    )

    line_counts = {
        name: len((source_root / name).read_text(encoding="utf-8").splitlines())
        for name in modules
    }

    assert all(count < 2500 for count in line_counts.values()), line_counts


def test_codex_comparison_facade_reexports_cohesive_implementations() -> None:
    """Keep object identity and the historical module identity on the stable facade."""
    assert comparison.ComparisonResult is codex_evaluation.ComparisonResult
    assert comparison.PlannerFeedbackExchange is codex_evaluation.PlannerFeedbackExchange
    assert comparison.LocalProposalGateway is codex_gateway.LocalProposalGateway
    assert comparison.ComparisonResult.__module__ == comparison.__name__
    assert comparison.LocalProposalGateway.__module__ == comparison.__name__


def test_gateway_resolves_default_factory_through_compatibility_facade(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Preserve the established monkeypatch seam after moving the gateway class."""
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"GGUF")
    factory_calls: list[dict[str, object]] = []

    class Model:
        def __call__(self, _prompt: str, **_kwargs: object) -> object:
            return {
                "choices": [
                    {
                        "text": json.dumps(
                            {
                                "schema_version": 1,
                                "baseline_sha": "a" * 40,
                                "task_id": "S1",
                                "edits": [
                                    {
                                        "operation": "create",
                                        "path": "src/example.py",
                                        "old_text": "",
                                        "new_text": "value = 1\n",
                                    }
                                ],
                                "tests": ["tests/unit/test_example.py"],
                                "make_commands": [
                                    "make test-specific TESTFILE=tests/unit/test_example.py"
                                ],
                                "commit_message": "test: preserve facade seam",
                            }
                        )
                    }
                ]
            }

    def factory(**kwargs: object) -> Model:
        factory_calls.append(kwargs)
        return Model()

    monkeypatch.setattr(comparison, "_default_model_factory", factory)

    proposal = comparison.LocalProposalGateway(model_path).propose("repair")

    assert isinstance(proposal, comparison.ProposalManifest)
    assert proposal.task_id == "S1"
    assert factory_calls == [
        {"model_path": str(model_path), "n_ctx": 0, "verbose": False}
    ]


def test_moved_gateway_keeps_strict_input_boundaries(tmp_path: Path) -> None:
    """Keep validation behavior intact at the newly extracted module boundary."""
    with pytest.raises(ValueError, match="compact prompt must be non-empty"):
        codex_gateway.bind_compact_focus_path("", "src/example.py")
    with pytest.raises(ValueError, match="compact focus path"):
        codex_gateway.bind_compact_focus_path("task", "../example.py")
    with pytest.raises(ValueError, match="exactly one trusted focus path"):
        codex_gateway._trusted_compact_focus_path("task")

    misplaced_binding = "task\nGLUDD_SELF_IMPROVE_FOCUS_PATH=src/example.py"
    with pytest.raises(ValueError, match="canonical leading order"):
        codex_gateway._model_visible_compact_prompt(misplaced_binding)

    with pytest.raises(ValueError, match="did not complete structured output"):
        codex_gateway._completion_text(
            {"choices": [{"finish_reason": "error", "text": "{}"}]},
            phase="proposal",
            budget=8,
            require_stop=True,
        )
    with pytest.raises(ValueError, match="compact focus path"):
        codex_gateway._decode_compact_span_proposal("{}", focus_path="../example.py")
    with pytest.raises(ValueError, match="exactly e"):
        codex_gateway._decode_compact_span_proposal("{}", focus_path="src/example.py")
    with pytest.raises(ValueError, match="coordinates must be integers"):
        codex_gateway._decode_compact_span_proposal(
            '{"e":[{"n":"1","s":1,"z":"replacement"}]}',
            focus_path="src/example.py",
        )
    with pytest.raises(FileNotFoundError, match="local GGUF is not readable"):
        codex_gateway.LocalProposalGateway(tmp_path / "missing.gguf")


def test_moved_gateway_extracts_fenced_json_without_prose() -> None:
    """Preserve the historical fenced-JSON compatibility adapter after extraction."""
    assert codex_gateway._extract_json_object("```json\n{\"ok\":true}\n```") == (
        '{"ok":true}'
    )
