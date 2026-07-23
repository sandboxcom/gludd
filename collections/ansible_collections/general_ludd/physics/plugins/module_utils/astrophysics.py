"""Astrophysics — high-energy phenomena, compact objects, gravitational waves, astroparticle physics."""

from __future__ import annotations

import math
from typing import Any

G: float = 6.67430e-11
C_LIGHT: float = 299792458.0
M_SUN: float = 1.98847e30
R_SUN: float = 6.957e8
SIGMA_SB: float = 5.670374419e-8
H_PLANCK: float = 6.62607015e-34
K_B: float = 1.380649e-23
YEAR_SEC: float = 365.25 * 86400.0
PARSEC: float = 3.085677581e16
MSUN_KG: float = M_SUN

COMPACT_OBJECTS: list[dict[str, Any]] = [
    {"type": "white_dwarf", "mass_range_msun": "0.17-1.4", "radius_km": "5000-15000", "density_g_cm3": 1e6, "support": "electron degeneracy pressure", "temperature_k": 25000, "examples": ["Sirius B", "Procyon B", "40 Eridani B"], "formation": "Low/intermediate-mass star (<8 Msun) core collapse after AGB phase."},
    {"type": "neutron_star", "mass_range_msun": "1.4-2.2", "radius_km": "10-15", "density_g_cm3": 1e14, "support": "neutron degeneracy pressure + strong force repulsion", "temperature_k": 1e6, "magnetic_field_G": 1e12, "examples": ["Crab pulsar (PSR B0531+21)", "PSR J0348+0432 (2.01 Msun)", "PSR J1614-2230 (1.97 Msun)"], "formation": "Core-collapse supernova of 8-25 Msun progenitor."},
    {"type": "magnetar", "mass_range_msun": "1.4-2.0", "radius_km": "10-12", "density_g_cm3": 1e14, "support": "neutron degeneracy pressure", "temperature_k": 1e6, "magnetic_field_G": 1e15, "examples": ["SGR 1806-20", "SGR 1900+14", "1E 2259+586"], "formation": "Neutron star with dynamo-amplified magnetic field >10^15 G."},
    {"type": "stellar_black_hole", "mass_range_msun": "3-100", "radius_km": "~3 * M/Msun (Schwarzschild)", "density_g_cm3": None, "support": "none (event horizon; singular at center)", "temperature_k": None, "examples": ["Cygnus X-1 (21 Msun)", "V404 Cygni (9 Msun)", "GW150914 remnant (62 Msun)"], "formation": "Core collapse of >25 Msun star, or binary merger."},
    {"type": "supermassive_black_hole", "mass_range_msun": "1e6-1e10", "radius_km": "~3e6-3e10 km", "density_g_cm3": None, "support": "none", "temperature_k": None, "examples": ["Sgr A* (4.3e6 Msun)", "M87* (6.5e9 Msun)", "TON 618 (6.6e10 Msun)"], "formation": "Direct collapse, seed BH accretion, or hierarchical mergers over cosmic time."},
]

GAMMA_RAY_BURSTS: list[dict[str, Any]] = [
    {"class": "short", "duration_s": "< 2", "energy_erg": 1e49, "progenitor": "Binary neutron star or NS-BH merger; kilonova (r-process) counterpart.", "redshift_range": "0.1-5", "prompt_mechanism": "Relativistic jet from accretion onto compact remnant.", "examples": ["GRB 170817A (GW170817)", "GRB 050509B", "GRB 090510"]},
    {"class": "long", "duration_s": "> 2", "energy_erg": 1e51, "progenitor": "Collapsar: core-collapse of rapidly rotating massive star (Type Ic-BL). Associated with broad-lined Type Ic SNe.", "redshift_range": "0.01-9.4", "prompt_mechanism": "Collimated relativistic jet piercing stellar envelope.", "examples": ["GRB 221009A (BOAT)", "GRB 990123", "GRB 080319B"]},
    {"class": "ultra_long", "duration_s": "> 1000", "energy_erg": 1e52, "progenitor": "Blue supergiant collapse or tidal disruption event.", "redshift_range": "0.5-3", "prompt_mechanism": "Sustained central engine (magnetar or long-lived BH accretion).", "examples": ["GRB 111209A", "GRB 130925A"]},
]

