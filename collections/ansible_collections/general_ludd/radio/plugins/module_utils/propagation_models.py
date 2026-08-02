"""
propagation_models -- RF path-loss model implementations.

Supported models:
    - Free-space path loss
    - ITM (Longley-Rice) -- core algorithm for irregular terrain
    - Hata-Okumura (urban / suburban / rural)
    - Two-ray ground reflection
    - Rain attenuation (ITU-R P.838)
    - Transhorizon interference (ITU-R P.452)
    - Gaseous absorption (ITU-R P.676)
    - Cloud / fog attenuation (ITU-R P.840)

Functions:
    free_space_loss(distance_m, freq_hz) -> loss_db
    hata_urban(distance_km, freq_mhz, h_tx_m, h_rx_m) -> loss_db
    hata_suburban(distance_km, freq_mhz, h_tx_m, h_rx_m) -> loss_db
    hata_rural(distance_km, freq_mhz, h_tx_m, h_rx_m) -> loss_db
    two_ray_loss(distance_m, h_tx_m, h_rx_m) -> loss_db
    itm_loss(distance_km, freq_mhz, h_tx_m, h_rx_m, ...) -> loss_db
    rain_attenuation(freq_ghz, rain_rate_mmh, distance_km, polarization) -> loss_db
    itu_p452_loss(distance_km, freq_ghz, h_tx_m, h_rx_m, time_percent, ...) -> dict
    gaseous_attenuation(freq_ghz, distance_km, temperature_c, pressure_hpa, water_density_gm3) -> dict
    cloud_attenuation(freq_ghz, distance_km, liquid_water_content_gm3, temperature_c) -> dict
    predict_path_loss(model, distance_km, frequency_mhz, ...) -> loss_db
"""

from __future__ import annotations

import math
from typing import Any, Literal


def free_space_loss(distance_m: float, freq_hz: float) -> float:
    """
    Free-space path loss in dB.

    FSPL = 20*log10(d) + 20*log10(f) - 147.55
    Where d in meters, f in Hz. Constant from 20*log10(4*pi/c).
    """
    return 20.0 * math.log10(distance_m) + 20.0 * math.log10(freq_hz) - 147.55


def hata_urban(distance_km: float, freq_mhz: float, h_tx_m: float, h_rx_m: float) -> float:
    """
    Hata-Okumura urban area path loss in dB.

    Valid: 150 <= freq <= 1500 MHz, 1 <= d <= 20 km,
           30 <= ht <= 200 m, 1 <= hr <= 10 m.

    L = 69.55 + 26.16*log10(f) - 13.82*log10(hb) - a(hm) + (44.9 - 6.55*log10(hb))*log10(d)
    """
    a_hr = _hata_a_hr(freq_mhz, h_rx_m, "urban_large")
    return (
        69.55
        + 26.16 * math.log10(freq_mhz)
        - 13.82 * math.log10(h_tx_m)
        - a_hr
        + (44.9 - 6.55 * math.log10(h_tx_m)) * math.log10(distance_km)
    )


def hata_suburban(distance_km: float, freq_mhz: float, h_tx_m: float, h_rx_m: float) -> float:
    """Hata-Okumura suburban area path loss in dB."""
    l_urban = hata_urban(distance_km, freq_mhz, h_tx_m, h_rx_m)
    correction = (2.0 * math.log10(freq_mhz / 28.0) ** 2) + 5.4
    return l_urban - correction


def hata_rural(distance_km: float, freq_mhz: float, h_tx_m: float, h_rx_m: float) -> float:
    """Hata-Okumura rural (open) area path loss in dB."""
    l_urban = hata_urban(distance_km, freq_mhz, h_tx_m, h_rx_m)
    correction = (
        4.78 * math.log10(freq_mhz) ** 2
        - 18.33 * math.log10(freq_mhz)
        + 40.94
    )
    return l_urban - correction


def _hata_a_hr(freq_mhz: float, h_rx_m: float, category: str) -> float:
    """Mobile antenna height correction factor for Hata model."""
    if category == "urban_small":
        return (1.1 * math.log10(freq_mhz) - 0.7) * h_rx_m - (1.56 * math.log10(freq_mhz) - 0.8)
    else:
        if freq_mhz <= 200:
            return 8.29 * (math.log10(1.54 * h_rx_m)) ** 2 - 1.1
        elif freq_mhz <= 1500:
            return 3.2 * (math.log10(11.75 * h_rx_m)) ** 2 - 4.97
        else:
            return 3.2 * (math.log10(11.75 * h_rx_m)) ** 2 - 4.97


def two_ray_loss(distance_m: float, h_tx_m: float, h_rx_m: float) -> float:
    """
    Two-ray ground-reflection path loss in dB.

    L = 40*log10(d) - 20*log10(ht) - 20*log10(hr)

    Assumes d >> ht, hr (far field) and flat earth.
    Path loss exponent = 4 (40*log10(d)).
    """
    return 40.0 * math.log10(distance_m) - 20.0 * math.log10(h_tx_m) - 20.0 * math.log10(h_rx_m)


