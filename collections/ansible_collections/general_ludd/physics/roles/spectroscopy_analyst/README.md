# `general_ludd.physics.spectroscopy_analyst` — Spectroscopy Analyst

Simulate and analyze spectroscopic data with peak detection and line fitting.

## Quick start

```yaml
- name: Analyze UV-Vis spectrum
  hosts: localhost
  vars:
    spectroscopy_technique: "uv_vis"
    spectroscopy_wl_min_nm: 200
    spectroscopy_wl_max_nm: 800
  roles:
    - general_ludd.physics.spectroscopy_analyst
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `spectroscopy_technique` | `uv_vis` | Spectroscopic technique |
| `spectroscopy_wl_min_nm` | `200` | Minimum wavelength (nm) |
| `spectroscopy_wl_max_nm` | `800` | Maximum wavelength (nm) |
| `spectroscopy_resolution_nm` | `1.0` | Spectral resolution (nm) |
| `spectroscopy_solvent` | `water` | Solvent |
| `spectroscopy_temperature_C` | `25.0` | Temperature (Celsius) |
| `spectroscopy_peak_threshold` | `0.1` | Peak detection threshold |
| `spectroscopy_output_dir` | `/tmp/gludd-spectroscopy` | Output directory |
