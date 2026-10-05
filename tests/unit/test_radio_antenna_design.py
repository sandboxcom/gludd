"""Tests for antenna_design script at collections/.../antenna_design.py."""

from __future__ import annotations

import contextlib
import math
import sys
from pathlib import Path

import pytest

import tests.conftest as test_support

_ANTENNA_DESIGN_PATH = (
    Path(__file__).resolve().parents[2]
    / "collections/ansible_collections/general_ludd/radio/roles/antenna_design/files/antenna_design.py"
)
ad = test_support._load_path_module_isolated(
    "general_ludd_radio_antenna_design_under_test", _ANTENNA_DESIGN_PATH
)

SPEED_OF_LIGHT_MS = 299_792_458.0


class TestAntennaDimensions:
    def test_creates_with_defaults(self):
        dims = ad.AntennaDimensions()
        assert dims.type == "dipole"
        assert dims.freq_hz == 144_000_000
        assert dims.impedance_ohms == 73.0
        assert dims.gain_dbi == 2.15

    def test_to_dict_has_required_keys(self):
        dims = ad.AntennaDimensions(freq_hz=146_000_000)
        result = dims.to_dict()
        for key in (
            "type", "freq_hz", "freq_mhz", "wavelength_m",
            "half_wavelength_m", "quarter_wavelength_m",
            "velocity_factor", "material", "conductor_diameter_mm",
            "element_length_m", "element_length_in",
            "impedance_ohms", "bandwidth_hz", "bandwidth_pct",
            "gain_dbi", "swr_typical", "polarization",
        ):
            assert key in result, f"missing key: {key}"

    def test_to_dict_freq_hz_converts_to_mhz(self):
        dims = ad.AntennaDimensions(freq_hz=146_000_000)
        result = dims.to_dict()
        assert result["freq_mhz"] == pytest.approx(146.0)

    def test_to_dict_zero_freq_bandwidth_pct(self):
        dims = ad.AntennaDimensions(freq_hz=0)
        result = dims.to_dict()
        assert result["bandwidth_pct"] == 0.0


class TestComputeKFactor:
    def test_zero_length_to_diameter(self):
        assert ad._compute_k_factor(0.0) == 1.0

    def test_negative_length_to_diameter(self):
        assert ad._compute_k_factor(-5.0) == 1.0

    def test_typical_dipole_ratio(self):
        half_wl = 1.0
        diam = 0.002
        k = ad._compute_k_factor(half_wl / diam)
        assert 0.95 < k < 1.0

    def test_very_large_ratio(self):
        k = ad._compute_k_factor(1e10)
        assert k == pytest.approx(0.96, rel=0.01)

    def test_small_ratio(self):
        k = ad._compute_k_factor(0.001)
        assert 0.90 < k < 1.0


class TestDesignDipole:
    def test_2m_dipole_basic(self):
        result = ad.design_dipole(146_000_000)
        assert isinstance(result, ad.AntennaDimensions)
        assert result.type == "dipole"
        assert result.freq_hz == 146_000_000
        assert result.gain_dbi == 2.15
        assert result.swr_typical == 1.5

    def test_2m_dipole_element_length(self):
        result = ad.design_dipole(146_000_000)
        wavelength = SPEED_OF_LIGHT_MS * 0.95 / 146_000_000
        half_wl = wavelength / 2.0
        expected_k = 0.978 + 0.0022 * math.log10(half_wl / 0.002)
        assert result.element_length_m == pytest.approx(half_wl * expected_k, rel=0.02)

    def test_dipole_impedance_positive_and_reasonable(self):
        result = ad.design_dipole(146_000_000)
        assert result.impedance_ohms > 0

    def test_dipole_wavelength_quarter_relationship(self):
        result = ad.design_dipole(146_000_000)
        assert result.half_wavelength_m == pytest.approx(result.wavelength_m / 2.0)
        assert result.quarter_wavelength_m == pytest.approx(result.wavelength_m / 4.0)

    def test_dipole_bandwidth_is_5_percent(self):
        result = ad.design_dipole(100_000_000)
        assert result.bandwidth_hz == pytest.approx(5_000_000, rel=0.01)

    def test_dipole_10m_band(self):
        result = ad.design_dipole(28_500_000)
        expected_wavelength = SPEED_OF_LIGHT_MS * 0.95 / 28_500_000
        assert result.wavelength_m == pytest.approx(expected_wavelength, rel=0.01)
        assert 4.9 < result.half_wavelength_m < 6.0

    def test_dipole_with_insulated_wire_material(self):
        result = ad.design_dipole(146_000_000, velocity_factor=0.80, material="insulated_wire")
        assert result.velocity_factor == 0.80
        assert result.material == "insulated_wire"
        expected_wavelength = SPEED_OF_LIGHT_MS * 0.80 / 146_000_000
        assert result.wavelength_m == pytest.approx(expected_wavelength, rel=0.01)

    def test_dipole_horizontal_polarization(self):
        result = ad.design_dipole(146_000_000, polarization="horizontal")
        assert result.polarization == "horizontal"

    def test_dipole_large_conductor_shorter_element(self):
        thick = ad.design_dipole(146_000_000, conductor_diameter_m=0.010)
        thin = ad.design_dipole(146_000_000, conductor_diameter_m=0.001)
        assert thick.element_length_m < thin.element_length_m

    def test_dipole_uhf_band(self):
        result = ad.design_dipole(440_000_000)
        assert result.wavelength_m < 1.0
        assert result.element_length_m < 0.5


