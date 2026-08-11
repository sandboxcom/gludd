from __future__ import annotations

import math

import pytest

from general_ludd.electronics.core import (
    BOLTZMANN,
    Impedance,
    adc_resolution,
    adc_snr,
    bjt_base_resistor,
    bjt_power_dissipation,
    capacitor_charge,
    capacitor_energy,
    capacitor_impedance,
    current_divider,
    db_to_ratio,
    e_series_values,
    i2c_pullup_max,
    i2c_pullup_min,
    inductor_energy,
    inductor_impedance,
    led_resistor,
    mosfet_gate_power,
    nearest_e_value,
    ohms_law_i,
    ohms_law_r,
    ohms_law_v,
    opamp_differential_gain,
    opamp_inverting_gain,
    opamp_non_inverting_gain,
    parallel_impedance,
    parallel_resistance,
    parallel_two,
    power_db_to_ratio,
    power_ratio_to_db,
    power_resistive,
    ratio_to_db,
    rc_cutoff_frequency,
    rc_time_constant,
    rc_voltage_charge,
    rc_voltage_discharge,
    rl_cutoff_frequency,
    rl_time_constant,
    rlc_quality_factor_series,
    rlc_series_resonant_frequency,
    series_impedance,
    series_resistance,
    settling_time_to,
    thermal_noise_rms,
    voltage_divider,
)

# ============================================================================
# Ohm's Law
# ============================================================================


class TestOhmsLaw:
    def test_v_from_i_r(self):
        assert ohms_law_v(2.0, 10.0) == 20.0
        assert ohms_law_v(0.0, 100.0) == 0.0

    def test_i_from_v_r(self):
        assert ohms_law_i(10.0, 5.0) == 2.0

    def test_i_zero_resistance_raises(self):
        with pytest.raises(ValueError, match="Resistance must be nonzero"):
            ohms_law_i(5.0, 0.0)

    def test_r_from_v_i(self):
        assert ohms_law_r(10.0, 2.0) == 5.0

    def test_r_zero_current_raises(self):
        with pytest.raises(ValueError, match="Current must be nonzero"):
            ohms_law_r(10.0, 0.0)


# ============================================================================
# Power
# ============================================================================


class TestPowerResistive:
    def test_v_times_i(self):
        assert power_resistive(voltage=12.0, current=2.0) == 24.0

    def test_i2r(self):
        assert power_resistive(current=3.0, resistance=4.0) == 36.0

    def test_v2_over_r(self):
        assert power_resistive(voltage=10.0, resistance=5.0) == 20.0

    def test_v2_over_zero_raises(self):
        with pytest.raises(ValueError, match="Resistance must be nonzero"):
            power_resistive(voltage=10.0, resistance=0.0)

    def test_insufficient_args_raises(self):
        with pytest.raises(ValueError, match="Provide at least two"):
            power_resistive(voltage=5.0)


# ============================================================================
# Series and parallel resistance
# ============================================================================


class TestSeriesResistance:
    def test_single(self):
        assert series_resistance(100.0) == 100.0

    def test_multiple(self):
        assert series_resistance(10.0, 20.0, 30.0) == 60.0

    def test_none(self):
        assert series_resistance() == 0.0


class TestParallelResistance:
    def test_two_equal(self):
        assert parallel_resistance(10.0, 10.0) == pytest.approx(5.0)

    def test_three(self):
        assert parallel_resistance(10.0, 20.0, 30.0) == pytest.approx(1.0 / (1 / 10 + 1 / 20 + 1 / 30))

    def test_empty(self):
        assert parallel_resistance() == float("inf")

    def test_zero_resistance_in_list(self):
        assert parallel_resistance(0.0, 10.0) == 0.0


class TestParallelTwo:
    def test_standard(self):
        assert parallel_two(4.0, 6.0) == pytest.approx(2.4)

    def test_one_zero(self):
        assert parallel_two(0.0, 100.0) == 0.0
        assert parallel_two(100.0, 0.0) == 0.0


# ============================================================================
# Voltage / current dividers
# ============================================================================


