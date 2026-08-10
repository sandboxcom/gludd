"""Deep tests for TerraformWatchdog class — cost estimate, budget, findings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from general_ludd.infra.terraform_watchdog import (
    TerraformWatchdog,
    WatchdogFinding,
)


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _tfstate_json(*resources: dict[str, Any]) -> str:
    return json.dumps({"version": 4, "terraform_version": "1.5.0", "resources": list(resources)})


def _resource(type_: str, cost: str | None = None, instances: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    inst_list = instances or []
    if cost is not None:
        inst_list = [{"attributes": {"monthly_cost_estimate": cost}}]
    return {"type": type_, "instances": inst_list}


# ── WatchdogFinding dataclass ─────────────────────────────────────────


def test_watchdog_finding_fields() -> None:
    f = WatchdogFinding(stack_name="prod", exceeded_budget=True, current_cost=150.0, budget_limit=100.0)
    assert f.stack_name == "prod"
    assert f.exceeded_budget is True
    assert f.current_cost == 150.0
    assert f.budget_limit == 100.0


def test_watchdog_finding_not_exceeded() -> None:
    f = WatchdogFinding(stack_name="dev", exceeded_budget=False, current_cost=45.0, budget_limit=80.0)
    assert f.exceeded_budget is False
    assert f.current_cost < f.budget_limit


# ── is_applied ────────────────────────────────────────────────────────


def test_is_applied_true_when_tfstate_exists(tmp_path: Path) -> None:
    stack_dir = tmp_path / "stacks" / "prod"
    _write_file(stack_dir / "terraform.tfstate", '{"version":4}')
    w = TerraformWatchdog(str(tmp_path / "stacks"))
    assert w.is_applied("prod") is True


def test_is_applied_false_when_tfstate_missing(tmp_path: Path) -> None:
    (tmp_path / "stacks" / "dev").mkdir(parents=True)
    w = TerraformWatchdog(str(tmp_path / "stacks"))
    assert w.is_applied("dev") is False


# ── current_cost_estimate ─────────────────────────────────────────────


def test_current_cost_returns_zero_when_no_tfstate(tmp_path: Path) -> None:
    (tmp_path / "stacks" / "empty").mkdir(parents=True)
    w = TerraformWatchdog(str(tmp_path / "stacks"))
    assert w.current_cost_estimate("empty") == 0.0


def test_current_cost_monthly_estimate(tmp_path: Path) -> None:
    tf = _tfstate_json(_resource("aws_instance", "150.0"))
    _write_file(tmp_path / "stacks" / "prod" / "terraform.tfstate", tf)
    w = TerraformWatchdog(str(tmp_path / "stacks"))
    assert w.current_cost_estimate("prod") == 150.0


def test_current_cost_falls_back_to_cost_estimate(tmp_path: Path) -> None:
    state = json.dumps(
        {
            "version": 4,
            "resources": [
                {
                    "type": "google_compute_instance",
                    "instances": [{"attributes": {"cost_estimate": "80.0"}}],
                }
            ],
        }
    )
    _write_file(tmp_path / "stacks" / "gcp" / "terraform.tfstate", state)
    w = TerraformWatchdog(str(tmp_path / "stacks"))
    assert w.current_cost_estimate("gcp") == 80.0


def test_current_cost_aggregates_multiple_resources(tmp_path: Path) -> None:
    state = json.dumps(
        {
            "version": 4,
            "resources": [
                {"type": "aws_instance", "instances": [{"attributes": {"monthly_cost_estimate": "100.0"}}]},
                {"type": "aws_eks_cluster", "instances": [{"attributes": {"monthly_cost_estimate": "200.0"}}]},
            ],
        }
    )
    _write_file(tmp_path / "stacks" / "big" / "terraform.tfstate", state)
    w = TerraformWatchdog(str(tmp_path / "stacks"))
    assert w.current_cost_estimate("big") == 300.0


def test_current_cost_aggregates_multiple_instances(tmp_path: Path) -> None:
    state = json.dumps(
        {
            "version": 4,
            "resources": [
                {
                    "type": "aws_instance",
                    "instances": [
                        {"attributes": {"monthly_cost_estimate": "50.0"}},
                        {"attributes": {"monthly_cost_estimate": "50.0"}},
                        {"attributes": {"monthly_cost_estimate": "50.0"}},
                    ],
                }
            ],
        }
    )
    _write_file(tmp_path / "stacks" / "cluster" / "terraform.tfstate", state)
    w = TerraformWatchdog(str(tmp_path / "stacks"))
    assert w.current_cost_estimate("cluster") == 150.0


def test_current_cost_uses_estimate_for_no_cost_field(tmp_path: Path) -> None:
    state = json.dumps(
        {
            "version": 4,
            "resources": [
                {
                    "type": "aws_instance",
                    "instances": [{"attributes": {"instance_type": "g4dn.xlarge"}}],
                }
            ],
        }
    )
    _write_file(tmp_path / "stacks" / "no-cost" / "terraform.tfstate", state)
    w = TerraformWatchdog(str(tmp_path / "stacks"))
    assert w.current_cost_estimate("no-cost") == 50.0


def test_current_cost_invalid_cost_falls_to_estimate(tmp_path: Path) -> None:
    state = json.dumps(
        {
            "version": 4,
            "resources": [
                {
                    "type": "google_compute_instance",
                    "instances": [{"attributes": {"monthly_cost_estimate": "free-tier"}}],
                }
            ],
        }
    )
    _write_file(tmp_path / "stacks" / "gcp" / "terraform.tfstate", state)
    w = TerraformWatchdog(str(tmp_path / "stacks"))
    assert w.current_cost_estimate("gcp") == 60.0


def test_current_cost_unknown_resource_type_default_estimate(tmp_path: Path) -> None:
    state = json.dumps(
        {
            "version": 4,
            "resources": [
                {
                    "type": "some_vendor_unknown_thing",
                    "instances": [{"attributes": {}}],
                }
            ],
        }
    )
    _write_file(tmp_path / "stacks" / "unknown" / "terraform.tfstate", state)
    w = TerraformWatchdog(str(tmp_path / "stacks"))
    assert w.current_cost_estimate("unknown") == 10.0


def test_current_cost_broken_json_returns_zero(tmp_path: Path) -> None:
    _write_file(tmp_path / "stacks" / "broken" / "terraform.tfstate", "not valid json {{{")
    w = TerraformWatchdog(str(tmp_path / "stacks"))
    assert w.current_cost_estimate("broken") == 0.0


def test_current_cost_no_instances_key(tmp_path: Path) -> None:
    tf = json.dumps(
        {
            "version": 4,
            "resources": [
                {"type": "aws_instance", "instances": []},
            ],
        }
    )
    _write_file(tmp_path / "stacks" / "empty-inst" / "terraform.tfstate", tf)
    w = TerraformWatchdog(str(tmp_path / "stacks"))
    assert w.current_cost_estimate("empty-inst") == 0.0


def test_current_cost_no_resources_key(tmp_path: Path) -> None:
    _write_file(tmp_path / "stacks" / "no-res" / "terraform.tfstate", '{"version":4,"terraform_version":"1.5.0"}')
    w = TerraformWatchdog(str(tmp_path / "stacks"))
    assert w.current_cost_estimate("no-res") == 0.0


# ── _read_budget ──────────────────────────────────────────────────────


def test_read_budget_returns_zero_when_no_file(tmp_path: Path) -> None:
    (tmp_path / "stacks" / "no-budget").mkdir(parents=True)
    w = TerraformWatchdog(str(tmp_path / "stacks"))
    assert w._read_budget("no-budget") == 0.0


def test_read_budget_parses_monthly_limit(tmp_path: Path) -> None:
    _write_file(tmp_path / "stacks" / "prod" / "budget.json", '{"monthly_limit_usd": 500.0}')
    w = TerraformWatchdog(str(tmp_path / "stacks"))
    assert w._read_budget("prod") == 500.0


def test_read_budget_missing_key_defaults_zero(tmp_path: Path) -> None:
    _write_file(tmp_path / "stacks" / "dev" / "budget.json", '{"project": "foo"}')
    w = TerraformWatchdog(str(tmp_path / "stacks"))
    assert w._read_budget("dev") == 0.0


def test_read_budget_broken_json_returns_zero(tmp_path: Path) -> None:
    _write_file(tmp_path / "stacks" / "broken" / "budget.json", "not json!!!")
    w = TerraformWatchdog(str(tmp_path / "stacks"))
    assert w._read_budget("broken") == 0.0


def test_read_budget_non_numeric_limit_returns_zero(tmp_path: Path) -> None:
    _write_file(tmp_path / "stacks" / "bad" / "budget.json", '{"monthly_limit_usd": "unlimited"}')
    w = TerraformWatchdog(str(tmp_path / "stacks"))
    assert w._read_budget("bad") == 0.0


# ── _estimate_from_resource_type ──────────────────────────────────────


@pytest.mark.parametrize(
    "res_type,expected",
    [
        ("aws_instance", 50.0),
        ("google_compute_instance", 60.0),
        ("azurerm_linux_virtual_machine", 55.0),
        ("aws_eks_cluster", 100.0),
        ("google_container_cluster", 110.0),
    ],
)
def test_estimate_known_resource_types(res_type: str, expected: float) -> None:
    assert TerraformWatchdog._estimate_from_resource_type(res_type, {}) == expected


def test_estimate_unknown_resource_type_defaults() -> None:
    assert TerraformWatchdog._estimate_from_resource_type("vsphere_vm", {}) == 10.0
    assert TerraformWatchdog._estimate_from_resource_type("", {}) == 10.0


# ── check_all_stacks ──────────────────────────────────────────────────


def test_check_all_stacks_empty_when_stacks_dir_missing(tmp_path: Path) -> None:
    w = TerraformWatchdog(str(tmp_path / "nonexistent"))
    assert w.check_all_stacks() == []


def test_check_all_stacks_empty_when_no_stacks_applied(tmp_path: Path) -> None:
    (tmp_path / "stacks" / "dev").mkdir(parents=True)
    (tmp_path / "stacks" / "prod").mkdir(parents=True)
    w = TerraformWatchdog(str(tmp_path / "stacks"))
    assert w.check_all_stacks() == []


def test_check_all_stacks_returns_findings_for_applied_stacks(tmp_path: Path) -> None:
    stacks = tmp_path / "stacks"
    _write_file(stacks / "prod" / "terraform.tfstate", _tfstate_json(_resource("aws_instance", "150.0")))
    _write_file(stacks / "prod" / "budget.json", '{"monthly_limit_usd": 100.0}')
    w = TerraformWatchdog(str(stacks))
    findings = w.check_all_stacks()
    assert len(findings) == 1
    assert findings[0].stack_name == "prod"
    assert findings[0].exceeded_budget is True
    assert findings[0].current_cost == 150.0
    assert findings[0].budget_limit == 100.0


def test_check_all_stacks_under_budget(tmp_path: Path) -> None:
    stacks = tmp_path / "stacks"
    _write_file(stacks / "dev" / "terraform.tfstate", _tfstate_json(_resource("aws_instance", "30.0")))
    _write_file(stacks / "dev" / "budget.json", '{"monthly_limit_usd": 100.0}')
    w = TerraformWatchdog(str(stacks))
    findings = w.check_all_stacks()
    assert len(findings) == 1
    assert findings[0].exceeded_budget is False


def test_check_all_stacks_zero_budget_never_exceeds(tmp_path: Path) -> None:
    stacks = tmp_path / "stacks"
    _write_file(stacks / "dev" / "terraform.tfstate", _tfstate_json(_resource("aws_instance", "500.0")))
    _write_file(stacks / "dev" / "budget.json", '{"monthly_limit_usd": 0.0}')
    w = TerraformWatchdog(str(stacks))
    findings = w.check_all_stacks()
    assert findings[0].exceeded_budget is False


def test_check_all_stacks_skips_not_applied(tmp_path: Path) -> None:
    stacks = tmp_path / "stacks"
    _write_file(stacks / "prod" / "terraform.tfstate", _tfstate_json(_resource("aws_instance", "100.0")))
    _write_file(stacks / "prod" / "budget.json", '{"monthly_limit_usd": 500.0}')
    (stacks / "dev").mkdir(parents=True)
    w = TerraformWatchdog(str(stacks))
    findings = w.check_all_stacks()
    assert len(findings) == 1
    assert findings[0].stack_name == "prod"


def test_check_all_stacks_multiple_findings(tmp_path: Path) -> None:
    stacks = tmp_path / "stacks"
    for name, cost in [("a", "10.0"), ("b", "200.0"), ("c", "80.0")]:
        _write_file(stacks / name / "terraform.tfstate", _tfstate_json(_resource("aws_instance", cost)))
        _write_file(stacks / name / "budget.json", '{"monthly_limit_usd": 100.0}')
    w = TerraformWatchdog(str(stacks))
    findings = w.check_all_stacks()
    assert len(findings) == 3
    assert {f.stack_name for f in findings} == {"a", "b", "c"}
    exceeded = {f.stack_name for f in findings if f.exceeded_budget}
    assert exceeded == {"b"}


# ── edge cases ────────────────────────────────────────────────────────


def test_path_traversal_not_possible(tmp_path: Path) -> None:
    w = TerraformWatchdog(str(tmp_path / "stacks"))
    assert w.is_applied("../../../etc/passwd") is False


def test_stack_name_with_special_chars(tmp_path: Path) -> None:
    stacks = tmp_path / "stacks"
    _write_file(stacks / "my-stack_v2" / "terraform.tfstate", _tfstate_json(_resource("aws_instance", "25.0")))
    _write_file(stacks / "my-stack_v2" / "budget.json", '{"monthly_limit_usd": 100.0}')
    w = TerraformWatchdog(str(stacks))
    findings = w.check_all_stacks()
    assert len(findings) == 1
    assert findings[0].stack_name == "my-stack_v2"
    assert findings[0].exceeded_budget is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
