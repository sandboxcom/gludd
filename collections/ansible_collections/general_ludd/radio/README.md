# `general_ludd.radio` -- Radio Communications Agent Collection

Ansible collection providing agents with radio frequency analysis, signal
processing, propagation modeling, antenna design, and communications system
capabilities.

## Roles

| Role | Purpose |
|---|---|
| `propagation_model` | RF path-loss modeling: ITM, Hata, free-space, rain attenuation |
| `antenna_design` | Antenna types, radiation patterns, impedance matching |
| `signal_identify` | Modulation classification, baud rate detection, signal fingerprinting |
| `signal_decode` | Digital signal decoding from raw IQ samples |
| `link_budget` | Link budget calculation with gain, loss, and margin analysis |
| `spectrum_scan` | Spectrum scanning, waterfall analysis, signal detection |
| `sdr_capture` | SDR hardware control, IQ sample capture, frequency tuning |
| `marine_decode` | Marine VHF/HF decode: DSC, NAVTEX, AIS, GMDSS |
| `exam_quiz` | Amateur radio exam preparation and quiz generation |
| `regulation_lookup` | ITU/FCC band plans, license class privileges, frequency allocations |

## Cross-Collection Imports

Radio propagation and antenna models depend on physics for electromagnetic
theory and mathematical computation:

| Physics Module | Used By |
|---|---|
| `electrodynamics.py` | `antenna_gain()`, refraction, polarization for antenna/propagation models |
| `math_identities.py` | dB/log conversions, series expansions used across all path-loss models |
| `physical_constants.py` | CODATA values (c, epsilon_0, mu_0) for wave equations and field calculations |

See `plugins/module_utils/cross_references.py` for the import layer.

## Related Collections

| Collection | Shared Domain | Cross-Use |
|---|---|---|
| `general_ludd.physics` | Electromagnetics, math, wave physics | `electrodynamics.py` (antenna gain, polarization), `math_identities.py` (dB/log conversions, series expansions), `physical_constants.py` (c, ε0, μ0) |

Use `get_cross_collection_help("propagation")` or `get_cross_collection_help("signal_processing")`
from `physics.plugins.module_utils.cross_collection` to discover all related roles.

## Quick start

```yaml
- name: Model RF propagation loss
  hosts: localhost
  vars:
    frequency_mhz: 900
    distance_km: 10
    model: "hata"
  roles:
    - general_ludd.radio.propagation_model
```

## Dependencies

- Python: numpy, scipy
- Optional per-role: pyrtlsdr (SDR), digital_rf (IQ capture)
- System: rtl-sdr drivers (sdr_capture role)
- Cross-collection: `general_ludd.physics` (electrodynamics, math, constants)
