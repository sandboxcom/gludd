# Model Routing Coherence Check

**Date:** 2026-06-16
**Scope:** Cross-file coherence audit of the model-routing subsystem.
**Files examined:**
- `docs/research/MODEL_ROUTING_RECOMMENDATION.md` (main tree)
- `src/general_ludd/schemas/benchmark.py` (main tree, identical in worktree)
- `src/general_ludd/scoring/router.py` (main tree, identical in worktree)
- `src/general_ludd/routing_roles/roles.py` (worktree only)
- `src/general_ludd/routing_roles/weights.py` (worktree only)
- `src/general_ludd/model_weights/` — absent (see gap 1)
- `docs/research/METRIC_AND_BIBLIOGRAPHY.md` — absent (see gap 2)
- `src/general_ludd/scoring/metric.py` — absent (see gap 2)

---

## 6-Line Summary

The TaskRole enum is fully coherent between the recommendation doc and `routing_roles/roles.py` — all four members match. The composite-score formula in `BenchmarkScores.composite_score` matches the doc exactly. However, seven of the ten TaskType cost/quality weight pairs in `routing_roles/weights.py` diverge from the values stated in the recommendation doc. Three entire artefacts referenced by the doc are missing from both trees (`model_weights/` package, `METRIC_AND_BIBLIOGRAPHY.md`, `scoring/metric.py`). The `routing_roles/` package itself is worktree-only and has not been merged to main. `BenchmarkResult` is missing the `task_role` field that the recommendation doc marks as P1.

---

## Coherence Check Results

### (1) Model picks in MODEL_ROUTING_RECOMMENDATION.md vs. seed_data.json

**Finding: CANNOT VERIFY — seed_data.json does not exist.**

The entire `src/general_ludd/model_weights/` package is absent from both the main tree and the worktree. The recommendation doc §4.2 labels this "P0" work. Until `schema.py`, `store.py`, `loader.py`, and `seed_data.json` are created, there is nothing to check against the doc's model assignments:

| TaskRole | Recommended model | seed_data.json row |
|---|---|---|
| `planner` | `claude_opus_48` / `claude_sonnet_46` | MISSING |
| `editor` | `claude_haiku_45` / `gpt4o_mini` | MISSING |
| `compactor` | `gemini_flash_lite_25` | MISSING |
| `enumerator` | `gpt41_nano` / `qwen3_32b` | MISSING |

**Fix item 1:** Create `src/general_ludd/model_weights/` with `schema.py`, `store.py`, `loader.py`, and `seed_data.json` matching the model assignments in recommendation doc §3.2.

---

### (2) TaskRole enum coherence between routing_roles/roles.py and model_weights/schema.py

**Finding: PARTIAL — roles.py is coherent with the doc; schema.py is absent.**

`routing_roles/roles.py` defines:
```python
class TaskRole(StrEnum):
    PLANNER   = "planner"
    EDITOR    = "editor"
    COMPACTOR = "compactor"
    ENUMERATOR = "enumerator"
```

This matches the recommendation doc §3.2 exactly. However, `model_weights/schema.py` does not exist, so the schema-side `TaskRole` reference cannot be verified.

Additionally, `BenchmarkResult` in `src/general_ludd/schemas/benchmark.py` has no `task_role` field. The recommendation doc §3.2 and §4.2-P1 calls for adding `task_role: TaskRole | None` to that dataclass.

**Fix item 2:** Once `model_weights/schema.py` is created, ensure its `TaskRole` enum is identical to (or imported from) `routing_roles/roles.py` — do not define two separate enums.

**Fix item 3:** Add `task_role: TaskRole | None = None` to `BenchmarkResult` in `src/general_ludd/schemas/benchmark.py`.

---

### (3) Per-TaskType cost/quality weights: routing_roles/weights.py vs. recommendation doc §3.4

**Finding: SEVEN OF TEN VALUES DIVERGE.**

The recommendation doc §3.4 table and `routing_roles/weights.py` agree on three TaskTypes (`SECURITY_FIX`, `BUG_FIX`, `INTEGRATION`). The remaining seven all differ:

