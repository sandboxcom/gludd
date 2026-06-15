# tla_scaffold

Generate a TLA+ specification and TLC config from a design description.

## Philosophy

Model what should be **IMPOSSIBLE**, not merely undesirable. The generated spec includes:
- `VARIABLES`, `Init`, `Next`, `Spec == Init /\ [][Next]_vars`
- `TypeOK` invariant (type safety)
- A named `INVARIANT` capturing the impossible state
- A `CONSTRAINT` bounding the state space (required for TLC to terminate)

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `design_text` | `""` | Design description to scaffold from |
| `spec_name` | `"MySpec"` | TLA+ module name (becomes filename) |
| `out_dir` | `"/tmp/gludd-tla-scaffold"` | Output directory |
| `enable_model_call` | `false` | Call LLM via gludd_model_call (gated) |
| `daemon_url` | `"http://localhost:8000"` | Daemon URL for gludd_model_call |
| `psk` | `""` | PSK for daemon auth |

## Artifacts

- `<out_dir>/<spec_name>.tla` — TLA+ spec
- `<out_dir>/<spec_name>.cfg` — TLC config (SPECIFICATION, INVARIANT, CONSTRAINT)
- `<out_dir>/tla_scaffold.json` — JSON metadata (vars[], invariant_name, constraint, source)

## Example

```yaml
- name: Scaffold counter spec
  ansible.builtin.include_role:
    name: general_ludd.formal.tla_scaffold
  vars:
    spec_name: Counter
    design_text: "A counter that increments and decrements. It must never go negative."
    out_dir: /tmp/my-specs
    enable_model_call: false
```