ACTIVE_GALACTIC_NUCLEI: list[dict[str, Any]] = [
    {"type": "quasar", "luminosity_erg_s": 1e46, "description": "Most luminous AGN. Accretion onto SMBH at Eddington or super-Eddington rates. Broad emission lines. Host galaxy visible for low-z.", "spectral_features": "Broad Ly-alpha, C IV, Mg II emission lines. UV/optical continuum with 'big blue bump' from accretion disk.", "examples": ["3C 273", "3C 48", "SDSS J0100+2802 (1.2e13 Lsun)"]},
    {"type": "blazar", "luminosity_erg_s": 1e47, "description": "AGN with relativistic jet pointed toward observer. Doppler-boosted continuum. Rapid variability (<hour timescales).", "spectral_features": "Featureless non-thermal continuum from radio to gamma-rays. Synchrotron + inverse Compton peaks.", "examples": ["BL Lacertae", "3C 454.3", "Markarian 501"]},
    {"type": "seyfert_1", "luminosity_erg_s": 1e43, "description": "Moderate-luminosity AGN with broad permitted lines (FWHM >1000 km/s) and narrow forbidden lines.", "spectral_features": "Broad H-alpha, H-beta. Narrow [O III] 5007A. Fe II emission complex.", "examples": ["NGC 4151", "NGC 5548", "Fairall 9"]},
    {"type": "seyfert_2", "luminosity_erg_s": 1e42, "description": "AGN with only narrow lines. Unified model: obscured Seyfert 1 with torus blocking BLR.", "spectral_features": "Narrow lines only. Polarized broad lines in some (hidden BLR). Strong [O III].", "examples": ["NGC 1068", "Circinus Galaxy", "MCG-03-34-64"]},
]

SUPERNOVA_TYPES: list[dict[str, Any]] = [
    {"type": "Ia", "progenitor": "CO white dwarf accreting from companion or WD-WD merger. Explodes at Chandrasekhar mass (~1.4 Msun).", "peak_mag": -19.3, "energy_erg": 1e51, "spectrum": "No hydrogen, strong Si II 6150A. Nickel-56 decay powers light curve.", "rate_per_century_per_galaxy": 0.3, "use": "Standardizable candles for cosmology (Phillips relation)."},
    {"type": "Ib", "progenitor": "Massive star (>25 Msun) that lost H envelope via winds or binary interaction.", "peak_mag": -17.5, "energy_erg": 1e51, "spectrum": "No hydrogen, He I lines present. Core-collapse origin.", "rate_per_century_per_galaxy": 0.5, "use": "Confirmed core-collapse. Often associated with GRBs."},
    {"type": "Ic", "progenitor": "Massive star that lost both H and He envelopes.", "peak_mag": -17.5, "energy_erg": 1e51, "spectrum": "No hydrogen, no helium. Broad-lined (Ic-BL) associated with GRBs.", "rate_per_century_per_galaxy": 0.5, "use": "GRB progenitors if broad-lined."},
    {"type": "II-P", "progenitor": "Red supergiant. Retains H envelope. Plateau in light curve from recombination wave.", "peak_mag": -16.5, "energy_erg": 1e51, "spectrum": "Strong hydrogen Balmer lines. P-Cygni profiles.", "rate_per_century_per_galaxy": 1.0, "use": "Direct progenitor detections (pre-explosion imaging)."},
    {"type": "II-L", "progenitor": "Massive star with partial H envelope.", "peak_mag": -17.0, "energy_erg": 1e51, "spectrum": "Hydrogen lines present. Linear decline after peak (no plateau).", "rate_per_century_per_galaxy": 0.2, "use": "Progenitor mass estimates."},
]

