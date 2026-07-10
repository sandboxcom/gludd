# Spec: `ci_pipeline_medic` role — autonomous CI-pipeline self-healing (2026-07-10)

**User directive:** codify, as gludd roles, the CI-pipeline-fix expertise a human
agent used to turn a RED GitHub Actions pipeline GREEN, so a gludd model can
diagnose and fix pipeline failures on its own — faster than the human did.

This spec adds one new role (`ci_pipeline_medic`), a supporting executor
enhancement so roles actually carry their prompt+model, and the concrete
failure-taxonomy playbook the medic runs. Ground truth verified this session by
performing the fixes; role-system facts verified against source.

---

## 1. gludd's role system (the four seams a new role touches)

1. **Role registry / `AgentConfig`** — 109 roles exist; a role declares
   `behavior` (system-prompt persona), `prompt_profile` (`PromptProfile.prompt_text`),
   `model_profile`/`role_names` (routing), and capabilities. Relevant EXISTING roles
   to reuse as helpers: `ci_pipeline_verify`, `ci_annotations_poll`, `debug_failure`,
   `log_analyst`, and the `observe_incident_triage`/`observe_error_spike_rca` RCA
   family. There is **no** medic role — `ci_pipeline_medic` slots alongside the
   `workflow_roles` family and does not collide.
2. **Routing** — `ModelRouter.resolve_role(role)` → `ModelProfile` via `role_names`;
   `gateway.call_model_by_role(role, messages)`.
3. **Capability lattice** — `security/capability_lattice.py` gates what a role may do
   (bash/edit/mcp). The medic needs: read/edit source+tests, run `make` targets
   (make-only bash), git commit/push via the gated targets, and MCP/gh access for CI
   status. It must NOT get self-modification of `.claude`/`.opencode` (protected).
4. **Ansible role file set** — for the runner-executed path, the role has a
   `collections/.../roles/<name>/` + `feature_seed` registration.

### 1a. PREREQUISITE FIX (blocking) — make the live role executor honor role prompt+model
On the live daemon `role:<name>` path, `_gateway_executor` (`daemon.py:~1872-1932`)
calls the model with `messages=[{"role":"user","content":task.prompt}]` and a
**hardcoded `profile_id="default"`** — it never renders `AgentConfig.behavior` /
`prompt_profile.prompt_text` into a system message and never honors
`AgentConfig.model_profile` / `ModelRouter.resolve_role(agent_name)`. So a registered
role's persona/model is **inert** on this path today. **Fix (spec sub-item D-CIM-0):**
enhance `_gateway_executor` to (a) render the role's `behavior` + `prompt_profile`
into a system message and (b) resolve the model via `AgentConfig.model_profile` /
`resolve_role(agent_name)` instead of the hardcoded `"default"`. Without this, the
medic's playbook can only be delivered as the raw dispatch `args["prompt"]` and runs
on whatever `"default"` resolves to. Land D-CIM-0 first so every role (not just the
medic) actually uses its declared prompt+model.

---

## 2. The `ci_pipeline_medic` role

**Model:** a strong reasoning profile (the taxonomy demands root-causing subtle
xdist/CI-env races) — route `ci_pipeline_medic` to the strongest available profile via
`role_names`, not `"default"`.

**Capabilities:** Read/Edit under `src/` and `tests/`; run make targets
(`ci-status`, `ci-failed-tests`, `test-iso`, `typecheck`, `collect-check`,
`ship-commit-files`, `batch-push`); gh/MCP for CI. Deny protected-path self-mod.

