"""Managed-host Python boundary contracts for the physics collection."""

from __future__ import annotations

import importlib
import re
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from ansible.module_utils import basic as ansible_basic

COLLECTION_ROOT = Path(__file__).resolve().parents[2]
ROLES_ROOT = COLLECTION_ROOT / "roles"
MODULE_FQCN = "general_ludd.physics.physics_analysis"
ANALYSIS_ROLES = {
    "latex_expert": "latex",
    "math_modeler": "math",
    "organic_synthesist": "organic_synthesis",
    "paper_reviewer": "paper_review",
    "particle_experiment_analyst": "particle_experiment",
    "quantum_computer": "quantum",
    "spectroscopy_analyst": "spectroscopy",
    "thermodynamics_engineer": "thermodynamics",
}
AMBIENT_PYTHON = re.compile(
    r"(?:^|[\s:'\"=])(?:/usr/bin/python3?|/usr/local/bin/python3?|python3?|py)(?:\s|$)"
)


def _serialized_tasks(role: str) -> str:
    path = ROLES_ROOT / role / "tasks/main.yml"
    return str(yaml.safe_dump(yaml.safe_load(path.read_text(encoding="utf-8")), sort_keys=False))


def _load_adapter() -> Any:
    collections_root = str(COLLECTION_ROOT.parents[2])
    sys.path.insert(0, collections_root)
    try:
        return importlib.import_module(
            "ansible_collections.general_ludd.physics.plugins.module_utils.physics_adapter"
        )
    finally:
        sys.path.remove(collections_root)


def _load_module() -> Any:
    collections_root = str(COLLECTION_ROOT.parents[2])
    sys.path.insert(0, collections_root)
    try:
        return importlib.import_module(
            "ansible_collections.general_ludd.physics.plugins.modules.physics_analysis"
        )
    finally:
        sys.path.remove(collections_root)


def test_roles_use_packaged_fqcn_module_without_ambient_python() -> None:
    for role, operation in ANALYSIS_ROLES.items():
        serialized = _serialized_tasks(role)
        assert MODULE_FQCN in serialized
        assert f"operation: {operation}" in serialized
        assert "ansible.builtin.script" not in serialized
        assert AMBIENT_PYTHON.search(serialized) is None


def test_fqcn_module_is_packaged_at_ansible_resolution_path() -> None:
    module_path = COLLECTION_ROOT / "plugins/modules/physics_analysis.py"
    source = module_path.read_text(encoding="utf-8")

    assert "AnsibleModule" in source
    assert "physics_adapter" in source
    assert '"no_log": True' in source


def test_adapter_preserves_math_contract(tmp_path: Path) -> None:
    adapter = _load_adapter()
    result = adapter.run_analysis(
        "math",
        {
            "model_type": "ode",
            "equation": "dy/dt = -k*y",
            "initial_y0": 1.0,
            "param_k": 0.5,
            "time_start": 0.0,
            "time_end": 1.0,
            "time_steps": 10,
        },
        str(tmp_path),
    )

    assert result["status"] == "success"
    assert result["path"].endswith("math_model_result.json")
    assert result["half_life"] > 0


@pytest.mark.parametrize(
    ("operation", "parameters", "result_key"),
    [
        (
            "latex",
            {
                "document_class": "article",
                "font_size": "11pt",
                "title": "Boundary",
                "author": "Gludd",
                "output_format": "tex",
            },
            "doc_path",
        ),
        (
            "organic_synthesis",
            {
                "target_molecule": "aspirin",
                "starting_material": "salicylic_acid",
                "solvent": "acetic_anhydride",
                "catalyst": "sulfuric_acid",
                "temperature_c": 85.0,
                "reaction_time_min": 15.0,
            },
            "expected_yield_pct",
        ),
        (
            "paper_review",
            {"paper_title": "", "paper_text": "", "review_depth": "standard"},
            "n_sections",
        ),
        (
            "particle_experiment",
            {
                "beam_energy_gev": 13.6,
                "target": "proton",
                "beam": "proton",
                "detector": "generic_4pi",
                "luminosity_inv_fb": 139.0,
                "analysis_channel": "H_to_ZZ_to_4l",
            },
            "expected_events",
        ),
        (
            "quantum",
            {
                "problem": "infinite_square_well",
                "well_width_nm": 1.0,
                "particle": "electron",
                "potential": "square_well",
                "dimensions": 1,
                "num_states": 3,
                "solver": "numpy",
            },
            "ground_state_eV",
        ),
        (
            "spectroscopy",
            {
                "technique": "uv_vis",
                "wl_min_nm": 200.0,
                "wl_max_nm": 800.0,
                "resolution_nm": 10.0,
                "solvent": "water",
                "temperature_c": 25.0,
                "peak_threshold": 0.1,
            },
            "n_peaks_detected",
        ),
        (
            "thermodynamics",
            {
                "substance": "water",
                "mass_kg": 1.0,
                "initial_temp_c": 25.0,
                "final_temp_c": 100.0,
                "pressure_atm": 1.0,
            },
            "entropy_J_K",
        ),
    ],
)
def test_adapter_preserves_all_role_contracts(
    tmp_path: Path,
    operation: str,
    parameters: dict[str, object],
    result_key: str,
) -> None:
    adapter = _load_adapter()
    output_dir = tmp_path / operation

    result = adapter.run_analysis(operation, parameters, str(output_dir))

    assert result["status"] == "success"
    assert result_key in result