class TestDesignYagi:
    def test_yagi_returns_dict_with_elements(self):
        result = ad.design_yagi(146_000_000)
        assert isinstance(result, dict)
        assert "elements" in result
        assert len(result["elements"]) == 3

    def test_yagi_element_names(self):
        result = ad.design_yagi(146_000_000)
        names = [e["name"] for e in result["elements"]]
        assert names == ["reflector", "driven_element", "director_1"]

    def test_yagi_reflector_longest(self):
        result = ad.design_yagi(146_000_000)
        elems = {e["name"]: e["length_m"] for e in result["elements"]}
        assert elems["reflector"] > elems["driven_element"]
        assert elems["driven_element"] > elems["director_1"]

    def test_yagi_element_positions(self):
        result = ad.design_yagi(146_000_000)
        elems = {e["name"]: e["position_m"] for e in result["elements"]}
        assert elems["reflector"] == 0.0
        assert elems["driven_element"] > 0.0
        assert elems["director_1"] > elems["driven_element"]

    def test_yagi_has_boom_length(self):
        result = ad.design_yagi(146_000_000)
        assert "boom_length_m" in result
        assert "boom_length_in" in result
        assert result["boom_length_m"] > 0

    def test_yagi_radiation_pattern(self):
        result = ad.design_yagi(146_000_000)
        pattern = result["radiation_pattern"]
        assert pattern["beamwidth_h_deg"] == 65
        assert pattern["beamwidth_v_deg"] == 55
        assert pattern["f_b_ratio_db"] == 15.0

    def test_yagi_gain_and_bandwidth(self):
        result = ad.design_yagi(146_000_000)
        assert result["gain_dbi"] == 7.15
        assert result["bandwidth_hz"] == pytest.approx(146_000_000 * 0.02, rel=0.01)

    def test_yagi_70cm_band(self):
        result = ad.design_yagi(440_000_000)
        wavelength = SPEED_OF_LIGHT_MS * 0.95 / 440_000_000
        spacing = wavelength * 0.2
        assert result["boom_length_m"] == pytest.approx(spacing * 3, rel=0.01)

    def test_yagi_default_aluminum_material(self):
        result = ad.design_yagi(146_000_000)
        assert result["material"] == "aluminum"


class TestDesignLoop:
    def test_loop_returns_dict(self):
        result = ad.design_loop(146_000_000)
        assert isinstance(result, dict)
        assert result["type"] == "loop"

    def test_loop_circumference_approx_wavelength(self):
        result = ad.design_loop(146_000_000)
        wavelength = SPEED_OF_LIGHT_MS * 0.95 / 146_000_000
        assert result["circumference_m"] == pytest.approx(wavelength * 1.02, rel=0.01)

    def test_loop_diameter_radius_relationship(self):
        result = ad.design_loop(146_000_000)
        assert result["loop_diameter_m"] == pytest.approx(result["loop_radius_m"] * 2.0, rel=0.01)

    def test_loop_has_capacitor_value(self):
        result = ad.design_loop(28_500_000)
        assert "required_capacitor_pf" in result
        assert result["required_capacitor_pf"] > 0

    def test_loop_matching_is_gamma(self):
        result = ad.design_loop(146_000_000)
        matching = result["matching"]
        assert matching["type"] == "gamma_match"
        assert matching["tap_point_pct"] == 25

    def test_loop_radiation_pattern(self):
        result = ad.design_loop(146_000_000)
        pattern = result["radiation_pattern"]
        assert pattern["beamwidth_h_deg"] == 80
        assert pattern["beamwidth_v_deg"] == 80
        assert pattern["f_b_ratio_db"] == 0.0

    def test_loop_bandwidth_3_percent(self):
        result = ad.design_loop(100_000_000)
        assert result["bandwidth_hz"] == pytest.approx(3_000_000, rel=0.01)

    def test_loop_gain(self):
        result = ad.design_loop(146_000_000)
        assert result["gain_dbi"] == 3.65