class TestVoltageDivider:
    def test_basic(self):
        assert voltage_divider(10.0, 10.0, 10.0) == 5.0

    def test_r2_zero(self):
        assert voltage_divider(10.0, 10.0, 0.0) == 0.0

    def test_with_load(self):
        v = voltage_divider(10.0, 10.0, 10.0, r_load=10.0)
        assert v == pytest.approx(10.0 * 5.0 / 15.0)

    def test_no_load(self):
        assert voltage_divider(10.0, 9.0, 1.0) == 1.0


class TestCurrentDivider:
    def test_split(self):
        assert current_divider(10.0, 10.0, 10.0) == 5.0

    def test_low_resistance_path(self):
        assert current_divider(10.0, 1.0, 9.0) == 9.0


# ============================================================================
# Time constants
# ============================================================================


class TestRcTimeConstant:
    def test_standard(self):
        assert rc_time_constant(1000.0, 1e-6) == 1e-3

    def test_zero(self):
        assert rc_time_constant(0.0, 1e-6) == 0.0


class TestRlTimeConstant:
    def test_standard(self):
        assert rl_time_constant(100.0, 0.1) == 0.001

    def test_zero_resistance_raises(self):
        with pytest.raises(ValueError, match="nonzero"):
            rl_time_constant(0.0, 0.1)


class TestRcVoltageCharge:
    def test_one_tau(self):
        v = rc_voltage_charge(10.0, t=1.0, tau=1.0)
        assert v == pytest.approx(10.0 * (1 - 1 / math.e), rel=1e-9)

    def test_five_tau(self):
        v = rc_voltage_charge(10.0, t=5.0, tau=1.0)
        assert v == pytest.approx(10.0 * (1 - math.exp(-5)), rel=1e-9)

    def test_zero_time(self):
        assert rc_voltage_charge(10.0, t=0.0, tau=1.0) == 0.0


class TestRcVoltageDischarge:
    def test_one_tau(self):
        v = rc_voltage_discharge(10.0, t=1.0, tau=1.0)
        assert v == pytest.approx(10.0 / math.e, rel=1e-9)

    def test_five_tau(self):
        v = rc_voltage_discharge(10.0, t=5.0, tau=1.0)
        assert v == pytest.approx(10.0 * math.exp(-5), rel=1e-9)


class TestSettlingTimeTo:
    def test_63_percent(self):
        t = settling_time_to(63.0, tau=1.0)
        assert t == pytest.approx(-math.log(0.37), rel=1e-6)

    def test_99_percent(self):
        t = settling_time_to(99.0, tau=1.0)
        assert t == pytest.approx(-math.log(0.01), rel=1e-6)

    def test_zero_percent_raises(self):
        with pytest.raises(ValueError):
            settling_time_to(0.0, tau=1.0)

    def test_100_percent_raises(self):
        with pytest.raises(ValueError):
            settling_time_to(100.0, tau=1.0)

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            settling_time_to(-5.0, tau=1.0)


# ============================================================================
# Filter cutoff frequencies
# ============================================================================


class TestRcCutoffFrequency:
    def test_standard(self):
        f = rc_cutoff_frequency(1000.0, 1e-6)
        assert f == pytest.approx(1.0 / (2 * math.pi * 1e-3))

    def test_zero_resistance_raises(self):
        with pytest.raises(ValueError, match="nonzero"):
            rc_cutoff_frequency(0.0, 1e-6)

    def test_zero_capacitance_raises(self):
        with pytest.raises(ValueError, match="nonzero"):
            rc_cutoff_frequency(1000.0, 0.0)


class TestRlCutoffFrequency:
    def test_standard(self):
        f = rl_cutoff_frequency(1000.0, 0.1)
        assert f == pytest.approx(1000.0 / (2 * math.pi * 0.1))

    def test_zero_inductance_raises(self):
        with pytest.raises(ValueError, match="nonzero"):
            rl_cutoff_frequency(100.0, 0.0)


# ============================================================================
# Impedance
# ============================================================================


