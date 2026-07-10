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

---

## CI failure taxonomy — codified roles (2026-07-10 session)

Eight failure CLASSES were actually hit and fixed this session (evidence: commits
`6f5b9222`, `20cbc0e4`, `7624be03`, the `2543152b` alembic/A1-A11 batch in
`docs/AGENTIC_IMPLEMENTATION_SPEC.md` §3.1, and code read directly from
`scripts/gate_async.sh`, `src/general_ludd/controllers/spend_limiter.py`,
`src/general_ludd/infra/gpu_metrics.py`, `tests/unit/test_type_safety_guardrails.py`,
`tests/unit/test_slurm_arg_hardening.py`, `Dockerfile`, `.github/workflows/build.yml`).
Each becomes a `ci_pipeline_medic` sub-role: same registry family (§1 above), same
capability lattice, same diagnostic-loop shell (§2a), differing only in the
classification signature and fix pattern below. All eight share one diagnostic
primitive:

**`make ci-failed-tests RUN=<id>` is the mandated first diagnostic step, never
`make ci-faillog RUN=<id>`.** Confirmed by reading both Makefile recipes:
`ci-faillog` runs `gh run view "$RUN" -R sandboxcom/gludd --log-failed | tail -120`
— a hard 120-line tail that silently truncates a multi-shard/multi-python-version
log, so a LATER job (e.g. the Python 3.11 leg, or a shard alphabetically later than
whatever fits in the last 120 lines) can be completely absent from the output with
no indication anything was cut. `ci-failed-tests` instead runs
`gh run view -R sandboxcom/gludd --log-failed | grep -E 'FAILED tests/|ERROR tests/|= .*(failed|error).* =' | sort -u`
over the FULL log — every job's failing node-ids surface regardless of log length.
A medic role that reaches for `ci-faillog` first will diagnose only whatever
happened to survive the tail and can miss an entire failing job/python-version.

### Role: `ci_medic_xdist_tmp_race`

**Trigger/detection:** in `gh run view --log-failed`, a `FileNotFoundError`,
`json.JSONDecodeError`, or an assertion on a counter/state value, appearing under
`-n>1` xdist output, on ONE python version only (3.11 xfail, 3.12 pass, or vice
versa) OR moving to a DIFFERENT test between consecutive CI runs of the identical
diff.
**Root-cause signature:** a hook/module writes to a hardcoded path shared across
all xdist workers — either an absolute `/tmp/gludd-*.json` (hook counters:
block-counter, block-reason, stop-tool-counts, text-complete) or a CWD-relative
repo-root file (`.gate-status`, `.gate-background.pid`). Two workers running the
same test file (or a fixture teardown in one worker vs a fresh run in another)
race on that single path: one worker's cleanup unlinks the file mid-read by
another.
**Diagnosis steps:**
1. `make ci-failed-tests RUN=<id>` → confirm the failure is `FileNotFoundError`/
   `KeyError`/a stale-counter assertion, and that it is NOT reproducible via
   `make test-iso TESTFILE=<file>` (single-worker) — only via
   `make test-xdist TESTFILE=<file> ID=<uniq>` (multi-worker). Non-reproduction
   under `test-iso` but reproduction under `test-xdist` is the confirming signature.
2. Read the fixture/module under test for a literal `/tmp/gludd-` string or a bare
   relative filename (`Path(".gate-status")` etc.) with no env-var indirection.
3. Check whether an env-override already exists (`os.environ.get("GLUDD_X_STATE", "/tmp/gludd-x.json")`
   pattern, per the `STATE_FILE_ENV_VARS` convention) — if not, that's the fix site.
**Fix pattern:** add an env-override to the module/plugin (`process.env.X ||
"/tmp/..."` in TS hooks, `os.environ.get("GLUDD_..._STATE", "/tmp/gludd-....json")`
in Python) keeping the literal as the production fallback (structural tests that
assert the literal string still pass), then have the test fixture set that env var
to a per-test `tmp_path` — OR, where the module hardcodes a path with no seam,
monkeypatch the module's path GLOBAL directly to `tmp_path` in an autouse fixture.
Precedent commits: `6f5b9222` (env-redirect block-counter/reason/tool-counts hook
paths), `20cbc0e4` (tmp_path-isolate gate-cleanup watchdog tests).
**Verification:** `make test-xdist TESTFILE=<file> ID=<uniq>` green on repeated runs
(run it 2-3x — a race can still pass by luck once); `make ci-failed-tests RUN=<id>`
clean on both 3.11 and 3.12 after push.

