# `general_ludd.physics.thermodynamics_engineer` — Thermodynamics Engineer

Compute heat transfer, phase changes, and entropy for configurable substances.

## Quick start

```yaml
- name: Heat water from 25 to 100 C
  hosts: localhost
  vars:
    thermo_substance: "water"
    thermo_mass_kg: 1.0
    thermo_initial_temp_C: 25.0
    thermo_final_temp_C: 100.0
  roles:
    - general_ludd.physics.thermodynamics_engineer
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `thermo_substance` | `water` | Substance for computation |
| `thermo_mass_kg` | `1.0` | Mass in kg |
| `thermo_initial_temp_C` | `25.0` | Initial temperature (C) |
| `thermo_final_temp_C` | `100.0` | Final temperature (C) |
| `thermo_pressure_atm` | `1.0` | Pressure (atm) |
| `thermo_compute_heat` | `true` | Compute heat transfer |
| `thermo_compute_phase` | `true` | Compute phase change energy |
| `thermo_compute_entropy` | `true` | Compute entropy change |
| `thermo_output_dir` | `/tmp/gludd-thermo` | Output directory |
