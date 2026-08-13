"""Regression tests for YAML role expressions that must stay lint-loadable."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from jinja2 import StrictUndefined
from jinja2.nativetypes import NativeEnvironment

ROOT = Path(__file__).resolve().parents[2]


def _tasks(relative_path: str) -> list[dict[str, Any]]:
    loaded = yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    return loaded


def _task(tasks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(task for task in tasks if task.get("name") == name)


def test_prompt_evaluator_bounded_slices_are_valid_jinja() -> None:
    tasks = _tasks(
        "collections/ansible_collections/general_ludd/agent/roles/"
        "log_prompt_evaluator/tasks/main.yml"
    )
    environment = NativeEnvironment(
        autoescape=True,
        undefined=StrictUndefined,
    )

    waste_facts = _task(tasks, "Build waste pattern summary")[
        "ansible.builtin.set_fact"
    ]
    recommendations = _task(tasks, "Build top recommendations")[
        "ansible.builtin.set_fact"
    ]

    evaluation = {
        "waste_patterns": [
            {"frequency": 5, "pattern": "duplicate context"},
            {"frequency": 3, "pattern": "verbose preamble"},
            {"frequency": 1, "pattern": "weak constraint"},
        ],
        "recommendations": ["first", "second", "third"],
    }

    rendered_waste = environment.from_string(
        waste_facts["_lpe_top_waste_patterns"]
    ).render(_lpe_eval=evaluation, max_recommendations="2")
    rendered_recommendations = environment.from_string(
        recommendations["_lpe_top_recs"]
    ).render(_lpe_eval=evaluation, max_recommendations="2")

    assert rendered_waste == ["duplicate context", "verbose preamble"]
    assert rendered_recommendations == ["first", "second"]


def test_dissector_template_uses_valid_default_jinja_delimiters() -> None:
    tasks = _tasks(
        "collections/ansible_collections/general_ludd/networking/roles/"
        "networking/tasks/dissector_create.yml"
    )
    block = _task(tasks, "Generate Wireshark dissector")["block"]
    template_task = _task(block, "Generate Lua dissector from template")
    template_options = template_task["ansible.builtin.template"]

    assert "variable_start_string" not in template_options
    assert "variable_end_string" not in template_options


def test_legacy_agent_networking_role_delegates_to_canonical_collection() -> None:
    tasks = _tasks(
        "collections/ansible_collections/general_ludd/agent/roles/"
        "networking/tasks/main.yml"
    )

    assert tasks == [
        {
            "name": "Delegate networking operations to the canonical collection role",
            "ansible.builtin.include_role": {
                "name": "general_ludd.networking.networking"
            },
        }
    ]