### Role: `ci_medic_log_state_pollution`

**Trigger/detection:** `caplog` assertion failures (`assert "..." in caplog.text`
fails, or caplog.text is empty) that are CI-only, appear on only one python
version, or change WHICH test fails between runs of the same diff — never
reproducible by running the failing file alone.
**Root-cause signature:** process-global logging state (handlers, `propagate`,
`disabled`, level) is mutated by whichever test/module runs first in an xdist
worker and never restored, so a LATER test in that worker inherits polluted
logger state.

**CORRECTION (2026-07-10 night-2, verified by direct read): the alembic
`fileConfig` cause is STALE — it is ALREADY FIXED in-tree, not an open
diagnosis target.** `alembic/env.py:18` already calls
`fileConfig(config.config_file_name, disable_existing_loggers=False)` (with
the rationale comment at `:13-17` explaining exactly this hazard), and two
independent guards already lock it in: `tests/conftest.py:131-197`
`_isolate_root_logger` (an autouse fixture that snapshots/restores
level/propagate/handlers/`.disabled` for the root logger AND every named
logger in `logging.Logger.manager.loggerDict`, specifically calling out
`fileConfig`'s `disable_existing_loggers` default as one of the hazards it
guards against) and `tests/unit/test_alembic_config.py` (regression-pins that
`alembic.ini` parses under `fileConfig` and documents the
`disable_existing_loggers=False` requirement inline). A model using this
role must NOT spend diagnostic effort re-discovering or re-fixing the
alembic `fileConfig` cause — treat it as closed and move straight to the
residual class below.

**Residual target (this is what the role should actually hunt for now):** any
OTHER site that mutates global logging/handler state without a
snapshot/restore — e.g. a test or module that calls
`logging.disable(...)`, reassigns `logger.handlers`, flips `.propagate`/
`.disabled` on a shared logger, or calls a THIRD-PARTY library's own
`fileConfig`/`dictConfig`-style setup — and leaves it mutated for whatever
test runs next in the same xdist worker. `_isolate_root_logger` is a
broad-spectrum backstop for this whole class, not just the alembic instance;
if a caplog failure of this shape recurs after `_isolate_root_logger` landed,
the new culprit is a state channel the fixture's snapshot doesn't cover
(e.g. a C-extension/module-level global outside `logging.Logger.manager`,
or a logger created and polluted AFTER the fixture's yield in a way that
escapes its "reset new loggers to defaults" pass) — diagnose that specific
gap, don't assume the fixture is absent.
**Diagnosis steps:**
1. `make ci-failed-tests RUN=<id>` → collect every caplog-failing node id across all
   shards (not just the first one printed).
2. Confirm `_isolate_root_logger` (`tests/conftest.py:131-197`) is actually active
   for the failing test (not opted out via a marker/override) before assuming a
   NEW pollution source — it already covers the alembic class and the general
   snapshot/restore pattern.
3. Reproduce order-dependence: `make test-xdist TESTFILE='<failing_file> <suspect_file>' ID=<uniq>`
   — pair the failing test with candidates that reconfigure logging, import a
   library with its own `fileConfig`/`dictConfig` call, or run early
   alphabetically in the same shard.
4. Grep (`make grep Q='fileConfig'` or `make grep Q='dictConfig'`) for any
   config-driven logging setup in the tree or its dependencies — check whether
   its state-mutating effect is covered by `_isolate_root_logger`'s snapshot
   surface or falls outside it.
**Fix pattern:** the alembic-class fix (`fileConfig(...,
disable_existing_loggers=False)`) is DONE — do not re-apply it. For a newly
found residual case: if it's a state channel `_isolate_root_logger` already
snapshots, the fixture should already contain it (bug is elsewhere or in test
ordering, not a snapshot gap); if it's a channel outside the fixture's
snapshot surface, extend `_isolate_root_logger` to snapshot/restore that
channel too, following its existing pattern (snapshot before yield, restore
after, reset newly-created loggers to defaults). `xdist_group` serialization
alone is NOT sufficient (it only forces two tests onto the same worker in the
same order; it does not stop one from polluting the other's global state).
**Verification:** the previously-failing set (fixed 2026-07-10 by the
`_isolate_root_logger` + `alembic/env.py:18` fix, now a closed regression
baseline — re-verify these still pass rather than re-diagnosing them from
scratch):
`tests/unit/test_worker_broadcast_401.py`, `test_worker_broadcast_psk.py`,
`test_worker_build_gateway.py`, `test_model_registry.py::test_download_unpinned_warns`,
`tests/security/test_daemon_auth_redteam.py::TestA3NoAuthDegraded::*`,
`test_spend_limiter_dispatch_wiring.py`, `test_webhook_fire_tracking.py`) all pass
in-shard on CI, both pythons; any NEW caplog-pollution failure found after this
correction is, by definition, the residual class above, not the alembic
instance.