def itm_loss(
    distance_km: float,
    freq_mhz: float,
    h_tx_m: float,
    h_rx_m: float,
    terrain_irregularity_m: float = 30.0,
    climate: int = 5,
    refractivity: float = 301.0,
    permittivity: float = 15.0,
    conductivity: float = 0.005,
    polarization: Literal["horizontal", "vertical"] = "horizontal",
    reliability: float = 0.50,
    confidence: float = 0.50,
) -> dict[str, Any]:
    """
    ITM (Longley-Rice) Irregular Terrain Model -- core algorithm.

    This implements the area-prediction mode for point-to-point estimates
    using the NTIA/ITS implementation algorithm.

    Parameters:
        distance_km: Path distance (1 to 2000 km)
        freq_mhz: Frequency (20 MHz to 20 GHz)
        h_tx_m: Transmitter antenna height above ground (0.5 to 3000 m)
        h_rx_m: Receiver antenna height above ground (0.5 to 3000 m)
        terrain_irregularity_m: Delta-h (interdecile range of terrain heights)
        climate: Radio climate code (1=equatorial, 5=continental temperate)
        refractivity: Surface refractivity (N-units, typically 250-400)
        permittivity: Relative permittivity of ground (typ 15 for average ground)
        conductivity: Ground conductivity in S/m (typ 0.005 for average ground)
        polarization: Signal polarization
        reliability: Time variability (0.01-0.99; 0.50 = median)
        confidence: Location variability (0.01-0.99; 0.50 = median)

    Returns dict with:
        loss_db: Total predicted path loss in dB
        free_space_loss_db: Reference free-space loss
        modes: dict of mode contributions (line_of_sight, diffraction, scatter)
    """

    fsl = _itm_free_space_loss(distance_km, freq_mhz)
    c = 299.792458
    wavelength_m = c / (freq_mhz * 1e6)

    h_e_tx = _effective_antenna_height(h_tx_m, terrain_irregularity_m)
    h_e_rx = _effective_antenna_height(h_rx_m, terrain_irregularity_m)

    d_horizon_tx = _radio_horizon(h_e_tx)
    d_horizon_rx = _radio_horizon(h_e_rx)
    d_los = d_horizon_tx + d_horizon_rx

    curvature_radius_km = 8493.0  # 4/3 Earth radius in km

    if distance_km <= d_los:
        diffraction_loss = _itm_los_diffraction(
            distance_km, h_e_tx, h_e_rx, freq_mhz, terrain_irregularity_m
        )
        tx_scatter_loss = 0.0
    elif distance_km <= d_los + 200.0:
        frac = (distance_km - d_los) / 200.0
        los_diff = _itm_los_diffraction(d_los, h_e_tx, h_e_rx, freq_mhz, terrain_irregularity_m)
        diff_loss = _itm_diffraction_total(
            distance_km, freq_mhz, h_e_tx, h_e_rx, terrain_irregularity_m,
            permittivity, conductivity, wavelength_m
        )
        diffraction_loss = los_diff + frac * (diff_loss - los_diff)
        tx_scatter_loss = 0.0
    else:
        diffraction_loss = _itm_diffraction_total(
            distance_km, freq_mhz, h_e_tx, h_e_rx, terrain_irregularity_m,
            permittivity, conductivity, wavelength_m
        )
        tx_scatter_theta = _scatter_angle(distance_km, h_e_tx, h_e_rx, terrain_irregularity_m)
        tx_scatter_loss = _troposcatter_loss(distance_km, freq_mhz, tx_scatter_theta, climate, refractivity)

    total_loss = fsl + max(diffraction_loss, tx_scatter_loss)

    return {
        "model": "ITM (Longley-Rice)",
        "distance_km": distance_km,
        "frequency_mhz": freq_mhz,
        "loss_db": round(total_loss, 2),
        "free_space_loss_db": round(fsl, 2),
        "modes": {
            "line_of_sight": {
                "active": distance_km <= d_los,
                "horizon_distance_km": round(d_los, 2),
                "diffraction_loss_db": round(diffraction_loss, 2),
            },
            "diffraction": {
                "diffraction_loss_db": round(diffraction_loss, 2),
            },
            "tropospheric_scatter": {
                "loss_db": round(tx_scatter_loss, 2),
            },
        },
        "terrain": {
            "irregularity_m": terrain_irregularity_m,
            "h_eff_tx_m": round(h_e_tx, 2),
            "h_eff_rx_m": round(h_e_rx, 2),
            "radio_horizon_tx_km": round(d_horizon_tx, 2),
            "radio_horizon_rx_km": round(d_horizon_rx, 2),
        },
    }


