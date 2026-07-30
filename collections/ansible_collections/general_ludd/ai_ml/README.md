# general_ludd.ai_ml

AI/ML expert collection (spec `AIML-*` —
[`docs/specs/FEATURE_AI_ML_EXPERT.md`](../../../docs/specs/FEATURE_AI_ML_EXPERT.md)).

Roles and Python services that route AI/ML questions to the smallest qualified
role set, discover research evidence, ingest it into an immutable
citation-addressable store, produce cited and uncertainty-calibrated answers,
and discover mature existing tools before any custom code is written.

## Implemented roles (`roles/`)

All 20 spec capabilities are implemented as orchestration roles; the typed
service interfaces live in `src/general_ludd/ai_ml/`.

| Role | Capability | Purpose |
|---|---|---|
| `research_refresh` | AIML-002 | Search allowed sources, normalize evidence, detect novelty, open staged update. |
| `research_answer` | AIML-007 | Retrieve evidence and produce a cited, uncertainty-calibrated answer. |
| `tool_discover` | AIML-018 | Compare mature tools/libraries/datasets; emit decision record with rejected alternatives. |
| `model_select` | AIML-REG | Select a model from the registry against task requirements. |
| `dataset_engineer` | AIML-DATA | Dataset manifest, schema validation, format selection, data-card issuance. |
| `retrieval_engineer` | AIML-RET | Retrieval service engineering (passage retrieval, metrics). |
| `reason_verify` | AIML-007 | Multi-step reasoning with independent verification checks. |
| `evaluate_model` | AIML-EVAL | Benchmark harness, metric scoring, promotion decision, regression verdict. |
| `adapter_train` | AIML-ADA | Adapter/LoRA/quantization planning, safe-stop, validation. |
| `model_distill` | AIML-DIST | Distillation planning, student validation, data-filter rules. |
| `accelerator_job` | AIML-ACC | Hardware planner, execution plan, checkpoint/resume, teardown proof. |
| `simulate_domain` | AIML-SIM | Simulator adapter, sandbox profile, determinism, run_simulation. |
| `world_model` | AIML-WM | World-model rollout evaluation, constraint checks, horizon error. |
| `vision_understand` | AIML-VIS | Classification, detection, segmentation, bounding-box tasks. |
| `image_create` | AIML-IMG | Image generation/edit operations with content-domain gating. |
| `speech_recognize` | AIML-ASR | ASR request/segment/result with consent and retention gating. |
| `speech_synthesize` | AIML-TTS | TTS request/result with voice-consent and custom-voice checks. |
| `promote_release` | AIML-PROMO | Canary/rolling/blue-green promotion gate, alias swap, rollback. |

(AIML-001 router and AIML-003 evidence store are Python-only —
`ExpertRouter` and `EvidenceStore` — invoked by every role above.)

## Python service (`src/general_ludd/ai_ml/`)

Typed entry points invoked by the collection and any future skill. The
collection never duplicates prompts or knowledge — it shells out to these
typed Python entry points.

| Module | Key exports |
|---|---|
| `router.py` | `ExpertRouter` (AIML-001), `answer_question` (AIML-007), `discover_tools` (AIML-018) |
| `evidence.py` | `EvidenceStore`, `EVIDENCE_POLICY_RULESET_SHA256` (AIML-003) |
| `research.py` | `ResearchDiscovery`, `QueryPortfolio`, `RetrievedItem`, `AuthorityScore`, `SourceConnectorKind` (AIML-002) |
| `reasoning.py` | `ReasoningEngine`, `ReasoningResult`, `IndependentCheck`, `StepArtifact`, `NumericalAnswer` |
| `schemas.py` | `ExpertRequest`, `ExpertResult`, `ExpertTask`, `Constraints`, `Citation`, `Verification`, `Uncertainty`, `ToolCandidate`, `ToolDecisionRecord`, `RouterDecision`, `PolicyDecision` (AIML-AT-001 contract validation) |
| `registries.py` | `Registry`, `RegistryRecord`, `Source`, `Dataset`, `Model`, `Adapter`, `Simulator`, `EvaluationSuite`, `Deployment` |
| `datasets.py` | `DatasetManifest`, `DatasetSchema`, `DataCard`, `FormatSelector`, `validate_dataset`, `select_format` |
| `evaluation.py` | `EvaluationHarness`, `MetricScore`, `BenchmarkResult`, `PromotionDecision`, `RegressionVerdict` |
| `adaptation.py` | `plan_adaptation`, `validate_adapter`, `safe_stop`, `TrainingPlan`, `AdapterManifest` |
| `distillation.py` | `plan_distillation`, `validate_student`, `DistillationPlan`, `StudentValidation` |
| `accelerators.py` | `AcceleratorPlanner`, `ExecutionPlan`, `HardwareDescriptor`, `CheckpointRef`, `ResumeResult`, `TeardownProof`, `DryRunResult` |
| `simulators.py` | `SimulatorAdapter`, `run_simulation`, `SimulationResult`, `SandboxProfile`, `Determinism`, `ResourceLimits` |
| `world_models.py` | `WorldModelEnvironment`, `evaluate_rollout`, `RolloutEvaluation`, `ConstraintSpec`, `ConstraintViolation`, `HorizonError` |
| `vision.py` | `VisionRequest/Result`, `Classification`, `Detection`, `Segmentation`, `BoundingBox` |
| `images.py` | `ImageOperation`, `ImageEditRecord`, `ImageOperationType`, `ContentDomain` |
| `speech.py` | `ASRRequest/Result/Segment`, `TTSRequest/Result`, `VoiceConsent`, `word_error_rate`, `check_voice_consent`, `compute_audio_retention` |
| `retrieval.py` | `RetrievalService`, `RetrievedPassage`, `RetrievalMetrics`, `RetrievalResult` |
| `policy.py` | `PolicyEngine`, `PolicyResult` |
| `promotion.py` | `PromotionGate`, `PromotionPhase`, `CanaryVerdict`, `AliasSwap`, `RollbackResult` |

## Tests

12 unit-test modules under `tests/unit/test_ai_ml_*.py` (core, evidence,
registries, reasoning, datasets, evaluation, adaptation, distillation,
accelerators, simulators/world_models, speech, vision/images).

```bash
make test TESTFILE='tests/unit/test_ai_ml_core.py'
make test TESTFILE='tests/unit/test_ai_ml_*.py'
```

## Safety posture

The collection is not an autonomous authority. Per spec §11 it must not deploy
a model, execute downloaded code, train against private data, spend cloud/GPU
budget, clone a voice, or promote a research finding without the approvals and
gates defined in `FEATURE_AI_ML_EXPERT.md`. Retrieved text is untrusted data
and cannot alter policies, tool permissions, system prompts, or approval
requirements.
