"""Structural guard against warnings emitted by the full Molecule matrix."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_MOLECULE_ROOT = _ROOT / "molecule"
_SCENARIOS = _MOLECULE_ROOT / "playbooks"
_MODULES = (
    _ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "plugins"
    / "modules"
)
_COLLECTIONS = _ROOT / "collections" / "ansible_collections" / "general_ludd"
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
_EXPECTED_RELEASE_SCENARIOS = 123
_CONDITIONAL_KEYS = {"changed_when", "failed_when", "that", "until", "when"}
_RESERVED_ROLE_DEFAULTS = {"timeout"}
_LEADING_JINJA_TEXT = re.compile(
    r"""^\s*-\s*["']?\{\{[^{}]+\}\}\s+[A-Za-z]"""
)


def _scenario_configs() -> list[Path]:
    source_configs = set(_SCENARIOS.glob("*/molecule.yml"))
    source_names = {config.parent.name for config in source_configs}
    runtime_configs = {
        config
        for config in _MOLECULE_ROOT.glob("*/molecule.yml")
        if config.parent.name in source_names
    }
    return sorted(source_configs | runtime_configs)


def _conditional_values(node: object) -> list[str]:
    values: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _CONDITIONAL_KEYS:
                candidates = value if isinstance(value, list) else [value]
                values.extend(
                    candidate for candidate in candidates if isinstance(candidate, str)
                )
            values.extend(_conditional_values(value))
    elif isinstance(node, list):
        for value in node:
            values.extend(_conditional_values(value))
    return values


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


def test_warning_contract_covers_every_release_scenario() -> None:
    configs = _scenario_configs()
    source_configs = sorted(_SCENARIOS.glob("*/molecule.yml"))
    names = {config.parent.name for config in configs}
    assert len(source_configs) == _EXPECTED_RELEASE_SCENARIOS
    assert len(names) == _EXPECTED_RELEASE_SCENARIOS
    assert {"project_init_role", "prompt_eval"} <= names
    assert (_MOLECULE_ROOT / "prompt_eval" / "molecule.yml") in configs


def test_molecule_conditionals_do_not_use_deprecated_jinja_delimiters() -> None:
    violations: list[str] = []
    scenario_roots = {config.parent for config in _scenario_configs()}
    playbook_paths = {
        playbook
        for scenario_root in scenario_roots
        for playbook in scenario_root.glob("default/*.yml")
    }
    for playbook_path in sorted(playbook_paths):
        for document in yaml.safe_load_all(playbook_path.read_text()):
            for conditional in _conditional_values(document):
                expression = conditional.strip()
                if expression.startswith("{{") and expression.endswith("}}"):
                    violations.append(
                        f"{playbook_path.relative_to(_ROOT)}: {expression}"
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


def test_role_strings_do_not_trigger_python_invalid_decimal_warning() -> None:
    violations: list[str] = []
    for task_path in sorted(_COLLECTIONS.glob("*/roles/*/tasks/*.yml")):
        for lineno, line in enumerate(task_path.read_text().splitlines(), start=1):
            if _LEADING_JINJA_TEXT.match(line):
                violations.append(
                    f"{task_path.relative_to(_ROOT)}:{lineno}: {line.strip()}"
                )

    assert not violations, "\n" + "\n".join(violations)


def test_role_defaults_do_not_shadow_reserved_ansible_variables() -> None:
    violations: list[str] = []
    for defaults_path in sorted(_COLLECTIONS.glob("*/roles/*/defaults/*.yml")):
        for document in yaml.safe_load_all(defaults_path.read_text()):
            if not isinstance(document, dict):
                continue
            for variable in sorted(_RESERVED_ROLE_DEFAULTS.intersection(document)):
                violations.append(
                    f"{defaults_path.relative_to(_ROOT)}: {variable}"
                )

    assert not violations, "\n" + "\n".join(violations)
