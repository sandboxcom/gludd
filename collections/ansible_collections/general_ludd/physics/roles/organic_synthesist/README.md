# `general_ludd.physics.organic_synthesist` — Organic Synthesis Planner

Plan organic synthesis routes, look up molecule data, and predict yields.

## Quick start

```yaml
- name: Synthesize aspirin
  hosts: localhost
  vars:
    synth_target_molecule: "aspirin"
    synth_temperature_C: 85.0
  roles:
    - general_ludd.physics.organic_synthesist
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `synth_target_molecule` | `aspirin` | Target molecule |
| `synth_starting_material` | `salicylic_acid` | Starting material |
| `synth_solvent` | `acetic_anhydride` | Reaction solvent |
| `synth_catalyst` | `sulfuric_acid` | Catalyst |
| `synth_temperature_C` | `85.0` | Reaction temperature (C) |
| `synth_reaction_time_min` | `15.0` | Reaction time (min) |
| `synth_output_dir` | `/tmp/gludd-synthesis` | Output directory |
