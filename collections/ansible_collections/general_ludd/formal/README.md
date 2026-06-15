# general_ludd.formal

Ansible collection for TLA+ formal-methods tasks in the `general_ludd` agentic SDLC harness.

## Philosophy

Model what should be **IMPOSSIBLE**, not merely undesirable.

- **Invariant** = a state that must NEVER be reached
- **State machine** = VARIABLES, Init, Next, Spec == Init /\ [][Next]_vars
- **Always bound** the state space with a CONSTRAINT (unbounded = TLC hangs)
- Verify invariants on the design, not on the implementation

## Roles

| Role | Purpose |
|------|---------|
| `tla_scaffold` | Generate `.tla` + `.cfg` from a design description (template-driven, deterministic) |
| `tla_parse` | SANY syntax/semantic check; graceful skip if jar absent |
| `tla_check` | Run TLC; classify outcome (success / invariant_violated / deadlock / parse_error / skipped) |
| `tla_trace_interpret` | Parse TLC counterexample traces into steps + narrative |
| `tla_pluscal` | Translate PlusCal to TLA+ via pcal.trans; graceful skip if jar absent |

## TLA+ Tooling

Requires `tla2tools.jar` and Java. Set `tla_tools_jar` to the jar path.

```
# TLC model check
java -cp tla2tools.jar tlc2.TLC MySpec.tla -config MySpec.cfg

# SANY parse
java -cp tla2tools.jar tla2sany.SANY MySpec.tla

# PlusCal translate
java -cp tla2tools.jar pcal.trans MySpec.tla
```

## TLC Outcome Markers

- **Success**: `Model checking completed. No error has been found.`
- **Invariant violation**: `Error: Invariant <Name> is violated.` + trace
- **Deadlock**: `Error: Deadlock reached.`

## Dependencies

- `general_ludd.agent` >= 0.1.0 (provides `gludd_model_call`, `gludd_message`, `gludd_facts`)
