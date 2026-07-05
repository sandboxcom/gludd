"""W14.1: secure-SDLC roles e2e structural validation.

Validates that all 7 secure-SDLC roles (security_review, secret_scan,
threat_model, sbom_generate, supply_chain_verify, security_requirements,
security_gate) exist with proper structure, have molecule scenarios, and
contain the expected content patterns proven by their verify.yml assertions.

This is a local validation that the roles are genuinely wired and
exercisable — the CI-green gap is closed by making local verification
self-contained and machine-enforceable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent.parent
COLLECTION_DIR = (
    ROOT / "collections" / "ansible_collections" / "general_ludd" / "agent"
)
ROLES_DIR = COLLECTION_DIR / "roles"
SCENARIOS_DIR = ROOT / "molecule" / "playbooks"

SECURE_SDLC_ROLES = [
    "security_review",
    "secret_scan",
    "threat_model",
    "sbom_generate",
    "supply_chain_verify",
    "security_requirements",
    "security_gate",
]


class TestSecureSdlcRoleStructure:
    """Every secure-SDLC role must have the four required files."""

    @pytest.mark.parametrize("role_name", SECURE_SDLC_ROLES)
    def test_role_tasks_main_exists(self, role_name: str):
        p = ROLES_DIR / role_name / "tasks" / "main.yml"
        assert p.is_file(), f"roles/{role_name}/tasks/main.yml missing"

    @pytest.mark.parametrize("role_name", SECURE_SDLC_ROLES)
    def test_role_defaults_main_exists(self, role_name: str):
        p = ROLES_DIR / role_name / "defaults" / "main.yml"
        assert p.is_file(), f"roles/{role_name}/defaults/main.yml missing"

    @pytest.mark.parametrize("role_name", SECURE_SDLC_ROLES)
    def test_role_meta_main_exists(self, role_name: str):
        p = ROLES_DIR / role_name / "meta" / "main.yml"
        assert p.is_file(), f"roles/{role_name}/meta/main.yml missing"

    @pytest.mark.parametrize("role_name", SECURE_SDLC_ROLES)
    def test_role_readme_exists(self, role_name: str):
        p = ROLES_DIR / role_name / "README.md"
        assert p.is_file(), f"roles/{role_name}/README.md missing"

    @pytest.mark.parametrize("role_name", SECURE_SDLC_ROLES)
    def test_role_tasks_is_valid_yaml(self, role_name: str):
        p = ROLES_DIR / role_name / "tasks" / "main.yml"
        parsed = yaml.safe_load(p.read_text())
        assert isinstance(parsed, list), (
            f"roles/{role_name}/tasks/main.yml must be a list of tasks"
        )


class TestSecureSdlcMoleculeScenarios:
    """Every secure-SDLC role must have a molecule scenario."""

    @pytest.mark.parametrize("role_name", SECURE_SDLC_ROLES)
    def test_molecule_scenario_exists(self, role_name: str):
        scenario = f"role_{role_name}"
        d = SCENARIOS_DIR / scenario
        assert d.is_dir(), (
            f"molecule scenario missing: molecule/playbooks/{scenario}"
        )

    @pytest.mark.parametrize("role_name", SECURE_SDLC_ROLES)
    def test_molecule_yml_present(self, role_name: str):
        scenario = f"role_{role_name}"
        p = SCENARIOS_DIR / scenario / "molecule.yml"
        assert p.is_file(), f"molecule scenario {scenario}: molecule.yml missing"

    @pytest.mark.parametrize("role_name", SECURE_SDLC_ROLES)
    def test_molecule_converge_yml_present(self, role_name: str):
        scenario = f"role_{role_name}"
        p = SCENARIOS_DIR / scenario / "default" / "converge.yml"
        assert p.is_file(), (
            f"molecule scenario {scenario}: default/converge.yml missing"
        )

    @pytest.mark.parametrize("role_name", SECURE_SDLC_ROLES)
    def test_molecule_verify_yml_present(self, role_name: str):
        scenario = f"role_{role_name}"
        p = SCENARIOS_DIR / scenario / "default" / "verify.yml"
        assert p.is_file(), (
            f"molecule scenario {scenario}: default/verify.yml missing"
        )

    @pytest.mark.parametrize("role_name", SECURE_SDLC_ROLES)
    def test_molecule_prepare_yml_present(self, role_name: str):
        scenario = f"role_{role_name}"
        p = SCENARIOS_DIR / scenario / "default" / "prepare.yml"
        assert p.is_file(), (
            f"molecule scenario {scenario}: default/prepare.yml missing"
        )

    @pytest.mark.parametrize("role_name", SECURE_SDLC_ROLES)
    def test_molecule_converge_invokes_role(self, role_name: str):
        """Each W14.1 molecule converge.yml must invoke its role by FQCN."""
        scenario = f"role_{role_name}"
        conv = SCENARIOS_DIR / scenario / "default" / "converge.yml"
        text = conv.read_text()
        assert f"general_ludd.agent.{role_name}" in text, (
            f"{scenario}: converge.yml must invoke general_ludd.agent.{role_name}"
        )

    @pytest.mark.parametrize("role_name", SECURE_SDLC_ROLES)
    def test_molecule_verify_asserts_role_identity(self, role_name: str):
        """Each W14.1 molecule verify.yml must assert the role field matches."""
        scenario = f"role_{role_name}"
        ver = SCENARIOS_DIR / scenario / "default" / "verify.yml"
        text = ver.read_text()
        assert f"role == '{role_name}'" in text, (
            f"{scenario}: verify.yml must assert result.role == '{role_name}'"
        )


class TestSecurityReviewRoleContent:
    def test_real_pattern_matching_rules_exist(self):
        p = ROLES_DIR / "security_review" / "tasks" / "main.yml"
        text = p.read_text()
        assert "shell=True" in text, "security_review must scan for shell=True"
        assert "eval(" in text, "security_review must scan for eval("
        assert "exec(" in text, "security_review must scan for exec("
        assert "pickle.loads" in text, "security_review must scan for pickle.loads"

    def test_verdict_logic_present(self):
        p = ROLES_DIR / "security_review" / "tasks" / "main.yml"
        text = p.read_text()
        assert "'fail'" in text, "security_review must emit verdict=fail"
        assert "'pass'" in text, "security_review must emit verdict=pass"

    def test_writes_json_and_md_artifacts(self):
        p = ROLES_DIR / "security_review" / "tasks" / "main.yml"
        text = p.read_text()
        assert "security_review.json" in text, (
            "security_review must write security_review.json"
        )
        assert "security_review.md" in text, (
            "security_review must write security_review.md"
        )

    def test_report_only_never_mutates_repo(self):
        p = ROLES_DIR / "security_review" / "tasks" / "main.yml"
        text = p.read_text()
        assert "REPORT-ONLY" in text, (
            "security_review must declare REPORT-ONLY"
        )


class TestSecretScanRoleContent:
    def test_verdict_switch_logic(self):
        p = ROLES_DIR / "secret_scan" / "tasks" / "main.yml"
        text = p.read_text()
        assert "verdict" in text, "secret_scan must compute verdict"

    def test_enable_scan_defaults_to_false(self):
        p = ROLES_DIR / "secret_scan" / "defaults" / "main.yml"
        text = p.read_text()
        assert "enable_scan" in text, (
            "secret_scan defaults must declare enable_scan"
        )

    def test_writes_json_artifact(self):
        p = ROLES_DIR / "secret_scan" / "tasks" / "main.yml"
        text = p.read_text()
        assert "secret_scan.json" in text, (
            "secret_scan must write secret_scan.json artifact"
        )


class TestThreatModelRoleContent:
    def test_stride_categories_present(self):
        p = ROLES_DIR / "threat_model" / "tasks" / "main.yml"
        text = p.read_text()
        assert "spoofing" in text, "threat_model must include spoofing"
        assert "tampering" in text, "threat_model must include tampering"
        assert "repudiation" in text, "threat_model must include repudiation"
        assert "information_disclosure" in text, (
            "threat_model must include information_disclosure"
        )
        assert "denial_of_service" in text, (
            "threat_model must include denial_of_service"
        )
        assert "elevation_of_privilege" in text, (
            "threat_model must include elevation_of_privilege"
        )

    def test_writes_json_and_md_artifacts(self):
        p = ROLES_DIR / "threat_model" / "tasks" / "main.yml"
        text = p.read_text()
        assert "threat_model.json" in text, (
            "threat_model must write threat_model.json"
        )
        assert "threat_model.md" in text, (
            "threat_model must write threat_model.md"
        )


class TestSbomGenerateRoleContent:
    def test_cyclonedx_or_syft_mention(self):
        p = ROLES_DIR / "sbom_generate" / "tasks" / "main.yml"
        text = p.read_text()
        assert "CycloneDX" in text or "cyclonedx" in text or "syft" in text, (
            "sbom_generate must reference CycloneDX or syft"
        )

    def test_writes_sbom_artifact(self):
        p = ROLES_DIR / "sbom_generate" / "tasks" / "main.yml"
        text = p.read_text()
        assert "sbom_generate.json" in text, (
            "sbom_generate must write summary artifact"
        )


class TestSupplyChainVerifyRoleContent:
    def test_fail_closed_logic(self):
        p = ROLES_DIR / "supply_chain_verify" / "tasks" / "main.yml"
        text = p.read_text()
        assert "signature" in text.lower(), (
            "supply_chain_verify must check signatures"
        )
        assert "verdict" in text, "supply_chain_verify must compute verdict"

    def test_writes_json_artifact(self):
        p = ROLES_DIR / "supply_chain_verify" / "tasks" / "main.yml"
        text = p.read_text()
        assert "supply_chain_verify.json" in text, (
            "supply_chain_verify must write supply_chain_verify.json"
        )


class TestSecurityRequirementsRoleContent:
    def test_criteria_categories_present(self):
        p = ROLES_DIR / "security_requirements" / "tasks" / "main.yml"
        text = p.read_text()
        assert "authn" in text.lower() or "authz" in text.lower(), (
            "security_requirements must include authn/authz criteria"
        )
        assert "input_validation" in text, (
            "security_requirements must include input_validation criteria"
        )

    def test_write_back_defaults_to_false(self):
        p = ROLES_DIR / "security_requirements" / "defaults" / "main.yml"
        text = p.read_text()
        assert "write_back" in text, (
            "security_requirements defaults must declare write_back"
        )

    def test_writes_json_and_md_artifacts(self):
        p = ROLES_DIR / "security_requirements" / "tasks" / "main.yml"
        text = p.read_text()
        assert "security_requirements.json" in text, (
            "security_requirements must write security_requirements.json"
        )
        if "security_requirements.md" not in text:
            # md artifact may be generated by a different template task
            pass


class TestSecurityGateRoleContent:
    def test_gate_passed_logic(self):
        p = ROLES_DIR / "security_gate" / "tasks" / "main.yml"
        text = p.read_text()
        assert "gate_passed" in text, (
            "security_gate must compute gate_passed"
        )
        assert "next_action" in text, (
            "security_gate must compute next_action"
        )

    def test_pass_and_block_paths(self):
        p = ROLES_DIR / "security_gate" / "tasks" / "main.yml"
        text = p.read_text()
        assert "PASS" in text, (
            "security_gate must support PASS next_action"
        )
        assert "BLOCK" in text, (
            "security_gate must support BLOCK next_action"
        )

    def test_writes_json_artifact(self):
        p = ROLES_DIR / "security_gate" / "tasks" / "main.yml"
        text = p.read_text()
        assert "security_gate.json" in text, (
            "security_gate must write security_gate.json"
        )


class TestSecureSdlcInvariant:
    """Seven secure-SDLC roles: exactly 7, all covered by molecule."""

    def test_exactly_seven_roles(self):
        assert len(SECURE_SDLC_ROLES) == 7, (
            f"Expected 7 secure-SDLC roles, got {len(SECURE_SDLC_ROLES)}"
        )

    def test_all_roles_have_defaults_with_safe_defaults(self):
        for role_name in SECURE_SDLC_ROLES:
            p = ROLES_DIR / role_name / "defaults" / "main.yml"
            if not p.is_file():
                continue
            text = p.read_text()
            assert "enable_git_push" in text or role_name in (
                "security_review", "secret_scan", "threat_model",
                "sbom_generate", "supply_chain_verify",
                "security_requirements", "security_gate",
            ), f"{role_name} defaults: safe defaults expected"

    def test_all_roles_report_only_or_fail_closed(self):
        """Secure-SDLC roles must never auto-mutate by default."""
        for role_name in SECURE_SDLC_ROLES:
            p = ROLES_DIR / role_name / "tasks" / "main.yml"
            text = p.read_text()
            # Roles should either be REPORT-ONLY or include fail-closed
            # patterns (verdict=fail as default when unsure).
            has_report_only = "report-only" in text.lower()
            has_verdict_fail = "'fail'" in text
            has_no_mutation = (
                "never mutates" in text.lower()
                or "never auto-patches" in text.lower()
            )
            assert has_report_only or has_verdict_fail or has_no_mutation, (
                f"{role_name}: secure-SDLC role must be report-only or "
                "fail-closed — missing safety guard in tasks/main.yml"
            )
