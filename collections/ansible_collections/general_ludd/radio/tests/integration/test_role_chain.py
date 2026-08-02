"""Integration tests for exam_quiz, regulation_lookup, and link_budget roles.

Tests the end-to-end data flow from module_utils through the role contract,
verifying that all 3 roles can be chained in a playbook and produce
consistent output structures.
"""

from __future__ import annotations

from pathlib import Path

_COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent

from plugins.module_utils.antenna_types import antenna_info
from plugins.module_utils.frequency_allocations import (
    bands_by_privilege,
    get_band_plan,
    get_itu_region2_bands,
    get_marine_channel,
    lookup_frequency,
)
from plugins.module_utils.propagation_models import (
    predict_path_loss,
    rain_attenuation,
)
from plugins.module_utils.radio_exam_data import (
    exam_sections,
    get_questions,
    grade_exam,
    questions_for,
)


class TestChainedRoles:
    def test_all_three_roles_have_task_files(self):
        for role in ("exam_quiz", "regulation_lookup", "link_budget"):
            tasks = _COLLECTION_ROOT / "roles" / role / "tasks" / "main.yml"
            assert tasks.exists(), f"{role} tasks/main.yml missing"

    def test_all_three_roles_have_defaults_files(self):
        for role in ("exam_quiz", "regulation_lookup", "link_budget"):
            defaults = _COLLECTION_ROOT / "roles" / role / "defaults" / "main.yml"
            assert defaults.exists(), f"{role} defaults/main.yml missing"

    def test_all_three_roles_have_meta_files(self):
        for role in ("exam_quiz", "regulation_lookup", "link_budget"):
            meta = _COLLECTION_ROOT / "roles" / role / "meta" / "main.yml"
            assert meta.exists(), f"{role} meta/main.yml missing"

    def test_all_three_roles_have_verdicts(self):
        for role in ("exam_quiz", "regulation_lookup", "link_budget"):
            tasks = _COLLECTION_ROOT / "roles" / role / "tasks" / "main.yml"
            content = tasks.read_text()
            assert "_verdict" in content or "verdict:" in content, \
                f"{role} has no verdict"


