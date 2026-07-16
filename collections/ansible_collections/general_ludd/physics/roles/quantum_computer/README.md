# `general_ludd.physics.quantum_computer` — Quantum Mechanics Solver

Solve the Schrodinger equation, compute eigenstates and eigenvalues, and simulate
wavefunctions for common quantum mechanical problems.

## Quick start

```yaml
- name: Solve infinite square well
  hosts: localhost
  vars:
    quantum_enabled: true
    quantum_problem: "infinite_square_well"
    quantum_well_width_nm: 1.0
    quantum_num_states: 5
  roles:
    - general_ludd.physics.quantum_computer
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `quantum_enabled` | `false` | Enable solver (safety gate) |
| `quantum_problem` | `infinite_square_well` | Problem type |
| `quantum_well_width_nm` | `1.0` | Well width in nm |
| `quantum_particle` | `electron` | Particle type |
| `quantum_potential` | `square_well` | Potential shape |
| `quantum_dimensions` | `1` | Spatial dimensions |
| `quantum_num_states` | `5` | Number of eigenstates |
| `quantum_solver` | `numpy` | Backend solver |
| `quantum_output_dir` | `/tmp/gludd-quantum` | Output directory |
