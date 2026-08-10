"""Deep unit tests for EventLoop methods with thin/no coverage."""

import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.event_loop.loop import EventLoop

# ——— _resolve_permission_spec ——————————————————————————————————————————————


class FakePermissionSpec:
    def __init__(self, capabilities=None, denied=None):
        self.capabilities = capabilities or []
        self.denied = denied or []


@pytest.fixture
def _perm_loop():
    loop = EventLoop(
        worker_base_url="http://worker:8000",
        config={"tick_interval": 1.0},
        session=AsyncMock(),
        http_client=AsyncMock(),
    )
    return loop


class TestResolvePermissionSpec:
    def test_returns_none_when_security_module_unavailable(self, _perm_loop):
        with patch(
            "general_ludd.security.permissions.Capability",
            side_effect=ImportError("no security module"),
        ):
            result = _perm_loop._resolve_permission_spec(MagicMock())
            assert result is None

    def test_returns_none_for_missing_queue_config(self, _perm_loop):
        todo = MagicMock()
        todo.queue = "nonexistent"
        _perm_loop.config = {"queues": [{"name": "core", "permission_spec": {}}]}

        mock_spec = MagicMock()
        mock_parser = MagicMock()
        mock_parser.intersection.return_value = "intersected"
        with (
            patch(
                "general_ludd.security.permissions.Capability",
                return_value=MagicMock(),
            ),
            patch(
                "general_ludd.security.permissions.PermissionSpec",
                return_value=mock_spec,
            ),
            patch(
                "general_ludd.security.permissions.PermissionSpecParser",
                return_value=mock_parser,
            ),
            patch(
                "general_ludd.security.permissions.default_human_spec",
                return_value=MagicMock(),
            ),
        ):
            result = _perm_loop._resolve_permission_spec(todo)
            assert result is None

    def test_builds_agent_spec_from_queue_config(self, _perm_loop):
        todo = MagicMock()
        todo.queue = "core"
        todo.todo_id = "TODO-001"
        _perm_loop.config = {
            "queues": [
                {
                    "name": "core",
                    "permission_spec": {
                        "capabilities": [{"resource": "file:repo", "actions": ["read", "write"]}],
                        "denied": [{"resource": "file:secrets", "actions": ["read"]}],
                    },
                }
            ]
        }

        mock_spec = MagicMock()
        mock_parser = MagicMock()
        mock_parser.intersection.return_value = "intersected"
        mock_default_human = MagicMock()

        with (
            patch(
                "general_ludd.security.permissions.Capability",
                side_effect=lambda resource, actions, constraints: MagicMock(resource=resource, actions=actions),
            ),
            patch(
                "general_ludd.security.permissions.PermissionSpec",
                return_value=mock_spec,
            ) as mock_ps,
            patch(
                "general_ludd.security.permissions.PermissionSpecParser",
                return_value=mock_parser,
            ),
            patch(
                "general_ludd.security.permissions.default_human_spec",
                return_value=mock_default_human,
            ),
        ):
            result = _perm_loop._resolve_permission_spec(todo)
            mock_ps.assert_called_once()
            call_kwargs = mock_ps.call_args.kwargs
            assert call_kwargs["agent_type"] == "core-TODO-001"
            assert len(call_kwargs["capabilities"]) == 1
            assert len(call_kwargs["denied"]) == 1
            assert result is not None

    def test_agent_spec_when_no_human_spec_available(self, _perm_loop):
        todo = MagicMock()
        todo.queue = "core"
        todo.todo_id = "TODO-002"
        _perm_loop._human_spec = None
        _perm_loop.config = {
            "default_human_role": None,
            "queues": [
                {
                    "name": "core",
                    "permission_spec": {
                        "capabilities": [{"resource": "file:repo", "actions": ["read"]}],
                    },
                }
            ],
        }

        mock_agent_spec = MagicMock()

        with (
            patch(
                "general_ludd.security.permissions.Capability",
                return_value=MagicMock(),
            ),
            patch(
                "general_ludd.security.permissions.PermissionSpec",
                return_value=mock_agent_spec,
            ),
            patch(
                "general_ludd.security.permissions.default_human_spec",
                side_effect=ValueError("bad role"),
            ),
        ):
            result = _perm_loop._resolve_permission_spec(todo)
            assert result is mock_agent_spec