class TestDesignPatch:
    def test_patch_returns_dict(self):
        result = ad.design_patch(2_450_000_000)
        assert isinstance(result, dict)
        assert result["type"] == "patch"

    def test_patch_has_substrate_info(self):
        result = ad.design_patch(2_450_000_000)
        sub = result["substrate"]
        assert sub["material"] == "FR-4"
        assert sub["dielectric_constant"] == 4.4
        assert sub["thickness_mm"] == 1.6

    def test_patch_dimensions_positive(self):
        result = ad.design_patch(2_450_000_000)
        assert result["patch_width_m"] > 0
        assert result["patch_length_m"] > 0
        assert result["patch_width_mm"] > 0
        assert result["patch_length_mm"] > 0

    def test_patch_ground_plane_larger_than_patch(self):
        result = ad.design_patch(2_450_000_000)
        assert result["ground_plane_mm"] > max(result["patch_width_mm"], result["patch_length_mm"])

    def test_patch_feed_inset_positive(self):
        result = ad.design_patch(2_450_000_000)
        assert result["feed_inset_mm"] > 0

    def test_patch_feed_line_width(self):
        result = ad.design_patch(2_450_000_000)
        assert result["feed_line_width_mm"] == 3.09
        assert result["impedance_50ohm_line_width_mm"] == 3.09

    def test_patch_radiation_pattern(self):
        result = ad.design_patch(2_450_000_000)
        pattern = result["radiation_pattern"]
        assert pattern["beamwidth_h_deg"] == 80
        assert pattern["beamwidth_v_deg"] == 70
        assert pattern["gain_above_ground_dbi"] == 8.0

    def test_patch_5ghz_wifi_band(self):
        result = ad.design_patch(5_800_000_000)
        assert result["patch_width_mm"] > result["patch_length_mm"]

    def test_patch_different_freq_produces_different_dimensions(self):
        low = ad.design_patch(2_450_000_000)
        high = ad.design_patch(5_800_000_000)
        assert low["patch_length_mm"] > high["patch_length_mm"]


class TestDesignDiscone:
    def test_discone_returns_dict(self):
        result = ad.design_discone(146_000_000)
        assert isinstance(result, dict)
        assert result["type"] == "discone"

    def test_discone_disc_and_cone_dimensions(self):
        result = ad.design_discone(146_000_000)
        assert "disc_diameter_mm" in result
        assert "cone_height_mm" in result
        assert "cone_base_diameter_mm" in result
        assert result["disc_diameter_mm"] > 0
        assert result["cone_height_mm"] > 0

    def test_discone_gap_is_positive(self):
        result = ad.design_discone(146_000_000)
        assert result["disc_cone_gap_mm"] > 2.0

    def test_discone_gap_increases_with_frequency(self):
        low = ad.design_discone(100_000_000)
        high = ad.design_discone(1_000_000_000)
        assert high["disc_cone_gap_mm"] > low["disc_cone_gap_mm"]

    def test_discone_bandwidth_is_large(self):
        result = ad.design_discone(146_000_000)
        assert result["bandwidth_hz"] == pytest.approx(146_000_000 * 4.0, rel=0.01)
        assert result["bandwidth_ratio"] == "4:1 typical (up to 10:1 achieved)"

    def test_discone_radiation_pattern(self):
        result = ad.design_discone(146_000_000)
        pattern = result["radiation_pattern"]
        assert pattern["pattern_type"] == "omnidirectional in azimuth"
        assert pattern["elevation_max_deg"] == 0
        assert pattern["polarization"] == "vertical"

    def test_discone_disc_larger_than_wavelength(self):
        result = ad.design_discone(146_000_000)
        wavelength = SPEED_OF_LIGHT_MS / 146_000_000
        disc_diameter_m = result["disc_diameter_mm"] / 1000.0
        assert disc_diameter_m > wavelength * 0.5

    def test_discone_gain_and_swr(self):
        result = ad.design_discone(146_000_000)
        assert result["gain_dbi"] == 1.8
        assert result["swr_typical"] == 2.0


class TestDesignersRegistry:
    def test_all_types_registered(self):
        assert sorted(ad.DESIGNERS) == ["dipole", "discone", "loop", "patch", "yagi"]

    def test_each_designer_is_callable(self):
        for key, func in ad.DESIGNERS.items():
            assert callable(func), f"{key} designer is not callable"

    def test_each_designer_accepts_freq_hz(self):
        import inspect
        for key, func in ad.DESIGNERS.items():
            sig = inspect.signature(func)
            assert "freq_hz" in sig.parameters, f"{key} missing freq_hz"


class TestVelocityFactors:
    def test_all_materials_have_factor(self):
        for material in ("copper", "aluminum", "steel", "stainless_steel",
                          "bare_wire", "insulated_wire", "air", "pcb_fr4"):
            assert material in ad.VELOCITY_FACTORS

    def test_factors_in_expected_range(self):
        for material, vf in ad.VELOCITY_FACTORS.items():
            assert 0.5 < vf <= 1.0, f"{material} VF={vf} out of range"


