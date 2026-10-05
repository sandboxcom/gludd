"""Tests for link_budget CLI at collections/.../link_budget/files/link_budget.py."""

from __future__ import annotations

import contextlib
import json
import math
import sys
from pathlib import Path

import pytest
from ansible_collections.general_ludd.radio.plugins.module_utils import (
    link_budget_runtime as lb,
)


class TestComputeLinkBudget:
    def test_returns_dict_with_required_keys(self):
        result = lb.compute_link_budget(
            tx_power_dbm=30.0,
            freq_hz=144_000_000,
            distance_m=10_000.0,
        )
        for key in (
            "eirp_dbm",
            "path_loss_db",
            "rx_signal_dbm",
            "fade_margin_db",
            "viable",
            "path_loss_model",
            "tx",
            "rx",
            "required_snr_db",
            "rx_sensitivity_dbm",
        ):
            assert key in result, f"missing key: {key}"

    def test_eirp_is_tx_power_plus_gain_minus_loss(self):
        result = lb.compute_link_budget(
            tx_power_dbm=30.0,
            tx_antenna_gain_dbi=10.0,
            tx_line_loss_db=1.0,
            tx_antenna_type=None,
            freq_hz=144_000_000,
            distance_m=10_000.0,
        )
        assert result["eirp_dbm"] == pytest.approx(39.0, abs=0.1)

    def test_rx_signal_matches_eirp_minus_path_plus_rx_gain(self):
        result = lb.compute_link_budget(
            tx_power_dbm=30.0,
            tx_antenna_gain_dbi=2.15,
            tx_line_loss_db=1.0,
            rx_antenna_gain_dbi=2.15,
            rx_line_loss_db=1.0,
            tx_antenna_type=None,
            rx_antenna_type=None,
            freq_hz=144_000_000,
            distance_m=10_000.0,
        )
        eirp = 30.0 + 2.15 - 1.0
        expected_rx = eirp - result["path_loss_db"] + 2.15 - 1.0
        assert result["rx_signal_dbm"] == pytest.approx(expected_rx, abs=0.5)

    def test_fade_margin_is_rx_signal_minus_sensitivity(self):
        result = lb.compute_link_budget(
            tx_power_dbm=30.0,
            tx_antenna_type=None,
            freq_hz=144_000_000,
            distance_m=10_000.0,
            rx_sensitivity_dbm=-120.0,
        )
        assert result["fade_margin_db"] == pytest.approx(
            result["rx_signal_dbm"] - (-120.0), abs=0.5
        )

    def test_viable_true_when_margin_exceeds_required_snr(self):
        result = lb.compute_link_budget(
            tx_power_dbm=100.0,
            tx_antenna_gain_dbi=20.0,
            tx_antenna_type=None,
            rx_antenna_gain_dbi=20.0,
            rx_antenna_type=None,
            freq_hz=144_000_000,
            distance_m=100.0,
            rx_sensitivity_dbm=-120.0,
            required_snr_db=10.0,
        )
        assert result["viable"] is True

    def test_viable_false_when_margin_below_required_snr(self):
        result = lb.compute_link_budget(
            tx_power_dbm=-50.0,
            tx_antenna_type=None,
            rx_antenna_type=None,
            freq_hz=144_000_000,
            distance_m=10_000_000.0,
            rx_sensitivity_dbm=-50.0,
            required_snr_db=100.0,
        )
        assert result["viable"] is False

    def test_default_model_is_free_space(self):
        result = lb.compute_link_budget(
            tx_power_dbm=30.0,
            tx_antenna_type=None,
            freq_hz=144_000_000,
            distance_m=10_000.0,
        )
        assert "free" in result["path_loss_model"].lower()

    def test_path_loss_input_echoes_distance_and_freq(self):
        result = lb.compute_link_budget(
            tx_power_dbm=30.0,
            tx_antenna_type=None,
            freq_hz=433_000_000,
            distance_m=5_000.0,
        )
        assert result["path_loss_input"]["distance_m"] == 5_000.0
        assert result["path_loss_input"]["frequency_hz"] == 433_000_000
        assert result["path_loss_input"]["distance_km"] == pytest.approx(5.0, abs=0.01)