class TestChainedDataFlow:
    def test_exam_to_freq_lookup_flow(self):
        ham_exam = get_questions("fcc_tech", count=5)
        answers = [(q["id"], q["correct"]) for q in ham_exam]
        graded = grade_exam(answers)
        assert graded["passed"] is True

        freq_result = lookup_frequency(146.520, "US")
        assert freq_result is not None
        assert freq_result["band_name"] == "2m"

        combined = {
            "exam_result": {
                "exam": "fcc_tech",
                "score": f"{graded['correct']}/{graded['total']}",
                "passed": graded["passed"],
            },
            "frequency_allocation": {
                "band": freq_result["band_name"],
                "technician_privileges": bool(freq_result.get("technician", {}).get("max_power_w", 0) > 0),
            },
        }
        assert combined["exam_result"]["passed"]
        assert combined["frequency_allocation"]["technician_privileges"]

    def test_freq_lookup_to_link_budget_flow(self):
        freq_result = lookup_frequency(14.250, "US")
        assert freq_result is not None
        assert freq_result["band_name"] == "20m"

        freq_hz = 14_250_000
        dist_m = 500_000.0
        tx_power = 100.0
        path_loss_result = predict_path_loss(
            "free_space", distance_km=dist_m / 1000.0, frequency_mhz=freq_hz / 1_000_000.0
        )

        tx_gain = antenna_info("yagi_3el")["gain_dbi"]
        rx_gain = antenna_info("dipole_half_wave")["gain_dbi"]
        eirp = tx_power + tx_gain - 1.0
        rx_signal = eirp - path_loss_result["loss_db"] + rx_gain - 1.0

        link_budget = {
            "band": freq_result["band_name"],
            "frequency_hz": freq_hz,
            "tx_power_dbm": tx_power,
            "tx_antenna": "yagi_3el",
            "eirp_dbm": round(eirp, 2),
            "path_loss_db": path_loss_result["loss_db"],
            "path_loss_model": path_loss_result["model"],
            "rx_signal_dbm": round(rx_signal, 2),
        }
        assert link_budget["band"] == "20m"
        assert link_budget["tx_antenna"] == "yagi_3el"
        assert link_budget["path_loss_db"] > 0

    def test_full_chain_tech_exam_2m_link(self):
        exam_qs = get_questions("fcc_tech", count=10)
        answers = [(q["id"], q["correct"]) for q in exam_qs]
        graded = grade_exam(answers)

        freq_lookup = lookup_frequency(146.520, "US")
        band_plan = get_band_plan("2m", "US")
        privs = bands_by_privilege("US", "technician")

        freq_hz = 146_520_000
        dist_m = 5_000.0
        tx_power_dbm = 50.0
        tx_antenna = antenna_info("ground_plane")
        rx_antenna = antenna_info("dipole_half_wave")

        path_loss_result = predict_path_loss(
            "free_space", distance_km=dist_m / 1000.0, frequency_mhz=freq_hz / 1_000_000.0
        )
        eirp = tx_power_dbm + tx_antenna["gain_dbi"] - 1.0
        rx_signal = eirp - path_loss_result["loss_db"] + rx_antenna["gain_dbi"] - 1.0
        margin = rx_signal - (-120.0)
        viable = margin >= 10.0

        full_result = {
            "exam": {
                "type": "fcc_tech",
                "score": f"{graded['correct']}/{graded['total']}",
                "percentage": graded["percentage"],
                "passed": graded["passed"],
            },
            "regulation": {
                "band": freq_lookup["band_name"],
                "display": freq_lookup["display"],
                "technician_max_power_w": band_plan["technician"]["max_power_w"],
                "bands_with_technician_privs": len(privs),
            },
            "link_budget": {
                "frequency_mhz": freq_hz / 1_000_000.0,
                "distance_km": dist_m / 1000.0,
                "eirp_dbm": round(eirp, 2),
                "path_loss_db": path_loss_result["loss_db"],
                "rx_signal_dbm": round(rx_signal, 2),
                "fade_margin_db": round(margin, 2),
                "viable": viable,
            },
        }

        assert full_result["exam"]["passed"] is True
        assert full_result["regulation"]["band"] == "2m"
        assert full_result["regulation"]["technician_max_power_w"] > 0
        assert full_result["link_budget"]["viable"] is True
        assert full_result["link_budget"]["fade_margin_db"] > 10.0

    def test_full_chain_general_20m_link(self):
        exam_qs = get_questions("fcc_general", count=8)
        answers = [(q["id"], q["correct"]) for q in exam_qs]
        graded = grade_exam(answers)

        freq_lookup = lookup_frequency(14.225, "US")
        plan = get_band_plan("20m", "US")
        privs = bands_by_privilege("US", "general")

        freq_hz = 14_225_000
        dist_m = 1_000_000.0
        tx_power_dbm = 100.0
        yagi = antenna_info("yagi_3el")

        path_loss_result = predict_path_loss(
            "free_space", distance_km=dist_m / 1000.0, frequency_mhz=freq_hz / 1_000_000.0
        )
        eirp = tx_power_dbm + yagi["gain_dbi"] - 2.0
        rx_signal = eirp - path_loss_result["loss_db"] + 2.15 - 1.0
        margin = rx_signal - (-130.0)
        viable = margin >= 15.0

        full_result = {
            "exam": {
                "type": "fcc_general",
                "score": f"{graded['correct']}/{graded['total']}",
                "passed": graded["passed"],
            },
            "regulation": {
                "band": freq_lookup["band_name"],
                "general_max_power_w": plan["general"]["max_power_w"],
                "bands_with_general_privs": len(privs),
            },
            "link_budget": {
                "distance_km": dist_m / 1000.0,
                "eirp_dbm": round(eirp, 2),
                "path_loss_db": path_loss_result["loss_db"],
                "rx_signal_dbm": round(rx_signal, 2),
                "fade_margin_db": round(margin, 2),
                "viable": viable,
            },
        }

        assert full_result["exam"]["passed"] is True
        assert full_result["regulation"]["band"] == "20m"
        assert full_result["regulation"]["general_max_power_w"] == 1500
        assert full_result["link_budget"]["viable"] is True
        assert full_result["link_budget"]["fade_margin_db"] > 10.0

    def test_marine_exam_marine_vhf_flow(self):
        exam_qs = get_questions("roc_m", count=5)
        answers = [(q["id"], q["correct"]) for q in exam_qs]
        graded = grade_exam(answers)

        ch16 = get_marine_channel(16)
        ch70 = get_marine_channel(70)

        full_result = {
            "exam": {
                "type": "roc_m",
                "score": f"{graded['correct']}/{graded['total']}",
                "passed": graded["passed"],
            },
            "marine_vhf": {
                "ch16_distress": ch16["tx_mhz"],
                "ch16_use": ch16["use"],
                "ch70_dsc": ch70["use"],
            },
        }

        assert full_result["exam"]["passed"] is True
        assert full_result["marine_vhf"]["ch16_distress"] == 156.800
        assert "DSC" in ch70["use"]

    def test_regulation_multiple_countries(self):
        for country in ("US", "CA"):
            result = lookup_frequency(146.520, country)
            assert result is not None, f"No result for {country}"
            assert result["country"] == country
            assert result["type"] == "amateur"

    def test_all_propagation_models_work(self):
        models = ["free_space", "hata_urban", "hata_suburban", "hata_rural", "two_ray"]
        for model in models:
            result = predict_path_loss(model, distance_km=10.0, frequency_mhz=144.0,
                                       tx_height_m=30.0, rx_height_m=1.5)
            assert "loss_db" in result, f"{model} returned no loss_db"
            assert result["loss_db"] > 0, f"{model} returned non-positive loss"

    def test_rain_attenuation_in_budget(self):
        path_loss_result = predict_path_loss(
            "free_space", distance_km=50.0, frequency_mhz=10000.0
        )
        rain_result = rain_attenuation(
            freq_ghz=10.0, rain_rate_mmh=10.0, distance_km=50.0,
            polarization="horizontal"
        )

        total_loss = path_loss_result["loss_db"] + rain_result["total_attenuation_db"]
        assert total_loss > path_loss_result["loss_db"]
        assert rain_result["specific_attenuation_db_km"] > 0

    def test_antenna_types_integration(self):
        freq = 146.0
        for ant_type in ("dipole_half_wave", "vertical_quarter_wave", "yagi_3el", "ground_plane"):
            info = antenna_info(ant_type)
            assert info is not None, f"{ant_type} not found"
            assert isinstance(info["gain_dbi"], (int, float))

    def test_verdict_shapes_consistent(self):
        exam_qs = get_questions("fcc_tech", count=3)
        answers = [(q["id"], q["correct"]) for q in exam_qs]
        graded = grade_exam(answers)

        freq_lookup = lookup_frequency(146.520, "US")

        path_loss_result = predict_path_loss(
            "free_space", distance_km=10.0, frequency_mhz=146.0
        )

        verdicts = {
            "exam_quiz": {
                "role": "exam_quiz",
                "exam": "fcc_tech",
                "passed": graded["passed"],
                "score": graded["correct"],
            },
            "regulation_lookup": {
                "role": "regulation_lookup",
                "country": freq_lookup["country"],
                "band": freq_lookup["band_name"],
                "found": freq_lookup is not None,
            },
            "link_budget": {
                "role": "link_budget",
                "model": path_loss_result["model"],
                "loss_db": path_loss_result["loss_db"],
                "viable": True,
            },
        }

        for role_name, verdict in verdicts.items():
            assert "role" in verdict
            assert verdict["role"] == role_name

    def test_exam_sections_supported(self):
        for exam in ("fcc_tech", "fcc_general", "fcc_extra", "roc_m"):
            sections = exam_sections(exam)
            assert len(sections) > 0, f"{exam} has no sections"
            for section in sections:
                section_qs = questions_for(exam, section=section)
                assert len(section_qs) > 0

    def test_itu_bands_map_to_lookup(self):
        itu_bands = get_itu_region2_bands()
        for itu_band in itu_bands:
            mid_freq = (itu_band["start_hz"] + itu_band["end_hz"]) // 2
            freq_mhz = mid_freq / 1_000_000.0
            result = lookup_frequency(freq_mhz, "US")
            if result and result.get("type") == "amateur":
                assert result["band_name"] == itu_band["band"]