| TaskType | Doc cost/quality | weights.py cost/quality | Delta (cost) |
|---|---|---|---|
| `SECURITY_FIX` | 0.05 / 0.95 | 0.05 / 0.95 | 0 — match |
| `BUG_FIX` | 0.15 / 0.85 | 0.15 / 0.85 | 0 — match |
| `INTEGRATION` | 0.20 / 0.80 | 0.20 / 0.80 | 0 — match |
| `DEBUGGING` | 0.15 / 0.85 | 0.10 / 0.90 | −0.05 |
| `OPTIMIZATION` | 0.25 / 0.75 | 0.20 / 0.80 | −0.05 |
| `FEATURE` | 0.20 / 0.80 | 0.30 / 0.70 | +0.10 |
| `TEST_WRITE` | 0.20 / 0.80 | 0.40 / 0.60 | +0.20 |
| `CODE_REVIEW` | 0.15 / 0.85 | 0.40 / 0.60 | +0.25 |
| `REFACTOR` | 0.25 / 0.75 | 0.30 / 0.70 | +0.05 |
| `DOCUMENTATION` | 0.40 / 0.60 | 0.45 / 0.55 | +0.05 |

The divergence is significant: `CODE_REVIEW` is the starkest case — the doc says quality-dominant (0.15/0.85) but `weights.py` treats it as cost-dominant (0.40/0.60). `TEST_WRITE` shows the same pattern.

`AdaptiveRouter.__init__` also carries a default `cost_weight=0.2 / quality_weight=0.8`, which aligns with the doc's overall philosophy but is not dynamically injected from `weights.py` at routing time — the router uses its constructor defaults, not the per-task table.

**Fix item 4:** Reconcile `routing_roles/weights.py` with the recommendation doc §3.4. Either update `weights.py` to match the doc, or update the doc to reflect the intentional divergence (and document the rationale). The seven diverging rows are listed above.

**Fix item 5:** Wire `routing_roles/weights.py` into `AdaptiveRouter.route()`. Currently the router uses its own `self.cost_weight` / `self.quality_weight` constructor parameters and ignores the per-task table entirely. `route()` should call `task_weights[task_type]` to obtain per-task weights instead of using the instance-level defaults.

---

### (4) Standard metric formula: METRIC_AND_BIBLIOGRAPHY.md vs. metric.py

**Finding: BOTH ARTEFACTS ARE ABSENT — cannot verify.**

Neither `docs/research/METRIC_AND_BIBLIOGRAPHY.md` nor `src/general_ludd/scoring/metric.py` exists in the main tree or the worktree. The recommendation doc describes the W$ formula as:

```text
W$ = W / log10(1 + median_$/Mtok)
where median_$/Mtok = (input_price + output_price) / 2
```

This formula is referenced in `AdaptiveRouter` comments but is not implemented as a standalone `metric.py` module.

**Fix item 6:** Create `docs/research/METRIC_AND_BIBLIOGRAPHY.md` documenting the W$ formula, composite score weights, and citations.

**Fix item 7:** Create `src/general_ludd/scoring/metric.py` implementing the W$ formula and composite score, and update `AdaptiveRouter` to import from it rather than inline the math.

---

### (5) routing_roles/ package not merged to main tree

**Finding: Package exists only in worktree `agent-aa7abecb24030ba7d/`.**

`src/general_ludd/routing_roles/roles.py` and `src/general_ludd/routing_roles/weights.py` are present in the worktree but absent from the main tree at `/Users/shawnwilson/gludd/`. The `AdaptiveRouter` in main cannot import from them until they are committed and merged.

**Fix item 8:** Merge (or cherry-pick) the `routing_roles/` package from the worktree into main before wiring it into `AdaptiveRouter`.

---

## Fix-Item Index

| # | File | What to change |
|---|---|---|
| 1 | `src/general_ludd/model_weights/` (CREATE) | Create schema.py, store.py, loader.py, seed_data.json with model assignments from recommendation doc §3.2 |
| 2 | `src/general_ludd/model_weights/schema.py` | Import `TaskRole` from `routing_roles.roles` — do not define a second enum |
| 3 | `src/general_ludd/schemas/benchmark.py` | Add `task_role: TaskRole \| None = None` to `BenchmarkResult` |
| 4 | `src/general_ludd/routing_roles/weights.py` | Reconcile 7 diverging rows with recommendation doc §3.4 (or update the doc with rationale) |
| 5 | `src/general_ludd/scoring/router.py` | Inject per-task weights from `routing_roles.weights.task_weights[task_type]` inside `route()` instead of using instance-level `self.cost_weight` / `self.quality_weight` |
| 6 | `docs/research/METRIC_AND_BIBLIOGRAPHY.md` (CREATE) | Document W$ formula, composite score weights, and citations |
| 7 | `src/general_ludd/scoring/metric.py` (CREATE) | Implement W$ formula and composite score; update `AdaptiveRouter` to import from it |
| 8 | `src/general_ludd/routing_roles/` (MERGE) | Merge `roles.py` and `weights.py` from worktree to main tree |