class TestAntennaLookup:
    def test_tx_gain_from_antenna_type_overrides_explicit(self):
        result = lb.compute_link_budget(
            tx_power_dbm=30.0,
            tx_antenna_type="yagi_5el",
            freq_hz=144_000_000,
            distance_m=10_000.0,
        )
        assert result["tx"]["antenna_gain_dbi"] == pytest.approx(10.0, abs=0.1)

    def test_unknown_antenna_falls_back_to_explicit_gain(self):
        result = lb.compute_link_budget(
            tx_power_dbm=30.0,
            tx_antenna_type="not_a_real_antenna",
            tx_antenna_gain_dbi=7.5,
            freq_hz=144_000_000,
            distance_m=10_000.0,
        )
        assert result["tx"]["antenna_gain_dbi"] == pytest.approx(7.5, abs=0.1)

    def test_tx_polarization_recorded(self):
        result = lb.compute_link_budget(
            tx_power_dbm=30.0,
            tx_antenna_type="dipole_half_wave",
            freq_hz=144_000_000,
            distance_m=10_000.0,
        )
        assert "polarization" in result["tx"]
        assert "horizontal" in result["tx"]["polarization"]

    def test_rx_polarization_recorded(self):
        result = lb.compute_link_budget(
            tx_power_dbm=30.0,
            tx_antenna_type="dipole_half_wave",
            rx_antenna_type="vertical_quarter_wave",
            freq_hz=144_000_000,
            distance_m=10_000.0,
        )
        assert "vertical" in result["rx"]["polarization"]


class TestRainAttenuation:
    def test_rain_attenuation_applied_when_enabled(self):
        result = lb.compute_link_budget(
            tx_power_dbm=30.0,
            tx_antenna_type=None,
            rx_antenna_type=None,
            freq_hz=12_000_000_000,
            distance_m=10_000.0,
            rain_enabled=True,
            rain_rate_mmh=25.0,
        )
        assert "rain_attenuation_db" in result
        assert "fade_margin_with_rain_db" in result
        assert "viable_with_rain" in result
        assert result["fade_margin_with_rain_db"] < result["fade_margin_db"]

    def test_rain_not_applied_when_disabled(self):
        result = lb.compute_link_budget(
            tx_power_dbm=30.0,
            tx_antenna_type=None,
            freq_hz=144_000_000,
            distance_m=10_000.0,
            rain_enabled=False,
        )
        assert "rain_attenuation_db" not in result

    def test_rain_attenuation_positive_at_ka_band(self):
        result = lb.compute_link_budget(
            tx_power_dbm=30.0,
            tx_antenna_type=None,
            rx_antenna_type=None,
            freq_hz=20_000_000_000,
            distance_m=10_000.0,
            rain_enabled=True,
            rain_rate_mmh=50.0,
        )
        assert result["rain_attenuation_db"] > 0.0


class TestPropagationModels:
    @pytest.mark.parametrize(
        "model",
        [
            "free_space",
            "hata_urban",
            "hata_suburban",
            "hata_rural",
            "two_ray",
        ],
    )
    def test_each_model_returns_finite_positive_path_loss(self, model):
        result = lb.compute_link_budget(
            tx_power_dbm=30.0,
            tx_antenna_type=None,
            rx_antenna_type=None,
            freq_hz=900_000_000,
            distance_m=5_000.0,
            model=model,
        )
        assert math.isfinite(result["path_loss_db"])
        assert result["path_loss_db"] > 0.0

    def test_invalid_model_raises(self):
        with pytest.raises(ValueError):
            lb.compute_link_budget(
                tx_power_dbm=30.0,
                tx_antenna_type=None,
                freq_hz=144_000_000,
                distance_m=10_000.0,
                model="flux_capacitor",
            )