GRAVITATIONAL_WAVE_SOURCES: list[dict[str, Any]] = [
    {"source": "binary_black_hole", "frequency_Hz": "10-1000", "strain_amplitude": 1e-21, "waveform": "Chirp: frequency and amplitude increase as orbit decays. Merger: peak strain. Ringdown: quasi-normal modes.", "mass_range_msun": "3-100 (stellar), up to 1e10 (SMBH coalescence)", "detectors": ["LIGO", "Virgo", "KAGRA", "LISA (future)"], "examples": ["GW150914 (35+30 Msun)", "GW190521 (85+66 Msun, IMBH)"]},
    {"source": "binary_neutron_star", "frequency_Hz": "10-2000", "strain_amplitude": 1e-22, "waveform": "Long inspiral (~minutes in band). Tidal deformability measurable. Post-merger: HMNS collapse or stable NS.", "mass_range_msun": "1.0-2.2", "detectors": ["LIGO", "Virgo", "KAGRA"], "examples": ["GW170817 (1.36+1.39 Msun)", "GW190425 (1.6+1.1 Msun unmodeled)"]},
    {"source": "ns_bh", "frequency_Hz": "10-1000", "strain_amplitude": 1e-22, "waveform": "Depends on mass ratio. Tidal disruption if NS crosses ISCO before BH horizon.", "mass_range_msun": "NS: 1.0-2.2, BH: 3-50", "detectors": ["LIGO", "Virgo", "KAGRA"], "examples": ["GW200105 (8.9+1.9 Msun)", "GW200115 (5.7+1.5 Msun)"]},
    {"source": "core_collapse_sn", "frequency_Hz": "100-1000", "strain_amplitude": 1e-23, "waveform": "Burst signal. Bounce, convection, SASI, and proto-NS oscillations.", "mass_range_msun": ">8", "detectors": ["LIGO", "Virgo", "KAGRA"], "examples": ["Undetected so far (SN 1987A upper limits set)"]},
    {"source": "stochastic_background", "frequency_Hz": "1e-9-1", "strain_amplitude": 1e-15, "waveform": "Unresolved superposition of many sources. Astrophysical: compact binaries. Cosmological: inflation, phase transitions.", "mass_range_msun": "N/A", "detectors": ["PTA (NANOGrav, EPTA, PPTA, InPTA)", "LISA (future)"], "examples": ["NANOGrav 15-year: evidence for GWB at nanohertz (2023)"]},
    {"source": "continuous_wave", "frequency_Hz": "20-2000", "strain_amplitude": 1e-26, "waveform": "Nearly monochromatic. From rotating non-axisymmetric neutron stars (mountains, r-modes).", "mass_range_msun": "1.0-2.0", "detectors": ["LIGO", "Virgo", "KAGRA"], "examples": ["Targeted searches: Crab, Vela, Cas A; all-sky searches ongoing"]},
]

COSMIC_RAYS: list[dict[str, Any]] = [
    {"component": "solar_wind", "energy_eV": "1 keV - 10 keV", "composition": "Protons, electrons, alpha particles.", "flux_m2_sr_s": None, "origin": "Solar corona and solar energetic particle events."},
    {"component": "solar_energetic_particles", "energy_eV": "10 keV - 1 GeV", "composition": "Protons (90%), alpha (9%), heavier ions.", "flux_m2_sr_s": None, "origin": "Solar flares, CME-driven shocks."},
    {"component": "anomalous_cosmic_rays", "energy_eV": "10 MeV - 100 MeV", "composition": "He, N, O, Ne (from interstellar neutrals).", "flux_m2_sr_s": None, "origin": "Interstellar neutrals ionized in heliosphere, accelerated at termination shock."},
    {"component": "galactic_cosmic_rays", "energy_eV": "100 MeV - 1 PeV", "composition": "Protons (87%), He (12%), heavier nuclei (1%), electrons (1%).", "flux_m2_sr_s": 100, "origin": "Supernova remnants (Fermi first-order acceleration). Up to knee at ~3 PeV."},
    {"component": "ultra_high_energy", "energy_eV": "> 1 EeV", "composition": "Protons or intermediate-mass nuclei (debated).", "flux_m2_sr_s": 1e-16, "origin": "Active galactic nuclei, GRBs, tidal disruption events. GZK cutoff at ~5e19 eV. Ankle at ~5e18 eV."},
]

