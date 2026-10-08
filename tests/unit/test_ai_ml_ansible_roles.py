"""Behavioral structure for AI/ML Ansible service roles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLES = ROOT / "collections/ansible_collections/general_ludd/ai_ml/roles"
ROLE_TASKS = {
    "accelerator_job": "train",
    "adapter_train": "train",
    "evaluate_model": "evaluate",
    "image_create": "image",
    "model_distill": "distill",
    "model_select": "question",
    "promote_release": "deploy",
    "reason_verify": "question",
    "retrieval_engineer": "research",
    "simulate_domain": "simulate",
    "speech_recognize": "speech",
    "speech_synthesize": "speech",
    "vision_understand": "vision",
    "world_model": "world_model",
}


def _load(path: Path) -> list[dict[str, Any]]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    return loaded


def test_every_service_role_invokes_the_shared_live_request_role() -> None:
    for role, task_name in ROLE_TASKS.items():
        source = (ROLES / role / "tasks/main.yml").read_text(encoding="utf-8")
        tasks = _load(ROLES / role / "tasks/main.yml")
        assert len(tasks) == 1
        assert "ansible.builtin.debug" not in source
        assert tasks[0]["ansible.builtin.include_role"]["name"] == (
            "general_ludd.ai_ml.service_request"
        )
        assert tasks[0]["vars"] == {
            "ai_ml_service_role": role,
            "ai_ml_service_task": task_name,
        }


def test_shared_role_calls_the_typed_endpoint_and_is_check_mode_safe() -> None:
    tasks = _load(ROLES / "service_request/tasks/main.yml")
    request = next(task for task in tasks if task["name"] == "Route AI/ML request")
    publish = next(task for task in tasks if task["name"] == "Publish AI/ML routing result")

    assert request["ansible.builtin.uri"]["method"] == "POST"
    assert request["ansible.builtin.uri"]["url"].endswith("/api/ai_ml/query")
    assert request["when"] == "not ansible_check_mode"
    assert request.get("ignore_errors") is None
    assert "ansible_check_mode" in publish["ansible.builtin.set_fact"]["ai_ml_service_result"]


def test_dataset_engineer_uses_its_native_controller_module() -> None:
    source = (ROLES / "dataset_engineer/tasks/main.yml").read_text(encoding="utf-8")
    tasks = _load(ROLES / "dataset_engineer/tasks/main.yml")

    assert len(tasks) == 1
    assert "general_ludd.ai_ml.dataset_admit" in tasks[0]
    assert "service_request" not in source
    assert "ansible.builtin.uri" not in source