class TestImpedance:
    def test_pure_resistance(self):
        z = Impedance(r=50.0, x=0.0)
        assert z.magnitude == 50.0
        assert z.phase_rad == 0.0
        assert z.phase_deg == 0.0

    def test_pure_inductive(self):
        z = Impedance(r=0.0, x=10.0)
        assert z.magnitude == 10.0
        assert z.phase_rad == pytest.approx(math.pi / 2)
        assert z.phase_deg == pytest.approx(90.0)

    def test_pure_capacitive(self):
        z = Impedance(r=0.0, x=-10.0)
        assert z.magnitude == 10.0
        assert z.phase_rad == pytest.approx(-math.pi / 2)
        assert z.phase_deg == pytest.approx(-90.0)

    def test_complex(self):
        z = Impedance(r=1.0, x=1.0)
        assert z.magnitude == pytest.approx(math.sqrt(2))
        assert z.phase_rad == pytest.approx(math.pi / 4)
        assert z.phase_deg == pytest.approx(45.0)

    def test_zero_r_zero_x(self):
        z = Impedance(r=0.0, x=0.0)
        assert z.magnitude == 0.0

    def test_frozen(self):
        z = Impedance(r=1.0, x=0.0)
        with pytest.raises(AttributeError):
            z.r = 2.0  # type: ignore[misc]


class TestCapacitorImpedance:
    def test_1kHz(self):
        z = capacitor_impedance(1e-6, 1000.0)
        assert z.r == 0.0
        assert z.x < 0.0
        assert z.magnitude == pytest.approx(1.0 / (2 * math.pi * 1000 * 1e-6))

    def test_zero_capacitance_raises(self):
        with pytest.raises(ValueError, match="nonzero"):
            capacitor_impedance(0.0, 1000.0)

    def test_dc_approach(self):
        with pytest.raises(ValueError, match="nonzero"):
            capacitor_impedance(1e-6, 0.0)


class TestInductorImpedance:
    def test_1kHz(self):
        z = inductor_impedance(0.1, 1000.0)
        assert z.r == 0.0
        assert z.x > 0.0
        assert z.magnitude == pytest.approx(2 * math.pi * 1000 * 0.1)

    def test_dc(self):
        z = inductor_impedance(0.1, 0.0)
        assert z.magnitude == 0.0


class TestSeriesImpedance:
    def test_two(self):
        z1 = Impedance(r=3.0, x=0.0)
        z2 = Impedance(r=0.0, x=4.0)
        z = series_impedance(z1, z2)
        assert z.r == 3.0
        assert z.x == 4.0
        assert z.magnitude == 5.0


class TestParallelImpedance:
    def test_two_equal_resistors(self):
        z = parallel_impedance(Impedance(r=10.0, x=0.0), Impedance(r=10.0, x=0.0))
        assert z.r == pytest.approx(5.0)
        assert z.x == pytest.approx(0.0)

    def test_empty(self):
        z = parallel_impedance()
        assert z.r == float("inf")


# ============================================================================
# Energy / charge
# ============================================================================


class TestCapacitorEnergy:
    def test_standard(self):
        assert capacitor_energy(1e-6, 10.0) == pytest.approx(5e-5)

    def test_zero_voltage(self):
        assert capacitor_energy(1e-6, 0.0) == 0.0


class TestCapacitorCharge:
    def test_standard(self):
        assert capacitor_charge(1e-6, 5.0) == pytest.approx(5e-6)

    def test_zero(self):
        assert capacitor_charge(1e-6, 0.0) == 0.0


class TestInductorEnergy:
    def test_standard(self):
        assert inductor_energy(0.1, 2.0) == 0.2

    def test_zero_current(self):
        assert inductor_energy(0.1, 0.0) == 0.0


# ============================================================================
# LED resistor
# ============================================================================


class TestLedResistor:
    def test_standard_5v_red(self):
        r = led_resistor(5.0, 2.0, 0.02)
        assert r == 150.0

    def test_3v3_blue(self):
        r = led_resistor(3.3, 3.0, 0.01)
        assert r == pytest.approx(30.0)

    def test_supply_below_vf_raises(self):
        with pytest.raises(ValueError, match="Supply voltage"):
            led_resistor(1.8, 2.0, 0.02)

    def test_zero_current_raises(self):
        with pytest.raises(ValueError, match="current must be positive"):
            led_resistor(5.0, 2.0, 0.0)


# ============================================================================
# Op-amp topologies
# ============================================================================


class TestOpampInvertingGain:
    def test_gain_two(self):
        assert opamp_inverting_gain(20e3, 10e3) == -2.0

    def test_gain_one(self):
        assert opamp_inverting_gain(10e3, 10e3) == -1.0

    def test_zero_r_input_raises(self):
        with pytest.raises(ValueError, match="nonzero"):
            opamp_inverting_gain(10e3, 0.0)


