"""Structural guard against warnings emitted by the full Molecule matrix."""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_SCENARIOS = _ROOT / "molecule" / "playbooks"
_MODULES = (
    _ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "plugins"
    / "modules"
)
_PLAYBOOK_PHASES = {
    "cleanup",
    "create",
    "destroy",
    "prepare",
    "converge",
    "side_effect",
    "verify",
}
_DEFAULT_TEST_SEQUENCE = [
    "dependency",
    "cleanup",
    "destroy",
    "syntax",
    "create",
    "prepare",
    "converge",
    "idempotence",
    "side_effect",
    "verify",
    "cleanup",
    "destroy",
]
_DRIVER_MANAGED_PHASES = {"create", "destroy"}


def _scenario_configs() -> list[Path]:
    return sorted(_SCENARIOS.glob("*/molecule.yml"))


def _mapped_playbook_exists(
    scenario_root: Path,
    mapped: str,
) -> bool:
    project_prefix = "${MOLECULE_PROJECT_DIRECTORY}/"
    if mapped.startswith(project_prefix):
        return (_ROOT / mapped.removeprefix(project_prefix)).is_file()
    if "${" in mapped:
        return False
    return (scenario_root / mapped).is_file()


def test_full_matrix_has_explicit_inventory_and_complete_sequences() -> None:
    violations: list[str] = []
    for config_path in _scenario_configs():
        config = yaml.safe_load(config_path.read_text())
        scenario = config_path.parent.name
        driver = config.get("driver", {}).get("name", "default")
        provisioner = config.get("provisioner", {})
        platforms = config.get("platforms", [])

        if not platforms:
            localhost = (
                provisioner.get("inventory", {})
                .get("hosts", {})
                .get("all", {})
                .get("hosts", {})
                .get("localhost", {})
            )
            if localhost.get("ansible_connection") != "local":
                violations.append(f"{scenario}: implicit localhost inventory")

        sequence = config.get("scenario", {}).get(
            "test_sequence", _DEFAULT_TEST_SEQUENCE
        )
        playbooks = provisioner.get("playbooks", {})
        for phase in sorted(_PLAYBOOK_PHASES.intersection(sequence)):
            if driver != "default" and phase in _DRIVER_MANAGED_PHASES:
                continue
            mapped = playbooks.get(phase, f"default/{phase}.yml")
            if not _mapped_playbook_exists(config_path.parent, mapped):
                violations.append(f"{scenario}: missing {phase} playbook")

        if "dependency" in sequence:
            for filename in ("requirements.yml", "collections.yml"):
                if not (config_path.parent / filename).is_file():
                    violations.append(
                        f"{scenario}: missing dependency file {filename}"
                    )

    assert not violations, "\n" + "\n".join(violations)


def test_modules_do_not_return_reserved_ansible_warning_keys() -> None:
    violations: list[str] = []
    for module_path in sorted(_MODULES.glob("*.py")):
        tree = ast.parse(module_path.read_text(), filename=str(module_path))
        for call in (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"exit_json", "fail_json"}
        ):
            reserved = {
                child.value
                for child in ast.walk(call)
                if isinstance(child, ast.Constant)
                and child.value in {"warnings", "deprecations"}
            }
            for key in sorted(reserved):
                violations.append(
                    f"{module_path.name}:{call.lineno}: reserved {key!r}"
                )

    assert not violations, "\n" + "\n".join(violations)
