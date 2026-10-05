"""Regression contract for warning-free Ansible syntax validation."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "config" / "ansible_syntax_inventory.yml"


def test_syntax_inventory_covers_local_and_model_worker_playbooks() -> None:
    """Every tracked playbook host pattern must resolve during syntax checks."""
    inventory = yaml.safe_load(INVENTORY.read_text(encoding="utf-8"))

    assert "localhost" in inventory["all"]["hosts"]
    worker_hosts = inventory["all"]["children"]["gludd_model_workers"]["hosts"]
    assert "gludd-model-worker-syntax" in worker_hosts


def test_ansible_syntax_target_uses_the_tracked_inventory() -> None:
    """The release syntax target must consume the warning-free inventory."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "-i config/ansible_syntax_inventory.yml --syntax-check" in makefile