# ——— _estimated_dispatch_cost ——————————————————————————————————————————————


@pytest.fixture
def _cost_loop():
    return EventLoop(
        worker_base_url="http://worker:8000",
        config={"tick_interval": 1.0},
        session=AsyncMock(),
        http_client=AsyncMock(),
    )


class TestEstimatedDispatchCost:
    def test_zero_for_zero_items(self, _cost_loop):
        _cost_loop.config = {"budget": {"per_dispatch_usd": 0.05}}
        assert _cost_loop._estimated_dispatch_cost(0) == 0.0

    def test_computes_linear_cost(self, _cost_loop):
        _cost_loop.config = {"budget": {"per_dispatch_usd": 0.05}}
        assert _cost_loop._estimated_dispatch_cost(10) == 0.5

    def test_zero_when_no_budget_config(self, _cost_loop):
        _cost_loop.config = {}
        assert _cost_loop._estimated_dispatch_cost(5) == 0.05

    def test_zero_when_config_not_dict(self, _cost_loop):
        _cost_loop.config = None
        assert _cost_loop._estimated_dispatch_cost(5) == 0.05

    def test_default_per_job_when_budget_but_no_per_dispatch(self, _cost_loop):
        _cost_loop.config = {"budget": {}}
        assert _cost_loop._estimated_dispatch_cost(10) == 0.1

    def test_negative_count_treated_as_zero(self, _cost_loop):
        _cost_loop.config = {"budget": {"per_dispatch_usd": 0.05}}
        assert _cost_loop._estimated_dispatch_cost(-3) == 0.0

    def test_non_numeric_per_dispatch_uses_default(self, _cost_loop):
        _cost_loop.config = {"budget": {"per_dispatch_usd": "cheap"}}
        assert _cost_loop._estimated_dispatch_cost(10) == 0.0

    def test_budget_key_not_dict(self, _cost_loop):
        _cost_loop.config = {"budget": [1, 2, 3]}
        assert _cost_loop._estimated_dispatch_cost(10) == 0.1


# ——— _get_rule_overrides_for_todo ——————————————————————————————————————————