def _itm_free_space_loss(d_km: float, f_mhz: float) -> float:
    return 32.45 + 20.0 * math.log10(d_km) + 20.0 * math.log10(f_mhz)


def _effective_antenna_height(h_m: float, delta_h: float) -> float:
    """Effective antenna height accounting for terrain irregularity."""
    if h_m < 5.0:
        return max(1.0, h_m)
    return h_m


def _radio_horizon(h_m: float) -> float:
    """Radio horizon distance in km (4/3 Earth model)."""
    return math.sqrt(2.0 * h_m / 0.001) * math.sqrt(4.0 / 3.0)


def _itm_los_diffraction(
    d_km: float, h_tx: float, h_rx: float, f_mhz: float, delta_h: float
) -> float:
    """Line-of-sight diffraction loss (knife-edge approximation)."""
    c = 299792.458
    wl = c / (f_mhz * 1e6)

    d1 = max(0.1, d_km * 0.25)
    d2 = max(0.1, d_km * 0.75)

    h_clearance = _path_clearance(d_km, h_tx, h_rx, delta_h)
    v = h_clearance * math.sqrt(2.0 * (d1 + d2) / (wl * d1 * d2 * 1000.0))

    if v <= -0.7:
        return 0.0
    elif v < 0:
        return 6.0 * (v + 0.7) ** 2
    else:
        return 6.9 + 20.0 * math.log10(math.sqrt((v - 0.1) ** 2 + 1.0) + v - 0.1)


def _itm_diffraction_total(
    d_km: float, f_mhz: float, h_tx: float, h_rx: float, delta_h: float,
    eps_r: float, sigma: float, wl: float
) -> float:
    """Total diffraction loss (knife-edge + rounded-Earth contribution)."""
    v = _diffraction_parameter(d_km, h_tx, h_rx, delta_h, wl)
    if v <= -0.7:
        return 0.0
    elif v < 0:
        loss = 6.0 * (v + 0.7) ** 2
    else:
        loss = 6.9 + 20.0 * math.log10(math.sqrt((v - 0.1) ** 2 + 1) + v - 0.1)

    d_horizon_sum = _radio_horizon(h_tx) + _radio_horizon(h_rx)
    if d_km > d_horizon_sum:
        excess = d_km - d_horizon_sum
        curvature_loss = 0.05 * excess * (f_mhz / 100.0) ** (1.0 / 3.0)
        loss += curvature_loss

    return loss


def _diffraction_parameter(d_km: float, h_tx: float, h_rx: float, delta_h: float, wl: float) -> float:
    """Fresnel-Kirchhoff diffraction parameter v."""
    d1 = d_km * 0.3
    d2 = d_km * 0.7
    h = _path_clearance(d_km, h_tx, h_rx, delta_h)
    denom = math.sqrt(wl * d1 * d2 * 1000.0 / (2.0 * (d1 + d2)))
    return h / denom if denom > 0 else 0.0


def _path_clearance(d_km: float, h_tx: float, h_rx: float, delta_h: float) -> float:
    """Effective path clearance above knife-edge obstruction."""
    r_earth_eff = 8493.0
    h_obstruction = delta_h / 2.0
    earth_bulge = d_km ** 2 / (2.0 * r_earth_eff)
    ray_height = h_tx + (h_rx - h_tx) * 0.5
    return ray_height - earth_bulge - h_obstruction


def _scatter_angle(d_km: float, h_tx: float, h_rx: float, delta_h: float) -> float:
    """Tropospheric scatter angle in radians."""
    r_earth_eff = 8493.0
    theta_tx = h_tx / (d_km * 1000.0) if d_km > 0 else 0
    theta_rx = h_rx / (d_km * 1000.0) if d_km > 0 else 0
    theta_geo = d_km / r_earth_eff
    return theta_tx + theta_rx + theta_geo


def _troposcatter_loss(d_km: float, f_mhz: float, theta_rad: float, climate: int, refractivity: float) -> float:
    """Tropospheric scatter loss estimate."""
    theta_mrad = theta_rad * 1000.0
    f_ghz = f_mhz / 1000.0
    l0 = 30.0 * math.log10(f_ghz) + 30.0 * math.log10(d_km) + 10.0 * math.log10(theta_mrad + 0.001) - 100.0

    n0 = refractivity - 301.0
    clim_correction = (climate - 5.0) * 0.5 if 1 <= climate <= 7 else 0.0

    return max(0.0, l0 - 0.2 * n0 - clim_correction)


