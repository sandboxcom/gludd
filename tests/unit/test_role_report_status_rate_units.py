"""Regression tests for ``report_status`` success-rate unit handling."""

from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment

_ROOT = Path(__file__).resolve().parents[2]
_TASKS = (
    _ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "roles"
    / "report_status"
    / "tasks"
    / "main.yml"
)


def _tasks_by_name() -> dict[str, dict[str, object]]:
    tasks = yaml.safe_load(_TASKS.read_text())
    return {str(task["name"]): task for task in tasks}


def test_success_rate_fraction_is_normalized_to_percentage_once() -> None:
    """The daemon's 0..1 fraction must become a 0..100 reporting value."""
    tasks = _tasks_by_name()
    gather = tasks["Set system health classification based on live facts"]
    gather_facts = gather["ansible.builtin.set_fact"]
    assert isinstance(gather_facts, dict)
    assert "_rs_success_rate_fraction" in gather_facts

    normalize = tasks["Normalize success rate fraction to percentage"]
    normalize_facts = normalize["ansible.builtin.set_fact"]
    assert isinstance(normalize_facts, dict)
    expression = str(normalize_facts["_rs_success_rate_pct"])

    rendered = Environment().from_string(expression).render(
        _rs_success_rate_fraction=0.92
    )
    assert float(rendered) == 92.0


def test_health_thresholds_and_output_use_percentage_value() -> None:
    """Classification and human output must use the same normalized unit."""
    tasks = _tasks_by_name()
    critical = tasks["Classify system health (critical — success rate below 50%)"]
    degraded = tasks["Classify system health (degraded — success rate 50-80%)"]
    healthy = tasks["Classify system health (healthy)"]

    assert "_rs_success_rate_pct" in str(critical["when"])
    assert "_rs_success_rate_pct" in str(degraded["when"])
    assert "_rs_success_rate_pct" in str(healthy["when"])
    assert "_rs_success_rate_pct" in str(critical["ansible.builtin.set_fact"])
    assert "_rs_success_rate_pct" in str(degraded["ansible.builtin.set_fact"])
    assert "_rs_success_rate_pct" in str(healthy["ansible.builtin.set_fact"])

    markdown = tasks["Write markdown status report"]["ansible.builtin.copy"]
    assert isinstance(markdown, dict)
    assert (
        "Success rate: {{ _rs_success_rate_pct }}%"
        in str(markdown["content"])
    )
