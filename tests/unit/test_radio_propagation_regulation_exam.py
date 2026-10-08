"""Tests for the propagation_model, regulation_lookup, exam_quiz standalone CLIs."""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

import pytest
from ansible_collections.general_ludd.radio.plugins.module_utils import (
    exam_quiz_runtime as eq,
)
from ansible_collections.general_ludd.radio.plugins.module_utils import (
    propagation_runtime as pm,
)
from ansible_collections.general_ludd.radio.plugins.module_utils import (
    radio_exam_data,
)
from ansible_collections.general_ludd.radio.plugins.module_utils import (
    regulation_lookup_runtime as rl,
)

# ============================================================================
# propagation_model.py
# ============================================================================


class TestPropagationModelAPI:
    def test_module_exports(self):
        assert hasattr(pm, "compute_path_loss")
        assert hasattr(pm, "PropagationVerdict")
        assert hasattr(pm, "VALID_MODELS")
        assert hasattr(pm, "main")

    def test_valid_models_complete(self):
        assert set(pm.VALID_MODELS) == {
            "free_space", "hata_urban", "hata_suburban",
            "hata_rural", "two_ray", "itm", "rain",
        }

    def test_propagation_verdict_defaults(self):
        v = pm.PropagationVerdict(
            model="free_space", freq_hz=433_000_000,
            distance_m=1000.0, tx_height_m=30.0, rx_height_m=1.5,
        )
        assert v.loss_db is None
        assert v.model_name == ""
        d = v.to_dict()
        assert d["verdict"] == "skipped"
        assert d["freq_mhz"] == pytest.approx(433.0)


class TestPropagationFreeSpace:
    def test_free_space_returns_loss(self):
        v = pm.compute_path_loss(
            model="free_space", freq_hz=433_000_000,
            distance_m=1000.0, tx_height_m=30.0, rx_height_m=1.5,
        )
        assert v.loss_db is not None
        assert v.loss_db > 0
        assert v.model_name == "Free-Space Path Loss"

    def test_free_space_loss_value(self):
        v = pm.compute_path_loss(
            model="free_space", freq_hz=2_400_000_000,
            distance_m=1000.0,
        )
        assert 90 < v.loss_db < 105

    def test_free_space_double_distance_adds_6db(self):
        v1 = pm.compute_path_loss("free_space", 433_000_000, 1000.0)
        v2 = pm.compute_path_loss("free_space", 433_000_000, 2000.0)
        delta = v2.loss_db - v1.loss_db
        assert 5.5 < delta < 6.5

    def test_freq_mhz_in_dict(self):
        v = pm.compute_path_loss("free_space", 146_000_000, 5000.0)
        d = v.to_dict()
        assert d["freq_mhz"] == pytest.approx(146.0)
        assert d["distance_km"] == pytest.approx(5.0)


class TestPropagationHata:
    def test_hata_urban_returns_loss(self):
        v = pm.compute_path_loss(
            "hata_urban", 900_000_000, 5000.0,
            tx_height_m=30.0, rx_height_m=1.5,
        )
        assert v.loss_db is not None
        assert v.loss_db > 100
        assert v.model_name == "Hata-Okumura Urban"

    def test_hata_suburban_less_loss_than_urban(self):
        urban = pm.compute_path_loss("hata_urban", 900_000_000, 5000.0)
        suburban = pm.compute_path_loss("hata_suburban", 900_000_000, 5000.0)
        assert suburban.loss_db < urban.loss_db

    def test_hata_rural_less_loss_than_suburban(self):
        suburban = pm.compute_path_loss("hata_suburban", 900_000_000, 5000.0)
        rural = pm.compute_path_loss("hata_rural", 900_000_000, 5000.0)
        assert rural.loss_db < suburban.loss_db


class TestPropagationTwoRay:
    def test_two_ray_returns_loss(self):
        v = pm.compute_path_loss(
            "two_ray", 433_000_000, 1000.0,
            tx_height_m=10.0, rx_height_m=2.0,
        )
        assert v.loss_db is not None
        assert v.model_name == "Two-Ray Ground Reflection"

    def test_two_ray_independent_of_freq(self):
        v1 = pm.compute_path_loss("two_ray", 100_000_000, 1000.0, tx_height_m=10.0, rx_height_m=2.0)
        v2 = pm.compute_path_loss("two_ray", 1_000_000_000, 1000.0, tx_height_m=10.0, rx_height_m=2.0)
        assert v1.loss_db == pytest.approx(v2.loss_db)