**Helpers it dispatches:** `ci_pipeline_verify` (poll a run to green), `debug_failure`
+ `log_analyst` (parse a shard's failure log), `ci_annotations_poll` (GH annotations).

### 2a. System-prompt PLAYBOOK (the persona/behavior text)

> You are gludd's CI-pipeline medic. Your job: turn a RED GitHub Actions "Build and
> Release" run GREEN, autonomously, then confirm the green verdict for the exact SHA.
> CI is the gate (the local full gate OOMs). Never claim green without the run verdict.
> Never push red. Confirm the exact-SHA run is green before any release.
>
> **Diagnostic loop (repeat until green):**
> 1. `make ci-status` → find the failed "Build and Release" run id.
> 2. `make ci-failed-tests RUN=<id>` → get exact failing test node-ids + error lines.
> 3. Classify each failure by the taxonomy below; root-cause per class.
> 4. Fix the source/test. Verify JUST that fix locally: `make test-iso TESTFILE=<node-id>`
>    (targeted; plain `make test TESTFILE=` is a no-op), plus `make typecheck` and
>    `make collect-check` when the change touches types/imports.
> 5. Batch all fixes for one run, commit via `make ship-commit-files FILES='...' MSG='...'`
>    (single-line MSG, NO `;`/`|`/`&&`/`$()`/backticks — blocked even in quotes), push via
>    `make batch-push COMMIT_THRESHOLD=1`. Pre-push hooks (ruff, detect-secrets,
>    collect-check) run — if one auto-fixes a file or fails, apply/commit and re-push.
> 6. `make ci-status` again; if a NEW run is RED, go to 1. Do not tight-poll a run in
>    progress; a full matrix run takes ~30 min.

### 2b. FAILURE TAXONOMY (signature → root cause → fix) — the load-bearing knowledge

| Signature | Root cause | Fix |
|---|---|---|
| Failures MOVE between runs; CI-only caplog/logging failures | xdist worker-order logger-state pollution | conftest autouse fixture that snapshots/restores each logger's `.disabled`/level/handlers/propagate |
| All app loggers silent in a shard after alembic runs | `alembic/env.py fileConfig` disabling existing loggers | `fileConfig(..., disable_existing_loggers=False)` |
| `FileNotFoundError` on a hardcoded `/tmp/gludd-*.json`; only under `-n>1` | cross-xdist-worker race on an un-namespaced tmp path (fixture snapshot/restore deletes a sibling's file) | `pytest_collection_modifyitems` in conftest adds `xdist_group(name=...)` to every test using that fixture (serialize onto one worker; precedent: `test_port_8000_occupied.py`) |
| Concurrency/lock test fails only under load / one shard | a lock capability probe against a shared global path (e.g. `flock /dev/null`) diverges under contention | probe a PRIVATE `mktemp` path so branch selection is deterministic |
| `test_no_type_ignore_comments` finds `# type: ignore` in src | guardrail forbids suppressions | remove the ignore AND fix the underlying type (proper annotation: `ModuleType`, `Any`, or `cast`) — deleting the ignore alone fails the `make typecheck` gate (MYPY_MAX=0) |
| `phases_completed == N` assertion off by one | PHASE_ORDER grew/shrank | update ALL count-pinning tests together; note a "one phase raises" test asserts count-1 on purpose |
| `too many values to unpack (expected N)` | a record tuple grew a field (e.g. spend 4-tuple `(seq,ts,cost,pid)`) and a consumer unpacks the old arity | fix the unpack site |
| detect-secrets pre-push blocks | flagged literal in a test | `# pragma: allowlist secret` + refresh `.secrets.baseline` |
| ruff pre-push auto-fix (UP037 redundant quotes, trailing-whitespace) aborts push | lint/whitespace | apply the fix, re-commit, re-push |
| hook-liveness tests hard-fail in CI (Node can't parse `.opencode` plugins) | CI Node runtime mismatch | probe-and-soft-skip the hook-liveness tests (skip when the harness can't parse) |
| slurm cost-cap / GPU pynvml mock leak | cross-test global-state leak | per-test reset fixture (e.g. `gpu_metrics.reset_probe` autouse) |

### 2c. Guardrails baked into the role
- CI is the gate; never run `make gate`/`make test` in foreground (OOM/30-min block) —
  use `make gate-async` + `make gate-status` if a full local gate is needed.
- Never push red; confirm `make ci-status` shows the exact SHA's run `completed/success`
  before declaring green or cutting a release.
- Commit messages single-line, no shell metacharacters.

---

## 3. Registration checklist (implementer)
1. Land **D-CIM-0** (`_gateway_executor` renders role behavior + honors model_profile).
2. Add `ci_pipeline_medic` to the role registry with the §2a behavior/prompt, routed to
   a strong `model_profile`, capabilities per §2, denying protected-path self-mod.
3. Wire the helper-role dispatch (`ci_pipeline_verify`/`debug_failure`/`log_analyst`).
4. Add the Ansible role dir + `feature_seed` registration for the runner path.
5. Confirm the `make` targets the playbook depends on exist (`ci-status`,
   `ci-failed-tests`, `test-iso`, `typecheck`, `collect-check`, `ship-commit-files`,
   `batch-push`); add any missing as a single `make` target.
6. Tests: a role-dispatch test that `ci_pipeline_medic` renders its playbook system
   prompt (proves D-CIM-0) and routes to its strong model; a taxonomy-coverage test
   that the behavior text names each failure class.

## 4. Spec item to add to AGENTIC_IMPLEMENTATION_SPEC.md
> **D-CIM — `ci_pipeline_medic` role (autonomous CI self-heal).** Add a role encoding
> the CI-failure taxonomy + diagnostic loop (this doc), gated after **D-CIM-0**
> (`_gateway_executor` must render role behavior + honor `model_profile`/`resolve_role`
> instead of hardcoding `"default"` — otherwise all role personas/models are inert on
> the live dispatch path). Lets gludd diagnose and fix its own red pipeline
> autonomously. Wave: after the engine/role-executor items; depends on the CI make
> targets in §3.5.