class TestOpampNonInvertingGain:
    def test_buffer(self):
        assert opamp_non_inverting_gain(0.0, 10e3) == 1.0

    def test_gain_11(self):
        assert opamp_non_inverting_gain(100e3, 10e3) == 11.0

    def test_zero_r1_raises(self):
        with pytest.raises(ValueError, match="nonzero"):
            opamp_non_inverting_gain(10e3, 0.0)


class TestOpampDifferentialGain:
    def test_basic(self):
        out = opamp_differential_gain(2.0, 3.0, 10e3, 10e3)
        assert out == 1.0

    def test_zero_diff(self):
        out = opamp_differential_gain(3.0, 3.0, 10e3, 10e3)
        assert out == 0.0


# ============================================================================
# E-series
# ============================================================================


class TestESeriesValues:
    def test_e12_length(self):
        assert len(e_series_values("E12")) == 12

    def test_e24_length(self):
        assert len(e_series_values("E24")) == 24

    def test_e12_values(self):
        vals = e_series_values("E12")
        assert vals[0] == 10.0
        assert vals[-1] == 82.0

    def test_decade(self):
        vals = e_series_values("E12", decade=1)
        assert vals[0] == 100.0
        assert vals[-1] == 820.0

    def test_negative_decade(self):
        vals = e_series_values("E12", decade=-1)
        assert vals[0] == 1.0
        assert vals[-1] == pytest.approx(8.2)

    def test_unknown_series_raises(self):
        with pytest.raises(ValueError, match="Unknown E-series"):
            e_series_values("E99")


class TestNearestEValue:
    def test_exact_e12(self):
        assert nearest_e_value(10.0, "E12") == 10.0

    def test_between_e12(self):
        assert nearest_e_value(14.0, "E12") == 15.0

    def test_closest_is_12(self):
        assert nearest_e_value(13.0, "E12") == 12.0

    def test_exact_e48(self):
        assert nearest_e_value(511.0, "E48") == 511.0


# ============================================================================
# Decibels
# ============================================================================


class TestDbRatio:
    def test_0db(self):
        assert db_to_ratio(0.0) == 1.0

    def test_20db(self):
        assert db_to_ratio(20.0) == pytest.approx(10.0)

    def test_minus_20db(self):
        assert db_to_ratio(-20.0) == pytest.approx(0.1)


class TestRatioToDb:
    def test_unity(self):
        assert ratio_to_db(1.0) == 0.0

    def test_ten(self):
        assert ratio_to_db(10.0) == pytest.approx(20.0)

    def test_nonpositive_raises(self):
        with pytest.raises(ValueError, match="positive"):
            ratio_to_db(0.0)
        with pytest.raises(ValueError, match="positive"):
            ratio_to_db(-1.0)


class TestPowerDb:
    def test_3db(self):
        assert power_db_to_ratio(3.0) == pytest.approx(2.0, rel=0.01)

    def test_10db(self):
        assert power_db_to_ratio(10.0) == pytest.approx(10.0)

    def test_ratio_to_dbm(self):
        assert power_ratio_to_db(2.0) == pytest.approx(3.0, rel=0.05)


# ============================================================================
# Thermal noise
# ============================================================================


class TestThermalNoiseRms:
    def test_defaults_50ohm(self):
        vn = thermal_noise_rms(50.0)
        expected = math.sqrt(4 * BOLTZMANN * 300 * 50 * 1)
        assert vn == pytest.approx(expected, rel=1e-9)

    def test_cold(self):
        vn_cold = thermal_noise_rms(1000.0, temperature_k=10.0)
        vn_hot = thermal_noise_rms(1000.0, temperature_k=300.0)
        assert vn_cold < vn_hot

    def test_wideband(self):
        vn = thermal_noise_rms(1000.0, bandwidth=1e6)
        expected = math.sqrt(4 * BOLTZMANN * 300 * 1000 * 1e6)
        assert vn == pytest.approx(expected, rel=1e-9)


# ============================================================================
# RLC resonance
# ============================================================================


