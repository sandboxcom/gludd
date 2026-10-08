"""Contracts for provider-neutral remote model-worker lifecycle playbooks."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PLAYBOOKS = ROOT / "playbooks"


def _play(path: str) -> dict[str, object]:
    document = yaml.safe_load((PLAYBOOKS / path).read_text(encoding="utf-8"))
    assert isinstance(document, list) and len(document) == 1
    play = document[0]
    assert isinstance(play, dict)
    return play


def test_deploy_playbook_attests_before_launch_with_serial_zdd() -> None:
    play = _play("model_worker_deploy.yml")

    assert play["hosts"] == "gludd_model_workers"
    assert play["gather_facts"] is False
    assert play["serial"] == 1
    assert play["any_errors_fatal"] is True
    assert play["roles"] == [
        {"role": "general_ludd.agent.attest_model_worker"},
        {"role": "general_ludd.agent.model_worker"},
    ]


def test_retire_playbook_removes_only_the_explicit_prior_generation() -> None:
    play = _play("model_worker_retire.yml")

    assert play["hosts"] == "gludd_model_workers"
    assert play["gather_facts"] is False
    assert play["serial"] == 1
    assert play["any_errors_fatal"] is True
    assert play["roles"] == [
        {
            "role": "general_ludd.agent.model_worker",
            "tasks_from": "retire",
        }
    ]