NEUTRINO_ASTROPHYSICS: list[dict[str, Any]] = [
    {"source": "solar_neutrinos", "flavor": "electron neutrino (nu_e)", "energy_range_MeV": "0.1-18", "flux_cm2_s": 6.5e10, "detection": "Radiochemical (Cl, Ga), Cherenkov (Super-K, SNO), scintillator (Borexino).", "physics": "pp-chain and CNO cycle. Flavor oscillation confirmed (Solar Neutrino Problem resolved)."},
    {"source": "atmospheric_neutrinos", "flavor": "nu_e, nu_mu", "energy_range_GeV": "0.1-1000", "flux_cm2_s": 1, "detection": "Cherenkov water/ice (Super-K, IceCube, ANTARES).", "physics": "Cosmic ray air showers. Up/down asymmetry proved neutrino oscillation."},
    {"source": "supernova_neutrinos", "flavor": "all flavors", "energy_range_MeV": "10-60", "flux_cm2_s": "1e10 (at 10 kpc)", "detection": "Water Cherenkov (Super-K), liquid scintillator (LVD, KamLAND), IceCube.", "physics": "99% of SN binding energy. SN 1987A: 25 events in Kamiokande II + IMB + Baksan. Early warning for next galactic SN."},
    {"source": "geoneutrinos", "flavor": "anti-nu_e", "energy_range_MeV": "1-5", "flux_cm2_s": 1e6, "detection": "Liquid scintillator (KamLAND, Borexino).", "physics": "Radiogenic heat: U-238, Th-232, K-40 decays in Earth's mantle and crust."},
    {"source": "high_energy_astrophysical", "flavor": "all flavors (1:1:1 at Earth)", "energy_range_TeV": "10-1e7", "flux_cm2_s": 1e-8, "detection": "Cherenkov in ice/water (IceCube, KM3NeT), radio (ARA, ARIANNA, GRAND), EAS (Pierre Auger).", "physics": "IceCube discovered diffuse astrophysical flux in 2013. Sources: TXS 0506+056 (blazar, 3.5 sigma), NGC 1068 (Seyfert, 4.2 sigma)."},
]

ASTROPARTICLE_DETECTORS: list[dict[str, Any]] = [
    {"name": "IceCube", "location": "South Pole", "detector_volume_km3": 1.0, "energy_range": "10 GeV - 10 PeV", "particle": "neutrino", "key_result": "First detection of high-energy astrophysical neutrinos (2013). Blazar TXS 0506+056 correlation (2018)."},
    {"name": "Pierre_Auger", "location": "Argentina (Malargue)", "detector_area_km2": 3000, "energy_range": "> 1 EeV", "particle": "UHECR", "key_result": "Suppression of flux at highest energies (GZK cutoff). Dipole anisotropy above 8 EeV."},
    {"name": "Fermi_LAT", "location": "LEO ~565 km", "energy_range": "20 MeV - >300 GeV", "particle": "gamma-ray", "key_result": "Fermi Bubbles, >5000 sources, GRB detection, Galactic Center GeV excess."},
    {"name": "CTA", "location": "Chile (south) + La Palma (north)", "detector_area_km2": None, "energy_range": "20 GeV - 300 TeV", "particle": "gamma-ray", "key_result": "Under construction. Will improve sensitivity over current IACTs (HESS, MAGIC, VERITAS) by factor 10."},
    {"name": "Kamiokande_SuperK", "location": "Japan (Kamioka mine)", "detector_volume_kt": 50, "energy_range": "5 MeV - TeV", "particle": "neutrino", "key_result": "Atmospheric neutrino oscillation (1998). Solar neutrino detection. Supernova neutrino watch."},
    {"name": "LIGO", "location": "USA (Hanford + Livingston)", "arm_length_km": 4, "frequency_Hz": "10-7000", "particle": "gravitational_wave", "key_result": "First GW detection GW150914 (2015). GW170817 multimessenger (2017). 90+ confident detections in O1-O3."},
]


def compute_schwarzschild_radius(mass_kg: float) -> float:
    return 2.0 * G * mass_kg / (C_LIGHT * C_LIGHT)


def compute_innermost_stable_circular_orbit(mass_kg: float) -> float:
    return 6.0 * G * mass_kg / (C_LIGHT * C_LIGHT)


def compute_photon_sphere_radius(mass_kg: float) -> float:
    return 3.0 * G * mass_kg / (C_LIGHT * C_LIGHT)


def compute_eddington_luminosity(mass_kg: float, opacity: float = 0.034) -> float:
    return 4.0 * math.pi * G * C_LIGHT * mass_kg / opacity


def compute_chandrasekhar_mass(mu_e: float = 2.0) -> float:
    return 5.76 * mu_e ** (-2) * M_SUN


def compute_tov_limit() -> dict[str, Any]:
    return {"mass_msun": 2.2, "radius_km": 12.0, "central_density": 2e18, "supports": ["neutron degeneracy", "strong force repulsion"], "uncertainty": "Equation of state dependent. Range: 1.9-3.0 Msun."}


