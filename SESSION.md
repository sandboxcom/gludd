# Session State

> Authoritative state: `make gate` output and `TASKS.md` evidence.
> SESSION.md is derived from gate output, not the other way around.
> IF THIS DISAGREES WITH `make gate`, THE GATE IS CORRECT.

## Last Updated
- 2026-06-11 (GLM_REMEDIATION_GUIDE_2.md — V0-V2.1 completed)

## Current Gate Status (2026-06-11)
- **Lint**: 0 errors
- **Typecheck**: 18 errors in 9 files (ratchet baseline: 18)
- **Collect**: 0 errors, 5,667 collected
- **Tests**: 5,544 passed, 0 failed, 30 skipped, 93 strict-xfailed
- **Smoke**: PASS (trap cleanup on failure)
- **Latest commit**: 506ed44 — V2.1+V1.8 (gateway executor + guardrail de-recurse)

## Phase V0 — Honest Green Gate: COMPLETE
- V0.1: 42 failures fixed (b09e4ce)
- V0.2: Smoke green + trap cleanup (60cdb4d)
- V0.3: Truth targets fixed — AND logic, freshness, epoch (bd87fa5)
- V0.4: Strict xfail ratchet — 93 xfailed, tolerances deleted (237123f)

## Phase V1 — Guardrails Round 2: COMPLETE
- V1.2: Stop-pattern sharpening + ratchet state check (2c9e33c)
- V1.2b: Anti-stop fuzz test — auto-parses BUGS.md, variant generation (a1c1185)
- V1.3: Smoke wired into gate + validate (306512e)
- V1.4: make init installs pre-commit hooks (a7e4ac0)
- V1.5: status-snapshot target (fe66e7f)
- V1.6: audit-evidence target (fe66e7f)
- V1.7: CI gate job with Python 3.11/3.12 matrix, version injection (f9e220f)
- V1.8: De-recurse guardrail self-test (b41684a)

## Phase V2 — In Progress
- V2.1: H5 gateway-backed executor wired in daemon lifespan (506ed44)
- V2.4: SESSION.md corrections (this update)
- V2.5: dist/sbom-test.json untracked, make untrack target (b41684a)
- V2.2, V2.3, V2.6: pending

## Known Gaps
- 93 pre-existing test failures tracked in config/ratchet.yml as strict-xfail
- 18 pre-existing mypy errors tracked in gate ratchet (≤18)
- 93 ratchet entries = known-unfixed work; gate reports test PASS 0 only because of strict-xfail
- V2.3: 171+ hardcoded port-8000 references need ephemeral port conversion
- V2.6: C0-C5/H4/H6 spine unimplemented
- V3-V4: OSS replacements + shipping readiness

## Corrections
- **ModelFailoverChain**: NOT dead code — `ModelGateway.call_model_with_retry()` walks `fallback_profiles`. See gateway.py:255-327.
- **R3.5**: Smoke is now wired into `gate` and `validate` (V1.3).
- All counts updated from current `make gate` output.
