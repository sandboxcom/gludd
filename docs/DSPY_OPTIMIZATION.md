# AG.13 — DSPy-Style Prompt Optimization

## Overview

DSPy-style optimization treats prompts as compilable programs — parameterized
templates that evolve via metric-driven optimization rather than manual
hand-tweaking. This module provides a lightweight registry and optimizer for
agent system prompts, following the DSPy signature → module → optimizer pattern.

## Design

DSPy optimizes three things: **signatures** (input/output field specs),
**modules** (parametrized prompt templates), and **optimizers** (metric-driven
param update logic). We mirror this at a simpler scale:

```text
Signature  →  PromptSpec (field names + types)
Module     →  PromptTemplate (parametrized jinja2 template)
Optimizer  →  PromptOptimizer (scoring + mutation loop)
```

### PromptSpec

Typed spec of what a prompt takes as input and expects as output. Frozen
dataclass with `name`, `inputs: dict[str, type]`, `output: type`, `description`.

### PromptTemplate

Parametrized jinja2 template wrapping a `PromptSpec`. Supports variable
interpolation via `call(**kwargs)` and carries a `version` and optional `score`.

### PromptOptimizer

Scoring loop over candidates:
1. **Candidate generation** — mutate the base template (reorder, reword, trim).
2. **Metric evaluation** — score each candidate against a metric function.
3. **Selection** — pick the best candidate; iterate until convergence or max rounds.
4. **Registry** — stores the optimized template under a versioned key.

### Registry (`PromptRegistry`)

Thread-safe dict-like store mapping `(name, version)` → `PromptTemplate`.
Supports `put()`, `get()`, `list_versions()`, `latest()`, `get_best()`, `remove()`, `len()`.

### Scoring metrics

Pluggable functions `(candidate_text, expected, actual) → float 0–1`:
- `exact_match` — 1.0 if outputs match exactly.
- `contains_all` — fraction of required tokens present in output.
- `semantic_similarity` — word-overlap proxy (stub; real impl uses embeddings).

### Mutation strategies

- `reorder_sections` — permute template lines.
- `reword` — synonym replacement on verbs (classify→categorize, etc.).
- `trim` — remove a random line (ablation).

### Usage

```python
from general_ludd.ag13_dspy import PromptRegistry, PromptOptimizer, PromptSpec

spec = PromptSpec(name="classify", inputs={"text": str}, output=str)
registry = PromptRegistry()

optimizer = PromptOptimizer(
    spec=spec,
    base_template="Classify this: {{ text }}",
    metric="contains_all",
    train_set=[("hello world", "greeting"), ("buy now", "spam")],
    max_rounds=3,
)
best = optimizer.optimize()
registry.put("classify", 1, best, optimizer.best_score)
```