def rain_attenuation(
    freq_ghz: float,
    rain_rate_mmh: float,
    distance_km: float,
    polarization: Literal["horizontal", "vertical"] = "horizontal",
) -> dict[str, Any]:
    """
    Rain attenuation per ITU-R P.838-3.

    A = k * R^alpha * d * r

    Parameters:
        freq_ghz: Frequency in GHz (1-1000 GHz)
        rain_rate_mmh: Rain rate in mm/h (0.25 for drizzle, 5 for moderate, 50 for heavy)
        distance_km: Path length through rain in km
        polarization: horizontal or vertical

    Returns dict with specific attenuation and total path loss.
    """
    k, alpha = _rain_coefficients(freq_ghz, polarization)

    specific_attenuation_db_km = k * (rain_rate_mmh ** alpha)

    if distance_km <= 0:
        total_attenuation_db = 0.0
    else:
        r_factor = 1.0 / (0.477 * distance_km ** 0.633 * rain_rate_mmh ** (0.073 * alpha) * freq_ghz ** 0.123 + 1.0)
        effective_distance = distance_km * r_factor
        total_attenuation_db = specific_attenuation_db_km * effective_distance

    return {
        "model": "ITU-R P.838-3 Rain Attenuation",
        "frequency_ghz": freq_ghz,
        "rain_rate_mmh": rain_rate_mmh,
        "distance_km": distance_km,
        "polarization": polarization,
        "specific_attenuation_db_km": round(specific_attenuation_db_km, 4),
        "total_attenuation_db": round(total_attenuation_db, 2),
        "coefficients": {"k": k, "alpha": alpha},
    }


def _rain_coefficients(freq_ghz: float, polarization: str) -> tuple[float, float]:
    """
    Return k and alpha coefficients for ITU-R P.838 rain attenuation.
    Values from ITU-R P.838-3 Table 1 for selected frequencies.
    """
    coeff_table = {
        1.0: {"horizontal": (0.0000387, 0.912), "vertical": (0.0000352, 0.880)},
        2.0: {"horizontal": (0.000154, 0.963), "vertical": (0.000138, 0.923)},
        4.0: {"horizontal": (0.000650, 1.121), "vertical": (0.000591, 1.075)},
        6.0: {"horizontal": (0.00175, 1.308), "vertical": (0.00155, 1.265)},
        7.0: {"horizontal": (0.00301, 1.332), "vertical": (0.00265, 1.312)},
        8.0: {"horizontal": (0.00454, 1.327), "vertical": (0.00395, 1.310)},
        10.0: {"horizontal": (0.0101, 1.276), "vertical": (0.00887, 1.264)},
        12.0: {"horizontal": (0.0188, 1.217), "vertical": (0.0168, 1.200)},
        15.0: {"horizontal": (0.0367, 1.154), "vertical": (0.0335, 1.128)},
        20.0: {"horizontal": (0.0751, 1.099), "vertical": (0.0691, 1.065)},
        25.0: {"horizontal": (0.124, 1.061), "vertical": (0.113, 1.030)},
        30.0: {"horizontal": (0.187, 1.021), "vertical": (0.167, 0.989)},
        35.0: {"horizontal": (0.263, 0.979), "vertical": (0.233, 0.963)},
        40.0: {"horizontal": (0.350, 0.939), "vertical": (0.310, 0.929)},
        45.0: {"horizontal": (0.442, 0.903), "vertical": (0.393, 0.897)},
        50.0: {"horizontal": (0.536, 0.873), "vertical": (0.479, 0.868)},
        60.0: {"horizontal": (0.707, 0.826), "vertical": (0.642, 0.824)},
        70.0: {"horizontal": (0.851, 0.793), "vertical": (0.784, 0.793)},
        80.0: {"horizontal": (0.975, 0.769), "vertical": (0.906, 0.769)},
        90.0: {"horizontal": (1.06, 0.753), "vertical": (0.999, 0.754)},
        100.0: {"horizontal": (1.12, 0.743), "vertical": (1.06, 0.744)},
        120.0: {"horizontal": (1.18, 0.731), "vertical": (1.13, 0.732)},
    }

    exact = coeff_table.get(freq_ghz)
    if exact is not None:
        return exact[polarization]

    freqs = sorted(coeff_table.keys())
    lower = None
    upper = None
    for f in freqs:
        if f <= freq_ghz:
            lower = f
        if f >= freq_ghz and upper is None:
            upper = f

    if lower is None:
        return coeff_table[freqs[0]][polarization]
    if upper is None:
        return coeff_table[freqs[-1]][polarization]
    if lower == upper:
        return coeff_table[lower][polarization]

    k_l, a_l = coeff_table[lower][polarization]
    k_u, a_u = coeff_table[upper][polarization]
    l_l = math.log10(lower) if lower > 0 else 0
    l_u = math.log10(upper) if upper > 0 else 0
    l_f = math.log10(freq_ghz) if freq_ghz > 0 else 0

    log_k = math.log10(k_l if k_l > 0 else 1e-10)
    log_k_u = math.log10(k_u if k_u > 0 else 1e-10)

    frac = (math.log10(freq_ghz) - math.log10(lower)) / (math.log10(upper) - math.log10(lower))
    log_k_interp = log_k + frac * (math.log10(k_u) - log_k)
    alpha_interp = a_l + frac * (a_u - a_l)

    return (10 ** log_k_interp, alpha_interp)