class TestRlcResonantFrequency:
    def test_1mH_1uF(self):
        f = rlc_series_resonant_frequency(1e-3, 1e-6)
        expected = 1.0 / (2 * math.pi * math.sqrt(1e-3 * 1e-6))
        assert f == pytest.approx(expected)

    def test_zero_inductance_raises(self):
        with pytest.raises(ValueError, match="nonzero"):
            rlc_series_resonant_frequency(0.0, 1e-6)

    def test_zero_capacitance_raises(self):
        with pytest.raises(ValueError, match="nonzero"):
            rlc_series_resonant_frequency(1e-3, 0.0)


class TestRlcQualityFactor:
    def test_series(self):
        q = rlc_quality_factor_series(10.0, 1e-3, 1e-6)
        expected = (1.0 / 10.0) * math.sqrt(1e-3 / 1e-6)
        assert q == pytest.approx(expected)

    def test_zero_resistance_raises(self):
        with pytest.raises(ValueError, match="nonzero"):
            rlc_quality_factor_series(0.0, 1e-3, 1e-6)

    def test_zero_capacitance_raises(self):
        with pytest.raises(ValueError, match="nonzero"):
            rlc_quality_factor_series(10.0, 1e-3, 0.0)


# ============================================================================
# BJT / MOSFET
# ============================================================================


class TestBjtBaseResistor:
    def test_saturation_switch(self):
        r = bjt_base_resistor(3.3, 0.7, 0.1, 100.0)
        ib = 0.1 / 100.0 * 10.0
        expected = (3.3 - 0.7) / ib
        assert r == pytest.approx(expected)

    def test_high_gain(self):
        r = bjt_base_resistor(5.0, 0.7, 0.02, 200.0)
        ib = 0.02 / 200.0 * 10.0
        expected = (5.0 - 0.7) / ib
        assert r == pytest.approx(expected)


class TestMosfetGatePower:
    def test_standard(self):
        p = mosfet_gate_power(1e-8, 10.0, 100e3)
        assert p == 0.01

    def test_no_switching(self):
        assert mosfet_gate_power(1e-8, 5.0, 0.0) == 0.0


class TestBjtPowerDissipation:
    def test_saturated(self):
        p = bjt_power_dissipation(0.2, 1.0)
        assert p == 0.2

    def test_linear(self):
        p = bjt_power_dissipation(6.0, 0.5)
        assert p == 3.0


# ============================================================================
# I2C pull-up
# ============================================================================


class TestI2cPullup:
    def test_max_400kHz(self):
        r = i2c_pullup_max(100e-12, 300e-9)
        expected = 300e-9 / (0.8473 * 100e-12)
        assert r == pytest.approx(expected)

    def test_min_3v3(self):
        r = i2c_pullup_min(3.3, 3e-3)
        assert r == pytest.approx(1100.0)

    def test_max_zero_cap_raises(self):
        with pytest.raises(ValueError, match="nonzero"):
            i2c_pullup_max(0.0, 300e-9)

    def test_min_zero_current_raises(self):
        with pytest.raises(ValueError, match="nonzero"):
            i2c_pullup_min(3.3, 0.0)


# ============================================================================
# ADC
# ============================================================================


class TestAdcSnr:
    def test_8_bit(self):
        assert adc_snr(8) == pytest.approx(49.92)

    def test_12_bit(self):
        assert adc_snr(12) == pytest.approx(74.0)

    def test_16_bit(self):
        assert adc_snr(16) == pytest.approx(98.08)

    def test_24_bit(self):
        assert adc_snr(24) == pytest.approx(146.24)


class TestAdcResolution:
    def test_10_bit_5v(self):
        res = adc_resolution(5.0, 10)
        assert res == pytest.approx(5.0 / 1023)

    def test_12_bit_3v3(self):
        res = adc_resolution(3.3, 12)
        assert res == pytest.approx(3.3 / 4095)

    def test_1_bit(self):
        assert adc_resolution(1.0, 1) == 1.0


# ============================================================================
# Edge cases cross-cutting
# ============================================================================


class TestImpedanceEdgeCases:
    def test_negative_x(self):
        z = Impedance(r=5.0, x=-12.0)
        assert z.magnitude == 13.0
        assert z.phase_rad < 0.0

    def test_frozen_immutable(self):
        z = Impedance(r=1.0, x=0.0)
        with pytest.raises(AttributeError):
            z.x = 5.0  # type: ignore[misc]


class TestParallelZeroAll:
    def test_all_zero(self):
        assert parallel_resistance(0.0, 0.0, 0.0) == 0.0