def test_adapter_fails_closed_for_unknown_or_incomplete_operations(tmp_path: Path) -> None:
    adapter = _load_adapter()

    with pytest.raises(ValueError, match="unsupported physics operation"):
        adapter.run_analysis("unknown", {}, str(tmp_path))
    with pytest.raises(ValueError, match="model_type"):
        adapter.run_analysis("math", {}, str(tmp_path))
    with pytest.raises(ValueError, match="output_dir"):
        adapter.run_analysis("math", {}, "")
    with pytest.raises(ValueError, match="initial_y0 must be numeric"):
        adapter.run_analysis(
            "math",
            {
                "model_type": "ode",
                "equation": "dy/dt = -k*y",
                "initial_y0": True,
            },
            str(tmp_path),
        )


def test_module_main_returns_structured_result(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    output: dict[str, Any] = {}

    class ExitModule(RuntimeError):
        """Signal a fake Ansible module exit."""

    class FakeAnsibleModule:
        def __init__(self, **_kwargs: object) -> None:
            self.params = {"operation": "math", "parameters": {}, "output_dir": "/tmp/result"}

        def exit_json(self, **kwargs: Any) -> None:
            output.update(kwargs)
            raise ExitModule

        def fail_json(self, **_kwargs: Any) -> None:
            raise AssertionError("unexpected failure")

    monkeypatch.setattr(module, "AnsibleModule", FakeAnsibleModule)
    monkeypatch.setattr(module, "run_analysis", lambda *_args: {"status": "success"})
    with pytest.raises(ExitModule):
        module.main()

    assert output == {"changed": True, "result": {"status": "success"}}


def test_module_main_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()

    class FailModule(RuntimeError):
        """Signal a fake Ansible module failure."""

    class FakeAnsibleModule:
        def __init__(self, **_kwargs: object) -> None:
            self.params = {"operation": "unknown", "parameters": {}, "output_dir": "/tmp/result"}

        def exit_json(self, **_kwargs: Any) -> None:
            raise AssertionError("unexpected success")

        def fail_json(self, **kwargs: Any) -> None:
            assert "unsupported physics operation" in kwargs["msg"]
            raise FailModule

    monkeypatch.setattr(module, "AnsibleModule", FakeAnsibleModule)
    with pytest.raises(FailModule):
        module.main()


def test_fqcn_module_script_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    module_path = COLLECTION_ROOT / "plugins/modules/physics_analysis.py"

    class ExitModule(RuntimeError):
        """Signal a fake Ansible module exit."""

    class FakeAnsibleModule:
        def __init__(self, **_kwargs: object) -> None:
            self.params = {"operation": "math", "parameters": {}, "output_dir": "/tmp/result"}

        def exit_json(self, **kwargs: Any) -> None:
            assert kwargs["result"] == {"status": "success"}
            raise ExitModule

        def fail_json(self, **_kwargs: Any) -> None:
            raise AssertionError("unexpected failure")

    adapter = _load_adapter()
    monkeypatch.setattr(adapter, "run_analysis", lambda *_args: {"status": "success"})
    monkeypatch.setattr(ansible_basic, "AnsibleModule", FakeAnsibleModule)
    with pytest.raises(ExitModule):
        runpy.run_path(str(module_path), run_name="__main__")