def itu_p452_loss(
    distance_km: float,
    freq_ghz: float,
    h_tx_m: float,
    h_rx_m: float,
    time_percent: float = 50.0,
    terrain_irregularity_m: float = 30.0,
    pressure_hpa: float = 1013.0,
    temperature_c: float = 15.0,
    sea_level_refractivity: float = 318.0,
) -> dict[str, Any]:
    """
    ITU-R P.452 transhorizon interference prediction.

    Estimates basic transmission loss not exceeded for the given time
    percentage by combining three propagation mechanisms:
      * diffraction (LOS + spherical-Earth beyond-horizon)
      * tropospheric scatter
      * ducting / layer-reflection (anomalous propagation)

    For interference analysis the worst case (lowest path loss) of the
    applicable mechanisms is taken as the predicted loss.

    Parameters:
        distance_km: Path distance (0.1 to ~2000 km)
        freq_ghz: Frequency in GHz (0.1 to ~50 GHz)
        h_tx_m: Transmitter antenna height above ground (m)
        h_rx_m: Receiver antenna height above ground (m)
        time_percent: Time percentage for which loss is NOT exceeded
            (1.0 to 50.0; 50 = median, smaller = rarer anomaly)
        terrain_irregularity_m: Delta-h terrain roughness (m)
        pressure_hpa: Atmospheric pressure (hPa)
        temperature_c: Air temperature (Celsius)
        sea_level_refractivity: Sea-level refractivity N0 (N-units, ~250-400)

    Returns dict with:
        loss_db: Predicted basic transmission loss (dB)
        free_space_loss_db: Free-space reference loss
        time_percent: Echoed time percentage
        components: {diffraction_loss_db, troposcatter_loss_db, ducting_loss_db}
    """
    fsl = _itm_free_space_loss(distance_km, freq_ghz * 1000.0)

    d_los = _p452_radio_horizon(h_tx_m) + _p452_radio_horizon(h_rx_m)

    diffraction_total = fsl + _p452_diffraction_excess(
        distance_km, d_los, freq_ghz, terrain_irregularity_m
    )
    troposcatter_total = _p452_troposcatter(
        distance_km, freq_ghz, h_tx_m, h_rx_m, sea_level_refractivity
    )
    ducting_total = fsl + _p452_ducting_excess(
        distance_km, d_los, freq_ghz, time_percent
    )

    loss_db = min(diffraction_total, troposcatter_total, ducting_total)

    return {
        "model": "ITU-R P.452 Transhorizon Interference",
        "distance_km": distance_km,
        "frequency_ghz": freq_ghz,
        "time_percent": time_percent,
        "loss_db": round(loss_db, 2),
        "free_space_loss_db": round(fsl, 2),
        "components": {
            "diffraction_loss_db": round(diffraction_total, 2),
            "troposcatter_loss_db": round(troposcatter_total, 2),
            "ducting_loss_db": round(ducting_total, 2),
        },
        "terrain": {
            "irregularity_m": terrain_irregularity_m,
            "radio_horizon_los_km": round(d_los, 2),
            "pressure_hpa": pressure_hpa,
            "temperature_c": temperature_c,
            "sea_level_refractivity": sea_level_refractivity,
        },
    }


def _p452_radio_horizon(h_m: float) -> float:
    """Standard radio-horizon distance (km) for a 4/3-Earth model."""
    return 4.12 * math.sqrt(max(h_m, 0.0))


def _p452_diffraction_excess(
    distance_km: float, d_los: float, freq_ghz: float, delta_h: float
) -> float:
    """Excess loss over free space from diffraction (knife-edge + spherical Earth)."""
    if distance_km <= d_los or d_los <= 0:
        v = _p452_knife_edge(distance_km, freq_ghz, delta_h)
        return _p452_j(v)
    beyond = distance_km - d_los
    return (
        10.0
        + 20.0 * math.log10(distance_km / d_los)
        + 0.3 * beyond * math.sqrt(max(freq_ghz, 0.1)) / 10.0
    )


def _p452_knife_edge(distance_km: float, freq_ghz: float, delta_h: float) -> float:
    """Fresnel-Kirchhoff v parameter for a single knife-edge obstruction."""
    wavelength_m = 0.3 / max(freq_ghz, 0.1)
    d1 = max(0.1, distance_km * 0.5)
    d2 = d1
    h = delta_h / 2.0
    denom = math.sqrt(2.0 * wavelength_m * d1 * d2 * 1000.0 / (d1 + d2))
    return h / denom if denom > 0 else 0.0


