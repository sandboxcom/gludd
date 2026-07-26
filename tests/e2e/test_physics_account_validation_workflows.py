"""E2E: Physics, account, and validation subsystem workflow tests."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from general_ludd.account.backup import get_deletion_policy
from general_ludd.account.deletion_notice import (
    SUPPORTED_SERVICES,
    build_deletion_notice,
    get_all_notices,
    get_policy_text,
)
from general_ludd.account.ephemeral import (
    AccountCredentials,
    EphemeralAccountManager,
)
from general_ludd.account.lifecycle_policy import (
    LifecycleAction,
    PolicyConfig,
    evaluate_lifecycle,
)
from general_ludd.physics.analytical_chemistry import (
    MassSpecPeak,
    calibrate_instrument,
    compute_retention_index,
    identify_from_mass_spectrum,
)
from general_ludd.physics.materials_science import (
    calculate_specific_strength,
    compare_materials,
    compute_archard_wear_volume,
    compute_band_gap_from_wavelength,
    compute_corrosion_rate,
    compute_galvanic_corrosion_risk,
    compute_hall_petch_strength,
    compute_refractive_index_contrast,
    compute_rule_of_mixtures,
    compute_surface_to_volume_ratio,
    get_material_properties,
    recommend_material,
)
from general_ludd.physics.mechanistic_interpretability import (
    analyze_attention_patterns,
    compute_faithfulness,
    compute_shap_values,
    detect_circuits,
    detect_induction_heads,
    grokking_detect,
    integrated_gradients,
    normalize_attribution,
    superposition_simulate,
    toy_model_reversal_curse,
)
from general_ludd.physics.research_paper_expert import (
    ExtractedData,
    PaperStructure,
    ReferenceEntry,
    assess_methodology,
    extract_data,
    format_reference,
    get_journal_metrics,
    identify_paper_structure,
    search_literature,
    suggest_method_fixes,
)
from general_ludd.validation.backlog_auditor import (
    FALSE_CLAIM,
    INCOMPLETE,
    VERIFIED_COMPLETE,
    BacklogAuditor,
    BacklogAuditReport,
    TaskVerdict,
)
from general_ludd.validation.gap_analyzer import (
    GapAnalyzer,
    GapItem,
    GapReport,
)
from general_ludd.validation.log_auditor import (
    AuditFinding,
    AuditReport,
    LogAuditor,
)
from general_ludd.validation.runner import (
    ValidationResult,
    ValidationRunner,
)

# ==============================================================================
# Physics — Analytical Chemistry
# ==============================================================================


class TestAnalyticalChemistryWorkflows:
    def test_identify_from_mass_spectrum_matches_fragments(self):
        peaks: list[MassSpecPeak] = [
            {"mz": 15.3, "intensity": 100.0, "assignment": "?", "delta_ppm": 2.0},
            {"mz": 18.2, "intensity": 80.0, "assignment": "?", "delta_ppm": 1.5},
            {"mz": 32.1, "intensity": 60.0, "assignment": "?", "delta_ppm": 0.8},
        ]
        result = identify_from_mass_spectrum(peaks)
        assert "methyl loss" in result["matched_fragments"]
        assert "water loss" in result["matched_fragments"]
        assert "methanol loss" in result["matched_fragments"]
        assert result["match_count"] == 3

    def test_identify_from_mass_spectrum_empty_peaks(self):
        result = identify_from_mass_spectrum([])
        assert result["matched_fragments"] == []
        assert result["match_count"] == 0

    def test_compute_retention_index_typical_values(self):
        ri = compute_retention_index(tr=4.5, tr_ref_low=4.0, tr_ref_high=5.0, n_low=10)
        assert 1040.0 < ri < 1060.0

    def test_compute_retention_index_raises_on_negative_n_low(self):
        with pytest.raises(ValueError, match="Carbon number"):
            compute_retention_index(tr=4.5, tr_ref_low=4.0, tr_ref_high=5.0, n_low=0)

    def test_compute_retention_index_raises_on_out_of_range_tr(self):
        with pytest.raises(ValueError, match="not between references"):
            compute_retention_index(tr=3.0, tr_ref_low=4.0, tr_ref_high=5.0, n_low=10)

    def test_calibrate_instrument_single_standard(self):
        result = calibrate_instrument(
            [
                {
                    "name": "NIST SRM 1643f",
                    "certified_value": 1.0,
                    "uncertainty": 0.02,
                    "unit": "ug/L",
                    "matrix": "water",
                }
            ],
            [2.5],
        )
        assert result["slope"] == 2.5
        assert result["intercept"] == 0.0
        assert result["calibration_valid"]

    def test_calibrate_instrument_multi_standard(self):
        from general_ludd.physics.analytical_chemistry import CalibrationStandard
        stds: list[CalibrationStandard] = [
            {"name": "std1", "certified_value": 1.0, "uncertainty": 0.01, "unit": "mg/L", "matrix": "water"},
            {"name": "std2", "certified_value": 5.0, "uncertainty": 0.05, "unit": "mg/L", "matrix": "water"},
            {"name": "std3", "certified_value": 10.0, "uncertainty": 0.10, "unit": "mg/L", "matrix": "water"},
        ]
        result = calibrate_instrument(stds, [2.0, 10.0, 20.0])
        assert result["calibration_valid"]
        assert result["r_squared"] > 0.95

    def test_calibrate_instrument_raises_on_empty(self):
        with pytest.raises(ValueError, match="non-empty"):
            calibrate_instrument([], [])

    def test_calibrate_instrument_raises_on_length_mismatch(self):
        with pytest.raises(ValueError, match="Length mismatch"):
            calibrate_instrument(
                [{"name": "a", "certified_value": 1.0, "uncertainty": 0.1, "unit": "x", "matrix": "y"}],
                [1.0, 2.0],
            )


# ==============================================================================
# Physics — Materials Science
# ==============================================================================


class TestMaterialsScienceWorkflows:
    def test_get_known_material(self):
        mat = get_material_properties("Aluminum 6061-T6")
        assert mat is not None
        assert mat["family"] == "metal"
        assert mat["density_g_cm3"] == 2.70

    def test_get_unknown_material_returns_none(self):
        assert get_material_properties("Unobtanium") is None

    def test_compare_materials_known_and_unknown(self):
        results = compare_materials(["Copper C11000", "ImaginaryMetal"])
        assert len(results) == 2
        assert results[0]["found"] is True
        assert results[1]["found"] is False
        assert results[1]["density_g_cm3"] is None

    def test_recommend_material_by_family(self):
        req: dict = {"preferred_family": "polymer", "min_tensile_strength_MPa": 50}
        matches = recommend_material(req)
        assert len(matches) > 0
        for m in matches:
            assert m["family"] == "polymer"
            assert m["tensile_strength_MPa"] >= 50

    def test_recommend_material_lightweight_high_strength(self):
        matches = recommend_material({
            "max_density_g_cm3": 3.0,
            "min_tensile_strength_MPa": 300,
        })
        names = [m["name"] for m in matches]
        assert "Aluminum 6061-T6" in names
        for m in matches:
            assert m["density_g_cm3"] <= 3.0

    def test_calculate_specific_strength(self):
        ss = calculate_specific_strength("Titanium Ti-6Al-4V")
        assert ss is not None
        assert 200 < ss < 250

    def test_compute_rule_of_mixtures_unidirectional(self):
        E_c = compute_rule_of_mixtures(vf=0.6, ef=230, em=3.5, orientation_factor=1.0)
        assert E_c > 130
        assert E_c < 145

    def test_compute_corrosion_rate_typical(self):
        rate = compute_corrosion_rate(
            weight_loss_g=0.05, area_cm2=10.0, time_hours=720, density_g_cm3=7.85,
        )
        assert 0.05 < rate < 0.15

    def test_compute_galvanic_corrosion_risk_severe(self):
        risk = compute_galvanic_corrosion_risk(anode_potential_v=-1.2, cathode_potential_v=0.3)
        assert risk["risk_level"] == "severe"
        assert risk["potential_difference_V"] > 0.5

    def test_compute_hall_petch_strength(self):
        sigma = compute_hall_petch_strength(d_grain_um=10.0, k_hall_petch=0.5, sigma0_MPa=100.0)
        assert 100.0 < sigma < 200.0

    def test_compute_archard_wear_volume(self):
        vol = compute_archard_wear_volume(
            normal_load_N=100.0, sliding_distance_m=1000.0,
            hardness_Pa=2e9, wear_coefficient=1e-4,
        )
        assert vol > 0
        assert vol < 1e-4

    def test_compute_band_gap_from_wavelength(self):
        e = compute_band_gap_from_wavelength(620.0)
        assert 1.9 < e < 2.1

    def test_compute_surface_to_volume_ratio_sphere(self):
        sv = compute_surface_to_volume_ratio(10.0, shape_factor=6.0)
        assert 0.59 < sv < 0.61

    def test_compute_refractive_index_contrast(self):
        contrast = compute_refractive_index_contrast(1.458, 1.768)
        assert 0.1 < contrast < 0.2


# ==============================================================================
# Physics — Mechanistic Interpretability
# ==============================================================================


class TestMechanisticInterpretabilityWorkflows:
    def test_normalize_attribution(self):
        result = normalize_attribution([0.0, 5.0, 10.0])
        assert result[0] == 0.0
        assert result[2] == 1.0

    def test_superposition_simulate(self):
        result = superposition_simulate(num_features=50, num_dimensions=10, sparsity=0.15, num_samples=500)
        assert "reconstruction_error" in result
        assert "feature_recovery_rate" in result
        assert result["compression_ratio"] == 5.0

    def test_toy_model_reversal_curse(self):
        result = toy_model_reversal_curse(num_tokens=20, num_layers=2, training_steps=20)
        assert "forward_accuracy" in result
        assert "reverse_accuracy" in result
        assert "reversal_gap" in result
        assert result["forward_accuracy"] > 0.0

    def test_detect_circuits(self):
        layers = [
            [[0.5, 0.3, 0.2], [0.1, 0.6, 0.3]],
            [[0.51, 0.29, 0.21], [0.11, 0.59, 0.31]],
        ]
        result = detect_circuits(layers, [0.1, 0.2, 0.3], 0.0, threshold=0.9)
        assert result["num_circuits"] >= 1
        assert result["layers_analyzed"] == 2

    def test_analyze_attention_patterns(self):
        attn = [[[0.3, 0.7], [0.5, 0.5]]]
        result = analyze_attention_patterns(attn)
        assert result["num_heads"] == 1
        assert len(result["mean_attention_per_head"]) == 1
        assert len(result["entropy_per_head"]) == 1

    def test_detect_induction_heads(self):
        attn = [[[0.05, 0.90, 0.05], [0.90, 0.05, 0.05], [0.05, 0.90, 0.05]]]
        heads = detect_induction_heads(attn)
        assert 0 in heads

    def test_compute_faithfulness_nonzero_input(self):
        def model_fn(x: list[float]) -> list[float]:
            return [sum(x)]
        attr = [1.0, 0.0, 0.0]
        score = compute_faithfulness(attr, model_fn, [3.0, 2.0, 1.0])
        assert score >= 0.0

    def test_grokking_detect(self):
        losses = [2.0] * 30 + [0.1] * 30
        accs = [0.3] * 30 + [0.95] * 30
        result = grokking_detect(losses, accs)
        assert result["detected"]

    def test_integrated_gradients_smoke(self):
        def model_fn(x: list[float]) -> list[float]:
            return [sum(x), -sum(x)]
        ig = integrated_gradients(model_fn, [1.0, 2.0, 3.0], steps=10)
        assert len(ig) == 3
        for v in ig:
            assert isinstance(v, float)

    def test_compute_shap_values_smoke(self):
        def model_fn(x: list[float]) -> list[float]:
            return [sum(x)]
        shap = compute_shap_values(model_fn, [1.0, 2.0, 3.0], num_samples=20)
        assert len(shap) == 3


# ==============================================================================
# Physics — Research Paper Expert
# ==============================================================================


class TestResearchPaperExpertWorkflows:
    def test_identify_paper_structure_basic(self):
        text = (
            "Quantum Computing with Trapped Ions\n"
            "Alice B. Smith, Charlie D. Jones\n\n"
            "Abstract\nWe demonstrate a novel entanglement protocol.\n\n"
            "Introduction\nRecent advances in quantum computing have enabled...\n\n"
            "Methods\nWe employed an ion trap apparatus to measure...\n\n"
            "Results\nFigure 1 shows the measured fidelity.\n\n"
            "Conclusion\nWe have demonstrated...\n\n"
            "References\n[1] Smith et al. Nature 2020.\n"
        )
        struct = identify_paper_structure(text)
        assert struct.title == "Quantum Computing with Trapped Ions"
        assert len(struct.authors) >= 1
        assert struct.abstract != ""
        assert len(struct.sections) >= 1

    def test_identify_paper_structure_extracts_doi(self):
        text = "Title\nAuthor\n\nDOI: 10.1234/quantum.2025.001\n\nAbstract\nContent.\n\nMethods\nSetup."
        struct = identify_paper_structure(text)
        assert struct.doi == "10.1234/quantum.2025.001"

    def test_assess_methodology_computational_physics_na(self):
        paper = PaperStructure(
            title="test",
            abstract="We performed DFT simulations on VASP using 1000 atoms",
        )
        report = assess_methodology(paper)
        assert report.quality.value == "not_applicable"

    def test_assess_methodology_clinical_with_controls(self):
        paper = PaperStructure(
            title="test",
            abstract=(
                "n = 120 patients. "
                "We used a control group and randomized assignment. "
                "Data were analysed using t-test and ANOVA. "
                "Data are available at repository. "
                "Code is available on GitHub."
            ),
        )
        report = assess_methodology(paper)
        assert report.sample_size == 120
        assert report.sample_size_adequate
        assert report.has_control_group
        assert report.has_randomization
        assert len(report.statistical_tests) >= 1

    def test_assess_methodology_full_reproducibility(self):
        paper = PaperStructure(
            title="test",
            abstract=(
                "n = 200. control group. randomized. t-test. "
                "data are available. code is available on GitHub. "
                "ethical approval was obtained. "
                "Competing interests: none declared."
            ),
        )
        report = assess_methodology(paper)
        assert report.has_data_availability
        assert report.has_code_availability
        assert report.reproducibility.value == "fully_reproducible"

    def test_extract_data_key_findings(self):
        paper = PaperStructure(title="Test Paper", abstract="We find that the effect is significant.")
        data = extract_data(paper)
        assert isinstance(data, ExtractedData)
        assert len(data.key_findings) >= 1

    def test_search_literature_arxiv(self):
        hits = search_literature("quantum computing", source="arxiv")
        assert len(hits) == 1
        assert "arxiv" in hits[0].arxiv_id

    def test_search_literature_unknown_source(self):
        hits = search_literature("something", source="nonexistent")
        assert hits == []

    def test_get_journal_metrics_known(self):
        metrics = get_journal_metrics("PRL")
        assert metrics["name"] == "Physical Review Letters"
        assert metrics["impact_factor"] > 0

    def test_get_journal_metrics_unknown(self):
        assert get_journal_metrics("NoSuchJournal") == {}

    def test_format_reference_aps_article(self):
        entry = ReferenceEntry(
            authors=["Smith", "Jones"],
            title="Quantum Effects",
            journal="Phys. Rev. Lett.",
            volume="130",
            pages="100001",
            year=2023,
        )
        ref = format_reference("APS", entry)
        assert "Smith and Jones" in ref
        assert "Phys. Rev. Lett." in ref

    def test_format_reference_arxiv(self):
        entry = ReferenceEntry(
            authors=["Doe"],
            title="A Theory",
            arxiv_id="2501.00001",
            year=2025,
        )
        ref = format_reference("Nature", entry)
        assert "arxiv" in ref.lower()

    def test_suggest_method_fixes(self):
        paper = PaperStructure(title="test", abstract="small study")
        report = assess_methodology(paper)
        fixes = suggest_method_fixes(report)
        assert isinstance(fixes, list)
        for f in fixes:
            assert isinstance(f, str)


# ==============================================================================
# Account — Deletion Notices
# ==============================================================================


class TestDeletionNoticeWorkflows:
    def test_supported_services_set(self):
        assert "aws" in SUPPORTED_SERVICES
        assert "gcp" in SUPPORTED_SERVICES
        assert "azure" in SUPPORTED_SERVICES
        assert "openai" in SUPPORTED_SERVICES
        assert len(SUPPORTED_SERVICES) >= 5

    def test_build_deletion_notice_aws(self):
        notice = build_deletion_notice("aws")
        assert "AWS" in notice
        assert "retention" in notice.lower() or "delete" in notice.lower()

    def test_build_deletion_notice_deepseek(self):
        notice = build_deletion_notice("deepseek")
        assert "DeepSeek" in notice

    def test_build_deletion_notice_unknown_raises(self):
        with pytest.raises(ValueError, match="unknown service"):
            build_deletion_notice("nonexistent-service")

    def test_get_all_notices(self):
        notices = get_all_notices()
        assert len(notices) == len(SUPPORTED_SERVICES)
        for svc in SUPPORTED_SERVICES:
            assert svc in notices
            assert isinstance(notices[svc], str)

    def test_get_policy_text(self):
        text = get_policy_text("azure")
        assert len(text) > 50
        assert "Azure" in text

    def test_get_deletion_policy_re_export(self):
        policy = get_deletion_policy("gcp")
        assert len(policy) > 50


# ==============================================================================
# Account — Lifecycle Policy
# ==============================================================================


class TestLifecyclePolicyWorkflows:
    def test_policy_config_defaults(self):
        cfg = PolicyConfig()
        assert cfg.auto_delete_after_use is True
        assert cfg.retention_period_hours == 24
        assert cfg.budget_limit == 10.0

    def test_policy_config_custom(self):
        cfg = PolicyConfig(auto_delete_after_use=False, retention_period_hours=48, budget_limit=50.0)
        assert cfg.auto_delete_after_use is False
        assert cfg.retention_period_hours == 48

    def test_policy_config_raises_on_negative_retention(self):
        with pytest.raises(ValueError):
            PolicyConfig(retention_period_hours=0)

    def test_policy_config_raises_on_negative_budget(self):
        with pytest.raises(ValueError):
            PolicyConfig(budget_limit=-1.0)

    def test_evaluate_lifecycle_create_when_none(self):
        cfg = PolicyConfig()
        action = evaluate_lifecycle(account_id=None, policy=cfg, active=False, age_hours=0)
        assert action == LifecycleAction.CREATE

    def test_evaluate_lifecycle_keep_when_auto_delete_off(self):
        cfg = PolicyConfig(auto_delete_after_use=False)
        action = evaluate_lifecycle(account_id="acct-1", policy=cfg, active=True, age_hours=100)
        assert action == LifecycleAction.KEEP

    def test_evaluate_lifecycle_delete_when_inactive(self):
        cfg = PolicyConfig()
        action = evaluate_lifecycle(account_id="acct-2", policy=cfg, active=False, age_hours=1)
        assert action == LifecycleAction.DELETE

    def test_evaluate_lifecycle_delete_when_past_retention(self):
        cfg = PolicyConfig(retention_period_hours=24)
        action = evaluate_lifecycle(account_id="acct-3", policy=cfg, active=True, age_hours=48)
        assert action == LifecycleAction.DELETE

    def test_evaluate_lifecycle_keep_within_retention(self):
        cfg = PolicyConfig(retention_period_hours=24)
        action = evaluate_lifecycle(account_id="acct-4", policy=cfg, active=True, age_hours=10)
        assert action == LifecycleAction.KEEP

    def test_policy_config_to_dict(self):
        cfg = PolicyConfig(budget_limit=25.0)
        d = cfg.to_dict()
        assert d["budget_limit"] == 25.0
        assert d["auto_delete_after_use"] is True


# ==============================================================================
# Account — Ephemeral Account Manager & Credentials
# ==============================================================================


class TestEphemeralAccountWorkflows:
    def test_credentials_model_creates(self):
        creds = AccountCredentials(
            account_id="aws-ephemeral-abc123",
            provider="aws",
            access_key_id="AKIAEXAMPLE",
            secret_access_key="supersecret",
            budget_limit=5.0,
        )
        assert creds.account_id == "aws-ephemeral-abc123"
        assert creds.provider == "aws"
        assert "secret_access_key" not in repr(creds)

    def test_manager_initialization_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg_path = os.path.join(tmpdir, "ephemeral-accounts.json")
            mgr = EphemeralAccountManager(registry_path=reg_path)
            assert mgr.list_accounts() == []

    def test_manager_registry_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg_path = os.path.join(tmpdir, "ephemeral-accounts.json")
            mgr = EphemeralAccountManager(registry_path=reg_path)
            assert os.path.basename(mgr.registry_path) == "ephemeral-accounts.json"


# ==============================================================================
# Validation — Runner
# ==============================================================================


class TestValidationRunnerWorkflows:
    def test_runner_creation_default_paths(self):
        runner = ValidationRunner(
            todo_id="TODO-001",
            worktree_path="/tmp/test-worktree",
            test_commands=["echo '0 passed'"],
            expected_worktree_root="/tmp",
        )
        assert runner.todo_id == "TODO-001"
        assert runner.test_commands == ["echo '0 passed'"]

    def test_runner_with_tmpdir_passing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = ValidationRunner(
                todo_id="TODO-EXEC-001",
                worktree_path=tmpdir,
                test_commands=["echo '1 passed'"],
                expected_worktree_root=tmpdir,
                enforce_runner_allowlist=False,
            )
            result = runner.run_validation()
            assert isinstance(result, ValidationResult)

    def test_validation_result_data_model(self):
        result = ValidationResult(
            success=True, passed_count=42, failed_count=0, output="All green",
            failures=[],
        )
        assert result.success
        assert result.passed_count == 42
        assert result.failed_count == 0
        assert result.failures == []

    def test_create_child_todos_no_failures(self):
        runner = ValidationRunner(
            todo_id="T-PASS", worktree_path="/tmp/x", test_commands=[],
            expected_worktree_root="/tmp",
        )
        result = ValidationResult(success=True, passed_count=3, failed_count=0, output="ok")
        child = runner.create_child_todos_for_failures(result)
        assert child == []

    def test_create_child_todos_with_failures(self):
        runner = ValidationRunner(
            todo_id="T-FAIL", worktree_path="/tmp/x", test_commands=[],
            expected_worktree_root="/tmp",
        )
        result = ValidationResult(
            success=False, passed_count=1, failed_count=2, output="err",
            failures=["test_a.py::test_x", "test_b.py::test_y"],
        )
        children = runner.create_child_todos_for_failures(result)
        assert len(children) == 2
        assert all(c["parent_todo_id"] == "T-FAIL" for c in children)

    def test_create_child_todos_zero_tests_zero_failures(self):
        runner = ValidationRunner(
            todo_id="T-ZERO", worktree_path="/tmp/x", test_commands=[],
            expected_worktree_root="/tmp",
        )
        result = ValidationResult(success=False, passed_count=0, failed_count=0, output="")
        children = runner.create_child_todos_for_failures(result)
        assert len(children) == 1
        assert children[0]["category"] == "missing_tests"


# ==============================================================================
# Validation — Gap Analyzer
# ==============================================================================


class TestGapAnalyzerWorkflows:
    def test_analyze_finds_implementation_without_test(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = os.path.join(tmpdir, "src", "mylib")
            os.makedirs(src_dir)
            Path(os.path.join(src_dir, "calculations.py")).write_text("def add(a, b): return a + b\n")
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="sprint1", repo_root=tmpdir)
            assert report.total_gaps >= 1
            cats = [g.category for g in report.gaps]
            assert "missing_tests" in cats

    def test_analyze_no_gap_when_test_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = os.path.join(tmpdir, "src", "mylib")
            test_dir = os.path.join(tmpdir, "tests")
            os.makedirs(src_dir)
            os.makedirs(test_dir)
            Path(os.path.join(src_dir, "calculations.py")).write_text("def add(a, b): return a + b\n")
            Path(os.path.join(test_dir, "test_calculations.py")).write_text("def test_add(): assert add(1, 2) == 3\n")
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="sprint1", repo_root=tmpdir)
            missing = [g for g in report.gaps if g.category == "missing_tests" and "calculations" in g.description]
            assert len(missing) == 0

    def test_gap_item_model(self):
        item = GapItem(
            category="missing_molecule",
            description="playbook deploy has no molecule",
            severity="high",
            suggested_action="add molecule scenario",
        )
        assert item.severity == "high"
        assert item.category == "missing_molecule"

    def test_gap_report_model(self):
        report = GapReport(total_gaps=5, gaps=[])
        assert report.total_gaps == 5
        assert not report.gaps

    def test_missing_molecule_detection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pb_dir = os.path.join(tmpdir, "playbooks")
            os.makedirs(pb_dir)
            Path(os.path.join(pb_dir, "deploy_app.yml")).write_text("---\n")
            analyzer = GapAnalyzer()
            report = analyzer.analyze(sprint_path="sprint1", repo_root=tmpdir)
            mol_gaps = [g for g in report.gaps if g.category == "missing_molecule"]
            assert len(mol_gaps) >= 1


# ==============================================================================
# Validation — Log Auditor
# ==============================================================================


class TestLogAuditorWorkflows:
    def test_audit_clean_logs_passes(self):
        auditor = LogAuditor()
        entries = [
            {"event": "task_complete", "correlation_id": "c1", "todo_id": "T-1"},
            {"event": "task_dispatch", "correlation_id": "c2", "todo_id": "T-2"},
        ]
        report = auditor.audit_logs(entries)
        assert report.total_findings == 0

    def test_audit_missing_correlation_id(self):
        auditor = LogAuditor()
        report = auditor.audit_logs([{"event": "task_error", "todo_id": "T-1"}])
        assert report.total_findings >= 1
        assert any(f.category == "missing_correlation_id" for f in report.findings)

    def test_audit_secret_like_openai_key(self):
        auditor = LogAuditor()
        entries = [{"event": "auth", "correlation_id": "c3", "key": "sk-qwertyuiop1234567890abcdefghijk"}]
        report = auditor.audit_logs(entries)
        sec = [f for f in report.findings if f.category == "secret_like_value"]
        assert len(sec) >= 1
        assert sec[0].severity == "critical"

    def test_audit_secret_like_github_token(self):
        auditor = LogAuditor()
        entries = [{"event": "push", "correlation_id": "c4", "token": "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}]
        report = auditor.audit_logs(entries)
        assert any(f.category == "secret_like_value" for f in report.findings)

    def test_audit_secret_like_aws_key_in_payload(self):
        auditor = LogAuditor()
        entries = [{
            "event": "config",
            "correlation_id": "c5",
            "payload": {"aws_key": "AKIA1234567890ABCDEF"},
        }]
        report = auditor.audit_logs(entries)
        assert any(f.category == "secret_like_value" for f in report.findings)

    def test_audit_stuck_todo_after_five_attempts(self):
        auditor = LogAuditor()
        entries = [{
            "event": "status_change",
            "correlation_id": "c6",
            "todo_id": "T-stuck",
            "attempt": 5,
            "from_status": "pending",
            "to_status": "pending",
        }]
        report = auditor.audit_logs(entries)
        stuck = [f for f in report.findings if f.category == "stuck_todo"]
        assert len(stuck) >= 1
        assert stuck[0].severity == "high"

    def test_audit_no_stuck_below_threshold(self):
        auditor = LogAuditor()
        entries = [{
            "event": "status_change", "correlation_id": "c7",
            "todo_id": "T-ok", "attempt": 4,
            "from_status": "pending", "to_status": "pending",
        }]
        report = auditor.audit_logs(entries)
        assert not any(f.category == "stuck_todo" for f in report.findings)

    def test_audit_finding_model(self):
        finding = AuditFinding(
            severity="high", category="stuck_todo",
            description="stuck", evidence="...",
        )
        assert finding.severity == "high"
        assert finding.category == "stuck_todo"

    def test_audit_report_model_empty(self):
        report = AuditReport()
        assert report.total_findings == 0
        assert report.findings == []


# ==============================================================================
# Validation — Backlog Auditor
# ==============================================================================


class TestBacklogAuditorWorkflows:
    def _fake_test_runner(self, node_ids: list[str]) -> dict[str, bool]:
        return {nid: True for nid in node_ids}

    def _fake_file_reader(self, path: str) -> str | None:
        return f"def test_{os.path.basename(path)}():\n    return True\n"

    def test_audit_completed_with_evidence(self):
        auditor = BacklogAuditor(
            repo_root="/tmp",
            test_runner=self._fake_test_runner,
            file_reader=self._fake_file_reader,
        )
        tasks = [{
            "id": "T-complete",
            "status": "completed",
            "evidence_test_ids": ["test_x.py::test_y"],
            "touched_files": [],
            "acceptance_criteria": [],
        }]
        report = auditor.audit(tasks)
        assert report.total_audited == 1
        assert report.verified_complete == 1
        assert report.false_claim == 0

    def test_audit_skips_non_completed(self):
        auditor = BacklogAuditor(
            repo_root="/tmp",
            test_runner=self._fake_test_runner,
            file_reader=self._fake_file_reader,
        )
        tasks = [{"id": "T-pending", "status": "pending"}]
        report = auditor.audit(tasks)
        assert report.total_audited == 0

    def test_audit_false_claim_no_evidence(self):
        auditor = BacklogAuditor(
            repo_root="/tmp",
            test_runner=self._fake_test_runner,
            file_reader=self._fake_file_reader,
        )
        tasks = [{"id": "T-false", "status": "done"}]
        report = auditor.audit(tasks)
        assert report.false_claim == 1

    def test_audit_false_claim_failing_test(self):
        def failing_runner(node_ids: list[str]) -> dict[str, bool]:
            return {nid: False for nid in node_ids}

        auditor = BacklogAuditor(
            repo_root="/tmp",
            test_runner=failing_runner,
            file_reader=self._fake_file_reader,
        )
        tasks = [{
            "id": "T-failtest",
            "status": "completed",
            "evidence_test_ids": ["test_fail.py::test_broken"],
            "touched_files": [],
        }]
        report = auditor.audit(tasks)
        assert report.false_claim == 1

    def test_audit_incomplete_with_stub_marker(self):
        auditor = BacklogAuditor(
            repo_root="/tmp",
            test_runner=self._fake_test_runner,
            file_reader=lambda _p: "def foo():\n    raise NotImplementedError\n",
        )
        tasks = [{
            "id": "T-stub",
            "status": "completed",
            "evidence_test_ids": ["test_x.py::test_y"],
            "touched_files": ["src/stub.py"],
        }]
        report = auditor.audit(tasks)
        assert report.incomplete >= 1

    def test_task_verdict_constants(self):
        assert FALSE_CLAIM == "FALSE_CLAIM"
        assert INCOMPLETE == "INCOMPLETE"
        assert VERIFIED_COMPLETE == "VERIFIED_COMPLETE"

    def test_backlog_audit_report_aggregates(self):
        report = BacklogAuditReport(
            total_audited=5, verified_complete=3, false_claim=1, incomplete=1,
            verdicts=[TaskVerdict(id="t1", verdict=VERIFIED_COMPLETE, reasons=["ok"])],
        )
        assert report.total_audited == 5
        assert len(report.verdicts) == 1
        assert report.verdicts[0].verdict == VERIFIED_COMPLETE
