from __future__ import annotations

from agentic_harness.controllers.bucket import allocate_buckets
from agentic_harness.controllers.load_scrape import LoadSnapshot, PressureLevel, classify_pressure
from agentic_harness.controllers.pid import BudgetController, ControllerInputs, ControllerOutputs, LoadController
from agentic_harness.rules.engine import Rule, RuleEngine, default_rules, evaluate_rules
from agentic_harness.schemas.queue import Queue
from agentic_harness.schemas.todo import ResourceProfile


class TestLoadControlE2E:
    def test_full_pressure_classification_pipeline(self):
        snap = LoadSnapshot(
            loadavg_1m=4.5,
            loadavg_5m=4.0,
            loadavg_10m=3.8,
            logical_cpu_count=4,
            cpu_percent=92.0,
            memory_available_percent=15.0,
            disk_free_percent=40.0,
            active_jobs=2,
        )
        pressure = classify_pressure(snap)
        assert pressure[ResourceProfile.LOCAL_HEAVY] == PressureLevel.HIGH
        assert pressure[ResourceProfile.AI_HEAVY] == PressureLevel.MEDIUM
        assert pressure[ResourceProfile.HYBRID] in (PressureLevel.HIGH, PressureLevel.LOW)

    def test_load_controller_evaluate_throttles_high_load(self):
        ctrl = LoadController(cpu_count=4, default_buckets=5)
        inputs = ControllerInputs(loadavg_10m=6.0, logical_cpu_count=4)
        outputs = ctrl.evaluate(inputs)
        assert outputs.desired_total_active_buckets < 5
        assert outputs.throttle_reasons

    def test_budget_controller_blocks_expensive_api_call(self):
        ctrl = BudgetController()
        assert not ctrl.check_api_budget(estimated_cost=250.0, budget_remaining=200.0)
        assert ctrl.check_api_budget(estimated_cost=50.0, budget_remaining=200.0)

    def test_non_api_burn_rate(self):
        ctrl = BudgetController(default_run_budget_usd=200.0, subscription_window_seconds=18000.0)
        result = ctrl.compute_non_api_burn(elapsed_seconds=9000.0, used_percent=60.0)
        assert result["burn_percent"] == 60.0
        assert result["target_percent"] == 49.5
        assert result["above_line"] is True

    def test_bucket_allocation_with_rules_and_pid(self):
        pid_output = ControllerOutputs(
            desired_total_active_buckets=5,
            desired_active_buckets_by_queue={"core": 3, "worker": 2, "ansible": 4},
        )
        queues = [
            Queue(queue_name="core", soft_cap=3, hard_cap=5),
            Queue(queue_name="worker", soft_cap=2, hard_cap=4),
            Queue(queue_name="ansible", resource_profile="local_heavy", soft_cap=4, hard_cap=6),
        ]
        result = allocate_buckets(pid_output=pid_output, rule_actions=[], queues=queues)
        assert result["core"] == 3
        assert result["worker"] == 2
        assert result["ansible"] == 4
        assert isinstance(result, dict)


class TestRuleEngineE2E:
    def test_default_rules_evaluate_dependency_route(self):
        rules = default_rules()
        actions = evaluate_rules(
            rules,
            context={"todo": {"work_type": "dependency"}, "queue": {"queue_name": "core"}},
        )
        dep_route = [a for a in actions if a.action_type == "route" and a.params.get("queue") == "dependency"]
        assert len(dep_route) >= 1

    def test_rule_routes_failing_validation_to_qa(self):
        rules = default_rules()
        actions = evaluate_rules(
            rules,
            context={"todo": {"status": "failed", "work_type": "code"}, "queue": {"queue_name": "core"}},
        )
        qa_route = [a for a in actions if a.action_type == "route" and a.params.get("queue") == "qa"]
        assert len(qa_route) >= 1

    def test_rule_routes_openbao_update_to_manual_hold(self):
        rules = default_rules()
        actions = evaluate_rules(
            rules,
            context={
                "todo": {"tags": ["openbao", "image_update"], "work_type": "infra"},
                "queue": {"queue_name": "infra"},
            },
        )
        manual_hold_routes = [
            a for a in actions
            if a.action_type == "route" and a.params.get("queue") == "manual_hold"
        ]
        assert len(manual_hold_routes) >= 1

    def test_rule_reduces_buckets_under_load(self):
        rules = default_rules()
        actions = evaluate_rules(
            rules,
            context={
                "load_snapshot": {"load_ratio": 1.5},
                "queue": {"resource_profile": "local_heavy", "queue_name": "ansible"},
            },
        )
        reduce_actions = [a for a in actions if a.action_type == "reduce_buckets"]
        assert len(reduce_actions) >= 1

    def test_custom_rule_evaluation(self):
        engine = RuleEngine(rules=[
            Rule(
                rule_id="custom-e2e",
                enabled=True,
                priority=100,
                scope="global",
                condition={"all": [{"field": "todo.risk_level", "op": "eq", "value": "critical"}]},
                actions=[{"type": "set_resource_profile", "value": "ai_heavy"}],
            )
        ])
        results = engine.evaluate(
            context={"todo": {"risk_level": "critical"}}
        )
        assert len(results) == 1
        assert results[0]["actions"][0]["value"] == "ai_heavy"