def _p452_j(v: float) -> float:
    """Knife-edge diffraction loss J(v) (dB)."""
    if v <= -0.78:
        return 0.0
    if v < 0:
        return 6.0 * (v + 0.7) ** 2
    return 6.9 + 20.0 * math.log10(math.sqrt((v - 0.1) ** 2 + 1.0) + v - 0.1)


def _p452_troposcatter(
    distance_km: float,
    freq_ghz: float,
    h_tx_m: float,
    h_rx_m: float,
    sea_level_refractivity: float,
) -> float:
    """Tropospheric-scatter basic transmission loss (ITU-R P.452 formulation)."""
    r_earth_km = 6371.0
    theta_rad = distance_km / r_earth_km + (h_tx_m + h_rx_m) / (distance_km * 1000.0)
    theta_mrad = theta_rad * 1000.0
    return (
        190.0
        + 20.0 * math.log10(max(freq_ghz, 0.1))
        + 20.0 * math.log10(max(distance_km, 0.1))
        + 0.573 * theta_mrad
        - 0.15 * sea_level_refractivity
    )


def _p452_ducting_excess(
    distance_km: float, d_los: float, freq_ghz: float, time_percent: float
) -> float:
    """
    Ducting / layer-reflection excess loss.

    Anomalous propagation is more prevalent at small time percentages,
    lowering the predicted transmission loss (stronger interference).
    """
    tp = min(max(time_percent, 0.001), 100.0)
    time_factor = 12.0 * (math.log10(50.0) - math.log10(tp))
    beyond = max(distance_km - d_los, 0.0)
    excess = 15.0 + 20.0 * math.log10(max(freq_ghz, 0.1)) - time_factor + 0.1 * beyond
    return max(0.0, excess)


def gaseous_attenuation(
    freq_ghz: float,
    distance_km: float,
    temperature_c: float = 15.0,
    pressure_hpa: float = 1013.0,
    water_density_gm3: float = 7.5,
) -> dict[str, Any]:
    """
    ITU-R P.676 gaseous attenuation (Annex 2 approximate method).

    Specific attenuation due to dry air (oxygen) and water vapour, plus the
    total path attenuation for a terrestrial (horizontal) link.

    Parameters:
        freq_ghz: Frequency in GHz (1 to ~350 GHz)
        distance_km: Path length in km
        temperature_c: Air temperature (Celsius)
        pressure_hpa: Total air pressure (hPa)
        water_density_gm3: Water-vapour density (g/m^3)

    Returns dict with:
        specific_attenuation_db_km: gamma_o + gamma_w (dB/km)
        oxygen_db_km: Oxygen (dry air) specific attenuation
        water_vapor_db_km: Water-vapour specific attenuation
        total_attenuation_db: total path attenuation
    """
    r_p = pressure_hpa / 1013.0
    t_kelvin = temperature_c + 273.15
    r_t = 288.0 / t_kelvin

    gamma_o = _p676_oxygen_specific(freq_ghz, r_p, r_t)
    gamma_w = _p676_water_vapor_specific(freq_ghz, r_p, r_t, water_density_gm3)
    specific = gamma_o + gamma_w
    total = specific * max(distance_km, 0.0) if distance_km > 0 else 0.0

    return {
        "model": "ITU-R P.676 Gaseous Attenuation",
        "frequency_ghz": freq_ghz,
        "distance_km": distance_km,
        "temperature_c": temperature_c,
        "pressure_hpa": pressure_hpa,
        "water_density_gm3": water_density_gm3,
        "oxygen_db_km": round(gamma_o, 6),
        "water_vapor_db_km": round(gamma_w, 6),
        "specific_attenuation_db_km": round(specific, 6),
        "total_attenuation_db": round(total, 4),
    }


def _p676_oxygen_specific(freq_ghz: float, r_p: float, r_t: float) -> float:
    """Specific attenuation due to oxygen / dry air (dB/km) per ITU-R P.676 Annex 2."""
    f = max(freq_ghz, 0.1)
    common = f * f * r_p * r_p * (r_t ** 3.5) * 1e-3
    if f <= 57.0:
        line57 = 7.5 / ((f - 57.0) ** 2 + 2.44 * r_p * r_p * (1.0 + 1.8 * r_t * r_t))
        base = 7.27 * r_t / (f * f + 0.351 * r_p * r_p * r_t * r_t)
        return common * (base + line57)
    if f < 63.0:
        peak = 15.0 / ((f - 60.0) ** 2 + 1.0 + 0.3 * r_p * r_p * (1.0 + 1.8 * r_t * r_t))
        base = 7.27 * r_t / (f * f + 0.351 * r_p * r_p * r_t * r_t)
        return common * (base + peak)
    return _p676_oxygen_high_freq(f, r_p, r_t)


