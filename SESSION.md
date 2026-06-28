# Session State

> Authoritative state: `make gate` output and `TASKS.md` evidence.
> SESSION.md is derived from gate output, not the other way around.
> IF THIS DISAGREES WITH `make gate`, THE GATE IS CORRECT.

## Last Updated
- 2026-06-28

## Current Work

- Landed plugin throttle fix (5cb6cb7): task-deadline warnings fire once per task, stops UI flood; added task TTL clear + 2 tests.
- Landed ansible layout migration (7d0ed12): single-collection-home, root `roles/` deleted, 2 playbooks converted to FQCN, `roles_path` dropped, +5 test guardrails.
- Landed lint/mypy debt cleanup (b7a5f5b): ruff 35→0, mypy 8→0, removed stale `noqa: BLE001`, dropped unused `F841` vars, `ClassVar` for tarball binaries, narrowed `int | None` pid, `isfinite None` guard, `pidlist` alias.
- Landed session-start orchestration contract + opencode.json schema guard (this commit):
  - New plugin `.opencode/plugin/enforce-session-start.ts` — PREPENDS a `🚨 SESSION-START DIRECTIVE` block as the FIRST section of the system prompt, naming TASKS.md/BUGS.md/ratchet.yml/SESSION.md as mandatory parallel reads and requiring a ≥10-wide subagent wave as action 2 (no prose between). Opt-in `tool.execute.before` hard gate via `GLUDD_SESSION_START_ENFORCE=1`. Registered in `opencode.json`.
  - New test `tests/unit/test_opencode_json_schema.py` — allowlist of all 35 schema-allowed top-level keys, asserts no unknown keys, regression-marker for the `env` breakage.
  - New test `tests/unit/test_session_start_plugin.py` — 15 tests pinning the directive-injection + opt-in hard-gate shape.
  - PreToolUse guard in `.opencode/plugin/enforce-make.ts` denies Write/Edit to `opencode.json` with unknown top-level keys.
  - New make target `validate-opencode-config`, wired as a `gate` prerequisite (runs first).
  - 2 new BUGS.md incidents (2026-06-28): top-level `env` key silently dropped by `additionalProperties: false`; agent answering first session prompt with prose instead of dispatching.
  - Merged AGENTS.md session-start sections into a single `## CRITICAL: Session-Start Orchestration Contract` section.
  - Drive-by: removed F401 unused import in `src/general_ludd/routers/render.py` (was blocking `make lint`).

## Last Commit
- See `make git-log` — this session's commit lands the session-start contract + schema guard (committed under `GLUDD_CI_IS_GATE=1`).

## Known Gaps

1. **Local commits still unpushed** (pending `make git-push-sandboxcom`): prior session work plus this commit.
2. **Full `make gate` (40 min) NOT run locally** this session — committed under the `GLUDD_CI_IS_GATE=1` exception (AGENTS.md "No-Commit-Bypass Policy → CI-as-Gate Override"). CI will validate on push.
3. **README.md alpha.3 → alpha.5 update in flight** (concurrent task — do not bump version here).
4. **`.secrets.baseline` has an uncommitted change** (hash regeneration only, not user-actionable).
5. **F1–F4 queue-lease fixes lack TASKS.md evidence rows** — need entries for bba8c92, 4e13936, 6e684b4, 14ee691.

## Next Steps

1. Push local commits to remote: `make git-push-sandboxcom`.
2. Verify CI green on the new tip: `make ci-verdict BRANCH=master` (headSha must match local tip + conclusion: success).
3. Add TASKS.md evidence rows for F1–F4 and the session commits still missing entries.
4. If cutting a release, update README status table (`make check-readme-status TAG=...`) then `make release-cut`.

## Current Gate Status (2026-06-28, targeted only)

<!-- gate:begin -->
- lint PASS 0          (`make lint` → All checks passed!)
- typecheck PASS 0     (`make typecheck` → Success)
- collect PASS 0       (`make collect-check` → Collection OK)
- test: 208 passed / 0 failed / 0 collection errors across targeted suites
  (`test_opencode_json_schema.py` 4, `test_session_start_plugin.py` 15,
  `test_anti_stop_fuzz.py` 8, `test_plugin_behavior.py` 61,
  `test_guardrails.py` 63/1skip, `test_opencode_plugin_ports.py` 57)
- smoke: NOT RUN
<!-- gate:end -->

> NOTE: Full `make gate` (40 min) NOT run locally this session — committed
> under the `GLUDD_CI_IS_GATE=1` exception (AGENTS.md "No-Commit-Bypass Policy →
> CI-as-Gate Override"). Only the targeted phases above were verified.

## Historical State

- 2026-06-28: session landed 3 commits locally (plugin throttle, layout migration, lint/mypy cleanup) on top of the unpushed F1–F4 queue-lease fixes.
- 2026-06-26: master advanced to `171946b` (merge of `feature/alpha4-green-the-gate`); CI later FAILED with 35 lint errors (now fixed locally).
- 2026-06-24: master at `d4f684d`; ratchet cleared 93→0; gate green (lint/typecheck/collect/test/smoke all PASS, 284+ tests).

## Multitasking Bugs

Floor-breach root cause analysis: `docs/audit/floor_breach_rootcause_2026-06-17.md`.
Floor raised 6→10 on 2026-06-22. Mitigations codified in AGENTS.md
"Steady-state dispatch" + `enforce-floor.ts` / `enforce-delegate.ts` plugins.

## Dead Code

Prior audit resolved: legacy orchestration shim deleted (no `src/` imports remain);
`pricing_intel` fully wired (`daemon.py`, `controllers/spend_limiter.py`,
`infra/pricing.py`, `routers/observe.py`). No outstanding dead-code gaps.