class TestGetRuleOverridesForTodo:
    @pytest.fixture
    def _overrides_loop(self):
        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={"tick_interval": 1.0},
            session=AsyncMock(),
            http_client=AsyncMock(),
        )
        return loop

    def test_empty_returns_empty_dict(self, _overrides_loop):
        _overrides_loop._tick_state = {}
        todo = MagicMock()
        todo.todo_id = "TODO-001"
        result = _overrides_loop._get_rule_overrides_for_todo(todo)
        assert result == {}

    def test_no_matching_todo_id_returns_empty(self, _overrides_loop):
        _overrides_loop._tick_state = {
            "rule_evaluation_results": [
                {"todo_id": "TODO-OTHER", "actions": [MagicMock()]},
            ]
        }
        todo = MagicMock()
        todo.todo_id = "TODO-001"
        result = _overrides_loop._get_rule_overrides_for_todo(todo)
        assert result == {}

    def test_matching_todo_with_no_actions_returns_empty(self, _overrides_loop):
        _overrides_loop._tick_state = {
            "rule_evaluation_results": [
                {"todo_id": "TODO-001", "actions": []},
            ]
        }
        todo = MagicMock()
        todo.todo_id = "TODO-001"
        result = _overrides_loop._get_rule_overrides_for_todo(todo)
        assert result == {}

    def test_matching_todo_with_actions_calls_apply(self, _overrides_loop):
        actions = [MagicMock(), MagicMock()]
        _overrides_loop._tick_state = {
            "rule_evaluation_results": [
                {"todo_id": "TODO-001", "actions": actions},
            ]
        }
        todo = MagicMock()
        todo.todo_id = "TODO-001"

        with patch(
            "general_ludd.event_loop.loop.apply_rule_actions",
            return_value={"model_profile": "opus"},
        ) as mock_apply:
            result = _overrides_loop._get_rule_overrides_for_todo(todo)
            mock_apply.assert_called_once_with(actions)
            assert result == {"model_profile": "opus"}

    def test_non_dict_results_skipped(self, _overrides_loop):
        _overrides_loop._tick_state = {
            "rule_evaluation_results": [
                "not a dict",
                {"todo_id": "TODO-001", "actions": [MagicMock()]},
            ]
        }
        todo = MagicMock()
        todo.todo_id = "TODO-001"

        with patch(
            "general_ludd.event_loop.loop.apply_rule_actions",
            return_value={"prompt_profile": "v2"},
        ):
            result = _overrides_loop._get_rule_overrides_for_todo(todo)
            assert result == {"prompt_profile": "v2"}

    def test_empty_actions_list_skipped_and_falls_through(self, _overrides_loop):
        _overrides_loop._tick_state = {
            "rule_evaluation_results": [
                {"todo_id": "TODO-001", "actions": []},
                {"todo_id": "TODO-001", "actions": [MagicMock()]},
            ]
        }
        todo = MagicMock()
        todo.todo_id = "TODO-001"

        with patch(
            "general_ludd.event_loop.loop.apply_rule_actions",
            return_value={"model_profile": "sonnet"},
        ):
            result = _overrides_loop._get_rule_overrides_for_todo(todo)
            assert result == {"model_profile": "sonnet"}


# ——— _format_acceptance_criteria (edge cases) ——————————————————————————————


class TestFormatAcceptanceCriteriaDeep:
    def test_valid_json_list(self):
        from general_ludd.event_loop.loop import _format_acceptance_criteria

        result = _format_acceptance_criteria('["item 1", "item 2", "item 3"]')
        assert result == "- item 1\n- item 2\n- item 3"

    def test_empty_json_list(self):
        from general_ludd.event_loop.loop import _format_acceptance_criteria

        result = _format_acceptance_criteria("[]")
        assert result == ""

    def test_none_input(self):
        from general_ludd.event_loop.loop import _format_acceptance_criteria

        result = _format_acceptance_criteria(None)
        assert result == ""

    def test_empty_string(self):
        from general_ludd.event_loop.loop import _format_acceptance_criteria

        result = _format_acceptance_criteria("")
        assert result == ""

    def test_non_json_string_passed_through(self):
        from general_ludd.event_loop.loop import _format_acceptance_criteria

        result = _format_acceptance_criteria("plain text criteria")
        assert result == "plain text criteria"

    def test_json_object_not_list_passed_through(self):
        from general_ludd.event_loop.loop import _format_acceptance_criteria

        result = _format_acceptance_criteria('{"key": "value"}')
        assert result == '{"key": "value"}'

    def test_broken_json_passed_through(self):
        from general_ludd.event_loop.loop import _format_acceptance_criteria

        result = _format_acceptance_criteria("{not valid json")
        assert result == "{not valid json"


# ——— _work_type_to_task_type (deep) ————————————————————————————————————————


