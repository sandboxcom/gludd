# Contributing

See [README.md](README.md) for project overview and setup.

## Development Setup

```bash
make init        # Set up dependencies + pre-commit hooks
make gate        # Run full quality gate (lint, typecheck, test, smoke)
```

## Workflow

1. **TDD**: Write a failing test first (`tests/`), confirm it fails (`make test-unit`), then implement.
2. **Feature branches**: `make feature-start MSG='feature/my-branch'`
3. **Commit**: `make git-commit MSG='descriptive message'` — requires green gate.
4. **Merge**: `make feature-done MSG='feature/my-branch'` — runs tests then merges with `--no-ff`.

## Code Style

- Python 3.11+, formatted with ruff (`make lint-fix`).
- Type annotations required; mypy threshold enforced.
- No `# noqa`, `# type: ignore`, or `# nosec` without documented justification.
- Use existing libraries; don't reinvent mature OSS tools.

## Testing

- `make test-unit` — unit tests (fast, no I/O)
- `make test-integration` — multi-subsystem tests
- `make test-e2e` — daemon-level tests
- `make gate` — full quality gate before commit

## Guardrails

This project uses opencode's three-layer guardrail system:
1. **opencode.json** — hard permission gates
2. **.opencode/plugin/enforce-make.ts** — runtime hooks
3. **AGENTS.md** — prompt-level rules

See `.opencode/skills/guardrail-pattern/SKILL.md` for the pattern reference.