class TestWireGaugeMM:
    def test_gauge_dict_has_entries(self):
        assert len(ad.WIRE_GAUGE_MM) > 0

    def test_thicker_wire_has_larger_diameter(self):
        for gauge in range(6, 24, 2):
            assert ad.WIRE_GAUGE_MM[gauge] > ad.WIRE_GAUGE_MM[gauge + 2]

    def test_common_gauge_14(self):
        assert ad.WIRE_GAUGE_MM[14] == pytest.approx(1.628, rel=0.01)


class TestMainFunction:
    def test_main_dipole_creates_json_output(self, tmp_path: Path, monkeypatch):
        import json
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "antenna_design.py",
                "--type", "dipole",
                "--freq", "146000000",
                "--polarization", "vertical",
                "--output-dir", str(tmp_path),
            ],
        )
        with contextlib.suppress(SystemExit):
            ad.main()
        output_file = tmp_path / "antenna_design.json"
        assert output_file.exists()
        data = json.loads(output_file.read_text())
        assert data["type"] == "dipole"
        assert data["freq_hz"] == 146_000_000
        assert "design_notes" in data
        assert len(data["design_notes"]) >= 2

    def test_main_yagi_adds_tuning_note(self, tmp_path: Path, monkeypatch):
        import json
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "antenna_design.py",
                "--type", "yagi",
                "--freq", "146000000",
                "--output-dir", str(tmp_path),
            ],
        )
        with contextlib.suppress(SystemExit):
            ad.main()
        data = json.loads((tmp_path / "antenna_design.json").read_text())
        assert any("tuning" in note.lower() for note in data["design_notes"])

    def test_main_patch_adds_fr4_note(self, tmp_path: Path, monkeypatch):
        import json
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "antenna_design.py",
                "--type", "patch",
                "--freq", "2450000000",
                "--output-dir", str(tmp_path),
            ],
        )
        with contextlib.suppress(SystemExit):
            ad.main()
        data = json.loads((tmp_path / "antenna_design.json").read_text())
        assert any("FR-4" in note for note in data["design_notes"])

    def test_main_discone_adds_gap_note(self, tmp_path: Path, monkeypatch):
        import json
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "antenna_design.py",
                "--type", "discone",
                "--freq", "146000000",
                "--output-dir", str(tmp_path),
            ],
        )
        with contextlib.suppress(SystemExit):
            ad.main()
        data = json.loads((tmp_path / "antenna_design.json").read_text())
        assert any("gap" in note.lower() for note in data["design_notes"])

    def test_main_outputs_to_stdout(self, capsys, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "antenna_design.py",
                "--type", "dipole",
                "--freq", "144000000",
                "--output-dir", str(tmp_path),
            ],
        )
        with contextlib.suppress(SystemExit):
            ad.main()
        captured = capsys.readouterr()
        assert "dipole" in captured.out
        assert "144000000" in captured.out

    def test_main_unsupported_type_exits(self, monkeypatch):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "antenna_design.py",
                "--type", "flux_capacitor",
                "--freq", "146000000",
            ],
        )
        with pytest.raises(SystemExit):
            ad.main()

    def test_main_non_existent_output_dir_created(self, tmp_path: Path, monkeypatch):
        nested = tmp_path / "deeply" / "nested" / "output"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "antenna_design.py",
                "--type", "dipole",
                "--freq", "146000000",
                "--output-dir", str(nested),
            ],
        )
        with contextlib.suppress(SystemExit):
            ad.main()
        assert (nested / "antenna_design.json").exists()


class TestEdgeCases:
    def test_dipole_zero_frequency(self):
        with pytest.raises(ZeroDivisionError):
            ad.design_dipole(0)

    def test_dipole_very_low_frequency(self):
        result = ad.design_dipole(100_000)
        assert result.wavelength_m > 1000.0

    def test_dipole_very_high_frequency(self):
        result = ad.design_dipole(10_000_000_000)
        assert result.wavelength_m < 0.1

    def test_yagi_very_low_frequency(self):
        result = ad.design_yagi(1_000_000)
        assert result["boom_length_m"] > 10.0

    def test_loop_very_low_frequency(self):
        result = ad.design_loop(1_000_000)
        assert result["loop_diameter_m"] > 50.0

    def test_patch_very_low_frequency(self):
        result = ad.design_patch(1_000_000)
        assert result["patch_width_mm"] > 100.0

    def test_discone_very_low_frequency(self):
        result = ad.design_discone(10_000_000)
        assert result["disc_diameter_mm"] > 1000.0


class TestSpeedOfLight:
    def test_constant_is_correct(self):
        assert ad.SPEED_OF_LIGHT_MS == 299_792_458.0