### Role: `ci_medic_pushtime_lint`

**Trigger/detection:** `make git-commit`/`make ship-commit-files` succeeds locally
(it only runs `collect-check`), but the subsequent push is rejected/fails, or a
push-time pre-push hook auto-fixes a file and aborts the push — ruff `E501` (line
>120 cols), `F401` (unused import), `RUF012` (mutable class-attribute default
needing `ClassVar`), or a missing end-of-file newline.
**Root-cause signature:** the gated commit path only checks collection, not lint —
lint drift accumulates silently until push time, where a stricter/full-repo ruff
pass (or the pre-push hook) catches it.
**Diagnosis steps:**
1. `make ci-failed-tests RUN=<id>` (if it reached CI) or read the local push-hook
   output directly — ruff prints the file:line:code for every violation.
2. `make lint` locally to reproduce the exact violation set before touching files.
**Fix pattern:** wrap long lines under 120 cols; delete unused imports (or use them/
prefix with `_` if intentionally re-exported); annotate mutable class defaults as
`ClassVar[...]`; ensure trailing newline. Precedent commit `7624be03` (ruff E501/
F401/RUF012 + EOF fix across web-retrieval and SSTI test files). Structural fix
(the ROLE, not just the one-off): a pre-push lint-gate step that runs `make lint`
and auto-fixes trivial ruff violations (`make lint-fix`) BEFORE allowing the push
to proceed, so this class stops reaching CI at all.
**Verification:** `make lint` clean locally; push succeeds; `make ci-failed-tests RUN=<id>`
shows no lint-stage failure on the new run.

### Role: `ci_medic_collection_mask`

