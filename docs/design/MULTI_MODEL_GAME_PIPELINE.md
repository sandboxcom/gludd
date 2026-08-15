# Multi-Model Game Generation Pipeline

Status: design (2026-08-06)

## Why single-model generation is insufficient

A single LLM generating game code AND evaluating correctness suffers from
self-consistency bias: the model that produced the code cannot reliably
detect its own defects. In the current `GameGenerator.generate_game()` path
(`src/general_ludd/cloud/game_e2e.py:214`), one model call emits Python
game code that must satisfy a structural contract (pygame import, game loop,
required controls, menu state) — but the contract is validated
deterministically afterward (`validate_game_code`, line 278). A reviewer
model running before execution catches semantic defects the generator may
miss: hallucinated controls, unreachable branches, rendering gaps, or
contract violations that pass AST-level checks but fail at runtime.

Separating generation from review also makes the coder slot eligible for
constrained (small/local) models via `SmallModelTaskPolicy` — a
generation-only model needs no execution, no credentials, and no repository
access, matching the policy's artifact-only, read-source impact boundary.

## Three-phase pipeline

```text
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   PLANNER    │────▶│    CODER     │────▶│   REVIEWER   │
│  (strong)    │     │  (flexible)  │     │  (strong)    │
└──────────────┘     └──────────────┘     └──────────────┘
       │                    │                    │
       ▼                    ▼                    ▼
  GameSpec +            Generated           Reviewed code
  prompt template       Python code         + diagnostics
  + architecture                           + accept/reject
```

### Phase 1 — Planner (strong model required)

- **Input:** `GameSpec` (name, genre, description, required controls, menu
  requirement, similarity threshold)
- **Output:** architecture description, component breakdown, prompt
  enrichment for the coder
- **Model requirements:** must reason about game architecture, 3D
  projection, control flow, and menu-state transitions. Planner output is
  the coder's prompt prefix — a broken plan produces broken code.
- **Routing role:** `planner` (Future; see §SmallModelTaskPolicy integration)

### Phase 2 — Coder (flexible model)

- **Input:** enriched prompt from Planner
- **Output:** self-contained `game.py` (pygame, single file, runnable)
- **Model requirements:** code generation only; no execution, no
  credentials, no repository mutation. This is the slot where a
  proven small/local model may substitute for a large model when its
  capability evidence passes the `SmallModelTaskPolicy` gate.
- **Routing role:** `coder` (§SmallModelTaskPolicy integration)
- **Contract checks:** `syntax_valid`, `import_ok`, `run_without_crash`

### Phase 3 — Reviewer (strong model required)

- **Input:** generated code + planning constraints + required-controls list
- **Output:** acceptance decision (ACCEPT / REJECT with diagnostic),
  semantic issues found, contract-compliance report
- **Model requirements:** must cross-reference code against the plan, detect
  hallucinated APIs, verify control coverage, and flag unreachable code.
  Reviewer fallibility directly gates pipeline correctness — a weak
  reviewer admits bad code.
- **Routing role:** `reviewer`

## Model requirements per phase

| Phase | Routing role | Min model tier | Small-model eligible? | Why |
|-------|-------------|----------------|----------------------|-----|
| Planner | `planner` | Strong (e.g. Opus, GPT-4-class) | No | Architectural reasoning, multi-constraint synthesis |
| Coder | `coder` | Medium+ (Sonnet-class) or proven small | **Yes, if proven** | Artifact-only output, no side effects, policy-gated |
| Reviewer | `reviewer` | Strong (same tier as Planner) | No | Must detect coder errors; reviewer fallibility is multiplicative |

## SmallModelTaskPolicy integration

The coder phase (`phase=2`) is the only slot eligible for constrained-model
dispatch under `SmallModelTaskPolicy`. The integration point already exists
in `GameGenerator._authorize_dispatch()` (game_e2e.py:242):

```python
task = SmallModelTaskSpec(
    task_id=f"fpx.1.game.{spec.name}",
    task_kind="coding",
    role=TaskRole.CODER,
    collection="gludd.fpx",
    impacts=frozenset({TaskImpact.READ_SOURCE, TaskImpact.WRITE_ARTIFACT}),
    acceptance_checks=("syntax_valid", "import_ok", "run_without_crash"),
)
decision = task_policy.authorize(task, model_identity, evidence)
```

### Policy gate contract (for the coder slot)

A small/local model may generate game code ONLY when:

1. **Task kind registered:** `coding` is in the bounded-task registry with
   role `coder`, collection `gludd.fpx`, artifact-only impacts.
2. **Identity bound:** `ModelIdentity` matches the exact weights, runtime,
   prompt contract, and tool/schema configuration for the model being
   dispatched. A profile alias pointing to new weights invalidates prior
   proof; dispatch fails closed.
3. **Evidence complete:** `CapabilityEvidence` shows ≥20/20 local cases
   passed on the exact acceptance checks (`syntax_valid`, `import_ok`,
   `run_without_crash`) with a successful test collection.
4. **No permanently excluded impacts:** generation is artifact-only — no
   execution, repository mutation, credential access, or deployment.
