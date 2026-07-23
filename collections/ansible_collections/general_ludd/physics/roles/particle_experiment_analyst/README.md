# `general_ludd.physics.particle_experiment_analyst` — Particle Experiment Analyst

Analyze particle collision data from high-energy physics experiments.
Computes cross-sections, branching ratios, and event yields.

## Quick start

```yaml
- name: Analyze LHC collision data
  hosts: localhost
  vars:
    particle_beam_energy_GeV: 13.6
    particle_analysis_channel: "H_to_ZZ_to_4l"
  roles:
    - general_ludd.physics.particle_experiment_analyst
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `particle_beam_energy_GeV` | `13.6` | Beam energy in GeV |
| `particle_target` | `proton` | Target particle |
| `particle_beam` | `proton` | Beam particle |
| `particle_detector` | `generic_4pi` | Detector type |
| `particle_luminosity_inv_fb` | `139.0` | Integrated luminosity (fb^-1) |
| `particle_analysis_channel` | `H_to_ZZ_to_4l` | Analysis channel |
| `particle_output_dir` | `/tmp/gludd-particle` | Output directory |