**Trigger/detection:** `make test-failures` reports "No failures" while the suite
is actually broken, or a push reds CI at the collection stage with zero test
results reported for an entire shard.
**Root-cause signature:** a stray/half-written test file for not-yet-implemented
code (e.g. a test-drafting subagent wrote a `tests/...` file directly instead of
returning proposed tests as text) breaks `pytest --co` for the whole shard, and
the historical `test-failures` target does not distinguish a collection ERROR from
"zero failures" — both look silent.
**Diagnosis steps:**
1. `make ci-failed-tests RUN=<id>` — a collection error surfaces as an `ERROR
   tests/...` line, not `FAILED`; treat any `ERROR` line as HIGHER priority than
   `FAILED` lines (it can mask an entire shard's real failures).
2. `make test-count` (or `make collect-check`) locally on the exact SHA — a
   collection error there reproduces the CI symptom deterministically and cheaply
   (no need to wait on a CI run).
**Fix pattern:** delete/fix the offending file so `pytest --co -q` exits 0; going
forward, test-draft/proposal subagents must return proposed tests as TEXT in
their report, never as files written directly into `tests/`, so a draft can never
break collection before human/gated review.
**Verification:** `make test-count` (or `collect-check`) exits 0 with the expected
collected-test count before any commit; never trust `make test-failures` alone on
a suite you haven't confirmed collects cleanly.

### Role: `ci_medic_type_ignore_ratchet`

**Trigger/detection:** `tests/unit/test_type_safety_guardrails.py::test_no_type_ignore_comments`
fails (a `# type: ignore` comment exists somewhere under `src/`), OR removing that
comment to satisfy the guardrail makes `make typecheck` (mypy) go red.
**Root-cause signature:** `test_no_type_ignore_comments` (guardrails.py:45-55) is a
hard ZERO-tolerance regex scan (`#\s*type:\s*ignore`) over every `src/**/*.py` file
except the allowlisted `security/fix_not_disable.py` — there is no count/baseline
to bump, only pass/fail. Separately, the mypy error-COUNT ratchet is
`MYPY_MAX` (`Makefile:6`, currently `0`), enforced in the gate/gate-lite recipes by
counting `error:` lines from `mypy src` and comparing against it (`Makefile:341-343`,
`:404`). These are two DIFFERENT guardrails that both key off "no suppressions":
deleting a `# type: ignore` can flip `test_no_type_ignore_comments` green while
making the underlying real type error surface and push `mypy`'s error count above
`MYPY_MAX`.
**Diagnosis steps:**
1. `make ci-failed-tests RUN=<id>` → identify whether the red is the guardrail test
   itself or a subsequent `make typecheck`/gate mypy-count failure (both can appear
   in the same run if a fix is half-applied).
2. `make typecheck` locally on the file that carried the ignore to see the real
   mypy error it was suppressing.
**Fix pattern:** never just delete the comment — restructure so the code
typechecks WITHOUT it: narrow with a proper `cast(...)` to a concrete type (not
`cast(Any, ...)`, which is itself banned by a sibling guardrail), a `TYPE_CHECKING`-
gated import + `ModuleType` annotation, an `isinstance` narrowing guard, or a
`Protocol`. Verify BOTH guardrails together, not just one.
**Verification:** `make test-iso TESTFILE=tests/unit/test_type_safety_guardrails.py::test_no_type_ignore_comments`
passes AND `make typecheck` shows the same or lower error count against `MYPY_MAX=0`.

### Role: `ci_medic_tuple_arity_drift`

**Trigger/detection:** `TypeError: too many values to unpack (expected N)` (or "not
enough values to unpack") at a record-consumption site — a persisted/serialized
record (spend ledger, event tuple, snapshot row) gained or lost a field and a
fixed-arity unpack (`a, b, c = record`) at some OTHER call site wasn't updated.
**Root-cause signature:** a record shape is produced in more than one place
(current-process append vs. a persisted/restored snapshot) and only one of the
producer/consumer pairs was updated when the shape changed. Concrete precedent
already in the tree (not a live bug, but the exact shape of the hazard this role
exists to catch): `src/general_ludd/controllers/spend_limiter.py:94-98` stores an
INTERNAL 4-tuple `(seq, ts, cost_usd, project_id)` per record (the `seq` field
added for the SPD-1 flush watermark), while the PUBLIC/persisted shape via
`snapshot()`/`restore()` is a 3-tuple `(timestamp, cost_usd, project_id)` — the
module explicitly documents this split and `restore()` branches on
`len(rec) == 2` vs `== 3` specifically so old 2-tuple snapshots (pre-project_id)
and new 3-tuples both restore correctly, rather than a single fixed-arity unpack
that would break on either shape.
**Diagnosis steps:**
1. `make ci-failed-tests RUN=<id>` → the traceback pinpoints the exact unpack line
   and the actual tuple length received.
2. Read the record's PRODUCER (append/dataclass/repository-row-to-tuple site) to
   see the current arity, then every CONSUMER (unpack sites, `len()` branches,
   serialization) to find the one still pinned to the old arity.
**Fix pattern:** update the stale unpack site to the new arity — prefer unpacking
by explicit `len()` branch (mirroring the `spend_limiter.restore()` pattern) or
named-field access (dataclass/NamedTuple) over positional unpack when a shape has
already drifted once, so the NEXT field addition can't silently break a consumer.
**Verification:** `make test-iso TESTFILE=<consumer's test file>` passes; add/extend
a round-trip test (`snapshot()` → `restore()` → assert equivalent state) covering
both the old and new arities if backward-compat matters.

### Role: `ci_medic_gate_lock_race`

**Trigger/detection:** the gate-async double-launch guard flakes — a concurrent
`make gate-async` invocation that SHOULD be refused ("another gate-async is
already running") instead launches a second gate, or a legitimate refusal is
inconsistent between local runs and CI/load.
**Root-cause signature:** `scripts/gate_async.sh`'s `_has_gnu_flock` probe
originally (hypothetically) tested lock capability against a SHARED global path
(e.g. `/dev/null`); under contention (concurrent xdist workers, or the two
processes in the concurrent-refusal test itself) multiple callers can transiently
fail that shared probe and diverge into the PID-file fallback branch, which holds
no kernel lock — breaking mutual exclusion exactly when it matters most (under
load). The FIXED code (already in tree, `scripts/gate_async.sh:38-50`) probes a
PRIVATE `mktemp` path per invocation instead, making the GNU-flock-vs-PID-file
branch selection deterministic regardless of contention.
**Diagnosis steps:**
1. `make ci-failed-tests RUN=<id>` → find the concurrency/lock test's failure
   (`tests/unit/test_gate_async.py` — the "(d) a second concurrent gate-async
   launch is refused by the flock" case).
2. Read `scripts/gate_async.sh`'s `_has_gnu_flock`/`_acquire_lock`: confirm the
   probe path is a fresh `mktemp` result, not a shared literal path.
**Fix pattern:** probe lock capability against a private, freshly-created path
(`mktemp`) rather than a shared global one, so branch selection can never depend
on contention from other concurrent callers. Apply the same rule to any OTHER
flock-based guard added later (e.g. `.gate-background.pid`'s PID-file guard).
**Verification:** `make test-iso TESTFILE=tests/unit/test_gate_async.py` passes,
including the concurrent-refusal case, run several times in a row (flakes need
repetition to falsify).

### Role: `ci_medic_infra_red`

**Trigger/detection:** a red job that is NOT a code-logic regression — it reds
because of test/CI infrastructure: GPU-metrics mock state leaking across tests,
a slurm subprocess call hanging with no timeout, the coverage-merge job dying on
missing shard data, a Docker build failing lockfile validation, or a Pages deploy
failing because the Pages SITE was never created.
**Root-cause signature (sub-classes actually hit this session):**
- **GPU pynvml mock leak** — `src/general_ludd/infra/gpu_metrics.py` monkeypatches
  `sys.modules["pynvml"]` and `importlib.reload`s the module in tests; if a test's
  cleanup reloads BEFORE the mock is unpatched, module globals `_NVML_AVAILABLE`/
  `_nvml` stay pointed at the stale mock — invisible in the polluting test, breaks
  a LATER test (e.g. `TestGPUMetricsCollectorUnavailable` seeing `is_available()`
  return `True` on a GPU-less runner). Fix: `reset_probe()` (gpu_metrics.py:33-49)
  as an autouse per-test reset of the availability seam.
- **Slurm hung-subprocess timeout** — bare `subprocess.run(...)` calls to
  `sbatch`/`squeue`/etc. with no `timeout=` could freeze forever against a hung
  `slurmctld`. Fix: every slurm subprocess call passes `timeout=60` (or `timeout=5`
  for lightweight `available()` probes) — see `tests/unit/test_slurm_arg_hardening.py:214-254`.
  Separately, `commit 4b961146` reordered `SlurmJobMonitor._poll()` (cost sampled
  every poll BEFORE the terminal-state check) — a semantics change that required
  reconciling `tests/unit/test_slurm_cost_cap.py` and
  `tests/integration/test_slurm_cost_cap.py` to the new elapsed-based cost values
  (the commit's own message over-claimed unrelated fixes — verify commit messages
  against the actual diff, don't trust them).
- **Coverage find-guard** — `.github/workflows/build.yml:363-390`: if every
  test-shard produced no `.coverage.*` artifact, `download-artifact` never creates
  `coverage-data/`, and an unguarded `find coverage-data -name '.coverage.*'` exits
  1 under `bash -e`, sinking an otherwise-green run. Fixed by `if [ -d coverage-data ]`
  guarding the `find`, and `if ls .coverage-shards/.coverage.* >/dev/null 2>&1`
  guarding the `coverage combine`/`coverage xml`/`coverage report` calls, else
  printing a non-gating skip message.
- **Dockerfile lockfile validation** — `Dockerfile:40-47,57-58` uses
  `uv sync --frozen` (not `--locked`) because CI/the build injects a timestamp
  build version into `pyproject.toml` that no longer matches `uv.lock`'s pinned
  project version; `--locked` would reject that mismatch and fail the build even
  though the third-party dependency set is unchanged.
- **Pages site never created** — `.github/workflows/pages.yml` deploys were
  failing because the GitHub Pages SITE itself had never been created (a one-time
  repo-admin action, distinct from the workflow running green) — not a workflow
  bug. Fix: `make pages-enable` (`gh api -X POST repos/.../pages -f
  build_type=workflow`), which needs local admin-scoped `gh` auth (the default
  `GITHUB_TOKEN` inside Actions cannot self-enable Pages even though
  `pages.yml`'s `configure-pages` step passes `enablement: true`).
**Diagnosis steps:**
1. `make ci-failed-tests RUN=<id>` → note WHICH job/shard is red; infra-class
   failures typically affect only `other`/`gpu`/`molecule`/`coverage`/`pages`
   jobs, not the code-logic unit shards, and often show a subprocess hang, a shell
   `set -e` exit, or a 404/permissions error rather than an assertion failure.
2. Cross-check `make pages-status` for Pages-specific reds; `make ci-jobs-anon RUN=<id>`
   for per-job timestamps to distinguish a hang/timeout from a real failure.
**Fix pattern:** per sub-class above; the common thread is "this is infra/harness,
not application logic" — fix the harness (reset fixture, timeout, guard,
lockfile flag, one-time admin action), never weaken the underlying test/assertion.
**Verification:** the specific job goes green on the next CI run for the exact
SHA; for Pages, additionally fetch the live URL (`https://sandboxcom.github.io/gludd/`)
and expect 200.