5. **Not already claimed:** the task ID is request-scope deduplicated; a
   second claim for the same task ID escalates.

### Default matrix (coder is not in the default registry)

`SmallModelTaskPolicy`'s built-in task matrix (small_model_policy.py:109)
includes `enumerator`, `compactor`, `editor`, and `reviewer` roles — but
**`coder` is intentionally absent**. To enable game-generation dispatch to
a constrained model, an injected `TaskContract` for `coder` + `gludd.fpx`
must be registered with its own local suite evidence. The contract cannot
override global high-impact exclusions.

## Fallback and escalation behavior

```text
dispatch(phase=2, model=small|local)
  │
  ├─ SmallModelTaskPolicy.authorize()
  │     ├─ LOCAL permitted → dispatch to small model
  │     │     └─ completion checks pass? → accept
  │     │     └─ completion checks fail (≤ retry bound)? → retry
  │     │     └─ retry budget exhausted? → ESCALATE
  │     └─ ESCALATE (no proof, wrong identity, excluded impact) →
  │                                    fall back to strong model
  │
  └─ strong-model dispatch (bypasses policy gate)
        └─ completion → proceed to Reviewer (phase 3)
```

### Escalation triggers

| Condition | Action |
|-----------|--------|
| No `TaskContract` for `coder` + `gludd.fpx` | ESCALATE → strong model (no proof exists) |
| `ModelIdentity` fingerprint mismatch | ESCALATE → strong model (proof invalidated by config change) |
| Evidence < 20/20 or collection error | ESCALATE → strong model (insufficient proof) |
| Task ID already claimed | ESCALATE → strong model (deduplication guard) |
| Completion checks fail on attempts 1-2 | Retry (within `max_attempts` bound, default 2) |
| Completion checks fail on attempt 3 | ESCALATE → strong model (retry budget exhausted) |
| Generated code fails `validate_game_code()` | ESCALATE to Planner re-plan + stronger coder |

### Planner+Coder combined fallback

When the Planner and Coder are different models and the coder's output is
rejected by the Reviewer, the pipeline back-loops: Reviewer diagnostics
feed back into the Planner for a revised prompt, and the Coder re-generates
with the enriched context. After `N` rejections (default 3), the entire
pipeline escalates to a single strong-model end-to-end pass (Planner+Coder
collapsed into one call) as a last-resort recovery path.

## Data flow diagram

```text
GameSpec ─────────────────────────────────────────────────────────────────┐
    │                                                                     │
    ▼                                                                     │
┌─────────┐    enriched_prompt    ┌─────────┐    generated_code    ┌──────────┐
│ Planner │ ────────────────────▶ │  Coder  │ ───────────────────▶ │ Reviewer │
│ (strong)│                       │(flexible)│                      │ (strong) │
└─────────┘                       └────┬────┘                      └────┬─────┘
                                       │                                │
                                       │ SmallModelTaskPolicy           │
                                       │ .authorize(task, id, proof)    │
                                       │   ├─ LOCAL → dispatch          │
                                       │   └─ ESCALATE → strong model   │
                                       │                                │
                                       ▼                                ▼
                                  game.py                      accept / reject
                                       │                      + diagnostics
                                       ▼
                               ┌──────────────┐
                               │ validate_game│
                               │ _code()      │
                               │ (deterministic│
                               │  AST check)   │
                               └──────┬───────┘
                                      │
                                      ▼
                               ┌──────────────┐
                               │ GameRunner   │
                               │ run_headless │
                               │ _inline()    │
                               └──────┬───────┘
                                      │
                                      ▼
                               ┌──────────────┐
                               │FrameComparator│
                               │ compare_frames│
                               │ (SSIM + PSNR) │
                               └──────────────┘
                                      │
                                      ▼
                                  E2EResult
```

## Integration with existing code

| Concern | Where | What changes |
|---------|-------|-------------|
| Planner phase | New: `GamePlanner` class or `GameGenerator` split | Enrich `GameSpec.prompt_template` with architectural guidance before coder dispatch |
| Coder phase | `GameGenerator.generate_game()` (line 214) | Already gated by `SmallModelTaskPolicy` when `task_policy` + `model_identity` supplied |
| Reviewer phase | New: `GameReviewer` class | Accept/reject with diagnostics; feed reject reasons back to Planner |
| Policy gate | `GameGenerator._authorize_dispatch()` (line 242) | Already implemented; `TaskRole.CODER` contract must be registered for small-model path |
| Fallback | New: pipeline-level escalation logic | Retry counter per phase, escalation to strong model on exhaustion |
| Pipeline orchestration | `AzureGameE2E.run_full_test()` (line 731) | Replace single `generator.generate_game()` with 3-phase orchestrated call |

## Related documents

- `docs/design/SMALL_MODEL_TASK_POLICY.md` — SmallModelTaskPolicy contract, routing roles, evidence binding
- `src/general_ludd/routing_roles/small_model_policy.py` — policy implementation
- `src/general_ludd/cloud/game_e2e.py` — current single-model E2E pipeline
- `src/general_ludd/schemas/benchmark.py` — `TaskRole` enum (planner, coder, reviewer, etc.)
