# general_ludd.chemistry

Chemistry expert collection implementing the top 5 capabilities from
`docs/specs/FEATURE_CHEMISTRY_EXPERT.md`:

| Capability | Role | Service function |
|------------|------|------------------|
| CHEM-001 Expert router | `general_ludd.chemistry.chemistry_router` | `route_chemistry_task` |
| CHEM-002 Chemical identity | `general_ludd.chemistry.identity_resolve` | `resolve_identity` |
| CHEM-005 Reaction reasoning | `general_ludd.chemistry.reaction_analyze` | `analyze_reaction` |
| CHEM-007 Stoichiometry | `general_ludd.chemistry.stoichiometry` | `molar_mass`, `stoichiometry_moles`, `stoichiometry_dilution`, `stoichiometry_yield` |
| CHEM-008 Safety and compatibility | `general_ludd.chemistry.hazard_review` | `screen_hazards` |

The chemical logic lives in `src/general_ludd/chemistry/core.py`; the roles in
this collection carry orchestration (parameter validation, output capture,
JSON marshalling) and never re-implement chemical algorithms.

## Safety boundary

The expert is **decision support**, not an autonomous laboratory operator.
High-risk and prohibited requests return `status: refused` with a `policy`
limitation rather than an actionable artifact. Wet-lab outputs are drafts
until an identified, qualified human approves the exact version for the
declared facility.

## Quick start

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.chemistry.identity_resolve
      vars:
        identity_query: "water"
        identity_output_dir: /tmp/chemistry
```
