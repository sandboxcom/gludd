"""Deep unit tests for langgraph_gateway standalone functions and edge cases.

Covers _heuristic_score, _parse_review_response, ReviewVerdict validation,
GraphState construction, and LangGraphGateway init paths not exercised by
the existing compiled/coverage test files.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from general_ludd.models.langgraph_gateway import (
    GraphState,
    LangGraphGateway,
    ReviewVerdict,
    _heuristic_score,
    _parse_review_response,
)

# ── _heuristic_score — standalone scorer ──


class TestHeuristicScore:
    def test_base_score_is_0_5(self) -> None:
        assert _heuristic_score("") == 0.5

    def test_bare_text_scores_0_5(self) -> None:
        assert _heuristic_score("hello world") == 0.5

    def test_def_keyword_adds_0_15(self) -> None:
        assert _heuristic_score("def foo(): pass") == 0.65

    def test_class_keyword_adds_0_15(self) -> None:
        assert _heuristic_score("class Foo: pass") == 0.65

    def test_import_keyword_adds_0_1(self) -> None:
        assert _heuristic_score("import os") == 0.6

    def test_return_keyword_adds_0_1(self) -> None:
        assert _heuristic_score("return 42") == 0.6

    def test_length_above_50_adds_0_05(self) -> None:
        long_text = "x" * 51
        assert _heuristic_score(long_text) == 0.55

    def test_length_exactly_50_no_bonus(self) -> None:
        exact_50 = "x" * 50
        assert _heuristic_score(exact_50) == 0.5

    def test_test_keyword_adds_0_1(self) -> None:
        assert _heuristic_score("test the thing") == 0.6

    def test_assert_keyword_adds_0_1(self) -> None:
        assert _heuristic_score("assert True") == 0.6

    def test_all_keywords_combined(self) -> None:
        content = (
            "def foo():\n    import os\n    class Bar:\n        pass\n    "
            "return os.path.join('test', 'assert')\n" + "x" * 60
        )
        score = _heuristic_score(content)
        assert score == 1.0

    def test_score_capped_at_1_0(self) -> None:
        content = "def x:\n class y:\n import os\n return True\n test assert\n" + "z" * 60
        score = _heuristic_score(content)
        assert score <= 1.0
        assert score == 1.0

    def test_case_insensitive_test(self) -> None:
        assert _heuristic_score("TEST") == 0.6
        assert _heuristic_score("AssertionError") == 0.6

    def test_def_inside_word_not_matched(self) -> None:
        assert _heuristic_score("definitely") == 0.5

    def test_class_inside_word_still_matched(self) -> None:
        assert _heuristic_score("subclass ") == 0.65

    def test_import_inside_word_still_matched(self) -> None:
        assert _heuristic_score("reimport ") == 0.6

    def test_return_inside_word_not_matched(self) -> None:
        assert _heuristic_score("returning ") == 0.5


# ── _parse_review_response — JSON extraction ──


class TestParseReviewResponse:
    def test_clean_json_parses(self) -> None:
        text = json.dumps({"review_passed": True, "quality_score": 0.9, "feedback": "great"})
        result = _parse_review_response(text)
        assert result.review_passed is True
        assert result.quality_score == pytest.approx(0.9)
        assert result.feedback == "great"

    def test_markdown_fenced_json(self) -> None:
        text = '```json\n{"review_passed": false, "quality_score": 0.3, "feedback": "bad"}\n```'
        result = _parse_review_response(text)
        assert result.review_passed is False
        assert result.quality_score == pytest.approx(0.3)
        assert result.feedback == "bad"

    def test_markdown_fence_without_lang_tag(self) -> None:
        text = '```\n{"review_passed": true, "quality_score": 0.75, "feedback": "ok"}\n```'
        result = _parse_review_response(text)
        assert result.review_passed is True
        assert result.quality_score == pytest.approx(0.75)

    def test_triple_backtick_with_extra_whitespace(self) -> None:
        text = '```   \n{"review_passed": true, "quality_score": 0.5, "feedback": "mid"}\n```'
        result = _parse_review_response(text)
        assert result.review_passed is True
        assert result.quality_score == pytest.approx(0.5)

    def test_missing_feedback_field_defaults(self) -> None:
        text = '{"review_passed": true, "quality_score": 1.0}'
        result = _parse_review_response(text)
        assert result.review_passed is True
        assert result.quality_score == pytest.approx(1.0)
        assert result.feedback == ""

    def test_missing_review_passed_defaults(self) -> None:
        text = '{"quality_score": 0.7, "feedback": "decent"}'
        result = _parse_review_response(text)
        assert result.review_passed is False
        assert result.quality_score == pytest.approx(0.7)

    def test_missing_quality_score_defaults(self) -> None:
        text = '{"review_passed": true, "feedback": "no score"}'
        result = _parse_review_response(text)
        assert result.quality_score == pytest.approx(0.0)

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(ValidationError):
            _parse_review_response("not json at all")

    def test_quality_score_out_of_range_raises(self) -> None:
        with pytest.raises(ValidationError):
            _parse_review_response('{"review_passed": true, "quality_score": 1.5}')

    def test_negative_quality_score_raises(self) -> None:
        with pytest.raises(ValidationError):
            _parse_review_response('{"review_passed": true, "quality_score": -0.1}')

    def test_markdown_with_leading_text_before_fence_raises(self) -> None:
        text = 'Here is the review:\n```json\n{"review_passed": true, "quality_score": 0.88, "feedback": "nice"}\n```'
        with pytest.raises(ValidationError) as exc_info:
            _parse_review_response(text)
        assert "Invalid JSON" in str(exc_info.value)

    def test_only_opening_fence_no_extraction(self) -> None:
        text = '```json\n{"review_passed": true, "quality_score": 0.5}'
        result = _parse_review_response(text)
        assert result.quality_score == pytest.approx(0.5)

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValidationError):
            _parse_review_response("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValidationError):
            _parse_review_response("   \n\t\n")


# ── ReviewVerdict model validation ──


class TestReviewVerdict:
    def test_default_values(self) -> None:
        v = ReviewVerdict()
        assert v.review_passed is False
        assert v.quality_score == 0.0
        assert v.feedback == ""

    def test_valid_construction(self) -> None:
        v = ReviewVerdict(review_passed=True, quality_score=0.95, feedback="excellent work")
        assert v.review_passed is True
        assert v.quality_score == pytest.approx(0.95)
        assert v.feedback == "excellent work"

    def test_quality_score_at_zero(self) -> None:
        v = ReviewVerdict(quality_score=0.0)
        assert v.quality_score == pytest.approx(0.0)

    def test_quality_score_at_one(self) -> None:
        v = ReviewVerdict(quality_score=1.0)
        assert v.quality_score == pytest.approx(1.0)

    def test_quality_score_at_boundary(self) -> None:
        v = ReviewVerdict(quality_score=0.9999)
        assert v.quality_score == pytest.approx(0.9999)

    def test_quality_score_below_zero_raises(self) -> None:
        with pytest.raises(ValidationError):
            ReviewVerdict(quality_score=-0.001)

    def test_quality_score_above_one_raises(self) -> None:
        with pytest.raises(ValidationError):
            ReviewVerdict(quality_score=1.001)

    def test_model_dump_json_roundtrips(self) -> None:
        v = ReviewVerdict(review_passed=True, quality_score=0.75, feedback="solid")
        dumped = v.model_dump_json()
        reloaded = ReviewVerdict.model_validate_json(dumped)
        assert reloaded.review_passed == v.review_passed
        assert reloaded.quality_score == v.quality_score
        assert reloaded.feedback == v.feedback


# ── GraphState construction ──


class TestGraphState:
    def test_minimal_construction(self) -> None:
        state = GraphState(messages=[], task_context={}, retry_count=0, warnings=[])
        assert state["messages"] == []
        assert state["retry_count"] == 0

    def test_extra_fields_allowed(self) -> None:
        state = GraphState(
            messages=[],
            task_context={},
            retry_count=0,
            warnings=[],
            classification="feature",
            selected_model="gpt-4o",
        )
        assert state["classification"] == "feature"
        assert state["selected_model"] == "gpt-4o"

    def test_total_false_allows_partial(self) -> None:
        state = GraphState(messages=[], task_context={})
        assert state.get("retry_count", 99) == 99
        assert state.get("warnings", ["fallback"]) == ["fallback"]

    def test_generated_content_none_by_default(self) -> None:
        state = GraphState(messages=[], task_context={})
        assert state.get("generated_content") is None

    def test_review_passed_not_set_by_default(self) -> None:
        state = GraphState(messages=[], task_context={})
        assert state.get("review_passed", 99) == 99

    def test_final_output_none_by_default(self) -> None:
        state = GraphState(messages=[], task_context={})
        assert state.get("final_output") is None


# ── LangGraphGateway init edge cases ──


class TestGatewayInitEdgeCases:
    def test_max_retries_zero(self) -> None:
        gw = LangGraphGateway(enable_graph=False, max_retries=0)
        assert gw._max_retries == 0

    def test_quality_threshold_zero(self) -> None:
        gw = LangGraphGateway(enable_graph=False, quality_threshold=0.0)
        assert gw._quality_threshold == 0.0

    def test_quality_threshold_one(self) -> None:
        gw = LangGraphGateway(enable_graph=False, quality_threshold=1.0)
        assert gw._quality_threshold == 1.0

    def test_all_scoring_components_provided(self) -> None:
        gw = LangGraphGateway(
            call_model_fn=MagicMock(),
            adaptive_router=MagicMock(),
            scoring_engine=MagicMock(),
            enable_graph=False,
        )
        assert gw._call_model is not None
        assert gw._router is not None
        assert gw._scoring is not None

    def test_langgraph_available_but_disabled(self) -> None:
        with patch.dict("sys.modules", {"langgraph.graph": MagicMock()}):
            gw = LangGraphGateway(enable_graph=False)
        assert gw._has_langgraph is True
        assert gw._graph is None

    def test_langgraph_build_exception_caught(self) -> None:
        with (
            patch.dict("sys.modules", {"langgraph.graph": MagicMock()}),
            patch.object(LangGraphGateway, "_build_graph", side_effect=RuntimeError("bad graph")),
        ):
            gw = LangGraphGateway(enable_graph=True)
        assert gw._has_langgraph is False
        assert gw._graph is None


# ── _call_single_shot edge cases ──


class TestCallSingleShotEdgeCases:
    @pytest.mark.asyncio
    async def test_result_is_string_not_object(self) -> None:
        call_fn = AsyncMock()
        call_fn.return_value = "string result"
        gw = LangGraphGateway(call_model_fn=call_fn, enable_graph=False)
        result = await gw._call_single_shot(
            messages=[{"role": "user", "content": "hi"}],
            task_context={},
            profile_id="default",
        )
        assert result["content"] == "string result"
        assert result["retries"] == 0

    @pytest.mark.asyncio
    async def test_task_context_work_type_used(self) -> None:
        call_fn = AsyncMock()
        call_fn.return_value = MagicMock(content="ok")
        gw = LangGraphGateway(call_model_fn=call_fn, enable_graph=False)
        result = await gw._call_single_shot(
            messages=[{"role": "user", "content": "hi"}],
            task_context={"work_type": "refactor"},
            profile_id="p1",
        )
        call_fn.assert_awaited_once()
        _, kwargs = call_fn.call_args
        assert kwargs["work_type"] == "refactor"
        assert result["model"] == "p1"

    @pytest.mark.asyncio
    async def test_task_context_missing_work_type_defaults_to_feature(self) -> None:
        call_fn = AsyncMock()
        call_fn.return_value = MagicMock(content="ok")
        gw = LangGraphGateway(call_model_fn=call_fn, enable_graph=False)
        await gw._call_single_shot(
            messages=[{"role": "user", "content": "hi"}],
            task_context={},
            profile_id="default",
        )
        _, kwargs = call_fn.call_args
        assert kwargs["work_type"] == "feature"


# ── _review_step edge cases ──


class TestReviewStepEdgeCases:
    @pytest.mark.asyncio
    async def test_with_both_call_model_and_scoring(self) -> None:
        scoring = MagicMock()
        scoring.score.return_value = 0.8

        async def _call(profile_id, messages, **kwargs):
            system_text = ""
            for m in messages:
                if isinstance(m, dict) and m.get("role") == "system":
                    system_text = m.get("content", "")
            if "code quality reviewer" in system_text:
                return MagicMock(content='{"review_passed": true, "quality_score": 0.92, "feedback": "good"}')
            return MagicMock(content="def x(): return 1")

        call_fn = AsyncMock(side_effect=_call)
        gw = LangGraphGateway(call_model_fn=call_fn, scoring_engine=scoring, enable_graph=False)
        state: GraphState = {
            "messages": [],
            "task_context": {},
            "classification": None,
            "selected_model": "default",
            "selected_prompt": None,
            "generated_output": "def x(): return 1",
            "quality_score": None,
            "retry_count": 0,
            "final_output": None,
            "warnings": [],
        }
        result = await gw._review_step(state)
        assert result["quality_score"] == pytest.approx(0.92)

    @pytest.mark.asyncio
    async def test_review_exception_falls_back_to_heuristic(self) -> None:
        async def _call(profile_id, messages, **kwargs):
            system_text = ""
            for m in messages:
                if isinstance(m, dict) and m.get("role") == "system":
                    system_text = m.get("content", "")
            if "code quality reviewer" in system_text:
                raise RuntimeError("reviewer down")
            return MagicMock(content="def x(): import os; return 'hello world output that is long enough'")

        call_fn = AsyncMock(side_effect=_call)
        gw = LangGraphGateway(call_model_fn=call_fn, enable_graph=False)
        state: GraphState = {
            "messages": [],
            "task_context": {},
            "classification": None,
            "selected_model": "default",
            "selected_prompt": None,
            "generated_output": "def x(): import os; return 'hello world output that is long enough'",
            "quality_score": None,
            "retry_count": 0,
            "final_output": None,
            "warnings": [],
        }
        result = await gw._review_step(state)
        assert result["quality_score"] == pytest.approx(0.9)


# ── _execute_graph_steps retry logic ──


class TestExecuteGraphStepsRetry:
    @pytest.mark.asyncio
    async def test_quality_above_threshold_on_first_try_breaks(self) -> None:
        call_fn = AsyncMock()
        call_fn.return_value = MagicMock(
            content="def foo():\n    import os\n    return 'hello world output that is long enough'"
        )
        gw = LangGraphGateway(
            call_model_fn=call_fn,
            enable_graph=False,
            quality_threshold=0.5,
            max_retries=2,
        )
        state: GraphState = {
            "messages": [],
            "task_context": {},
            "classification": None,
            "selected_model": "default",
            "selected_prompt": None,
            "generated_output": None,
            "quality_score": None,
            "retry_count": 0,
            "final_output": None,
            "warnings": [],
        }
        result = await gw._execute_graph_steps(state)
        assert result["retries"] == 0
        assert result["content"] != ""

    @pytest.mark.asyncio
    async def test_stops_at_max_retries_with_best_output(self) -> None:
        call_fn = AsyncMock()
        call_fn.return_value = MagicMock(content="short")
        gw = LangGraphGateway(
            call_model_fn=call_fn,
            enable_graph=False,
            quality_threshold=0.9,
            max_retries=2,
        )
        state: GraphState = {
            "messages": [],
            "task_context": {},
            "classification": None,
            "selected_model": "default",
            "selected_prompt": None,
            "generated_output": None,
            "quality_score": None,
            "retry_count": 0,
            "final_output": None,
            "warnings": [],
        }
        result = await gw._execute_graph_steps(state)
        assert result["retries"] == 2
        assert result["content"] == "short"
        assert any("Max retries" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_exception_in_generate_step_adds_warning(self) -> None:
        call_fn = AsyncMock(side_effect=RuntimeError("generation error"))
        gw = LangGraphGateway(
            call_model_fn=call_fn,
            enable_graph=False,
            quality_threshold=0.5,
            max_retries=2,
        )
        state: GraphState = {
            "messages": [],
            "task_context": {},
            "classification": None,
            "selected_model": "default",
            "selected_prompt": None,
            "generated_output": None,
            "quality_score": None,
            "retry_count": 0,
            "final_output": None,
            "warnings": [],
        }
        result = await gw._execute_graph_steps(state)
        assert result["content"] == ""
        assert any("Generation failed" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_task_context_with_dashed_work_type(self) -> None:
        call_fn = AsyncMock()
        call_fn.return_value = MagicMock(
            content="def x():\n    import os\n    return 'hello world output that is long enough'"
        )
        gw = LangGraphGateway(
            call_model_fn=call_fn,
            enable_graph=False,
            quality_threshold=0.5,
            max_retries=1,
        )
        state: GraphState = {
            "messages": [],
            "task_context": {"work_type": "bug-fix"},
            "classification": None,
            "selected_model": "default",
            "selected_prompt": None,
            "generated_output": None,
            "quality_score": None,
            "retry_count": 0,
            "final_output": None,
            "warnings": [],
        }
        result = await gw._execute_graph_steps(state)
        assert result["retries"] == 0

    @pytest.mark.asyncio
    async def test_quality_score_exactly_at_threshold_passes(self) -> None:
        call_fn = AsyncMock()
        call_fn.return_value = MagicMock(content="def foo():\n    import os\n    return 'x'")
        gw = LangGraphGateway(
            call_model_fn=call_fn,
            enable_graph=False,
            quality_threshold=0.85,
            max_retries=1,
        )
        state: GraphState = {
            "messages": [],
            "task_context": {},
            "classification": None,
            "selected_model": "default",
            "selected_prompt": None,
            "generated_output": None,
            "quality_score": None,
            "retry_count": 0,
            "final_output": None,
            "warnings": [],
        }
        result = await gw._execute_graph_steps(state)
        assert result["retries"] == 0
        assert result["content"] != ""
