# sprint_plan

Selects backlog todos into a sprint using real capacity-fit math.

## Capacity Derivation

- If `sprint_capacity > 0`: use it directly
- If `sprint_capacity == 0`: derive from `max(5, round(total_runs * success_rate))` from facts.history

## Selection Algorithm

Iterates backlog items in order, adds to sprint if `running_points + item_points <= capacity`, else moves to spillover. Total points is the real sum.

## Variables

| Variable | Default | Description |
|---|---|---|
| `sprint_name` | `Sprint-1` | Sprint identifier |
| `sprint_capacity` | `0` | Points capacity (0 = derive from history) |
| `write_back` | `false` | Write selection back to gludd_db |
| `artifact_dir` | `/tmp/gludd-sprint-plan` | Output directory |

## Artifacts

- `sprint_plan.json` — selected[], total_points (≤ capacity), capacity, spillover[]
- `sprint_plan.md` — markdown sprint card