def _p676_oxygen_high_freq(f: float, r_p: float, r_t: float) -> float:
    """Oxygen specific attenuation for f >= 63 GHz (continuation of Annex 2)."""
    common = f * f * r_p * r_p * (r_t ** 3.5) * 1e-3
    line118 = 0.41 * r_t / ((f - 118.75) ** 2 + 0.36 * r_p * r_p * r_t * r_t)
    base = 0.001 * r_t
    return common * (base + line118)


def _p676_water_vapor_specific(
    freq_ghz: float, r_p: float, r_t: float, water_density_gm3: float
) -> float:
    """Specific attenuation due to water vapour (dB/km) per ITU-R P.676 Annex 2."""
    f = max(freq_ghz, 0.1)
    common = f * f * (r_t ** 2.5) * r_p * water_density_gm3 * 1e-4
    base = 3.27 * r_t
    line22 = 1.67 / ((f - 22.2) ** 2 + 8.28 * r_p * r_p)
    line183 = 7.5 / ((f - 183.3) ** 2 + 9.16 * r_p * r_p * (1.0 + 0.53 * r_t * r_t))
    line325 = 4.6 / ((f - 325.4) ** 2 + 9.16 * r_p * r_p * (1.0 + 0.53 * r_t * r_t))
    return common * (base + line22 + line183 + line325)


def cloud_attenuation(
    freq_ghz: float,
    distance_km: float,
    liquid_water_content_gm3: float = 0.5,
    temperature_c: float = 10.0,
) -> dict[str, Any]:
    """
    ITU-R P.840 attenuation due to clouds and fog.

    Uses the Ray double-Debye dielectric model for water to compute the
    specific attenuation coefficient K_l (dB/km per g/m^3), then multiplies
    by the liquid-water content and path length.

    Parameters:
        freq_ghz: Frequency in GHz (up to ~200 GHz)
        distance_km: Path length through the cloud / fog (km)
        liquid_water_content_gm3: Liquid water content M (g/m^3)
        temperature_c: Cloud temperature (Celsius)

    Returns dict with:
        specific_attenuation_db_km: gamma_c = K_l * M (dB/km)
        total_attenuation_db: total path attenuation
        coefficient_db_km_per_gm3: K_l specific attenuation coefficient
    """
    k_l = _p840_specific_coefficient(freq_ghz, temperature_c)
    specific = k_l * max(liquid_water_content_gm3, 0.0)
    if distance_km > 0 and liquid_water_content_gm3 > 0:
        total = specific * distance_km
    else:
        total = 0.0

    return {
        "model": "ITU-R P.840 Cloud / Fog Attenuation",
        "frequency_ghz": freq_ghz,
        "distance_km": distance_km,
        "liquid_water_content_gm3": liquid_water_content_gm3,
        "temperature_c": temperature_c,
        "coefficient_db_km_per_gm3": round(k_l, 6),
        "specific_attenuation_db_km": round(specific, 6),
        "total_attenuation_db": round(total, 4),
    }


def _p840_specific_coefficient(freq_ghz: float, temperature_c: float) -> float:
    """
    Cloud specific attenuation coefficient K_l (dB/km per g/m^3).

    K_l = 0.819 * f / (eps'' * (1 + eta^2))
    where the complex dielectric constant of water follows the
    Ray (Debye) relaxation model.
    """
    f = max(freq_ghz, 0.1)
    theta = 300.0 / (temperature_c + 273.15) - 1.0
    eps_0 = 77.66 + 103.3 * theta
    eps_inf = 3.17
    f_p = 20.20 - 146.4 * theta + 316.0 * theta * theta
    f_p = max(f_p, 0.1)

    denom = 1.0 + (f / f_p) ** 2
    eps_prime = eps_inf + (eps_0 - eps_inf) / denom
    eps_double_prime = (f / f_p) * (eps_0 - eps_inf) / denom

    if eps_double_prime <= 0:
        return 0.0

    eta = (eps_prime + 2.0) / eps_double_prime
    return 0.819 * f / (eps_double_prime * (1.0 + eta * eta))