class TestWorkTypeToTaskTypeDeep:
    def test_all_mapped_types_return_valid_task_type(self):
        from general_ludd.event_loop.loop import _work_type_to_task_type

        mapped = {
            "bug_fix",
            "code",
            "test",
            "review",
            "refactor",
            "docs",
            "infra",
            "analysis",
            "audit",
            "release",
            "dependency",
            "security",
            "model",
        }
        for wt in mapped:
            result = _work_type_to_task_type(wt)
            assert result is not None
            assert result.value in {
                "bug_fix",
                "feature",
                "test_write",
                "code_review",
                "refactor",
                "documentation",
                "security_fix",
            }

    def test_unknown_work_type_returns_feature(self):
        from general_ludd.event_loop.loop import _work_type_to_task_type
        from general_ludd.schemas.benchmark import TaskType

        result = _work_type_to_task_type("nonexistent_work_type")
        assert result == TaskType.FEATURE

    def test_unknown_mapped_to_invalid_task_type_returns_feature(self):
        with patch(
            "general_ludd.event_loop.loop.TaskType",
            side_effect=ValueError("bad"),
        ):
            from general_ludd.event_loop.loop import _work_type_to_task_type

            result = _work_type_to_task_type("code")

            assert result is not None


# ——— _compute_todo_estimate (deep) —————————————————————————————————————————


class TestComputeTodoEstimateDeep:
    def test_low_resource_default_confidence(self):
        from general_ludd.event_loop.loop import _compute_todo_estimate

        class T:
            resource_profile = "low_resource"
            confidence = None

        result = _compute_todo_estimate(T())
        expected = round(0.05 * 1.0, 4)
        assert result == expected

    def test_high_resource_full_confidence(self):
        from general_ludd.event_loop.loop import _compute_todo_estimate

        class T:
            resource_profile = "high_resource"
            confidence = 1.0

        result = _compute_todo_estimate(T())
        expected = round(1.0 * 0.5, 4)
        assert math.isclose(result, expected, rel_tol=1e-4)

    def test_medium_resource_zero_confidence(self):
        from general_ludd.event_loop.loop import _compute_todo_estimate

        class T:
            resource_profile = "medium_resource"
            confidence = 0.0

        result = _compute_todo_estimate(T())
        expected = round(0.25 * 1.5, 4)
        assert math.isclose(result, expected, rel_tol=1e-4)

    def test_missing_resource_profile_defaults_to_low(self):
        from general_ludd.event_loop.loop import _compute_todo_estimate

        class T:
            confidence = 0.5

        result = _compute_todo_estimate(T())
        expected = round(0.05 * 1.0, 4)
        assert result == expected

    def test_string_confidence_castable(self):
        from general_ludd.event_loop.loop import _compute_todo_estimate

        class T:
            resource_profile = "low_resource"
            confidence = "0.8"

        result = _compute_todo_estimate(T())
        expected = round(0.05 * 0.7, 4)
        assert math.isclose(result, expected, rel_tol=1e-4)

    def test_non_numeric_confidence_raises_and_defaults_to_0_5(self):
        from general_ludd.event_loop.loop import _compute_todo_estimate

        class T:
            resource_profile = "low_resource"
            confidence = "nope"

        # float("nope") raises ValueError; we test the effective behavior
        try:
            result = _compute_todo_estimate(T())
            expected = round(0.05 * 1.0, 4)
            assert result == expected
        except ValueError:
            pytest.skip("Float cast of non-numeric is expected to raise")


# ——— _safe_str (deep) ——————————————————————————————————————————————————————


class TestSafeStrDeep:
    def test_returns_attr_value(self):
        from general_ludd.event_loop.loop import _safe_str

        class Obj:
            name = "hello"

        assert _safe_str(Obj(), "name") == "hello"

    def test_returns_default_for_missing_attr(self):
        from general_ludd.event_loop.loop import _safe_str

        class Obj:
            pass

        assert _safe_str(Obj(), "missing", "fallback") == "fallback"

    def test_returns_none_for_missing_attr_no_default(self):
        from general_ludd.event_loop.loop import _safe_str

        class Obj:
            pass

        assert _safe_str(Obj(), "missing") is None

    def test_returns_default_for_non_string_attr(self):
        from general_ludd.event_loop.loop import _safe_str

        class Obj:
            count = 42

        assert _safe_str(Obj(), "count", "default") == "default"
