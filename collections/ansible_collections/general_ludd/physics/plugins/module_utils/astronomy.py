"""Astronomy utilities — celestial mechanics, stellar physics, cosmology, exoplanets."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

G = 6.67430e-11
C_LIGHT = 299792458.0
PARSEC = 3.085677581e16
AU = 1.495978707e11
M_SUN = 1.98847e30
R_SUN = 6.957e8
L_SUN = 3.828e26
YEAR_SEC = 365.25 * 86400.0
H0 = 70.0
SIGMA_SB = 5.670374419e-8


@dataclass
class OrbitalConfig:
    primary_mass_kg: float = M_SUN
    semi_major_axis_m: float = AU
    eccentricity: float = 0.0
    inclination_deg: float = 0.0
    raan_deg: float = 0.0
    arg_periapsis_deg: float = 0.0
    epoch: float = 0.0


SOLAR_SYSTEM_BODIES: dict[str, dict[str, Any]] = {
    "mercury": {"semi_major_axis_AU": 0.387, "eccentricity": 0.2056, "orbital_period_yr": 0.241, "mass_kg": 3.301e23, "radius_km": 2439.7, "inclination_deg": 7.0},
    "venus": {"semi_major_axis_AU": 0.723, "eccentricity": 0.0068, "orbital_period_yr": 0.615, "mass_kg": 4.867e24, "radius_km": 6051.8, "inclination_deg": 3.39},
    "earth": {"semi_major_axis_AU": 1.000, "eccentricity": 0.0167, "orbital_period_yr": 1.000, "mass_kg": 5.972e24, "radius_km": 6371.0, "inclination_deg": 0.0},
    "mars": {"semi_major_axis_AU": 1.524, "eccentricity": 0.0934, "orbital_period_yr": 1.881, "mass_kg": 6.417e23, "radius_km": 3389.5, "inclination_deg": 1.85},
    "jupiter": {"semi_major_axis_AU": 5.204, "eccentricity": 0.0484, "orbital_period_yr": 11.86, "mass_kg": 1.898e27, "radius_km": 69911.0, "inclination_deg": 1.30},
    "saturn": {"semi_major_axis_AU": 9.537, "eccentricity": 0.0539, "orbital_period_yr": 29.46, "mass_kg": 5.683e26, "radius_km": 58232.0, "inclination_deg": 2.49},
    "uranus": {"semi_major_axis_AU": 19.19, "eccentricity": 0.0473, "orbital_period_yr": 84.01, "mass_kg": 8.681e25, "radius_km": 25362.0, "inclination_deg": 0.77},
    "neptune": {"semi_major_axis_AU": 30.07, "eccentricity": 0.0086, "orbital_period_yr": 164.8, "mass_kg": 1.024e26, "radius_km": 24622.0, "inclination_deg": 1.77},
}

SPECTRAL_CLASSES: list[dict[str, Any]] = [
    {"class": "O", "temp_k": 40000.0, "color": "blue", "mass_msun": 60.0, "lifetime_yr": 1e6, "lines": "He II, N III, Si IV", "fraction_pct": 0.00003},
    {"class": "B", "temp_k": 20000.0, "color": "blue-white", "mass_msun": 18.0, "lifetime_yr": 1e7, "lines": "He I, H I", "fraction_pct": 0.13},
    {"class": "A", "temp_k": 8500.0, "color": "white", "mass_msun": 3.2, "lifetime_yr": 5e8, "lines": "H I strong, Ca II", "fraction_pct": 0.6},
    {"class": "F", "temp_k": 6500.0, "color": "yellow-white", "mass_msun": 1.7, "lifetime_yr": 5e9, "lines": "Ca II, Fe I, Fe II", "fraction_pct": 3.0},
    {"class": "G", "temp_k": 5500.0, "color": "yellow", "mass_msun": 1.1, "lifetime_yr": 1e10, "lines": "Ca II, Fe I, CH", "fraction_pct": 7.6},
    {"class": "K", "temp_k": 4000.0, "color": "orange", "mass_msun": 0.8, "lifetime_yr": 5e10, "lines": "TiO, Ca I, Fe I", "fraction_pct": 12.1},
    {"class": "M", "temp_k": 3000.0, "color": "red", "mass_msun": 0.3, "lifetime_yr": 2e11, "lines": "TiO, VO, CaH", "fraction_pct": 76.4},
    {"class": "L", "temp_k": 2000.0, "color": "deep-red", "mass_msun": 0.09, "lifetime_yr": None, "lines": "FeH, CrH, alkali", "fraction_pct": None},
    {"class": "T", "temp_k": 1300.0, "color": "brown-dwarf", "mass_msun": 0.05, "lifetime_yr": None, "lines": "CH4, H2O", "fraction_pct": None},
    {"class": "Y", "temp_k": 600.0, "color": "sub-brown-dwarf", "mass_msun": 0.01, "lifetime_yr": None, "lines": "NH3, H2O, CH4", "fraction_pct": None},
]

STELLAR_EVOLUTION_STAGES: list[dict[str, Any]] = [
    {"stage": "protostar", "description": "Collapsing molecular cloud core. Heats by gravitational contraction. Not yet fusing hydrogen.", "duration_yr": 1e5, "mass_range_msun": "0.08-100"},
    {"stage": "main_sequence", "description": "Core hydrogen fusion (p-p chain or CNO cycle). Longest stable phase. Hydrostatic equilibrium.", "duration_yr": 1e10, "mass_range_msun": "0.08-100"},
    {"stage": "subgiant", "description": "Core hydrogen depleted. Hydrogen shell burning begins. Radius increases.", "duration_yr": 1e8, "mass_range_msun": "0.5-8"},
    {"stage": "red_giant", "description": "Hydrogen shell burning + helium core contraction. Luminosity increases dramatically.", "duration_yr": 1e8, "mass_range_msun": "0.5-8"},
    {"stage": "horizontal_branch", "description": "Core helium fusion (triple-alpha). Stable helium burning. RGB tip flips to HB.", "duration_yr": 1e8, "mass_range_msun": "0.5-8"},
    {"stage": "asymptotic_giant_branch", "description": "Helium shell + hydrogen shell burning. Thermal pulses. Strong mass loss.", "duration_yr": 1e7, "mass_range_msun": "0.5-8"},
    {"stage": "planetary_nebula", "description": "Ejected outer layers form glowing shell. Central star becomes white dwarf.", "duration_yr": 1e4, "mass_range_msun": "0.5-8"},
    {"stage": "white_dwarf", "description": "Electron-degenerate carbon/oxygen core. Cools over billions of years.", "duration_yr": 1e10, "mass_range_msun": "<1.4 (Chandrasekhar)"},
    {"stage": "supernova_II", "description": "Iron core collapse above Chandrasekhar mass. Outer layers ejected. Forms neutron star or black hole.", "duration_yr": None, "mass_range_msun": ">8"},
    {"stage": "neutron_star", "description": "Neutron-degenerate remnant. Radius ~10 km. Supported by neutron degeneracy pressure.", "duration_yr": None, "mass_range_msun": "1.4-2.2"},
    {"stage": "black_hole", "description": "Gravitational collapse beyond TOV limit (~3 M_sun). Event horizon forms.", "duration_yr": None, "mass_range_msun": ">3"},
]

NUCLEOSYNTHESIS_PROCESSES: list[dict[str, Any]] = [
    {"process": "big_bang", "products": ["H-1", "He-4", "D", "He-3", "Li-7"], "temperature_k": 1e9, "environment": "Early universe (first 3 minutes)", "notes": "Produced ~75% H, ~25% He by mass."},
    {"process": "pp_chain", "products": ["He-4 from H-1"], "temperature_k": 1.5e7, "environment": "Low-mass stars (<1.3 M_sun)", "notes": "Dominant in Sun. Net: 4p -> He-4 + 2e+ + 2nu + 26.73 MeV."},
    {"process": "cno_cycle", "products": ["He-4 from H-1 (C, N, O catalysts)"], "temperature_k": 2e7, "environment": "Stars >1.3 M_sun", "notes": "More temperature-sensitive than pp-chain. Dominant in massive MS stars."},
    {"process": "triple_alpha", "products": ["C-12 from He-4"], "temperature_k": 1e8, "environment": "Red giant cores", "notes": "3 He-4 -> C-12 + 7.275 MeV. Resonant (Hoyle state at 7.65 MeV)."},
    {"process": "alpha_process", "products": ["O-16", "Ne-20", "Mg-24", "Si-28"], "temperature_k": 2e8, "environment": "Massive stars (pre-supernova)", "notes": "Successive alpha captures: C-12 -> O-16 -> Ne-20 -> ... -> Ni-56."},
    {"process": "s_process", "products": ["Elements beyond Fe (slow neutron capture)"], "temperature_k": 3e8, "environment": "AGB stars", "notes": "Neutron capture slower than beta-decay. Builds up to Bi-209."},
    {"process": "r_process", "products": ["Heavy elements (rapid neutron capture)"], "temperature_k": 1e9, "environment": "Neutron star mergers / core-collapse SNe", "notes": "Neutron capture faster than beta-decay. Produces half of elements beyond Fe."},
    {"process": "p_process", "products": ["Proton-rich isotopes"], "temperature_k": 2e9, "environment": "Supernova shock fronts", "notes": "Photodisintegration. Produces ~35 stable proton-rich isotopes."},
]

HUBBLE_SEQUENCE: list[dict[str, Any]] = [
    {"type": "E0", "category": "elliptical", "features": "Nearly spherical. Old stellar population. Little gas/dust.", "bulge_ratio": 1.0, "disk": False, "star_formation": "none"},
    {"type": "E7", "category": "elliptical", "features": "Highly flattened. Velocity dispersion supports shape.", "bulge_ratio": 1.0, "disk": False, "star_formation": "none"},
    {"type": "S0", "category": "lenticular", "features": "Disk + central bulge. No spiral arms. Gas-depleted.", "bulge_ratio": 0.6, "disk": True, "star_formation": "minimal"},
    {"type": "Sa", "category": "spiral", "features": "Tightly wound arms, large bulge. Low SFR.", "bulge_ratio": 0.5, "disk": True, "star_formation": "low"},
    {"type": "Sb", "category": "spiral", "features": "Moderately wound arms, medium bulge. Moderate SFR.", "bulge_ratio": 0.3, "disk": True, "star_formation": "moderate"},
    {"type": "Sc", "category": "spiral", "features": "Loosely wound arms, small bulge. High SFR.", "bulge_ratio": 0.2, "disk": True, "star_formation": "high"},
    {"type": "SBa", "category": "barred_spiral", "features": "Strong central bar + tight spiral arms.", "bulge_ratio": 0.5, "disk": True, "star_formation": "low"},
    {"type": "SBc", "category": "barred_spiral", "features": "Bar + loose arms. Bars drive gas inflow.", "bulge_ratio": 0.2, "disk": True, "star_formation": "high"},
    {"type": "Irr", "category": "irregular", "features": "No regular structure. High gas fraction. Often tidally disturbed.", "bulge_ratio": None, "disk": False, "star_formation": "burst"},
]

DARK_MATTER_EVIDENCE: list[dict[str, Any]] = [
    {"observation": "galaxy_rotation_curves", "description": "V_rot stays flat at large radii, implying M(r) propto r beyond visible disk.", "key_paper": "Rubin & Ford 1970", "sigma": ">5"},
    {"observation": "cluster_velocity_dispersions", "description": "Galaxies in clusters move too fast to be bound by visible mass alone.", "key_paper": "Zwicky 1933", "sigma": ">5"},
    {"observation": "gravitational_lensing", "description": "Lensing mass exceeds luminous mass in clusters (Bullet Cluster).", "key_paper": "Clowe et al. 2006", "sigma": ">8"},
    {"observation": "cmb_anisotropies", "description": "CMB power spectrum peaks constrain Omega_m h^2 = 0.12 with <1% precision.", "key_paper": "Planck 2018", "sigma": ">50"},
    {"observation": "baryon_acoustic_oscillations", "description": "BAO feature in galaxy correlation function measures Omega_m independently.", "key_paper": "SDSS/eBOSS", "sigma": ">10"},
]

LAMBDA_CDM_PARAMETERS: dict[str, float] = {
    "Omega_m": 0.315,
    "Omega_Lambda": 0.685,
    "Omega_b": 0.049,
    "Omega_c": 0.264,
    "H0_kms_Mpc": 67.4,
    "sigma_8": 0.811,
    "n_s": 0.965,
    "tau_reio": 0.054,
}

COSMIC_MICROWAVE_BACKGROUND: dict[str, Any] = {
    "temperature_k": 2.72548,
    "peak_frequency_GHz": 160.23,
    "dipole_amplitude_mK": 3.362,
    "dipole_direction": "l=264 deg, b=48 deg",
    "anisotropy_rms": 18e-6,
    "age_at_decoupling_yr": 380000.0,
    "redshift_decoupling": 1090.0,
    "optical_depth": 0.054,
}

EXOPLANET_DETECTION_METHODS: list[dict[str, Any]] = [
    {"method": "transit", "observable": "Periodic dimming of stellar flux", "sensitivity": "Large planets, short periods", "first_detection": "HD 209458b (1999)", "count_discovered": 4200, "pros": "Radius, atmosphere via transmission spectroscopy", "cons": "Requires edge-on orbit (~0.5% probability for Earth-Sun analog)"},
    {"method": "radial_velocity", "observable": "Doppler shift of stellar spectrum", "sensitivity": "Close-in massive planets", "first_detection": "51 Pegasi b (1995)", "count_discovered": 1000, "pros": "Minimum mass (m sin i), orbital parameters", "cons": "Stellar activity noise. Inclination degeneracy."},
    {"method": "direct_imaging", "observable": "Direct photon detection of planet", "sensitivity": "Young, hot, wide-orbit planets", "first_detection": "2M1207b (2004)", "count_discovered": 50, "pros": "Spectrum, atmospheric composition", "cons": "Requires coronagraph. Limited to >5 AU."},
    {"method": "microlensing", "observable": "Gravitational magnification by planet-hosting star", "sensitivity": "Earth-mass planets at 1-10 AU", "first_detection": "OGLE-2003-BLG-235Lb (2004)", "count_discovered": 150, "pros": "Sensitive to free-floating planets. No stellar light needed.", "cons": "Non-repeatable events. No follow-up possible."},
    {"method": "astrometry", "observable": "Proper motion wobble of host star", "sensitivity": "Massive planets, wide orbits", "first_detection": "Gaia-era (ongoing)", "count_discovered": 5, "pros": "Direct mass (no sin i). Works for face-on systems.", "cons": "Requires micro-arcsecond precision."},
]

HABITABLE_ZONE_FACTORS: list[dict[str, Any]] = [
    {"factor": "stellar_flux", "description": "HZ centered on distance where stellar flux matches Earth's insolation (1 S_earth).", "range": "0.95-1.67 AU (Sun)"},
    {"factor": "greenhouse_effect", "description": "Atmospheric CO2/H2O raise surface temperature above equilibrium. Runaway greenhouse at inner edge.", "notes": "Venus: inner edge exceeded. Earth: balanced. Mars: outer edge."},
    {"factor": "carbonate_silicate_cycle", "description": "CO2 weathering thermostat regulates climate on geological timescales.", "notes": "Requires plate tectonics and liquid water."},
    {"factor": "tidal_locking", "description": "M-dwarf planets may be tidally locked. Terminator zone may be habitable.", "range": "<0.3 AU for M5V"},
    {"factor": "magnetic_field", "description": "Dynamo-generated field shields atmosphere from stellar wind erosion.", "notes": "Mars lost its dynamo ~4 Gyr ago; atmosphere eroded."},
    {"factor": "orbital_stability", "description": "Eccentricity and obliquity variations must not drive climate to extremes.", "notes": "Milankovitch cycles: Earth's obliquity varies 22.1-24.5 deg over 41 kyr."},
]

OBSERVATORIES: list[dict[str, Any]] = [
    {"name": "HST", "full_name": "Hubble Space Telescope", "wavelength": "UV/optical/NIR", "aperture_m": 2.4, "orbit": "LEO ~540 km", "launched": 1990, "key_instruments": "ACS, WFC3, COS, STIS"},
    {"name": "JWST", "full_name": "James Webb Space Telescope", "wavelength": "IR (0.6-28.5 um)", "aperture_m": 6.5, "orbit": "L2 halo", "launched": 2021, "key_instruments": "NIRCam, NIRSpec, MIRI, FGS/NIRISS"},
    {"name": "Chandra", "full_name": "Chandra X-ray Observatory", "wavelength": "X-ray (0.1-10 keV)", "aperture_m": 1.2, "orbit": "Highly elliptical", "launched": 1999, "key_instruments": "HRC, ACIS, HETG/LETG"},
    {"name": "VLA", "full_name": "Very Large Array", "wavelength": "Radio (0.6-410 cm)", "aperture_m": "25 m x 27 dishes", "orbit": "Ground (New Mexico)", "launched": 1980, "key_instruments": "WIDAR correlator, 4 configs (A-D)"},
    {"name": "ALMA", "full_name": "Atacama Large Millimeter/submillimeter Array", "wavelength": "mm/sub-mm (0.3-9.6 mm)", "aperture_m": "12 m x 50 dishes", "orbit": "Ground (Chile, 5000m)", "launched": 2013, "key_instruments": "Band 3-10 receivers"},
    {"name": "Keck", "full_name": "W. M. Keck Observatory", "wavelength": "Optical/IR", "aperture_m": "10.0 m x 2 telescopes", "orbit": "Ground (Mauna Kea, 4145m)", "launched": 1993, "key_instruments": "DEIMOS, HIRES, NIRSPEC, OSIRIS"},
    {"name": "Gaia", "full_name": "Gaia astrometry mission", "wavelength": "Optical (330-1050 nm)", "aperture_m": 1.45, "orbit": "L2 Lissajous", "launched": 2013, "key_instruments": "Astrometric instrument, BP/RP photometers, RVS"},
    {"name": "Fermi", "full_name": "Fermi Gamma-ray Space Telescope", "wavelength": "Gamma-ray (8 keV - 300 GeV)", "aperture_m": None, "orbit": "LEO ~565 km", "launched": 2008, "key_instruments": "LAT, GBM"},
    {"name": "TESS", "full_name": "Transiting Exoplanet Survey Satellite", "wavelength": "Optical (600-1000 nm)", "aperture_m": 0.1, "orbit": "HEO 2:1 lunar resonance", "launched": 2018, "key_instruments": "4 wide-field CCD cameras"},
    {"name": "LIGO", "full_name": "Laser Interferometer Gravitational-wave Observatory", "wavelength": "Gravitational waves (30-7000 Hz)", "aperture_m": None, "orbit": "Ground (Hanford + Livingston)", "launched": 2015, "key_instruments": "4 km Fabry-Perot arms, 200 W laser"},
]

INFLATIONARY_MODELS: list[dict[str, Any]] = [
    {"model": "slow_roll", "description": "Scalar inflaton field phi rolls slowly down potential V(phi). Eta and epsilon slow-roll parameters small.", "predictions": "ns ~ 0.96-0.97, r <= 0.1, Gaussian adiabatic perturbations", "status": "Consistent with Planck 2018 constraints."},
    {"model": "Starobinsky_R2", "description": "f(R) = R + R^2/(6M^2) gravity. Equivalent to scalar field with plateau potential.", "predictions": "ns = 0.965, r = 0.0035, minimal tensor modes", "status": "Favored by Planck+BICEP/Keck r < 0.036."},
    {"model": "chaotic", "description": "V(phi) = m^2 phi^2 / 2 quadratic potential. Large-field inflation phi > M_pl.", "predictions": "ns = 0.967, r = 0.13, detectable B-modes", "status": "Ruled out by Planck 2018 + BICEP/Keck (r too large)."},
    {"model": "natural", "description": "Axion-like potential V(phi) = Lambda^4 [1 + cos(phi/f)]. Shift symmetry protects flatness.", "predictions": "ns = 0.95-0.96, r variable with f", "status": "Allowed for super-Planckian decay constant f > M_pl."},
]


def compute_orbital_period(semi_major_axis_m: float, primary_mass_kg: float) -> float:
    """Compute Keplerian orbital period: T = 2*pi*sqrt(a^3/(G*M))."""
    return 2.0 * math.pi * math.sqrt(semi_major_axis_m ** 3 / (G * primary_mass_kg))


def compute_vis_viva(r_m: float, a_m: float, primary_mass_kg: float) -> float:
    """Compute orbital speed via vis-viva: v = sqrt(G*M*(2/r - 1/a))."""
    return math.sqrt(G * primary_mass_kg * (2.0 / r_m - 1.0 / a_m))


def compute_escape_velocity(r_m: float, mass_kg: float) -> float:
    """Compute escape velocity: v_esc = sqrt(2*G*M / r)."""
    return math.sqrt(2.0 * G * mass_kg / r_m)


def compute_orbital_elements(body: str) -> dict[str, Any] | None:
    """Return orbital elements for a Solar System body."""
    b = SOLAR_SYSTEM_BODIES.get(body.lower())
    if b is None:
        return None
    a_m = b["semi_major_axis_AU"] * AU
    period_s = compute_orbital_period(a_m, M_SUN)
    period_yr = period_s / YEAR_SEC
    return {
        "body": body.lower(),
        "semi_major_axis_m": a_m,
        "semi_major_axis_AU": b["semi_major_axis_AU"],
        "eccentricity": b["eccentricity"],
        "inclination_deg": b["inclination_deg"],
        "orbital_period_s": period_s,
        "orbital_period_yr": round(period_yr, 3),
        "orbital_velocity_ms": compute_vis_viva(a_m, a_m, M_SUN),
    }


def compute_hill_sphere(a_m: float, planet_mass_kg: float, star_mass_kg: float) -> float:
    """Compute Hill sphere radius: r_H = a * (m / (3*M))^(1/3)."""
    return a_m * (planet_mass_kg / (3.0 * star_mass_kg)) ** (1.0 / 3.0)


def compute_roche_limit(primary_radius_m: float, primary_density: float, secondary_density: float) -> float:
    """Compute Roche limit: d = 2.44 * R * (rho_p / rho_s)^(1/3) for fluid satellites."""
    return 2.44 * primary_radius_m * (primary_density / secondary_density) ** (1.0 / 3.0)


def compute_orbital_energy(a_m: float, m1_kg: float, m2_kg: float) -> float:
    """Compute specific orbital energy: epsilon = -G*M_total / (2*a)."""
    return -G * (m1_kg + m2_kg) / (2.0 * a_m)


def classify_star(spectral_type: str) -> dict[str, Any] | None:
    """Return spectral class data for a given spectral type classification."""
    letter = spectral_type[0].upper()
    for sc in SPECTRAL_CLASSES:
        if sc["class"] == letter:
            return sc
    return None


def compute_luminosity_from_mass(mass_msun: float) -> float:
    """Compute main-sequence luminosity: L/L_sun ~ (M/M_sun)^3.5 (mass-luminosity relation)."""
    return mass_msun ** 3.5


def compute_main_sequence_lifetime(mass_msun: float) -> float:
    """Compute MS lifetime: t ~ 1e10 * (M/M_sun)^(-2.5) yr."""
    return 1e10 * mass_msun ** (-2.5)


def compute_schwarzschild_radius(mass_kg: float) -> float:
    """Compute Schwarzschild radius: r_s = 2*G*M / c^2."""
    return 2.0 * G * mass_kg / (C_LIGHT * C_LIGHT)


def compute_effective_temperature(luminosity_w: float, radius_m: float) -> float:
    """Compute effective temperature from Stefan-Boltzmann: T_eff = (L / (4*pi*R^2*sigma))^(1/4)."""
    return (luminosity_w / (4.0 * math.pi * radius_m * radius_m * SIGMA_SB)) ** 0.25


def compute_redshift(wavelength_obs: float, wavelength_rest: float) -> float:
    """Compute redshift: z = (lambda_obs - lambda_rest) / lambda_rest."""
    return (wavelength_obs - wavelength_rest) / wavelength_rest


def compute_recession_velocity(z: float) -> float:
    """Compute recession velocity from redshift using relativistic formula: v = c * ((1+z)^2 - 1) / ((1+z)^2 + 1)."""
    zp1 = 1.0 + z
    zp1_sq = zp1 * zp1
    return C_LIGHT * (zp1_sq - 1.0) / (zp1_sq + 1.0)


def compute_hubble_distance(z: float) -> float:
    """Compute approximate distance via Hubble law: d = c*z / H0 (for z << 1)."""
    return C_LIGHT * z / (H0 * 1000.0)


def compute_lookback_time(z: float) -> float:
    """Compute approximate lookback time using Hubble time: t_lookback ~ z / (H0*(1+z))."""
    H0_per_yr = H0 * 1000.0 / (PARSEC * 1e6)
    return z / (H0_per_yr * (1.0 + z))


def compute_comoving_distance(z: float) -> float:
    """Compute approximate comoving distance (flat Lambda-CDM, z<2): d_c = (c/H0) * z / (1 + 1.8*z)."""
    d_h = C_LIGHT / (H0 * 1000.0)
    return d_h * z / (1.0 + 1.8 * z)


def compute_angular_diameter_distance(z: float) -> float:
    """Angular diameter distance: D_A = D_c / (1+z)."""
    d_c = compute_comoving_distance(z)
    return d_c / (1.0 + z)


def compute_luminosity_distance(z: float) -> float:
    """Luminosity distance: D_L = D_c * (1+z)."""
    d_c = compute_comoving_distance(z)
    return d_c * (1.0 + z)


def compute_friedmann_density_parameters() -> dict[str, float]:
    """Return Lambda-CDM density parameters with flatness check."""
    omega_total = LAMBDA_CDM_PARAMETERS["Omega_m"] + LAMBDA_CDM_PARAMETERS["Omega_Lambda"]
    return {
        "Omega_m": LAMBDA_CDM_PARAMETERS["Omega_m"],
        "Omega_Lambda": LAMBDA_CDM_PARAMETERS["Omega_Lambda"],
        "Omega_b": LAMBDA_CDM_PARAMETERS["Omega_b"],
        "Omega_c": LAMBDA_CDM_PARAMETERS["Omega_c"],
        "Omega_k": 1.0 - omega_total,
        "Omega_total": omega_total,
        "H0_kms_Mpc": LAMBDA_CDM_PARAMETERS["H0_kms_Mpc"],
    }


def compute_transit_depth(planet_radius_m: float, star_radius_m: float) -> float:
    """Compute transit depth: delta = (R_p / R_star)^2."""
    return (planet_radius_m / star_radius_m) ** 2


def compute_radial_velocity_semi_amplitude(planet_mass_kg: float, star_mass_kg: float, period_s: float, inclination_deg: float = 90.0) -> float:
    """Compute RV semi-amplitude: K = (2*pi*G/P)^(1/3) * (m_p*sin(i)) / (M_star)^(2/3) / sqrt(1-e^2)."""
    inc_rad = math.radians(inclination_deg)
    factor = (2.0 * math.pi * G / period_s) ** (1.0 / 3.0)
    return factor * planet_mass_kg * math.sin(inc_rad) / (star_mass_kg ** (2.0 / 3.0))


def compute_habitable_zone_boundaries(luminosity_solar: float) -> dict[str, float]:
    """Compute habitable zone inner/outer boundaries in AU using Kopparapu+ (2013) approach."""
    inner = 0.95 * math.sqrt(luminosity_solar)
    outer = 1.67 * math.sqrt(luminosity_solar)
    return {"inner_AU": inner, "outer_AU": outer, "luminosity_Lsun": luminosity_solar}


def compute_equilibrium_temperature(stellar_luminosity_w: float, orbital_distance_m: float, albedo: float = 0.3) -> float:
    """Compute planetary equilibrium temperature: T_eq = (L_star * (1-A) / (16*pi*sigma*d^2))^(1/4)."""
    numerator = stellar_luminosity_w * (1.0 - albedo)
    denominator = 16.0 * math.pi * SIGMA_SB * orbital_distance_m * orbital_distance_m
    return (numerator / denominator) ** 0.25


def compute_angular_resolution(wavelength_m: float, aperture_m: float) -> float:
    """Compute diffraction-limited angular resolution (Rayleigh criterion): theta = 1.22 * lambda / D in radians."""
    return 1.22 * wavelength_m / aperture_m


def compute_light_gathering_power(aperture_m: float) -> float:
    """Compute light gathering power relative to human eye (pupil ~7mm)."""
    pupil_area = math.pi * (0.0035 ** 2)
    telescope_area = math.pi * (aperture_m / 2.0) ** 2
    return telescope_area / pupil_area


def compute_magnitude_limit(aperture_m: float, exposure_s: float = 1.0) -> float:
    """Compute approximate magnitude limit: m_lim ~ 2.5*log10(LGP) + 6.0 + 2.5*log10(t_exp)."""
    lgp = compute_light_gathering_power(aperture_m)
    return 2.5 * math.log10(lgp) + 6.0 + 2.5 * math.log10(exposure_s)


def get_solar_system_body_list() -> list[str]:
    """Return names of all tracked Solar System bodies."""
    return list(SOLAR_SYSTEM_BODIES.keys())


def get_spectral_class_list() -> list[str]:
    """Return list of known spectral classes."""
    return [s["class"] for s in SPECTRAL_CLASSES]


def get_observatory(name: str) -> dict[str, Any] | None:
    """Return observatory data by name (case-insensitive match)."""
    for o in OBSERVATORIES:
        if o["name"].lower() == name.lower():
            return o
    return None


def get_all_observatories() -> list[str]:
    """Return names of all tracked observatories."""
    return [o["name"] for o in OBSERVATORIES]