class TestMainCli:
    def _run_main(self, tmp_path: Path, *extra: str) -> dict:
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                sys,
                "argv",
                [
                    "link_budget.py",
                    "--tx-power", "30",
                    "--freq-hz", "144000000",
                    "--distance-m", "10000",
                    "--output-dir", str(tmp_path),
                    *extra,
                ],
            )
            with contextlib.suppress(SystemExit):
                lb.main()
        return json.loads((tmp_path / "link_budget.json").read_text())

    def test_main_writes_json_file(self, tmp_path: Path):
        data = self._run_main(tmp_path)
        assert "eirp_dbm" in data
        assert "fade_margin_db" in data
        assert "viable" in data

    def test_main_outputs_json_to_stdout(self, capsys, tmp_path: Path):
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                sys,
                "argv",
                [
                    "link_budget.py",
                    "--tx-power", "37",
                    "--freq-hz", "146000000",
                    "--distance-m", "10000",
                    "--output-dir", str(tmp_path),
                ],
            )
            with contextlib.suppress(SystemExit):
                lb.main()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["tx"]["power_dbm"] == 37.0

    def test_main_creates_nested_output_dir(self, tmp_path: Path):
        nested = tmp_path / "deeply" / "nested"
        self._run_main(nested)
        assert (nested / "link_budget.json").exists()

    def test_main_rain_flag_produces_rain_fields(self, tmp_path: Path):
        data = self._run_main(
            tmp_path,
            "--freq-hz", "12000000000",
            "--rain-enabled",
            "--rain-rate", "25",
        )
        assert "rain_attenuation_db" in data

    def test_main_invalid_model_exits_nonzero(self):
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                sys,
                "argv",
                [
                    "link_budget.py",
                    "--tx-power", "30",
                    "--freq-hz", "144000000",
                    "--distance-m", "10000",
                    "--model", "flux_capacitor",
                ],
            )
            with pytest.raises(SystemExit):
                lb.main()

    def test_main_antenna_type_overrides_gain(self, tmp_path: Path):
        data = self._run_main(tmp_path, "--tx-antenna-type", "yagi_3el")
        assert data["tx"]["antenna_gain_dbi"] == pytest.approx(7.0, abs=0.1)


class TestEdgeCases:
    def test_zero_distance_raises(self):
        with pytest.raises((ValueError, ZeroDivisionError)):
            lb.compute_link_budget(
                tx_power_dbm=30.0,
                tx_antenna_type=None,
                freq_hz=144_000_000,
                distance_m=0.0,
            )

    def test_zero_freq_raises(self):
        with pytest.raises((ValueError, ZeroDivisionError)):
            lb.compute_link_budget(
                tx_power_dbm=30.0,
                tx_antenna_type=None,
                freq_hz=0,
                distance_m=10_000.0,
            )

    def test_negative_distance_raises(self):
        with pytest.raises(ValueError):
            lb.compute_link_budget(
                tx_power_dbm=30.0,
                tx_antenna_type=None,
                freq_hz=144_000_000,
                distance_m=-1.0,
            )

    def test_high_power_short_link_has_large_margin(self):
        result = lb.compute_link_budget(
            tx_power_dbm=50.0,
            tx_antenna_gain_dbi=20.0,
            tx_antenna_type=None,
            rx_antenna_gain_dbi=20.0,
            rx_antenna_type=None,
            freq_hz=5_800_000_000,
            distance_m=100.0,
        )
        assert result["viable"] is True
        assert result["fade_margin_db"] > 50.0


class TestValidModelsConstant:
    def test_valid_models_includes_all_canonical(self):
        for m in ("free_space", "hata_urban", "hata_suburban", "hata_rural", "two_ray", "rain"):
            assert m in lb.VALID_MODELS
