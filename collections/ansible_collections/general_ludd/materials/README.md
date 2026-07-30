# general_ludd.materials

Materials engineering expert collection (spec MATE-001).

## Roles (top 5 from spec §3)

| Role | Purpose |
|---|---|
| `requirements_capture` | Normalize loads, environment, life, geometry, manufacturing, and inspection constraints. |
| `material_select` | Screen and rank material candidates with traceable margins and unknowns. |
| `polymer_process_plan` | Select process windows for thermoplastic / thermoset forming. |
| `metal_forming_plan` | Select alloy condition, forming sequence, and springback controls. |
| `strength_assess` | Check static strength margins with fail-closed safety behavior. |

## Python layer

The property/access functions live in `src/general_ludd/materials/core.py` and
are unit-tested in `tests/unit/test_materials_core.py`.

## Safety

Every property record identifies units, basis, method, uncertainty, and
condition. Missing condition metadata is flagged `insufficient_context`.
Unit mismatch or missing data blocks a positive verdict (fail-closed per
MATE-SAFE-006).
