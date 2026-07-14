"""Tests for propagation_models module."""

from __future__ import annotations

from plugins.module_utils.propagation_models import (
    free_space_loss,
    hata_urban,
    hata_suburban,
    hata_rural,
    two_ray_loss,
    itm_loss,
    rain_attenuation,
    predict_path_loss,
)


def test_free_space_loss_positive():
    loss = free_space_loss(1000.0, 146_000_000)
    assert loss > 0.0


def test_free_space_loss_increases_with_distance():
    loss_1km = free_space_loss(1000.0, 146_000_000)
    loss_10km = free_space_loss(10000.0, 146_000_000)
    assert loss_10km > loss_1km


def test_free_space_loss_increases_with_frequency():
    loss_146 = free_space_loss(1000.0, 146_000_000)
    loss_440 = free_space_loss(1000.0, 440_000_000)
    assert loss_440 > loss_146


def test_free_space_loss_known_value():
    loss = free_space_loss(1000.0, 100_000_000)
    expected = 20.0 * __import__("math").log10(1000.0) + 20.0 * __import__("math").log10(100_000_000) - 147.55
    assert loss == expected


def test_hata_urban_returns_float():
    loss = hata_urban(5.0, 900.0, 30.0, 1.5)
    assert isinstance(loss, float)
    assert loss > 0.0


def test_hata_urban_increases_with_distance():
    loss_5 = hata_urban(5.0, 900.0, 30.0, 1.5)
    loss_10 = hata_urban(10.0, 900.0, 30.0, 1.5)
    assert loss_10 > loss_5


def test_hata_suburban_less_than_urban():
    urban = hata_urban(5.0, 900.0, 30.0, 1.5)
    suburban = hata_suburban(5.0, 900.0, 30.0, 1.5)
    assert suburban < urban


def test_hata_rural_less_than_urban():
    urban = hata_urban(5.0, 900.0, 30.0, 1.5)
    rural = hata_rural(5.0, 900.0, 30.0, 1.5)
    assert rural < urban


def test_two_ray_loss_positive():
    loss = two_ray_loss(1000.0, 30.0, 1.5)
    assert loss > 0.0


def test_two_ray_loss_increases_with_distance():
    loss_1km = two_ray_loss(1000.0, 30.0, 1.5)
    loss_10km = two_ray_loss(10000.0, 30.0, 1.5)
    assert loss_10km > loss_1km


def test_two_ray_loss_decreases_with_height():
    loss_low = two_ray_loss(5000.0, 10.0, 1.5)
    loss_high = two_ray_loss(5000.0, 100.0, 1.5)
    assert loss_high < loss_low


def test_itm_loss_returns_dict_with_loss():
    result = itm_loss(50.0, 146.0, 30.0, 1.5)
    assert isinstance(result, dict)
    assert "loss_db" in result
    assert result["loss_db"] > 0
    assert "free_space_loss_db" in result
    assert "modes" in result


def test_itm_loss_los_scenario():
    result = itm_loss(10.0, 146.0, 30.0, 10.0)
    mode = result["modes"]["line_of_sight"]
    assert mode["active"]


def test_itm_loss_over_horizon():
    result = itm_loss(200.0, 146.0, 30.0, 1.5)
    assert result["loss_db"] > result["free_space_loss_db"]


def test_itm_loss_terrain_dict():
    result = itm_loss(50.0, 146.0, 30.0, 1.5)
    terrain = result["terrain"]
    assert "irregularity_m" in terrain
    assert "h_eff_tx_m" in terrain
    assert "h_eff_rx_m" in terrain


def test_rain_attenuation_returns_dict():
    result = rain_attenuation(10.0, 12.5, 5.0)
    assert isinstance(result, dict)
    assert "specific_attenuation_db_km" in result
    assert "total_attenuation_db" in result
    assert result["specific_attenuation_db_km"] > 0


def test_rain_attenuation_increases_with_rain_rate():
    light = rain_attenuation(10.0, 2.5, 5.0)
    heavy = rain_attenuation(10.0, 50.0, 5.0)
    assert heavy["total_attenuation_db"] > light["total_attenuation_db"]


def test_rain_attenuation_increases_with_distance():
    short = rain_attenuation(10.0, 12.5, 1.0)
    long = rain_attenuation(10.0, 12.5, 10.0)
    assert long["total_attenuation_db"] > short["total_attenuation_db"]


def test_rain_attenuation_zero_distance():
    result = rain_attenuation(10.0, 12.5, 0.0)
    assert result["total_attenuation_db"] == 0.0


def test_rain_attenuation_interpolates_frequencies():
    freq = 11.0
    result = rain_attenuation(freq, 12.5, 5.0)
    r10 = rain_attenuation(10.0, 12.5, 5.0)
    r12 = rain_attenuation(12.0, 12.5, 5.0)
    assert result["specific_attenuation_db_km"] >= min(r10["specific_attenuation_db_km"], r12["specific_attenuation_db_km"])
    assert result["specific_attenuation_db_km"] <= max(r10["specific_attenuation_db_km"], r12["specific_attenuation_db_km"])


def test_rain_attenuation_vertical_polarization():
    h = rain_attenuation(10.0, 12.5, 5.0, "horizontal")
    v = rain_attenuation(10.0, 12.5, 5.0, "vertical")
    assert h["total_attenuation_db"] != v["total_attenuation_db"]


def test_predict_path_loss_free_space():
    result = predict_path_loss("free_space", 10.0, 146.0)
    assert result["model"] == "Free-Space Path Loss"
    assert isinstance(result["loss_db"], float)
    assert result["loss_db"] > 0


def test_predict_path_loss_hata_urban():
    result = predict_path_loss("hata_urban", 5.0, 900.0, 30.0, 1.5)
    assert result["model"] == "Hata-Okumura Urban"
    assert result["loss_db"] > 0


def test_predict_path_loss_hata_suburban():
    result = predict_path_loss("hata_suburban", 5.0, 900.0, 30.0, 1.5)
    assert result["loss_db"] > 0


def test_predict_path_loss_hata_rural():
    result = predict_path_loss("hata_rural", 5.0, 900.0, 30.0, 1.5)
    assert result["loss_db"] > 0


def test_predict_path_loss_two_ray():
    result = predict_path_loss("two_ray", 1.0, tx_height_m=30.0, rx_height_m=1.5)
    assert result["loss_db"] > 0


def test_predict_path_loss_itm():
    result = predict_path_loss("itm", 50.0, 146.0, 30.0, 1.5)
    assert result["model"] == "ITM (Longley-Rice)"
    assert result["loss_db"] > 0


def test_predict_path_loss_rain():
    result = predict_path_loss("rain", 5.0, frequency_mhz=10000.0, rain_rate_mmh=25.0)
    assert "ITU-R P.838" in result["model"]
    assert result["total_attenuation_db"] > 0


def test_predict_path_loss_default_params():
    result = predict_path_loss("free_space", 1.0)
    assert result["loss_db"] > 0


def test_predict_path_loss_unknown_model():
    result = predict_path_loss("quantum_entanglement", 10.0)
    assert "error" in result