class TestPropagationITM:
    def test_itm_returns_loss(self):
        v = pm.compute_path_loss(
            "itm", 433_000_000, 50000.0,
            tx_height_m=30.0, rx_height_m=10.0,
            terrain_irregularity_m=50.0,
        )
        assert v.loss_db is not None
        assert v.loss_db > 0
        assert "Longley-Rice" in v.model_name or "ITM" in v.model_name

    def test_itm_has_modes_extra(self):
        v = pm.compute_path_loss("itm", 433_000_000, 10000.0, tx_height_m=30.0, rx_height_m=10.0)
        d = v.to_dict()
        assert "extra" in d


class TestPropagationRain:
    def test_rain_returns_attenuation(self):
        v = pm.compute_path_loss(
            "rain", 10_000_000_000, 10000.0,
            polarization="horizontal",
            rain_rate_mmh=25.0,
        )
        assert v.loss_db is not None
        assert v.loss_db > 0
        assert "P.838" in v.model_name or "Rain" in v.model_name

    def test_rain_heavier_more_loss(self):
        light = pm.compute_path_loss("rain", 10_000_000_000, 10000.0, rain_rate_mmh=5.0)
        heavy = pm.compute_path_loss("rain", 10_000_000_000, 10000.0, rain_rate_mmh=50.0)
        assert heavy.loss_db > light.loss_db


class TestPropagationInvalid:
    def test_unknown_model_returns_error(self):
        v = pm.compute_path_loss("flux_capacitor", 433_000_000, 1000.0)
        assert v.loss_db is None
        assert "error" in v.extra


class TestPropagationMain:
    def test_main_free_space_writes_json(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "propagation_model.py",
                "--model", "free_space",
                "--freq-hz", "433000000",
                "--distance-m", "1000",
                "--output-dir", str(tmp_path),
            ],
        )
        with contextlib.suppress(SystemExit):
            pm.main()
        out = tmp_path / "propagation_model.json"
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["model"] == "free_space"
        assert data["loss_db"] is not None
        assert data["verdict"] == "success"


# ============================================================================
# regulation_lookup.py
# ============================================================================


class TestRegulationAPI:
    def test_module_exports(self):
        assert hasattr(rl, "lookup")
        assert hasattr(rl, "RegulationVerdict")
        assert hasattr(rl, "itu_bands")
        assert hasattr(rl, "main")
        assert hasattr(rl, "SUPPORTED_COUNTRIES")

    def test_supported_countries(self):
        assert "US" in rl.SUPPORTED_COUNTRIES
        assert "CA" in rl.SUPPORTED_COUNTRIES


class TestRegulationFrequencyLookup:
    def test_lookup_2m_us(self):
        v = rl.lookup("US", freq_mhz=146.52)
        assert v.frequency_lookup is not None
        assert v.frequency_lookup["type"] == "amateur"
        assert v.frequency_lookup["band_name"] == "2m"
        assert "technician" in v.frequency_lookup

    def test_lookup_20m_us(self):
        v = rl.lookup("US", freq_mhz=14.205)
        assert v.frequency_lookup is not None
        assert v.frequency_lookup["band_name"] == "20m"

    def test_lookup_outside_amateur(self):
        v = rl.lookup("US", freq_mhz=123.0)
        assert v.error is not None

    def test_lookup_marine_channel_16(self):
        # Marine channels are returned via the --marine-channel path, not by
        # frequency lookup (US/CA countries have amateur bands that short-
        # circuit lookup_frequency before the marine fallback runs).
        v = rl.lookup("US", marine_channel=16)
        assert v.marine_channel is not None
        assert v.marine_channel["channel"] == 16
        assert "DISTRESS" in v.marine_channel["use"]

    def test_lookup_ca_band(self):
        v = rl.lookup("CA", freq_mhz=147.0)
        assert v.frequency_lookup is not None
        assert v.frequency_lookup["band_name"] == "2m"


