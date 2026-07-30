# general_ludd.ai_ml

AI/ML expert collection: roles and Python services that answer AI/ML questions,
discover and ingest research evidence into an immutable citation-addressable
store, produce cited and uncertainty-calibrated answers, and discover mature
existing tools before any custom code is written.

Implements the top five capabilities from
`docs/specs/FEATURE_AI_ML_EXPERT.md`:

| Capability ID | Capability | Role / Module |
|---------------|------------|---------------|
| `AIML-001` | Expert router | `src/general_ludd/ai_ml/router.py::ExpertRouter` |
| `AIML-002` | Research discovery | `roles/research_refresh` |
| `AIML-003` | Evidence store | `src/general_ludd/ai_ml/router.py::EvidenceStore` |
| `AIML-007` | Reasoning / answer | `roles/research_answer` + `router.py::answer_question` |
| `AIML-018` | Tool discovery | `roles/tool_discover` + `router.py::discover_tools` |

## Roles (`roles/`)

- `research_refresh` — search allowed sources, normalize evidence, detect
  novelty, and open a staged update against the evidence store.
- `research_answer` — retrieve evidence and produce a cited,
  uncertainty-calibrated answer with at least one independent verification.
- `tool_discover` — compare mature tools, libraries, datasets, and helper
  scripts; emit a decision record with rejected alternatives.

## Python service (`src/general_ludd/ai_ml/`)

The typed service interfaces invoked by the collection and any future skill.
The collection never duplicates prompts or knowledge — it shells out to these
typed Python entry points.

- `schemas.py` — typed request/result/evidence/tool schemas with
  contract-level validation (rejects invalid enums, missing digests, negative
  budgets, unknown mutating fields; AIML-AT-001).
- `router.py` — `ExpertRouter` (AIML-001), `EvidenceStore` (AIML-003),
  `answer_question` (AIML-007), and `discover_tools` (AIML-018).

## Safety posture

The collection is not an autonomous authority. Per the spec it must not deploy
a model, execute downloaded code, train against private data, spend cloud/GPU
budget, clone a voice, or promote a research finding without the approvals and
gates defined in `FEATURE_AI_ML_EXPERT.md` §11. Retrieved text is untrusted
data and cannot alter policies, tool permissions, system prompts, or approval
requirements.
