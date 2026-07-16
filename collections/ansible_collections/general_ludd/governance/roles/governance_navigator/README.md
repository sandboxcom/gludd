# governance_navigator

Composite **unified query interface** for the governance collection. Given a
natural-language `governance_navigator_query`, determines which governance
module(s) to consult and dispatches the matching sub-roles, aggregating their
verdicts into a single `governance_navigator_verdict`.

## Routing logic

The query is lowercased and matched against a keyword table (see
`vars/main.yml`). A module is routed when any of its keywords appears in the
query, or when it is named in `governance_navigator_force_routes`.

| Module | Example keywords |
|--------|------------------|
| `borders` | border, neighbour, landlocked, frontier |
| `governing_bodies` | parliament, congress, legislature, judiciary, senate |
| `tax_currency` | tax, currency, vat, euro, dollar, fiscal |
| `conflicts_treaties` | war, treaty, sanctions, ceasefire, NATO |
| `civic_services` | passport, healthcare, electoral, postal, identity |
| `decision_makers` | president, prime minister, chancellor, monarch |
| `info_classification` | classified, top secret, confidential, clearance |

Country-specific modules require a 2-letter `governance_navigator_country`;
`info_classification` does not (it is scheme-based).

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `governance_navigator_enabled` | `false` | Must be `true` to run |
| `governance_navigator_query` | `""` | Natural-language query |
| `governance_navigator_country` | `null` | ISO country code (required for country modules) |
| `governance_navigator_scheme` | `data` | Scheme used when routing to `info_classification` |
| `governance_navigator_force_routes` | `[]` | Force-enable modules by name |
| `governance_navigator_output_dir` | `/tmp/gludd-governance-navigator` | Artifact directory |

## Result facts

- `governance_navigator_verdict` — unified result (matched_routes, dispatched_modules, per-module `routes`)
- `governance_navigator_routes` — dict of each dispatched module's verdict

## Example

```yaml
- role: general_ludd.governance.governance_navigator
  vars:
    governance_navigator_enabled: true
    governance_navigator_country: DE
    governance_navigator_query: "What currency does Germany use and which parliament governs it?"
```
This routes to both `tax_currency` and `governing_bodies` for `DE`.