class TestRegulationBandPlan:
    def test_band_plan_20m(self):
        v = rl.lookup("US", band_name="20m")
        assert v.band_plan is not None
        assert v.band_plan["start_hz"] == 14_000_000
        assert v.band_plan["end_hz"] == 14_350_000

    def test_band_plan_has_privileges(self):
        v = rl.lookup("US", band_name="80m")
        assert v.band_plan is not None
        assert "extra" in v.band_plan
        assert v.band_plan["extra"]["max_power_w"] == 1500

    def test_band_plan_unknown_returns_error(self):
        v = rl.lookup("US", band_name="999m")
        assert v.band_plan is None
        assert v.error is not None


class TestRegulationLicensePrivileges:
    def test_extra_privileges_nonempty(self):
        v = rl.lookup("US", license_class="extra")
        assert len(v.license_privileges) > 0
        names = [p["band_name"] for p in v.license_privileges]
        assert "20m" in names

    def test_technician_max_power_typical(self):
        v = rl.lookup("US", license_class="technician")
        assert any(p["band_name"] == "2m" for p in v.license_privileges)


class TestRegulationMarineChannel:
    def test_marine_channel_16_distress(self):
        v = rl.lookup("US", marine_channel=16)
        assert v.marine_channel is not None
        assert v.marine_channel["channel"] == 16
        assert "DISTRESS" in v.marine_channel["use"]

    def test_marine_channel_70_dsc(self):
        v = rl.lookup("US", marine_channel=70)
        assert v.marine_channel is not None
        assert v.marine_channel["simplex"] is True

    def test_marine_channel_invalid(self):
        v = rl.lookup("US", marine_channel=999)
        assert v.marine_channel is None


class TestRegulationITU:
    def test_itu_bands_returns_list(self):
        bands = rl.itu_bands()
        assert isinstance(bands, list)
        assert len(bands) >= 10
        names = [b["band"] for b in bands]
        assert "20m" in names
        assert "160m" in names


class TestRegulationVerdictDict:
    def test_to_dict_country_supported(self):
        v = rl.RegulationVerdict(country="US")
        d = v.to_dict()
        assert d["country_supported"] is True

    def test_to_dict_unsupported_country(self):
        v = rl.RegulationVerdict(country="ZZ")
        d = v.to_dict()
        assert d["country_supported"] is False


class TestRegulationMain:
    def test_main_freq_lookup(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "regulation_lookup.py",
                "--country", "US",
                "--freq-mhz", "146.52",
                "--output-dir", str(tmp_path),
            ],
        )
        with contextlib.suppress(SystemExit):
            rl.main()
        out = tmp_path / "regulation_lookup.json"
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["country"] == "US"
        assert data["frequency_lookup"]["band_name"] == "2m"

    def test_main_requires_lookup_arg(self, monkeypatch):
        monkeypatch.setattr(
            sys, "argv", ["regulation_lookup.py", "--country", "US"]
        )
        with pytest.raises(SystemExit):
            rl.main()


# ============================================================================
# exam_quiz.py
# ============================================================================


class TestExamQuizAPI:
    def test_module_exports(self):
        assert hasattr(eq, "load_questions")
        assert hasattr(eq, "grade_answers")
        assert hasattr(eq, "ExamQuizVerdict")
        assert hasattr(eq, "VALID_EXAMS")
        assert hasattr(eq, "main")

    def test_valid_exams_complete(self):
        assert set(eq.VALID_EXAMS) == {
            "fcc_tech", "fcc_general", "fcc_extra", "roc_m", "gmdss",
        }


