# `general_ludd.radio.antenna_design` — Antenna Design & Simulation

Design antennas using analytical equations and optional electromagnetic simulation.

## Quick start

```yaml
- name: Design 2m dipole
  hosts: localhost
  vars:
    antenna_design_enabled: true
    antenna_design_type: dipole
    antenna_design_freq_hz: 146000000
    antenna_design_polarization: vertical
  roles:
    - general_ludd.radio.antenna_design
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `antenna_design_enabled` | `false` | Enable design |
| `antenna_design_type` | `dipole` | dipole / yagi / loop / patch / discone |
| `antenna_design_freq_hz` | `144000000` | Target frequency in Hz |
| `antenna_design_polarization` | `vertical` | vertical / horizontal / circular |
| `antenna_design_impedance_ohms` | `50.0` | Feedpoint impedance |
| `antenna_design_simulator` | `equation` | equation / nec2 / openems |
