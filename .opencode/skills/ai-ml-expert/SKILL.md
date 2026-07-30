---
name: ai-ml-expert
description: "Use for AI/ML questions and mutations: routing requests to the smallest qualified role, answering cited questions with calibrated uncertainty, discovering tools with a decision record, building/training adapters, distilling models, curating datasets, evaluating benchmarks, promoting releases through canaries, running simulations, speech (ASR/TTS), vision (classification/detection/segmentation), image generation, and world-model rollout evaluation. Trigger keywords: machine learning, ML, AI, model, training, fine-tune, adapter, LoRA, distillation, dataset, datacard, benchmark, evaluation, retrieval, RAG, evidence, citation, reasoning, uncertainty, canary, promotion, rollback, accelerator, GPU, TPU, checkpoint, simulator, sandbox, ASR, TTS, speech, vision, segmentation, detection, world model, rollout, tool discovery."
location: "/Users/shawnwilson/gludd/.opencode/skills/ai-ml-expert/SKILL.md"
---

# AI/ML Expert

A typed expert service covering the full model lifecycle: route a request to the
smallest qualified role, ground every answer in an immutable evidence store, and
gate every mutation behind an approval token. Implements AIML-001 through
AIML-018 from `docs/specs/FEATURE_AI_ML_EXPERT.md`. The ansible collection under
`collections/ansible_collections/general_ludd/ai_ml/` wraps these typed entry
points and carries no independent ML logic.

## When to Use

Any ML task: "what's the uncertainty-calibrated answer to X (with citations)?",
"train an adapter for model Y", "distill model A into student B", "discover a
tool that does Z (with rejected alternatives)", "promote this checkpoint through
a canary", "evaluate this benchmark suite". If the query is about releasing git
artifacts, use `git-release-captain` instead.

## Available Roles

The `ExpertRouter` maps an `ExpertTask` to the smallest qualified role set
(spec §3.2). Mutation tasks (`TRAIN`, `DISTILL`, `DEPLOY`, `DATASET`) require an
approval token; read-only tasks (`QUESTION`, `RESEARCH`, `EVALUATE`) never do.

| ExpertTask | Role(s) | Module |
|---|---|---|
| `QUESTION` | research_answer | `router.answer_question` |
| `RESEARCH` | research_refresh, research_answer | `research.ResearchDiscovery` |
| `DATASET` | dataset_engineer | `datasets.{select_format,validate_dataset}` |
| `TRAIN` | adapter_train | `adaptation.{plan_adaptation,validate_adapter,safe_stop}` |
| `DISTILL` | model_distill | `distillation.{plan_distillation,validate_student}` |
| `SPEECH` | speech_recognize | `speech.{ASRRequest,TTSRequest,word_error_rate}` |
| `VISION` | vision_understand | `vision.{VisionRequest,Classification,Detection,Segmentation}` |
| `IMAGE` | image_create | `images` |
| `WORLD_MODEL` | world_model | `world_models.evaluate_rollout` |
| `SIMULATE` | simulate_domain | `simulators.run_simulation` |
| `EVALUATE` | evaluate_model | `evaluation.EvaluationHarness` |
| `DEPLOY` | promote_release | `promotion.PromotionGate`, `registries.Registry` |

Cross-cutting services: `EvidenceStore` (AIML-003, content-addressed + tenant-
isolated), `ReasoningEngine` (AIML-007, independent verification), `PolicyEngine`,
`AcceleratorPlanner`, `RetrievalService`.

## Service API Entry Points

| Entry point | Purpose |
|---|---|
| `ExpertRouter().route(request)` | Returns `RouterDecision(matched_roles, refusal_reason)` |
| `answer_question(request, evidence)` | Cited answer; failing verification never yields `succeeded` |
| `discover_tools(request, ...)` | `ToolDecisionRecord` with rejected alternatives + mandatory spike |
| `EvidenceStore().record(...)` / `.locate(locator)` | Immutable, content-addressed artifact store |
| `ReasoningEngine().run(...)` | `ReasoningResult` with `NumericalAnswer` + `IndependentCheck` |

Request/response shapes: `ExpertRequest`, `ExpertResult`, `ExpertTask`,
`ResultStatus` (`succeeded`/`degraded`/`refused`/`failed`/`awaiting_approval`),
`Constraints`, `Uncertainty`, `Citation`, `Verification`.

## Safety Boundaries

- **Router never silently relaxes a constraint.** Unmet constraints or a missing
  approval token on a mutation produce `refusal_reason`, not a degraded result.
- **Online-required tasks** (`QUESTION`, `RESEARCH`) refuse when
  `constraints.offline` is set — an ungrounded answer is worse than none.
- **Answers carry calibrated `Uncertainty`** and a failing independent
  verification downgrades the result (AIML-AT-007).
- **Tool discovery always emits rejected alternatives + a mandatory integration
  spike** — never a bare recommendation (AIML-018).
- **Evidence is content-addressed and tenant-isolated**; duplicate content yields
  one artifact with multiple locators (AIML-AT-002).
- **Mutation tasks** (`TRAIN`, `DISTILL`, `DEPLOY`, `DATASET`) require an approval
  token recorded in the evidence store.

## Usage Examples

```python
from general_ludd.ai_ml import ExpertRouter, answer_question, EvidenceStore
from general_ludd.ai_ml.schemas import ExpertRequest, ExpertTask

decision = ExpertRouter().route(
    ExpertRequest(task=ExpertTask.QUESTION, query="...", constraints={...})
)
# decision.matched_roles == ("research_answer",) or decision.refusal_reason

store = EvidenceStore()
result = answer_question(request, evidence=store)
# result.status, result.answer, result.uncertainty, result.citations
```

## See Also

- `git-release-captain` — artifact release / deployment orchestration
- `docs/specs/FEATURE_AI_ML_EXPERT.md` — full capability spec
- `src/general_ludd/physics/mechanistic_interpretability.py` — saliency / circuits / probing
