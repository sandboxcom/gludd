# validate_and_push

Codifies the recurring "make validate → push if green" workflow.

## What it does

1. Gathers live daemon facts (`gludd_facts`) for system context.
2. Runs `make validate` (unless `enable_validate: false` or check_mode).
3. Evaluates push eligibility: validation must pass AND `enable_push: true` AND not check_mode.
4. Pushes to remote (`make push_make_target`) only when all three conditions are met.
5. Writes `validate_and_push.json` + `validate_and_push.md` artifacts.
6. Sends `gludd_message` to `handoff_recipient` with outcome (optional).

## Outcome States

| Outcome | Meaning |
|---|---|
| `validation_failed` | Validation failed; push blocked |
| `validated_no_push` | Validation passed; push not requested |
| `validated_dry_run` | Validation passed; push skipped (check_mode) |
| `pushed` | Validation passed and push succeeded |
| `push_failed` | Validation passed but push failed |

## Safety model

- `enable_push: false` (default) — push never runs
- Push also blocked if: validation failed, check_mode active
- Validation can be skipped with `enable_validate: false` + `validate_output_override`

## Key variables

| Variable | Default | Description |
|---|---|---|
| `enable_push` | `false` | Set true to push after successful validation |
| `enable_validate` | `true` | Set false to skip validation (use with override) |
| `validate_make_target` | `"validate"` | Make target for validation |
| `push_make_target` | `"git-push-sandboxcom"` | Make target for push |
| `validate_output_override` | `"Full validation passed..."` | Used when validate doesn't run |
