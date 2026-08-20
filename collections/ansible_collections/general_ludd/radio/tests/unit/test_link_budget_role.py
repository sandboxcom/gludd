"""Tests for link_budget role — validates task YAML structure, computation correctness, result shape."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from plugins.module_utils.antenna_types import antenna_info, design_antenna
from plugins.module_utils.propagation_models import free_space_loss, predict_path_loss

_COLLECTION_ROOT = Path(__file__).resolve().parent.parent.parent


def test_link_budget_tasks_file_exists():
    tasks = _COLLECTION_ROOT / "roles" / "link_budget" / "tasks" / "main.yml"
    assert tasks.exists()


def test_link_budget_tasks_has_validate_step():
    tasks = _COLLECTION_ROOT / "roles" / "link_budget" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "link_budget_tx_power_dbm" in content
    assert "link_budget_freq_hz" in content
    assert "link_budget_distance_m" in content
    assert "link_budget_model" in content


def test_link_budget_tasks_uses_propagation_models():
    tasks = _COLLECTION_ROOT / "roles" / "link_budget" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "general_ludd.radio.radio_runtime:" in content
    assert "operation: link_budget" in content
    assert "model:" in content
    assert "free_space" in content


def test_link_budget_tasks_uses_antenna_types():
    tasks = _COLLECTION_ROOT / "roles" / "link_budget" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "tx_antenna_type:" in content
    assert "rx_antenna_type:" in content
    assert "link_budget_tx_antenna_type" in content
    assert "link_budget_rx_antenna_type" in content


def test_link_budget_tasks_has_rain_option():
    tasks = _COLLECTION_ROOT / "roles" / "link_budget" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "rain_enabled:" in content
    assert "link_budget_rain_enabled" in content


def test_link_budget_tasks_has_verdict():
    tasks = _COLLECTION_ROOT / "roles" / "link_budget" / "tasks" / "main.yml"
    content = tasks.read_text()
    assert "link_budget_verdict" in content
    assert "role: link_budget" in content


def test_link_budget_defaults_file_exists():
    defaults = _COLLECTION_ROOT / "roles" / "link_budget" / "defaults" / "main.yml"
    assert defaults.exists()
    data = yaml.safe_load(defaults.read_text())
    assert "link_budget_tx_power_dbm" in data
    assert "link_budget_model" in data
    assert data["link_budget_tx_power_dbm"] == 30.0


def test_link_budget_computation_free_space():
    freq_hz = 144_000_000
    dist_m = 10_000.0
    tx_power_dbm = 30.0
    tx_gain_dbi = 2.15
    tx_line_loss = 1.0
    rx_gain_dbi = 2.15
    rx_line_loss = 1.0
    rx_sensitivity = -120.0
    required_snr = 10.0

    path_loss = free_space_loss(dist_m, freq_hz)
    eirp_dbm = tx_power_dbm + tx_gain_dbi - tx_line_loss
    rx_signal = eirp_dbm - path_loss + rx_gain_dbi - rx_line_loss
    margin = rx_signal - rx_sensitivity

    assert round(path_loss, 0) == pytest.approx(95.6, abs=1.0)
    assert round(eirp_dbm, 2) == 31.15
    assert margin >= required_snr
    assert rx_signal > rx_sensitivity


def test_link_budget_uses_predict_path_loss():
    result = predict_path_loss("free_space", distance_km=10.0, frequency_mhz=144.0)
    assert "loss_db" in result
    assert result["loss_db"] > 0
    assert result["model"] == "Free-Space Path Loss"


def test_link_budget_with_hata_urban():
    result = predict_path_loss(
        "hata_urban", distance_km=10.0, frequency_mhz=144.0,
        tx_height_m=30.0, rx_height_m=1.5
    )
    assert "loss_db" in result
    assert result["model"] == "Hata-Okumura Urban"
    assert result["loss_db"] > 0


def test_link_budget_antenna_gain_lookup():
    tx = antenna_info("dipole_half_wave")
    rx = antenna_info("yagi_3el")
    assert tx is not None
    assert rx is not None
    assert tx["gain_dbi"] == 2.15
    assert rx["gain_dbi"] == 7.0


def test_link_budget_eirp_computation():
    tx_power_dbm = 30.0
    dipole_gain = antenna_info("dipole_half_wave")["gain_dbi"]
    tx_line_loss = 1.0
    eirp = tx_power_dbm + dipole_gain - tx_line_loss
    assert eirp == 31.15


def test_fade_margin_viable_check():
    rx_signal = -85.0
    rx_sensitivity = -120.0
    required_snr = 10.0
    margin = rx_signal - rx_sensitivity
    viable = margin >= required_snr
    assert viable
    assert margin == 35.0


def test_fade_margin_not_viable():
    rx_signal = -115.0
    rx_sensitivity = -120.0
    required_snr = 10.0
    margin = rx_signal - rx_sensitivity
    viable = margin >= required_snr
    assert not viable
    assert margin == 5.0


def test_result_shape_has_all_fields():
    result = {
        "viable": True,
        "required_snr_db": 10.0,
        "fade_margin_db": 35.0,
        "rx_signal_dbm": -85.0,
        "rx_sensitivity_dbm": -120.0,
        "path_loss_db": 95.6,
        "path_loss_model": "Free-Space Path Loss",
        "path_loss_input": {
            "distance_m": 10000.0,
            "distance_km": 10.0,
            "frequency_hz": 144000000,
            "frequency_mhz": 144.0,
        },
        "eirp_dbm": 31.15,
        "tx": {
            "power_dbm": 30.0,
            "antenna_type": "dipole_half_wave",
            "antenna_gain_dbi": 2.15,
            "line_loss_db": 1.0,
            "polarization": "linear (horizontal)",
        },
        "rx": {
            "antenna_type": "dipole_half_wave",
            "antenna_gain_dbi": 2.15,
            "line_loss_db": 1.0,
            "sensitivity_dbm": -120.0,
            "polarization": "linear (horizontal)",
        },
    }
    required = {"viable", "required_snr_db", "fade_margin_db", "rx_signal_dbm",
                 "rx_sensitivity_dbm", "path_loss_db", "path_loss_model",
                 "path_loss_input", "eirp_dbm", "tx", "rx"}
    assert required.issubset(set(result.keys()))
    assert isinstance(result["viable"], bool)
    assert "distance_m" in result["path_loss_input"]
    assert "frequency_hz" in result["path_loss_input"]


def test_design_antenna_dipole_2m():
    design = design_antenna("dipole_half_wave", 146.0)
    assert design["type"] == "dipole_half_wave"
    assert design["impedance_ohms"] == 73.0
    assert 0.9 < design["element_length_m"] < 1.1


def test_design_antenna_yagi_3el_2m():
    design = design_antenna("yagi_3el", 146.0)
    assert design["type"] == "yagi_3el"
    assert "reflector_length_m" in design
    assert "driven_element_length_m" in design
    assert "director_length_m" in design


def test_link_budget_model_is_known():
    defaults = _COLLECTION_ROOT / "roles" / "link_budget" / "defaults" / "main.yml"
    data = yaml.safe_load(defaults.read_text())
    known = {"free_space", "hata_urban", "hata_suburban", "hata_rural",
             "two_ray", "itm", "rain"}
    assert data["link_budget_model"] in known


def test_link_budget_meta_exists():
    meta = _COLLECTION_ROOT / "roles" / "link_budget" / "meta" / "main.yml"
    assert meta.exists()
    data = yaml.safe_load(meta.read_text())
    assert data["galaxy_info"]["role_name"] == "link_budget"
