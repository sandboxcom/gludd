"""Structural test verifying governance molecule scenario scaffolding."""

from __future__ import annotations

import os
from pathlib import Path

MOLECULE_ROOT = Path(__file__).resolve().parents[2] / "molecule" / "playbooks"
EXPECTED_SCENARIOS = (
    "role_governance_borders",
    "role_governance_tax_currency_info",
    "role_governance_military_service",
)
REQUIRED_FILES = ("molecule.yml", "default/prepare.yml", "default/converge.yml", "default/verify.yml")
STUB_FILES = (
    ("role_governance_borders", "default/borders_stub.py"),
    ("role_governance_tax_currency_info", "default/tax_currency_stub.py"),
    ("role_governance_military_service", "default/military_service_stub.py"),
)

REQUIRED_MOLECULE_KEYS = ("driver", "platforms", "provisioner", "verifier", "scenario")
REQUIRED_PLAYBOOK_REFS = ("prepare", "converge", "verify")


class TestGovernanceMoleculeScenariosExist:
    def test_all_scenario_dirs_present(self):
        for scenario in EXPECTED_SCENARIOS:
            path = MOLECULE_ROOT / scenario
            assert path.is_dir(), f"Missing scenario directory: {path}"

    def test_all_required_files_per_scenario(self):
        for scenario in EXPECTED_SCENARIOS:
            for rel_path in REQUIRED_FILES:
                full = MOLECULE_ROOT / scenario / rel_path
                assert full.is_file(), f"Missing required file: {full}"

    def test_all_stub_scripts_present(self):
        for scenario, stub_rel in STUB_FILES:
            full = MOLECULE_ROOT / scenario / stub_rel
            assert full.is_file(), f"Missing stub script: {full}"

    def test_molecule_yml_has_required_keys(self):
        import yaml

        for scenario in EXPECTED_SCENARIOS:
            path = MOLECULE_ROOT / scenario / "molecule.yml"
            with open(path) as fh:
                doc = yaml.safe_load(fh)
            for key in REQUIRED_MOLECULE_KEYS:
                assert key in doc, f"{scenario}/molecule.yml missing key: {key}"
            playbooks = doc.get("provisioner", {}).get("playbooks", {})
            for ref in REQUIRED_PLAYBOOK_REFS:
                assert ref in playbooks, f"{scenario}/molecule.yml missing playbook ref: {ref}"

    def test_stub_scripts_are_readable(self):
        for scenario, stub_rel in STUB_FILES:
            full = MOLECULE_ROOT / scenario / stub_rel
            assert os.access(full, os.R_OK), f"Stub not readable: {full}"

    def test_governance_roles_still_exist(self):
        roles_root = (
            Path(__file__).resolve().parents[2]
            / "collections"
            / "ansible_collections"
            / "general_ludd"
            / "governance"
            / "roles"
        )
        for role_name in ("borders", "tax_currency_info", "military_service"):
            path = roles_root / role_name
            assert path.is_dir(), f"Governance role missing: {path}"
            assert (path / "tasks" / "main.yml").is_file(), f"Role tasks missing: {path}"

    def test_converge_playbooks_use_governance_fqcn(self):
        fqcns = {
            "role_governance_borders": "general_ludd.governance.borders",
            "role_governance_tax_currency_info": "general_ludd.governance.tax_currency_info",
            "role_governance_military_service": "general_ludd.governance.military_service",
        }
        for scenario, fqcn in fqcns.items():
            path = MOLECULE_ROOT / scenario / "default" / "converge.yml"
            content = path.read_text()
            assert fqcn in content, f"{scenario} converge.yml missing FQCN: {fqcn}"
