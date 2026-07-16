# `general_ludd.physics.math_modeler` — Math Modeler

Solve ODEs, perform regression, compute statistics.

## Quick start

```yaml
- name: Solve exponential decay ODE
  hosts: localhost
  vars:
    math_model_type: "ode_first_order"
    math_equation: "dy/dt = -k * y"
    math_param_k: 0.5
  roles:
    - general_ludd.physics.math_modeler
```

## Variables

| Variable | Default | Description |
|---|---|---|
| `math_model_type` | `ode_first_order` | Model type |
| `math_equation` | `dy/dt = -k * y` | Equation to solve |
| `math_initial_y0` | `1.0` | Initial condition y(0) |
| `math_param_k` | `0.5` | Rate constant k |
| `math_time_start` | `0.0` | Start time |
| `math_time_end` | `10.0` | End time |
| `math_time_steps` | `100` | Number of time steps |
| `math_output_dir` | `/tmp/gludd-math` | Output directory |