def predict_path_loss(
    model: str,
    distance_km: float,
    frequency_mhz: float = 146.0,
    tx_height_m: float = 30.0,
    rx_height_m: float = 1.5,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Dispatch to the appropriate path loss model and return results.

    Parameters:
        model: One of "free_space", "hata_urban", "hata_suburban", "hata_rural",
               "two_ray", "itm", "rain", "itu_p452", "gaseous", "cloud"
        distance_km: Path distance in km
        frequency_mhz: Frequency in MHz
        tx_height_m: Transmitter height in meters
        rx_height_m: Receiver height in meters
        **kwargs: Model-specific parameters

    Returns dict with model name, input parameters, and loss_db.
    """
    distance_m = distance_km * 1000.0
    freq_hz = frequency_mhz * 1_000_000

    if model == "free_space":
        loss = free_space_loss(distance_m, freq_hz)
        return {
            "model": "Free-Space Path Loss",
            "distance_km": distance_km,
            "frequency_mhz": frequency_mhz,
            "loss_db": round(loss, 2),
        }

    elif model == "hata_urban":
        loss = hata_urban(distance_km, frequency_mhz, tx_height_m, rx_height_m)
        return {
            "model": "Hata-Okumura Urban",
            "distance_km": distance_km,
            "frequency_mhz": frequency_mhz,
            "tx_height_m": tx_height_m,
            "rx_height_m": rx_height_m,
            "loss_db": round(loss, 2),
        }

    elif model == "hata_suburban":
        loss = hata_suburban(distance_km, frequency_mhz, tx_height_m, rx_height_m)
        return {
            "model": "Hata-Okumura Suburban",
            "distance_km": distance_km,
            "frequency_mhz": frequency_mhz,
            "tx_height_m": tx_height_m,
            "rx_height_m": rx_height_m,
            "loss_db": round(loss, 2),
        }

    elif model == "hata_rural":
        loss = hata_rural(distance_km, frequency_mhz, tx_height_m, rx_height_m)
        return {
            "model": "Hata-Okumura Rural",
            "distance_km": distance_km,
            "frequency_mhz": frequency_mhz,
            "tx_height_m": tx_height_m,
            "rx_height_m": rx_height_m,
            "loss_db": round(loss, 2),
        }

    elif model == "two_ray":
        loss = two_ray_loss(distance_m, tx_height_m, rx_height_m)
        return {
            "model": "Two-Ray Ground Reflection",
            "distance_km": distance_km,
            "tx_height_m": tx_height_m,
            "rx_height_m": rx_height_m,
            "loss_db": round(loss, 2),
        }

    elif model == "itm":
        terrain_irregularity = kwargs.get("terrain_irregularity_m", 30.0)
        climate = kwargs.get("climate", 5)
        refractivity = kwargs.get("refractivity", 301.0)
        permittivity = kwargs.get("permittivity", 15.0)
        conductivity = kwargs.get("conductivity", 0.005)
        polarization = kwargs.get("polarization", "horizontal")
        reliability = kwargs.get("reliability", 0.50)
        confidence = kwargs.get("confidence", 0.50)

        return itm_loss(
            distance_km=distance_km,
            freq_mhz=frequency_mhz,
            h_tx_m=tx_height_m,
            h_rx_m=rx_height_m,
            terrain_irregularity_m=terrain_irregularity,
            climate=climate,
            refractivity=refractivity,
            permittivity=permittivity,
            conductivity=conductivity,
            polarization=polarization,
            reliability=reliability,
            confidence=confidence,
        )

    elif model == "rain":
        freq_ghz = frequency_mhz / 1000.0
        rain_rate = kwargs.get("rain_rate_mmh", 5.0)
        polarization = kwargs.get("polarization", "horizontal")
        return rain_attenuation(freq_ghz, rain_rate, distance_km, polarization)

    elif model == "itu_p452":
        freq_ghz = frequency_mhz / 1000.0
        time_percent = kwargs.get("time_percent", 50.0)
        terrain_irregularity = kwargs.get("terrain_irregularity_m", 30.0)
        return itu_p452_loss(
            distance_km=distance_km,
            freq_ghz=freq_ghz,
            h_tx_m=tx_height_m,
            h_rx_m=rx_height_m,
            time_percent=time_percent,
            terrain_irregularity_m=terrain_irregularity,
        )

    elif model == "gaseous":
        freq_ghz = frequency_mhz / 1000.0
        temperature_c = kwargs.get("temperature_c", 15.0)
        pressure_hpa = kwargs.get("pressure_hpa", 1013.0)
        water_density_gm3 = kwargs.get("water_density_gm3", 7.5)
        return gaseous_attenuation(
            freq_ghz, distance_km, temperature_c, pressure_hpa, water_density_gm3
        )

    elif model == "cloud":
        freq_ghz = frequency_mhz / 1000.0
        liquid_water_content_gm3 = kwargs.get("liquid_water_content_gm3", 0.5)
        temperature_c = kwargs.get("temperature_c", 10.0)
        return cloud_attenuation(
            freq_ghz, distance_km, liquid_water_content_gm3, temperature_c
        )

    return {"error": f"Unknown model: {model}", "valid_models": [
        "free_space", "hata_urban", "hata_suburban", "hata_rural",
        "two_ray", "itm", "rain", "itu_p452", "gaseous", "cloud",
    ]}


__all__ = [
    "cloud_attenuation",
    "free_space_loss",
    "gaseous_attenuation",
    "hata_rural",
    "hata_suburban",
    "hata_urban",
    "itm_loss",
    "itu_p452_loss",
    "predict_path_loss",
    "rain_attenuation",
    "two_ray_loss",
]