def compute_gravitational_wave_chirp_mass(m1_kg: float, m2_kg: float) -> float:
    return (m2_kg ** 3 / (m1_kg + m2_kg) ** (1.0 / 5.0)) ** 0.2


def compute_gravitational_wave_strain(mass_kg: float, distance_m: float, frequency_Hz: float) -> float:
    chirp_m = compute_gravitational_wave_chirp_mass(mass_kg, mass_kg)
    return 2.0 * (G * chirp_m / C_LIGHT ** 3) ** (5.0 / 3.0) * (math.pi * frequency_Hz) ** (2.0 / 3.0) / distance_m


def compute_accretion_luminosity(mass_accretion_rate_kg_s: float, efficiency: float = 0.1) -> float:
    return efficiency * mass_accretion_rate_kg_s * C_LIGHT * C_LIGHT


def compute_bondi_accretion_rate(mass_kg: float, ambient_density: float, sound_speed: float) -> float:
    return 4.0 * math.pi * (G * mass_kg) ** 2 * ambient_density / (sound_speed ** 3)


def compute_magnetic_dipole_braking_index(period_s: float, period_derivative: float) -> float:
    return 3.0


def compute_spin_down_luminosity(moment_of_inertia: float, period_s: float, period_derivative: float) -> float:
    angular_freq = 2.0 * math.pi / period_s
    return 4.0 * math.pi ** 2 * moment_of_inertia * period_derivative / (period_s ** 3)


def compute_synchrotron_critical_frequency(magnetic_field_T: float, electron_energy_J: float) -> float:
    m_e = 9.1093837015e-31
    e_charge = 1.602176634e-19
    gamma = electron_energy_J / (m_e * C_LIGHT * C_LIGHT)
    return 3.0 * gamma ** 2 * e_charge * magnetic_field_T / (4.0 * math.pi * m_e)


def compute_ic_energy(photon_energy_J: float, electron_energy_J: float) -> float:
    m_e = 9.1093837015e-31
    gamma = electron_energy_J / (m_e * C_LIGHT * C_LIGHT)
    return gamma ** 2 * photon_energy_J


def compute_neutrino_cross_section_approx(neutrino_energy_GeV: float) -> float:
    return 6.7e-43 * neutrino_energy_GeV


def compute_greisen_zatsepin_kuzmin_threshold() -> dict[str, Any]:
    return {"energy_threshold_eV": 5e19, "process": "p + gamma_CMB -> Delta+ -> p + pi0 or n + pi+", "mean_free_path_Mpc": 100, "notes": "Limits UHECR propagation from distant sources to <100 Mpc at >6e19 eV."}


def compute_crab_pulsar_parameters() -> dict[str, Any]:
    return {"period_ms": 33.0, "period_derivative": 4.2e-13, "spin_down_luminosity_erg_s": 4.5e38, "age_yr": 960, "magnetic_field_G": 3.8e12, "rotational_energy_erg": 1e49}


def compute_agn_unified_model() -> dict[str, Any]:
    return {"components": ["supermassive black hole", "accretion disk (UV/optical)", "broad-line region (BLR, ~0.1 pc)", "dusty torus (1-10 pc)", "narrow-line region (NLR, ~100 pc)", "relativistic jet (if present)"], "viewing_angle_dependence": "Type 1 (face-on): see BLR, disk. Type 2 (edge-on): torus obscures BLR. Blazar: jet pointed at observer.", "references": "Antonucci (1993), Urry & Padovani (1995)"}


def get_compact_object(object_type: str) -> dict[str, Any] | None:
    for o in COMPACT_OBJECTS:
        if o["type"] == object_type:
            return o
    return None


def get_grb_class(grb_type: str) -> dict[str, Any] | None:
    for g in GAMMA_RAY_BURSTS:
        if g["class"] == grb_type:
            return g
    return None


def get_gw_source(source_type: str) -> dict[str, Any] | None:
    for s in GRAVITATIONAL_WAVE_SOURCES:
        if s["source"] == source_type:
            return s
    return None


def list_compact_objects() -> list[str]:
    return [o["type"] for o in COMPACT_OBJECTS]


def list_grb_classes() -> list[str]:
    return [g["class"] for g in GAMMA_RAY_BURSTS]


def list_gw_sources() -> list[str]:
    return [s["source"] for s in GRAVITATIONAL_WAVE_SOURCES]