class TestExamLoadQuestions:
    def test_load_fcc_tech_with_seed(self):
        v = eq.load_questions("fcc_tech", count=5, seed=42)
        assert v.exam == "fcc_tech"
        assert len(v.questions) == 5
        assert v.seed == 42
        assert v.total_available > 5

    def test_load_more_than_available(self):
        v = eq.load_questions("fcc_tech", count=999, seed=1)
        assert len(v.questions) == v.total_available

    def test_load_seed_reproducible(self):
        v1 = eq.load_questions("fcc_tech", count=5, seed=99)
        v2 = eq.load_questions("fcc_tech", count=5, seed=99)
        ids1 = [q["id"] for q in v1.questions]
        ids2 = [q["id"] for q in v2.questions]
        assert ids1 == ids2

    def test_load_questions_have_choices(self):
        v = eq.load_questions("fcc_tech", count=3, seed=7)
        for q in v.questions:
            assert "id" in q
            assert "text" in q
            assert "choices" in q
            assert len(q["choices"]) >= 3

    def test_load_invalid_exam_returns_empty(self):
        v = eq.load_questions("nonexistent", count=5)
        assert v.questions == []

    def test_to_dict_includes_metadata(self):
        v = eq.load_questions("fcc_general", count=2, seed=3)
        d = v.to_dict()
        assert d["exam"] == "fcc_general"
        assert d["exam_display"] == "Fcc General"
        assert d["verdict"] == "loaded"


class TestExamGrade:
    def test_grade_all_correct(self):
        qs = radio_exam_data.get_questions("fcc_tech", 3)
        answers = {q["id"]: q["correct"] for q in qs}
        v = eq.grade_answers("fcc_tech", answers, count=3, seed=42)
        assert v.grade is not None
        assert v.grade["correct"] == 3
        assert v.grade["total"] == 3
        assert v.grade["percentage"] == 100.0
        assert v.grade["passed"] is True

    def test_grade_all_wrong(self):
        qs = radio_exam_data.get_questions("fcc_tech", 3)
        answers = {q["id"]: (q["correct"] + 1) % len(q["choices"]) for q in qs}
        v = eq.grade_answers("fcc_tech", answers, count=3, seed=42)
        assert v.grade["correct"] == 0
        assert v.grade["percentage"] == 0.0
        assert v.grade["passed"] is False

    def test_grade_mixed(self):
        qs = radio_exam_data.get_questions("fcc_tech", 4)
        answers = {}
        for i, q in enumerate(qs):
            answers[q["id"]] = q["correct"] if i < 2 else (q["correct"] + 1) % len(q["choices"])
        v = eq.grade_answers("fcc_tech", answers, count=4, seed=42)
        assert v.grade["correct"] == 2
        assert v.grade["total"] == 4
        assert v.grade["percentage"] == 50.0

    def test_grade_threshold_pass(self):
        pct = round(7 / 10 * 100, 1)
        assert pct >= eq.PASS_THRESHOLD_PCT

    def test_grade_unknown_question(self):
        v = eq.grade_answers("fcc_tech", {"FAKE01": 0}, count=1, seed=1)
        assert v.grade["correct"] == 0
        assert v.grade["results"][0]["explanation"] == "Unknown question ID"


class TestExamQuizMain:
    def test_main_load_writes_json(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "exam_quiz.py",
                "--exam", "fcc_tech",
                "--count", "3",
                "--seed", "42",
                "--output-dir", str(tmp_path),
            ],
        )
        with contextlib.suppress(SystemExit):
            eq.main()
        out = tmp_path / "exam_quiz.json"
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["exam"] == "fcc_tech"
        assert len(data["questions"]) == 3
        assert data["verdict"] == "loaded"

    def test_main_grade_with_answers(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "exam_quiz.py",
                "--exam", "fcc_tech",
                "--count", "2",
                "--seed", "5",
                "--answers", json.dumps({"T1A01": 1, "T1A02": 2}),
                "--output-dir", str(tmp_path),
            ],
        )
        with contextlib.suppress(SystemExit):
            eq.main()
        out = tmp_path / "exam_quiz.json"
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["verdict"] == "graded"
        assert "grade" in data

    def test_main_text_format(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "exam_quiz.py",
                "--exam", "fcc_tech",
                "--count", "2",
                "--seed", "5",
                "--format", "text",
                "--output-dir", str(tmp_path),
            ],
        )
        with contextlib.suppress(SystemExit):
            eq.main()
        out = tmp_path / "exam_quiz.txt"
        assert out.exists()
        text = out.read_text()
        assert "Fcc Tech" in text
        assert "Questions loaded" in text

    def test_main_invalid_answers_json(self, monkeypatch):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "exam_quiz.py",
                "--exam", "fcc_tech",
                "--answers", "{not valid json",
            ],
        )
        with pytest.raises(SystemExit):
            eq.main()
