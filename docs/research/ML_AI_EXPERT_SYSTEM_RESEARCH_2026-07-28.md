# Machine-learning and AI expert-system research dossier

**Date:** 2026-07-28  
**Status:** Research baseline for implementation specifications  
**Scope:** Machine learning, artificial intelligence, foundation models, agent
systems, evaluation, safety, and production systems  
**Companion specification:**
[`SPEC_ML_AI_EXPERT_AND_SAFE_SELF_IMPROVEMENT.md`](../design/specs/SPEC_ML_AI_EXPERT_AND_SAFE_SELF_IMPROVEMENT.md)

## 1. Purpose and limits

This dossier supplies the evidence base for three Gludd changes:

1. an `general_ludd.ml_ai_expert` collection and expert role family that can
   research, reason about, test, and explain ML/AI questions;
2. a bounded self-improvement laboratory that learns from Gludd's outcomes
   without editing the live daemon or trusting its own unverified judgment; and
3. reusable improvements to Gludd's evidence, retrieval, evaluation, routing,
   provenance, and experiment infrastructure.

“Answer any question” is an aspiration, not a correctness guarantee. No finite
model or corpus is complete, current, or infallible. The implementable contract
is instead: decompose the question, retrieve versioned evidence, use appropriate
tools, expose uncertainty, abstain when evidence is insufficient, and make every
material claim independently checkable.

This is a deliberately deep **bounded catalog**, not a claim to enumerate every
ML/AI publication. It covers the major subfields and links to living indexes
that provide breadth beyond the selected foundational and implementation
resources. Sources were checked on 2026-07-28. Paper dates below are original
publication or preprint dates, not retrieval dates.

### 1.1 Evidence labels

| Label | Meaning |
|---|---|
| Established | Repeated empirical support, standard text, standard, or mature maintained implementation |
| Promising | Peer-reviewed/preprint evidence exists, but external replication or production evidence is limited |
| Practitioner signal | User report or operational experience; useful for failure discovery, not proof of causality |
| Proposal | Gludd design inference that still requires an evaluation before adoption |

### 1.2 Research method

- Prefer primary papers, official standards, official documentation, and
  canonical repositories.
- Use technical blogs to connect results and operational implications, not as
  substitutes for primary evidence.
- Use forum threads and issue trackers to discover persistent real-world
  failure modes.
- Record negative evidence and limitations; a high benchmark score is not a
  universal capability claim.
- Keep model/vendor recommendations outside the timeless expert taxonomy.
  Model selection must be learned from fresh, task-specific Gludd evaluations.

## 2. Executive synthesis

The research supports seven architectural conclusions.

1. **Expertise is a system property.** Retrieval, tools, memory, planning,
   verification, and calibrated refusal are as important as the selected model.
   ReAct, Toolformer, RAG, and agent benchmarks all support composing a model
   with explicit external operations rather than assuming parametric recall.
2. **Retrieval and generation need separate evaluation.** Production RAG
   failures frequently originate in parsing, chunking, metadata filtering, or
   ranking. A fluent generator cannot recover evidence it never received.
3. **One model must not author and certify its own improvement.** LLM judges
   exhibit position and self-preference biases; iterative self-refinement can
   reward-hack a shared evaluator. Promotions need deterministic tests,
   independent judges, blinded ordering, held-out cases, and human approval for
   high-risk changes.
4. **Self-improvement belongs in an isolated project workspace.** The Darwin
   Gödel Machine demonstrates empirically validated code evolution, but also
   uses sandboxing and oversight. Gludd's current direct live-tree update path
   is the wrong boundary for experiments.
5. **Evaluation is multidimensional and per task.** Accuracy alone hides cost,
   latency, regressions, unsafe behavior, calibration, contamination, and
   trajectory failures. HELM, Inspect, lm-evaluation-harness, SWE-bench, GAIA,
   and agent-evaluation guidance provide reusable patterns.
6. **Untrusted evidence is data, never instruction.** Retrieved pages, papers,
   tool output, memories, and model-generated critiques may contain prompt
   injections or poisoned claims. Capability checks and provenance must remain
   outside model control.
7. **Zero-downtime change requires reversible additive rollout.** Shadow
   evaluation, champion/challenger selection, canaries, explicit promotion, and
   fast rollback are safer than hot-swapping an unevaluated candidate.

## 3. Living discovery resources

These sources provide the breadth needed to keep an expert collection current.

| Resource | Kind | Why it belongs in the expert source registry |
|---|---|---|
| [arXiv CS.AI](https://arxiv.org/list/cs.AI/recent), [CS.LG](https://arxiv.org/list/cs.LG/recent), [CS.CL](https://arxiv.org/list/cs.CL/recent), [CS.CV](https://arxiv.org/list/cs.CV/recent) | Primary preprints | Broad, fast publication stream; versions and withdrawal notices must be retained |
| [PMLR](https://proceedings.mlr.press/) | Proceedings | Canonical open proceedings for ICML, AISTATS, CoRL, and related venues |
| [NeurIPS proceedings](https://proceedings.neurips.cc/) | Proceedings | Primary conference papers and supplements |
| [OpenReview](https://openreview.net/) | Papers and peer review | Papers plus public review/revision history for many ML venues |
| [JMLR](https://www.jmlr.org/) and [TMLR](https://www.jmlr.org/tmlr/) | Journals | Long-form and open-review ML research |
| [ACL Anthology](https://aclanthology.org/) | Proceedings | Canonical computational-linguistics archive |
| [CVF Open Access](https://openaccess.thecvf.com/) | Proceedings | CVPR, ICCV, and WACV papers |
| [Semantic Scholar](https://www.semanticscholar.org/) and [API](https://api.semanticscholar.org/api-docs/) | Citation graph | Citation discovery and metadata; never use citation count as a quality oracle |
| [OpenAlex](https://docs.openalex.org/) | Scholarly graph | Open metadata, concepts, institutions, and cited-by relationships |
| [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) | DOI metadata | DOI, version, correction, relation, and publication metadata |
| [Retraction Watch database](https://retractionwatch.com/retraction-watch-database-user-guide/) | Integrity data | Retraction/correction signal for freshness and trust policy |
| [Hugging Face papers](https://huggingface.co/papers) | Paper/code discovery | Links papers, models, datasets, and discussions; secondary discovery only |
| [Papers with Code](https://paperswithcode.com/) | Paper/code discovery | Benchmark and implementation links; verify against the canonical repository |
| [Stanford CRFM](https://crfm.stanford.edu/) | Foundation-model research | Evaluation, transparency, and foundation-model research hub |
| [BAIR blog](https://bair.berkeley.edu/blog/) | Technical articles | Research explanations from Berkeley AI Research |
| [Google DeepMind publications](https://deepmind.google/research/publications/) | Primary lab publications | Research and technical reports |
| [Anthropic research](https://www.anthropic.com/research) | Primary lab publications | Alignment, interpretability, agents, and safety evaluations |
| [OpenAI research](https://openai.com/research/) | Primary lab publications | Model, alignment, agent, and safety research |
| [Microsoft Research AI](https://www.microsoft.com/en-us/research/research-area/artificial-intelligence/) | Primary lab publications | Systems, agents, ML, and responsible AI |
| [Distill](https://distill.pub/) | Technical articles | High-quality interactive explanations; archive is no longer actively publishing |
| [Lil'Log](https://lilianweng.github.io/) | Technical synthesis | Deep, citation-rich surveys such as [LLM-powered autonomous agents](https://lilianweng.github.io/posts/2023-06-23-agent/) |

## 4. Foundations: statistics, optimization, classical AI, and ML

An expert role must not reduce “AI” to recent LLM work. These resources cover
the conceptual and mathematical base needed to choose methods correctly.

| Resource | Date/type | Use |
|---|---|---|
| [Artificial Intelligence: A Modern Approach](https://aima.cs.berkeley.edu/) | Living textbook | Search, planning, logic, probability, decisions, learning, robotics, and ethics |
| [The Elements of Statistical Learning](https://hastie.su.domains/ElemStatLearn/) | 2009 textbook | Statistical learning theory and classical supervised/unsupervised methods |
| [An Introduction to Statistical Learning](https://www.statlearning.com/) | Living textbook/code | Applied statistical learning with reproducible labs |
| [Probabilistic Machine Learning](https://probml.github.io/pml-book/) | 2022–2023 open books | Probabilistic modeling, inference, deep generative models, and decision making |
| [Deep Learning](https://www.deeplearningbook.org/) | 2016 open book | Optimization, regularization, sequence models, representation learning |
| [Dive into Deep Learning](https://d2l.ai/) | Living open book/code | Mathematical exposition with runnable PyTorch/JAX/TensorFlow examples |
| [CS229 notes](https://cs229.stanford.edu/main_notes.pdf) | Course notes | Core supervised, unsupervised, generative, and learning-theory material |
| [CS231n](https://cs231n.stanford.edu/) | Course | Computer vision and deep visual representations |
| [CS224N](https://web.stanford.edu/class/cs224n/) | Course | NLP and foundation-model foundations |
| [Reinforcement Learning: An Introduction](http://incompleteideas.net/book/the-book-2nd.html) | 2018 open text | Value functions, control, policy gradients, and planning |
| [Causal Inference: What If](https://www.hsph.harvard.edu/miguel-hernan/causal-inference-book/) | 2020 open text | Identification and estimation; separates prediction from intervention |
| [Interpretable Machine Learning](https://christophm.github.io/interpretable-ml-book/) | Living open text | Model-agnostic explanation methods and limitations |
| [Hidden Technical Debt in Machine Learning Systems](https://papers.nips.cc/paper/5656-hidden-technical-debt-in-machine-learning-systems) | 2015 paper | Entanglement, feedback loops, data dependencies, and system-level debt |
| [Rules of Machine Learning](https://developers.google.com/machine-learning/guides/rules-of-ml) | Engineering guidance | Production-first baselines, instrumentation, and pipeline discipline |

### 4.1 Core method implementations

| Project | Reuse boundary |
|---|---|
| [NumPy](https://numpy.org/doc/stable/), [SciPy](https://docs.scipy.org/doc/scipy/), [pandas](https://pandas.pydata.org/docs/) | Numerical and data primitives; do not reimplement |
| [scikit-learn](https://scikit-learn.org/stable/) | Classical estimators, pipelines, preprocessing, metrics, calibration, model selection |
| [XGBoost](https://xgboost.readthedocs.io/), [LightGBM](https://lightgbm.readthedocs.io/), [CatBoost](https://catboost.ai/) | Mature gradient-boosted trees |
| [PyMC](https://www.pymc.io/welcome.html) and [NumPyro](https://num.pyro.ai/) | Bayesian inference and probabilistic programs |
| [Optuna](https://optuna.org/) and [Ray Tune](https://docs.ray.io/en/latest/tune/) | Hyperparameter optimization; Gludd should orchestrate, not recreate search algorithms |
| [SHAP](https://shap.readthedocs.io/) and [Captum](https://captum.ai/) | Established explanation/attribution libraries with known limitations |
| [Evidently](https://docs.evidentlyai.com/) | Optional drift and data-quality adapter; Gludd still owns policy and outcome monitoring |

### 4.2 Subfield depth map

The role taxonomy must route into deeper method families instead of answering
every problem as a language-model problem.

| Subfield | Primary/deep resources | Implementation resources and boundary |
|---|---|---|
| Search, symbolic reasoning, and planning | AIMA; [International Planning Competition](https://www.icaps-conference.org/competitions/); [PDDL4J](https://github.com/pellierd/pddl4j); [SAT Handbook](https://www.iospress.com/catalog/books/handbook-of-satisfiability-2) | Reuse [Z3](https://github.com/Z3Prover/z3), established planners, and domain standards; require executable plan validation |
| Probabilistic graphical models and decisions | [Probabilistic Graphical Models course](https://ermongroup.github.io/cs228-notes/), [Probabilistic Machine Learning](https://probml.github.io/pml-book/) | Reuse PyMC/NumPyro; store priors, posterior checks, and decision loss |
| Causal inference | [Causal Inference: What If](https://www.hsph.harvard.edu/miguel-hernan/causal-inference-book/), [The Book of Why resources](http://bayes.cs.ucla.edu/WHY/) | Reuse [DoWhy](https://www.pywhy.org/dowhy/) and [EconML](https://github.com/py-why/EconML); reject causal language when identification assumptions are absent |
| Reinforcement learning | Sutton/Barto; [DQN](https://www.nature.com/articles/nature14236), [PPO](https://arxiv.org/abs/1707.06347), [Soft Actor-Critic](https://arxiv.org/abs/1801.01290), [Decision Transformer](https://arxiv.org/abs/2106.01345) | Reuse [Gymnasium](https://gymnasium.farama.org/), [PettingZoo](https://pettingzoo.farama.org/), [Stable-Baselines3](https://stable-baselines3.readthedocs.io/), or [RLlib](https://docs.ray.io/en/latest/rllib/); version environment APIs and seeds |
| Graph learning and knowledge representation | [Graph neural networks review](https://arxiv.org/abs/1812.08434), [W3C RDF 1.1](https://www.w3.org/TR/rdf11-concepts/), [W3C OWL 2](https://www.w3.org/TR/owl2-overview/) | Reuse [PyTorch Geometric](https://pytorch-geometric.readthedocs.io/) or [DGL](https://www.dgl.ai/); distinguish learned graph scores from logical entailment |
| Time series and forecasting | [Forecasting: Principles and Practice](https://otexts.com/fpp3/), [Temporal Fusion Transformers](https://arxiv.org/abs/1912.09363) | Reuse [sktime](https://www.sktime.net/) and classical baselines; evaluate rolling-origin splits, seasonality, and leakage |
| Robotics and embodied AI | [ROS 2 documentation](https://docs.ros.org/en/rolling/), [MoveIt 2](https://moveit.picknik.ai/), [Open X-Embodiment](https://arxiv.org/abs/2310.08864) | Simulation is not physical validation; require safety envelopes, real-time constraints, and operator authority |
| Scientific ML | [Physics-informed neural networks](https://www.sciencedirect.com/science/article/pii/S0021999118307125), [DeepXDE](https://github.com/lululxvi/deepxde), [JAX](https://github.com/jax-ml/jax) | Preserve units, equations, solver tolerances, and numerical baselines; a lower training loss is not physical validity |
| Privacy and federated learning | [Differential privacy](https://www.cis.upenn.edu/~aaroth/Papers/privacybook.pdf), [FedAvg](https://arxiv.org/abs/1602.05629) | Reuse [Opacus](https://opacus.ai/) and [Flower](https://flower.ai/); report privacy accountant, threat model, client sampling, and secure-aggregation assumptions |
| Fairness and responsible ML | [Fairness and Machine Learning](https://fairmlbook.org/), model cards, datasheets | Reuse [Fairlearn](https://fairlearn.org/) or [AI Fairness 360](https://aif360.res.ibm.com/); choose metrics from the use context and report incompatible objectives |
| AutoML and optimization | [Bayesian Optimization tutorial](https://arxiv.org/abs/1807.02811), [Neural Architecture Search survey](https://jmlr.org/papers/v20/18-598.html) | Reuse Optuna/Ray Tune; nested validation and hard resource ceilings are mandatory |

## 5. Deep learning and foundation-model architectures

| Resource | Date | Established contribution and limitation |
|---|---:|---|
| [Attention Is All You Need](https://arxiv.org/abs/1706.03762) | 2017 | Transformer and scaled dot-product attention; standard attention is quadratic in sequence length |
| [BERT](https://arxiv.org/abs/1810.04805) | 2018 | Bidirectional masked-language pretraining for representation learning |
| [T5](https://arxiv.org/abs/1910.10683) | 2019 | Text-to-text task unification and systematic transfer study |
| [Language Models are Few-Shot Learners](https://arxiv.org/abs/2005.14165) | 2020 | In-context learning at scale; includes early benchmark-contamination analysis |
| [Scaling Laws for Neural Language Models](https://arxiv.org/abs/2001.08361) | 2020 | Empirical loss scaling with parameters, data, and compute; laws are regime/data dependent |
| [Training Compute-Optimal Large Language Models](https://arxiv.org/abs/2203.15556) | 2022 | Chinchilla compute allocation; inference demand changes the economic optimum |
| [Switch Transformers](https://arxiv.org/abs/2101.03961) | 2021 | Sparse mixture-of-experts scaling; routing balance and serving complexity remain operational costs |
| [Mamba](https://arxiv.org/abs/2312.00752) and [official code](https://github.com/state-spaces/mamba) | 2023 | Selective state-space sequence model with linear-time recurrence; architecture-specific evaluation is required |
| [Vision Transformer](https://arxiv.org/abs/2010.11929) | 2020 | Transformer applied to image patches |
| [Denoising Diffusion Probabilistic Models](https://arxiv.org/abs/2006.11239) | 2020 | Modern diffusion formulation |
| [Latent Diffusion Models](https://arxiv.org/abs/2112.10752) | 2021 | Diffusion in learned latent space for lower generation cost |
| [Graph Neural Networks: A Review](https://arxiv.org/abs/1812.08434) | 2018 | Message-passing and graph-learning taxonomy |

**Design implication:** the expert needs architecture cards that state
complexity, data regime, inductive bias, failure modes, and evaluation evidence.
It must not rank methods by recency or parameter count.

## 6. Training, adaptation, and alignment

| Resource | Date | Design use |
|---|---:|---|
| [Adam](https://arxiv.org/abs/1412.6980) and [Decoupled Weight Decay/AdamW](https://arxiv.org/abs/1711.05101) | 2014/2017 | Optimizer method cards; include convergence and generalization caveats |
| [FLAN instruction tuning](https://arxiv.org/abs/2109.01652) | 2021 | Task mixture and instruction generalization |
| [InstructGPT/RLHF](https://arxiv.org/abs/2203.02155) | 2022 | SFT, preference model, PPO pipeline; reports alignment tax and residual bias |
| [Constitutional AI](https://arxiv.org/abs/2212.08073) | 2022 | Principles plus AI feedback; a constitution does not replace external safety tests |
| [Direct Preference Optimization](https://arxiv.org/abs/2305.18290) | 2023 | Preference optimization without explicit reward-model RL |
| [RLAIF versus RLHF](https://arxiv.org/abs/2309.00267) | 2023 | AI feedback can scale preference labeling but inherits judge failure modes |
| [LoRA](https://arxiv.org/abs/2106.09685) | 2021 | Low-rank parameter-efficient adaptation |
| [QLoRA](https://arxiv.org/abs/2305.14314) | 2023 | Quantized base model plus LoRA; enables bounded local experiments |
| [Hugging Face PEFT](https://huggingface.co/docs/peft/main/index) | Living code/docs | Mature adapter surface for parameter-efficient methods |
| [Hugging Face TRL](https://huggingface.co/docs/trl/) | Living code/docs | SFT and preference/RL trainers; use behind an optional adapter |
| [DataComp](https://arxiv.org/abs/2304.14108) | 2023 | Data curation as a controlled benchmark, not an untracked preprocessing step |
| [Datasheets for Datasets](https://arxiv.org/abs/1803.09010) | 2018 | Dataset motivation, composition, collection, preprocessing, and use disclosure |
| [Model Cards](https://arxiv.org/abs/1810.03993) | 2018 | Intended use, metrics, slices, limitations, and ethical considerations |

**Design implication:** Gludd should initially optimize prompts, retrieval,
tools, routing, and small adapters. Weight updates require a separately
authorized training plane, dataset lineage, licensing review, privacy checks,
held-out evaluation, and artifact rollback.

### 6.1 PEFT, adapters, composition, and routing

Parameter-efficient adaptation is not one feature. Training method, checkpoint
format, base-model compatibility, composition, routing, serving, and rollback
are separate concerns.

| Resource | Date | Evidence and Gludd consequence |
|---|---:|---|
| [LoRA](https://arxiv.org/abs/2106.09685) | 2021 | Low-rank weight updates; rank, target modules, alpha, base revision, and merge state are material experiment inputs |
| [QLoRA](https://arxiv.org/abs/2305.14314) | 2023 | 4-bit quantized base plus LoRA; report quantizer, compute dtype, double quantization, paged optimizer, and peak memory |
| [DoRA](https://arxiv.org/abs/2402.09353) and [official code](https://github.com/NVlabs/DoRA) | 2024 | Decomposes magnitude and direction; reported gains do not remove its runtime and layer-support constraints |
| [AdaLoRA](https://arxiv.org/abs/2303.10512) | 2023 | Allocates rank by importance; final per-layer ranks belong in the artifact manifest |
| [IA3](https://arxiv.org/abs/2205.05638) | 2022 | Learned activation rescaling offers another small adapter family |
| [Prefix-Tuning](https://arxiv.org/abs/2101.00190) and [Prompt Tuning](https://arxiv.org/abs/2104.08691) | 2021 | Prompt parameters have different serving and composition semantics from weight adapters |
| [AdapterHub](https://arxiv.org/abs/2007.07779) and [Adapters library](https://arxiv.org/abs/2311.11077) | 2020/2023 | Mature composable adapter abstractions; evaluate rather than build a second tuning library |
| [Hugging Face PEFT](https://huggingface.co/docs/peft/en/index) | Living docs/code | Preferred optional adapter for LoRA, QLoRA, DoRA, IA3, prompt methods, merging, and hotswapping |
| [PEFT checkpoint format](https://huggingface.co/docs/peft/main/developer_guides/checkpoint) | Living docs | Adapter weights alone are incomplete; config and exact base revision are required; prefer `safetensors` over pickle-backed `.bin` |
| [safetensors](https://github.com/huggingface/safetensors) | Mature code | Non-executable tensor format; still validate dimensions, dtype, metadata, size, and digest |
| [LoRAHub](https://arxiv.org/abs/2307.13269) and [code](https://github.com/sail-sg/lorahub) | 2023/2024 | Few-shot adapter composition can trade tokens for adapter search, but did not universally beat in-context learning |
| [X-LoRA](https://arxiv.org/abs/2402.07148) | 2024 | Token/layer-level mixture of adapter experts; routing adds a new model with its own evaluation and failure surface |
| [Instance-level dynamic LoRA composition](https://aclanthology.org/2024.findings-emnlp.326/) | 2024 | Input-specific composition makes route decisions part of provenance |
| [S-LoRA](https://arxiv.org/abs/2311.03285) and [Punica](https://arxiv.org/abs/2310.18547) | 2023 | Multi-tenant adapter serving can share a base, but scheduler/KV/adapter memory and isolation must be measured |
| [vLLM LoRA serving](https://docs.vllm.ai/en/stable/features/lora/) | Living docs | Optional production backend; ranks, target modules, formats, lineage, dynamic-load authority, and mixed-MoE layouts must be validated before load |

Adapters MUST be keyed by the digest of the base weights, architecture,
tokenizer, vocabulary, chat template, quantization configuration, and target
module map. A matching human-readable model name is insufficient. Merge and
unmerge equivalence needs an executable tolerance test; composition and routing
need held-out evaluation against the base and every constituent adapter.

Operational reports reinforce the need for compatibility checks:
[PEFT issue 1802](https://github.com/huggingface/peft/issues/1802) documents
surprising behavior while switching multiple adapters, and
[PEFT issue 1226](https://github.com/huggingface/peft/issues/1226) records
merged-versus-active-adapter differences. Current PEFT documentation also warns
that QDoRA has reported incompatibility with DeepSpeed ZeRO-2. These are
practitioner/maintainer signals, not evidence that every combination fails.

### 6.2 Dataset representations, streaming, and interchange

| Resource/format | Best use | Required cautions |
|---|---|---|
| [Hugging Face Datasets paper](https://arxiv.org/abs/2109.02846), [library](https://github.com/huggingface/datasets), and [loading docs](https://huggingface.co/docs/datasets/loading) | Versioned dataset interface, transforms, memory mapping, and streaming | Pin library/PyArrow versions, code revision, builder config, split, and transform fingerprint |
| [Apache Arrow format](https://arrow.apache.org/docs/format/index.html) | Typed columnar in-memory/interchange representation | Arrow IPC file and stream formats differ; preserve extension metadata and schema |
| [Apache Parquet format](https://parquet.apache.org/docs/file-format/) | Durable compressed columnar shards and predicate pushdown | Record schema, row-group/shard policy, compression, logical types, and statistics exposure |
| [JSON Lines](https://jsonlines.org/) | Reviewable append/stream interchange and fixtures | Enforce UTF-8, one valid JSON value per line, schema, record size, and canonical hashing |
| [WebDataset](https://github.com/webdataset/webdataset) | Sequential tar shards for large multimodal training streams | Index member names, byte digests, sample grouping, shard sizes, and shuffle semantics |
| [Hugging Face dataset cards](https://huggingface.co/docs/hub/datasets-cards) and [Datasheets for Datasets](https://arxiv.org/abs/1803.09010) | Intended use, composition, collection, preprocessing, licensing, and limitations | A card is mandatory metadata, not proof that the data is safe or correct |
| [Croissant](https://mlcommons.org/working-groups/data/croissant/) | Portable dataset metadata and resource descriptions | Use as interchange; Gludd retains its authoritative policy and lineage IDs |
| [DVC](https://dvc.org/doc) and [lakeFS](https://docs.lakefs.io/) | Optional large-artifact/data version backends | Git pointers are not content provenance unless remote object digests and retention are verified |

Gludd should standardize a logical record/dataset manifest and make formats
adapters. JSONL is the reviewable small-data baseline; Parquet is the default
large tabular artifact; Arrow is the in-process/cache representation; WebDataset
is optional for large sequential multimodal streams. Format conversion MUST
preserve record IDs, split, schema, nullability, label semantics, ordering
contract, and provenance rather than merely preserving values that happen to
deserialize.

Persistent issues show why conformance tests are necessary:
[datasets issue 2377](https://github.com/huggingface/datasets/issues/2377)
explains that a Datasets Arrow stream is not automatically a Feather/Arrow IPC
file; [issue 5053](https://github.com/huggingface/datasets/issues/5053) records
an intermittent JSON streaming parse failure; and
[issue 4883](https://github.com/huggingface/datasets/issues/4883) records
monotonically increasing resident memory in a data-loader workload.

## 7. Retrieval, RAG, memory, and knowledge

| Resource | Date | What the expert must retain |
|---|---:|---|
| [Dense Passage Retrieval](https://arxiv.org/abs/2004.04906) | 2020 | Dual-encoder dense retrieval baseline |
| [Retrieval-Augmented Generation](https://arxiv.org/abs/2005.11401) | 2020 | Joint retrieval/generation formulation with source-conditioned output |
| [ColBERT](https://arxiv.org/abs/2004.12832) | 2020 | Late interaction trades storage/compute for fine-grained matching |
| [HyDE](https://arxiv.org/abs/2212.10496) | 2022 | Hypothetical-document query expansion; generated expansion can introduce bias |
| [RAPTOR](https://arxiv.org/abs/2401.18059) | 2024 | Recursive summaries for hierarchical retrieval |
| [Self-RAG](https://arxiv.org/abs/2310.11511) | 2023 | Selective retrieval and reflection tokens; training-specific results do not validate arbitrary pipelines |
| [Corrective RAG](https://arxiv.org/abs/2401.15884) | 2024 | Retrieval-quality evaluator and corrective actions |
| [From Local to Global: GraphRAG](https://arxiv.org/abs/2404.16130) and [Microsoft GraphRAG](https://github.com/microsoft/graphrag) | 2024 | Community summaries for corpus-level questions; expensive indexing and provenance need explicit budgets |
| [BRIGHT](https://arxiv.org/abs/2407.12883) | 2024 | Reasoning-intensive retrieval remains difficult even when documents are available |
| [RAGAS](https://arxiv.org/abs/2309.15217) and [code](https://github.com/explodinggradients/ragas) | 2023 | Retrieval/generation metrics; model-graded scores require calibration |
| [BEIR](https://arxiv.org/abs/2104.08663) | 2021 | Heterogeneous zero-shot retrieval benchmark |
| [MTEB](https://arxiv.org/abs/2210.07316) | 2022 | Broad embedding evaluation across tasks and languages |
| [Lost in the Middle](https://arxiv.org/abs/2307.03172) | 2023 | Long context does not guarantee uniform use of evidence |
| [MemGPT](https://arxiv.org/abs/2310.08560) | 2023 | Tiered context/memory management; paging adds control complexity |
| [LangGraph memory](https://langchain-ai.github.io/langgraph/concepts/memory/) | Living docs | Existing Gludd-compatible store/checkpoint concepts |
| [pgvector](https://github.com/pgvector/pgvector) | Mature code | Preferred production vector extension when PostgreSQL is already authoritative |

### 7.1 Retrieval evaluation contract

The expert must score at least:

- parse success and section fidelity;
- retrieval recall at `k`, precision at `k`, MRR/nDCG, and duplicate rate;
- evidence freshness, version, retraction/correction state, and license;
- answer claim support, citation correctness, contradiction handling, and
  abstention;
- latency, tokens, storage, and monetary cost.

Generation metrics may not mask a failed retriever. Gold evidence IDs must be
evaluated before answer style.

### 7.2 Vector, sparse, graph, hybrid, and reranking depth

| Layer | Primary resources | Design boundary |
|---|---|---|
| Lexical | BM25; PostgreSQL [full-text search](https://www.postgresql.org/docs/current/textsearch.html) | Exact terms, identifiers, filters, and deterministic offline fallback |
| Learned sparse | [SPLADE](https://arxiv.org/abs/2107.05720) and [SPLADE v2](https://arxiv.org/abs/2109.10086) | Optional model; sparse vocabulary, regularization, latency, and license belong in the model card |
| Dense | [DPR](https://arxiv.org/abs/2004.04906), [FAISS](https://github.com/facebookresearch/faiss), [pgvector](https://github.com/pgvector/pgvector) | Approximate search needs recall measurement against an exact subset |
| Late interaction | [ColBERTv2](https://arxiv.org/abs/2112.01488) | Stronger token-level matching with larger index/compute surface |
| Hybrid fusion | [Hybrid-fusion analysis](https://arxiv.org/abs/2210.11934) and pgvector's official hybrid example | RRF is not universally best; tune fusion only on development data and preserve component ranks |
| Cross-encoder reranking | [Sentence Transformers CrossEncoder](https://www.sbert.net/examples/cross_encoder/applications/README.html) | Rerank a bounded candidate set; never hide first-stage recall or exceed latency budget |
| Graph retrieval | [GraphRAG paper](https://arxiv.org/abs/2404.16130), [Microsoft GraphRAG](https://github.com/microsoft/graphrag), RDF/OWL standards | Graph extraction is model-derived evidence; retain source spans and do not equate graph edges with truth |
| Production store | PostgreSQL full text + pgvector first; external vector/graph systems only through adapters | Structured authoritative state must override stale derived indexes |

The retriever should use query classification, metadata/source filters, lexical
and dense candidates, version-aware graph traversal, score/rank fusion, bounded
reranking, neighbor/section expansion, and diversity control. Each stage emits
its own candidate IDs and scores. A graph edge, embedding similarity, or
reranker score is a retrieval signal—not claim verification.

A 2026 practitioner post describes a
[stale vector index contradicting current relational state](https://www.reddit.com/r/LocalLLaMA/comments/1r69w5y/rag_failure_in_production_our_vector_store_served/);
longer-running threads likewise emphasize
[document CRUD/index synchronization](https://www.reddit.com/r/LocalLLaMA/comments/1cfrqlz/)
and retrieval latency as corpora grow. The specification therefore makes
source-of-truth revision, index watermarks, tombstones, and reconciliation
blocking correctness controls.

### 7.3 Web research, search, and resource discovery

The expert requires a source registry with ordered, policy-aware fallbacks. No
single search engine or scholarly graph has complete coverage.

| Source class | Preferred source | Fallbacks and boundary |
|---|---|---|
| General web discovery | Self-hosted [SearXNG API](https://docs.searxng.org/dev/search_api.html) | Configured vendor search APIs; direct site search; never scrape a search UI as an implicit fallback |
| Historical web | [Internet Archive CDX API](https://archive.org/developers/wayback-cdx-server.html) | [Common Crawl index](https://index.commoncrawl.org/) and WARC; archive results are historical, not current |
| Web-scale/bulk | [Common Crawl URL index](https://commoncrawl.org/url-index) | Fetch only selected WARC records; bulk jobs use columnar index and separate budgets |
| Scholarly identity/DOI | [Crossref REST](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) | DataCite, publisher record; compare rather than overwrite conflicting metadata |
| Scholarly graph | [OpenAlex](https://docs.openalex.org/) | [Semantic Scholar API](https://api.semanticscholar.org/api-docs/), Crossref references, OpenCitations |
| Preprints/peer review | [arXiv API](https://info.arxiv.org/help/api/index.html), [OpenReview API](https://docs.openreview.net/) | Venue proceedings and author repositories; preserve version/review state |
| Biomedicine | [NCBI E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25501/) and [Europe PMC API](https://europepmc.org/RestfulWebService) | PubMed Central open text; never infer clinical validity from index presence |
| Computer science | [DBLP API](https://dblp.org/faq/How+to+use+the+dblp+search+API.html), ACL/CVF/PMLR proceedings | OpenAlex/Semantic Scholar; verify canonical venue and version |
| Code/issues | [GitHub REST search](https://docs.github.com/en/rest/search/search) and repository APIs | GitLab/forge APIs; pin commit/tag and retain issue state/timestamps |
| Questions/practice | [Stack Exchange API](https://api.stackexchange.com/docs) and named forums | Practitioner signal only; preserve score/date/accepted-answer status and corroborate material claims |
| Retractions/corrections | [Crossref relations](https://www.crossref.org/documentation/retrieve-metadata/rest-api/rest-api-filters/) and Retraction Watch | Publisher/venue notice; retraction state overrides ranking popularity |

The broker MUST honor [RFC 9309](https://www.ietf.org/rfc/rfc9309.html), service
terms, licenses, authentication, `Retry-After`, rate and concurrency headers,
and cache validators. Crossref documents public/polite concurrency limits;
OpenAlex uses request/credit limits; public SearXNG instances may disable JSON;
Common Crawl explicitly asks clients not to parallelize index queries. Provider
availability and result ordering are therefore recorded observations, not hidden
implementation details.

Discovery is a reproducible protocol: expand synonyms/identifiers, search
independent indexes, deduplicate by stable identifiers and content, follow
backward/forward citations, inspect corrections and negative evidence, stop at a
declared saturation/budget rule, and publish the complete query/source ledger.

## 8. Reasoning, planning, agents, and tools

| Resource | Date | Evidence and caveat |
|---|---:|---|
| [Chain-of-Thought Prompting](https://arxiv.org/abs/2201.11903) | 2022 | Intermediate reasoning can improve some tasks; generated rationales are not proof |
| [Self-Consistency](https://arxiv.org/abs/2203.11171) | 2022 | Sample-and-aggregate improves some reasoning tasks at higher cost |
| [Program-Aided Language Models](https://arxiv.org/abs/2211.10435) | 2022 | Delegate exact computation to a runtime |
| [ReAct](https://arxiv.org/abs/2210.03629) | 2022 | Interleave reasoning and environmental action |
| [MRKL Systems](https://arxiv.org/abs/2205.00445) | 2022 | Route questions to specialized modules |
| [Toolformer](https://arxiv.org/abs/2302.04761) | 2023 | Self-supervised tool-use examples; tool authorization remains external |
| [Tree of Thoughts](https://arxiv.org/abs/2305.10601) | 2023 | Search over reasoning candidates; branch factor needs a hard budget |
| [Reflexion](https://arxiv.org/abs/2303.11366) | 2023 | Verbal feedback and episodic memory improve selected tasks |
| [LATS](https://arxiv.org/abs/2310.04406) | 2023 | Search, environment feedback, and reflection; compute can grow rapidly |
| [Generative Agents](https://arxiv.org/abs/2304.03442) | 2023 | Memory, reflection, and planning architecture |
| [Voyager](https://arxiv.org/abs/2305.16291) | 2023 | Automatic curriculum and executable skill library in a bounded game environment |
| [SWE-agent](https://arxiv.org/abs/2405.15793) and [code](https://github.com/SWE-agent/SWE-agent) | 2024 | Agent-computer interface matters for software tasks |
| [OpenHands](https://github.com/OpenHands/OpenHands) | Living code | Mature software-agent implementation and evaluation reference |
| [Model Context Protocol specification](https://github.com/modelcontextprotocol/modelcontextprotocol) | Living standard | Typed tool/resource interchange; transport does not confer trust |
| [LangGraph](https://github.com/langchain-ai/langgraph) | Living code | Durable graph execution and human-in-the-loop primitives |
| [Microsoft AutoGen](https://github.com/microsoft/autogen) | Living code | Multi-agent/event-driven patterns; use as reference or adapter, not a required core |
| [Semantic Kernel](https://github.com/microsoft/semantic-kernel) | Living code | Typed plugins, planning, and enterprise integration |
| [Haystack](https://github.com/deepset-ai/haystack) | Living code | Production-oriented RAG pipelines |
| [LlamaIndex](https://github.com/run-llama/llama_index) | Living code | Data connectors, indexes, and retrieval workflows |

**Design implication:** Gludd needs explicit state machines, typed tool schemas,
iteration ceilings, idempotency keys, capability checks, and terminal-state
tests. A prompt that says “stop” is not a control plane.

### 8.1 Process supervision, verifiable reasoning, and private-CoT-safe output

| Resource | Date | Design use |
|---|---:|---|
| [Let's Verify Step by Step](https://arxiv.org/abs/2305.20050) and [PRM800K](https://github.com/openai/prm800k) | 2023 | Process labels can improve selected math tasks; step feedback quality and transfer must be evaluated |
| [Faithful Chain-of-Thought Reasoning](https://arxiv.org/abs/2211.12588) | 2022 | Translate to an executable symbolic program and use a deterministic solver where possible |
| [Measuring Faithfulness in Chain-of-Thought](https://arxiv.org/abs/2307.13702) | 2023 | A written chain is not necessarily the causal reasoning process |
| [Reasoning models do not always say what they think](https://www.anthropic.com/research/reasoning-models-dont-say-think) | 2025 | Hidden-hint experiments show that visible reasoning cannot be the sole safety monitor |
| [OpenAI CoT monitorability evaluation](https://openai.com/index/evaluating-chain-of-thought-monitorability/) | 2025 | Monitorability is an empirical model/property to track, not an assumed invariant |
| [ProcessBench](https://arxiv.org/abs/2412.06559) | 2024 | Evaluate whether a verifier locates the first erroneous math step |
| [Program-Aided Language Models](https://arxiv.org/abs/2211.10435) and [Faithful CoT](https://arxiv.org/abs/2211.12588) | 2022 | Prefer inspectable programs and solver outputs for exact subproblems |

Gludd should allow a provider's private reasoning channel to remain private.
The user-facing product is a **verification record**, not a claim to reveal
internal cognition: assumptions, subproblem DAG, evidence, tool inputs/outputs,
proof/check status, concise derivation, alternatives, and limitations. Raw
private chain-of-thought MUST NOT be required, persisted, logged, placed in
training data, or exposed through an API. If a provider returns reasoning-like
content, treat it as sensitive untrusted model output and retain only a
policy-authorized diagnostic artifact.

Process supervision is admissible only when a step has a stable externally
checkable meaning. For open-ended prose, process reward models can learn style
or evaluator preferences. Outcome checks, formal solvers, execution, unit
tests, citations, and independent review remain the promotion authority.

## 9. Evaluation, calibration, and reproducibility

| Resource | Date | Reuse decision |
|---|---:|---|
| [HELM paper](https://arxiv.org/abs/2211.09110) and [code](https://github.com/stanford-crfm/helm) | 2022 | Metric/scenario taxonomy; HELM enters maintenance mode in 2026, so do not make it Gludd's sole engine |
| [BIG-bench](https://arxiv.org/abs/2206.04615) | 2022 | Broad collaborative tasks; saturation and contamination require fresh holdouts |
| [MMLU](https://arxiv.org/abs/2009.03300) | 2020 | Broad knowledge baseline, not an agent or research benchmark |
| [GPQA](https://arxiv.org/abs/2311.12022) | 2023 | Graduate-level questions designed to resist simple web lookup |
| [SWE-bench](https://arxiv.org/abs/2310.06770) and [code](https://github.com/swe-bench/SWE-bench) | 2023 | Repository issue resolution with executable tests |
| [SWE-bench Live](https://arxiv.org/abs/2505.23419) | 2025 | Fresher tasks reduce contamination risk |
| [GAIA](https://arxiv.org/abs/2311.12983) | 2023 | Real questions requiring browsing, tools, and multi-step reasoning |
| [AgentBench](https://arxiv.org/abs/2308.03688) | 2023 | Multi-environment agent evaluation |
| [WebArena](https://arxiv.org/abs/2307.13854) | 2023 | Reproducible web tasks |
| [OSWorld](https://arxiv.org/abs/2404.07972) | 2024 | Multimodal computer-use tasks |
| [CORE-Bench](https://arxiv.org/abs/2407.16791) | 2024 | Computational reproducibility from papers and artifacts |
| [RE-Bench](https://arxiv.org/abs/2411.15114) | 2024 | Long-horizon ML research-engineering environments |
| [ScienceAgentBench](https://arxiv.org/abs/2410.05080) | 2024 | Data-driven scientific-discovery tasks |
| [lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness) | Mature code | Default adapter for standard LM tasks; pin task config, prompt, tokenizer, and backend |
| [Inspect AI](https://inspect.aisi.org.uk/) and [code](https://github.com/UKGovernmentBEIS/inspect_ai) | Mature code | Default adapter for safety/agent eval definitions and transcripts |
| [OpenAI Evals](https://github.com/openai/evals) | Code/reference | Reuse datasets/eval patterns when licensing and provider independence fit |
| [Promptfoo](https://www.promptfoo.dev/) | Mature code | Optional CI/red-team adapter, especially cross-provider prompt matrices |
| [Lessons from reproducible LM evaluation](https://arxiv.org/abs/2405.14782) | 2024 | Prompt, implementation, and backend details materially change results |
| [Language Models (Mostly) Know What They Know](https://arxiv.org/abs/2207.05221) | 2022 | Elicited confidence can support selective answering but must be calibrated |
| [Semantic uncertainty](https://arxiv.org/abs/2302.09664) | 2023 | Cluster meaning-equivalent generations to detect uncertain answers |
| [Position bias in LLM judges](https://arxiv.org/abs/2406.07791) | 2024 | Pairwise judge order must be swapped and aggregated |
| [Self-preference bias in LLM judges](https://arxiv.org/abs/2410.21819) | 2024 | A candidate's own model family must not be the only judge |
| [Benchmark contamination survey](https://arxiv.org/abs/2406.04244) | 2024 | Every evaluation record needs contamination risk and data cutoff |

### 9.1 Required evaluation hierarchy

1. Deterministic schema, security, and executable correctness checks.
2. Task-specific reference or property-based metrics.
3. Calibrated model judges only where deterministic grading is insufficient.
4. Blinded human review for high-impact or ambiguous promotions.
5. Production outcome monitoring with explicit counterfactual caveats.

Model-graded metrics must store judge identity/version, prompt hash, ordering,
raw rationale, and repeated scores. Candidate and judge independence is a
policy decision, not a prompt suggestion.

## 10. Safety, security, privacy, and governance

| Resource | Date | Design requirement |
|---|---:|---|
| [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework) | 2023 | Govern, map, measure, and manage risk throughout the lifecycle |
| [NIST Generative AI Profile](https://doi.org/10.6028/NIST.AI.600-1) | 2024 | GenAI-specific risks and actions |
| [NIST Adversarial ML taxonomy](https://doi.org/10.6028/NIST.AI.100-2e2025) | 2025 | Prompt injection/jailbreak and other AML threats |
| [NCSC secure AI development guidelines](https://www.ncsc.gov.uk/collection/guidelines-secure-ai-system-development) | 2023 | Secure design, development, deployment, and operation |
| [OWASP Top 10 for LLM Applications](https://genai.owasp.org/llm-top-10/) | Living | Prompt injection, sensitive disclosure, supply chain, excessive agency, and resource risks |
| [MITRE ATLAS](https://atlas.mitre.org/) | Living | Adversarial tactics and techniques for AI systems |
| [AI Incident Database](https://incidentdatabase.ai/) | Living | Real incident discovery and taxonomy |
| [Indirect prompt injection](https://arxiv.org/abs/2302.12173) | 2023 | Retrieved content can control tool-using agents unless isolated |
| [Anthropic browser prompt-injection defenses](https://www.anthropic.com/research/prompt-injection-defenses) | 2025 | Defense in depth helps but no browser agent is immune |
| [Anthropic sabotage evaluations](https://www.anthropic.com/research/sabotage-evaluations) | 2024 | Evaluate sandbagging, code sabotage, oversight manipulation, and decision influence |
| [Anthropic agentic-misalignment experiments](https://www.anthropic.com/research/agentic-misalignment) | 2025 | High-autonomy, sensitive-access simulations justify least privilege and oversight |
| [OpenAI Preparedness Framework](https://openai.com/index/updating-our-preparedness-framework/) | Living | Capability thresholds and safeguards; vendor framework is an input, not Gludd policy |
| [Anthropic Responsible Scaling Policy](https://www.anthropic.com/responsible-scaling-policy) | Living | Capability-linked safeguards |
| [Google DeepMind Frontier Safety Framework](https://deepmind.google/blog/introducing-the-frontier-safety-framework/) | Living | Critical capability levels and early-warning evaluations |
| [EU AI Act](https://eur-lex.europa.eu/eli/reg/2024/1689/oj) | 2024 law | Risk classification, documentation, transparency, and governance where applicable |
| [OECD AI Principles](https://oecd.ai/en/ai-principles) | Living principles | Human-centered values, transparency, robustness, and accountability |
| [Opacus](https://opacus.ai/) | Mature code | Optional differential-privacy training adapter |
| [Adversarial Robustness Toolbox](https://github.com/Trusted-AI/adversarial-robustness-toolbox) | Mature code | Optional adversarial robustness tests |

### 10.1 Non-negotiable controls

- Retrieved text is delimited as untrusted data and cannot alter system policy.
- Every tool call is separately authorized against a capability, tenant,
  project, resource, and risk class.
- Secrets and personal data are removed before model, memory, trace, or eval
  persistence.
- Training-data use records consent/license, retention, source, transformations,
  and deletion lineage.
- A candidate may never modify its evaluator, promotion policy, capability
  checks, audit trail, or rollback mechanism.
- High-risk actions require operator approval even when the model is confident.

## 11. Efficiency, training systems, and serving

| Resource | Date | Reuse or measurement |
|---|---:|---|
| [FlashAttention](https://arxiv.org/abs/2205.14135) and [FlashAttention-2](https://arxiv.org/abs/2307.08691) | 2022/2023 | IO-aware exact attention; benchmark on supported hardware |
| [ZeRO](https://arxiv.org/abs/1910.02054) and [DeepSpeed](https://github.com/microsoft/DeepSpeed) | 2019/living | Partitioned optimizer/training state; use upstream |
| [Megatron-LM](https://arxiv.org/abs/2104.04473) and [code](https://github.com/NVIDIA/Megatron-LM) | 2021 | Tensor, pipeline, and data parallel composition |
| [PyTorch FSDP](https://pytorch.org/docs/stable/fsdp.html) | Living | Preferred PyTorch-native sharding adapter for suitable workloads |
| [GPTQ](https://arxiv.org/abs/2210.17323) | 2022 | Post-training weight quantization |
| [AWQ](https://arxiv.org/abs/2306.00978) | 2023 | Activation-aware weight quantization |
| [Speculative decoding](https://arxiv.org/abs/2211.17192) | 2022 | Exact-distribution acceleration with a draft model |
| [PagedAttention/vLLM](https://arxiv.org/abs/2309.06180) and [code](https://github.com/vllm-project/vllm) | 2023 | High-throughput serving and KV-cache paging |
| [SGLang](https://arxiv.org/abs/2312.07104) and [code](https://github.com/sgl-project/sglang) | 2023 | Structured LM programs and prefix reuse |
| [NVIDIA Triton Inference Server](https://github.com/triton-inference-server/server) | Mature code | Multi-framework serving, batching, and metrics |
| [KServe](https://kserve.github.io/website/) | Mature code | Kubernetes-native model serving and rollout |
| [Ray Serve](https://docs.ray.io/en/latest/serve/) | Mature code | Python-native distributed serving and composition |
| [BentoML](https://docs.bentoml.com/) | Mature code | Packaging and service deployment |
| [CodeCarbon](https://mlco2.github.io/codecarbon/) | Mature code | Optional energy/carbon observation, not a quality proxy |

**Required Gludd measurements:** time to first token, inter-token latency,
throughput, queue time, peak RAM/VRAM, CPU/GPU utilization, cold-start time,
cost, energy when available, output quality, and error rate. An optimization is
not a win if it changes task correctness or safety beyond the declared bound.

## 12. Continual learning and safe self-improvement

| Resource | Date | Evidence and limitation |
|---|---:|---|
| [STaR](https://arxiv.org/abs/2203.14465) | 2022 | Bootstrap rationales from correct answers; depends on trusted answer signals |
| [Self-Refine](https://arxiv.org/abs/2303.17651) | 2023 | Iterative feedback/refinement improves selected tasks without weight updates |
| [Reflexion](https://arxiv.org/abs/2303.11366) | 2023 | Episodic verbal reinforcement |
| [Self-Play Fine-Tuning](https://arxiv.org/abs/2401.01335) | 2024 | Iterative self-play preference data; quality and collapse require monitoring |
| [Self-Rewarding Language Models](https://arxiv.org/abs/2401.10020) | 2024 | Model-generated rewards; evaluator capture is a central risk |
| [Spontaneous reward hacking in self-refinement](https://arxiv.org/abs/2407.04549) | 2024 | Shared generator/evaluator can improve reward while reducing human preference |
| [DSPy paper](https://openreview.net/forum?id=PFS4ffN9Yx) and [code](https://github.com/stanfordnlp/dspy) | 2023/living | Compile LM programs against explicit metrics |
| [TextGrad](https://github.com/zou-group/textgrad) | 2024/living | Textual feedback optimization; experimental and judge-dependent |
| [OPRO](https://arxiv.org/abs/2309.03409) | 2023 | LLM as optimizer over textual solutions |
| [Promptbreeder](https://arxiv.org/abs/2309.16797) | 2023 | Evolutionary self-referential prompt adaptation |
| [Voyager](https://arxiv.org/abs/2305.16291) | 2023 | Curriculum, skill library, and iterative environment feedback |
| [Darwin Gödel Machine](https://arxiv.org/abs/2505.22954) and [code](https://github.com/jennyzzt/dgm) | 2025 | Archive-based code-agent evolution with empirical benchmarks, sandboxing, and oversight |
| [Continual learning survey](https://arxiv.org/abs/2302.00487) | 2023 | Stability/plasticity, replay, regularization, and architectural methods |
| [Avalanche](https://avalanche.continualai.org/) | Mature code | Optional continual-learning experiment library |

### 12.1 Safe interpretation for Gludd

The papers show that iterative systems can improve measured performance; they
do **not** justify unconstrained recursive self-modification. Gludd must:

- improve a candidate in an isolated workspace;
- preserve the incumbent and a diverse archive;
- evaluate on versioned train/development/holdout/adversarial sets;
- use deterministic checks before model judges;
- forbid candidate access to hidden tests and promotion policy;
- require a statistically meaningful win with zero safety regressions;
- canary the candidate under bounded traffic;
- roll back automatically on outcome or resource regression; and
- retain operator authority and a global kill switch.

## 13. Interpretability and scientific diagnosis

| Resource | Date | Use |
|---|---:|---|
| [A Mathematical Framework for Transformer Circuits](https://transformer-circuits.pub/2021/framework/index.html) | 2021 | Transformer circuit decomposition |
| [In-context learning and induction heads](https://transformer-circuits.pub/2022/in-context-learning-and-induction-heads/index.html) | 2022 | Mechanistic hypothesis for in-context learning |
| [Toy Models of Superposition](https://transformer-circuits.pub/2022/toy_model/index.html) | 2022 | Feature superposition and sparse representations |
| [ROME/causal tracing](https://arxiv.org/abs/2202.05262) | 2022 | Causal tracing and model editing; localization is not a complete explanation |
| [Tracr](https://arxiv.org/abs/2207.06991) | 2022 | Compile programs into transformers for ground-truth interpretability studies |
| [Automated Circuit Discovery](https://arxiv.org/abs/2304.14997) | 2023 | Automated causal circuit search |
| [Scaling Monosemanticity](https://transformer-circuits.pub/2024/scaling-monosemanticity/index.html) | 2024 | Sparse features at larger scale; interpretation remains incomplete |
| [TransformerLens](https://github.com/TransformerLensOrg/TransformerLens) | Mature code | Activation caching, patching, ablation, and model adapters |
| [SAELens](https://github.com/decoderesearch/SAELens) | Mature/research code | Train and analyze sparse autoencoders |
| [NNsight](https://github.com/ndif-team/nnsight) | Mature/research code | Interventions and remote/local model inspection |

Interpretability output is evidence about a model, not an automatic safety
certificate. The expert must state whether a method is correlational,
attributional, or causally interventional and preserve the exact model revision.
Gludd already contains
`src/general_ludd/physics/mechanistic_interpretability.py`; the collection
should expose and test those primitives rather than create a second
implementation.

## 14. Multimodal learning

| Resource | Date | Capability/failure surface |
|---|---:|---|
| [CLIP](https://arxiv.org/abs/2103.00020) | 2021 | Contrastive vision-language representation; dataset bias and adversarial robustness matter |
| [Flamingo](https://arxiv.org/abs/2204.14198) | 2022 | Few-shot interleaved visual-language input |
| [LLaVA](https://arxiv.org/abs/2304.08485) and [code](https://github.com/haotian-liu/LLaVA) | 2023 | Visual instruction tuning |
| [ImageBind](https://arxiv.org/abs/2305.05665) and [code](https://github.com/facebookresearch/ImageBind) | 2023 | Shared embedding space across six modalities |
| [Whisper](https://arxiv.org/abs/2212.04356) and [code](https://github.com/openai/whisper) | 2022 | Weakly supervised speech recognition/translation |
| [Segment Anything](https://arxiv.org/abs/2304.02643) and [code](https://github.com/facebookresearch/segment-anything) | 2023 | Promptable image segmentation |
| [LLaVA-NeXT](https://github.com/LLaVA-VL/LLaVA-NeXT) | Living code | Evolving multimodal model reference |
| [VHELM](https://crfm.stanford.edu/helm/vhelm/latest/) | Living benchmark | Holistic vision-language evaluation |

Multimodal evidence needs modality-specific parsers, hashes, timestamps, spatial
or temporal citations, accessibility metadata, and adversarial tests. OCR or
transcription output is derived evidence and must link to the original region.

### 14.1 Photo generation, editing, control, and media provenance

| Resource | Date | Capability and boundary |
|---|---:|---|
| [DALL-E 2/unCLIP](https://arxiv.org/abs/2204.06125) and [Imagen](https://arxiv.org/abs/2205.11487) | 2022 | Text-image generation references; reported prompt alignment is model/data specific |
| [Latent Diffusion](https://arxiv.org/abs/2112.10752) and [SDXL](https://arxiv.org/abs/2307.01952) | 2021/2023 | Latent generation and larger cascaded pipeline; record VAE, text encoders, scheduler, and resolution |
| [Hugging Face Diffusers](https://github.com/huggingface/diffusers) | Mature code | Preferred optional generation/editing adapter; Gludd owns policy, provenance, and evaluation |
| [DreamBooth](https://arxiv.org/abs/2208.12242) | 2022 | Subject-driven personalization; consent, memorization, and identity misuse require explicit review |
| [ControlNet](https://arxiv.org/abs/2302.05543) and [official code](https://github.com/lllyasviel/ControlNet) | 2023 | Spatial conditioning from edges/depth/pose; condition extraction is a versioned transformation |
| [T2I-Adapter](https://arxiv.org/abs/2302.08453) | 2023 | Lightweight control adapters |
| [IP-Adapter](https://arxiv.org/abs/2308.06721) | 2023 | Image-prompt adapter; reference-image rights and identity risks remain |
| [InstructPix2Pix](https://arxiv.org/abs/2211.09800) and [code](https://github.com/timothybrooks/instruct-pix2pix) | 2022 | Instruction-driven editing; retain original, mask/region, instruction, and output |
| [Segment Anything](https://arxiv.org/abs/2304.02643), [Grounding DINO](https://arxiv.org/abs/2303.05499) | 2023 | Optional region/grounding tools; predictions are derived annotations |
| [FID](https://arxiv.org/abs/1706.08500), [CLIPScore](https://arxiv.org/abs/2104.08718), [GenEval](https://arxiv.org/abs/2310.11513) | 2017/2021/2023 | Distribution, alignment, and compositional metrics; none measures all quality or safety dimensions |
| [C2PA specification](https://spec.c2pa.org/specifications/) | Living standard | Sign and validate creation/edit lineage when supported; missing or stripped credentials do not prove an asset is authentic |

Image generation and editing need separate operations. Generation creates a new
asset from prompts/conditions; editing preserves an ingredient graph and
declared region/operation intent. Both must record model and component digests,
adapter weights/scales, scheduler, seed/generator state, dimensions, precision,
device/backend, safety decisions, input ingredient digests, and output pixel
digest. Exact pixels are not guaranteed across all hardware/backends; the
manifest must say whether replay is byte-exact, numerically bounded, or merely
configuration-complete.

Long-lived implementation issues include Diffusers
[per-image seed recovery issue 208](https://github.com/huggingface/diffusers/issues/208)
and [Kohya-style LoRA compatibility issue 4348](https://github.com/huggingface/diffusers/issues/4348).
Forum users continue to encounter model/LoRA/workflow incompatibilities and
deprecated parameter guidance. This supports explicit pipeline-component
compatibility matrices and golden-image tolerances rather than a generic
“supports LoRA” flag.

## 14A. Mathematics, theorem proving, and scientific discovery

### 14A.1 Mathematics and formal proof

| Resource | Date | Design use |
|---|---:|---|
| [Minerva](https://arxiv.org/abs/2206.14858) | 2022 | Quantitative reasoning model; generated derivations still require checking |
| [Lean 4](https://github.com/leanprover/lean4), [mathlib4](https://github.com/leanprover-community/mathlib4) | Mature code | Preferred formal-proof adapter for an initial implementation; pin toolchain and library commit |
| [LeanDojo](https://arxiv.org/abs/2306.15626) and [code](https://github.com/lean-dojo/LeanDojo) | 2023 | Reproducible Lean environments and theorem-proving interaction |
| [miniF2F](https://arxiv.org/abs/2109.00110), [ProofNet](https://arxiv.org/abs/2302.12433), [PutnamBench](https://arxiv.org/abs/2407.11214) | 2021–2024 | Formal-math benchmark slices; track statement translations and duplicates |
| [AlphaGeometry](https://www.nature.com/articles/s41586-023-06747-5) and [official code](https://github.com/google-deepmind/alphageometry) | 2024 | Neural proposal plus symbolic deduction for geometry |
| [AlphaProof/AlphaGeometry 2 report](https://deepmind.google/blog/ai-solves-imo-problems-at-silver-medal-level/) | 2024 | Formal proof can certify a result, but compute and manual formalization remain important |
| [DeepSeek-Prover](https://arxiv.org/abs/2405.14333) and [code](https://github.com/deepseek-ai/DeepSeek-Prover-V1.5) | 2024 | Open theorem-proving model and data-generation reference |
| [SymPy](https://www.sympy.org/), [SageMath](https://www.sagemath.org/), [Z3](https://github.com/Z3Prover/z3) | Mature code | Exact algebra, number theory, optimization/SMT tools; record assumptions and versions |

The role must distinguish numeric calculation, symbolic manipulation, informal
derivation, counterexample search, and formal proof. Only a kernel-accepted proof
may be labeled `formally_verified`; successful numeric tests or a plausible
natural-language proof are weaker evidence. Translation between natural and
formal statements is a separate, human-reviewable artifact.

Lean/mathlib versions move together: the official
[dependency guide](https://github.com/leanprover-community/mathlib4/wiki/Using-mathlib4-as-a-dependency)
requires a matching Lean toolchain. Lean
[issue 4190](https://github.com/leanprover/lean4/issues/4190) also shows how a
misleading local “No goals” message can coexist with errors elsewhere. Gludd
must trust the full build/kernel exit state, not a model or one UI message.

### 14A.2 Scientific discovery and experimentation

| Resource | Date | Evidence and caution |
|---|---:|---|
| [AlphaFold](https://www.nature.com/articles/s41586-021-03819-2) | 2021 | Domain-specific scientific prediction with confidence estimates; prediction is not experimental validation |
| [FunSearch](https://www.nature.com/articles/s41586-023-06924-6) | 2023 | LLM proposals plus executable evaluator and evolutionary search |
| [ChemCrow](https://arxiv.org/abs/2304.05376) | 2023 | Chemistry tool use improved tasks; paper reports that GPT-4 evaluation could not distinguish clearly wrong baselines, supporting expert/deterministic checks |
| [Coscientist](https://www.nature.com/articles/s41586-023-06792-0) | 2023 | Semi-autonomous chemistry planning and laboratory control under a bounded setup |
| [AI Scientist](https://arxiv.org/abs/2408.06292) and [code](https://github.com/SakanaAI/AI-Scientist) | 2024 | End-to-end ML research loop; automated review is not independent scientific validation |
| [ScienceAgentBench](https://arxiv.org/abs/2410.05080) | 2024 | 102 expert-validated data-driven tasks across four disciplines |
| [BLADE](https://arxiv.org/abs/2408.09667) | 2024 | Open-ended data-driven science evaluation |
| [CORE-Bench](https://arxiv.org/abs/2407.16791) and [RE-Bench](https://arxiv.org/abs/2411.15114) | 2024 | Computational reproducibility and research-engineering benchmarks |

Gludd may propose hypotheses, literature maps, simulations, analysis plans, and
bounded computational experiments. It may not claim discovery from novelty
scores, automated reviews, or one successful run. Physical, biomedical,
chemical, environmental, or human-subject actions require domain policy,
qualified human approval, and the relevant institutional/safety process.
Negative results, preregistered analyses, units, controls, uncertainty,
multiple-testing corrections, raw data, environment, and replication attempts
are first-class artifacts.

A persistent
[AI Scientist installation/use thread](https://www.reddit.com/r/learnmachinelearning/comments/1fuw2yb/)
asks whether users can reproduce the system outside its demonstrated fields;
discussion of the original release also disputes equating an automated review
threshold with a strong paper. The Gludd consequence is strict separation of
hypothesis generation, experiment execution, statistical analysis, independent
review, and real-world validation.

## 15. Production ML and observability

| Project/resource | Reuse decision |
|---|---|
| [MLflow](https://www.mlflow.org/docs/latest/) | Optional adapter for experiment/run/model metadata; do not duplicate it inside Gludd |
| [DVC](https://dvc.org/doc) | Optional dataset/artifact versioning for filesystem/Git workflows |
| [Feast](https://docs.feast.dev/) | Optional feature-store adapter where online/offline feature consistency is needed |
| [Kubeflow Pipelines](https://www.kubeflow.org/docs/components/pipelines/) | Optional Kubernetes pipeline backend |
| [Weights & Biases](https://docs.wandb.ai/) | Optional hosted experiment backend; never a required source of truth |
| [OpenLineage](https://openlineage.io/docs/) | Standard lineage event model worth adapting |
| [OpenTelemetry](https://opentelemetry.io/docs/) and [GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) | Canonical trace/metric transport for model and agent operations |
| [Great Expectations](https://docs.greatexpectations.io/) | Optional data-contract adapter |
| [Pandera](https://pandera.readthedocs.io/) | DataFrame schema checks suitable for deterministic gates |
| [KServe](https://kserve.github.io/website/) | Model serving/canary backend where Kubernetes is available |

The source of truth remains Gludd's project/task/evidence records. External
systems are adapters with stable IDs and export/import contracts, not required
global singletons.

## 16. Long-lived practitioner and user issue evidence

Forum reports are not controlled studies. They are included because they expose
recurring failure classes that benchmark-only design misses.

| Thread/issue | Date | Persistent signal | Specification consequence |
|---|---:|---|---|
| [Agents + LLM in production](https://www.reddit.com/r/MachineLearning/comments/1b5j322/) | 2024-03-03 | Practitioners report trajectory divergence, poor reliability, opacity, and a preference for testable structured steps | Explicit state machines, bounded iterations, typed outputs, terminal assertions, and per-step traces |
| [LangGraph tool-message endless loop](https://github.com/langchain-ai/langgraph/discussions/1097) | 2024 | Tool output was not interpreted and the agent loop could continue | Loop detector must examine repeated state/action/observation tuples, not rely on prompting |
| [LangChain invalid tool after call](https://github.com/langchain-ai/langchain/issues/1559) | 2023 | Tool identity/schema integration has failed across versions and model adapters | Pin tool schema/version; validate round-trip compatibility before dispatch |
| [RAG observations](https://www.reddit.com/r/LocalLLaMA/comments/1jn5ngq/) | 2025-03 | Even small structured corpora hallucinated when retrieval/context handling was weak | Evaluate parse/retrieval independently and show retrieved evidence in traces |
| [Where production RAG effort goes](https://www.reddit.com/r/LocalLLaMA/comments/1hnbam5/) | 2024-12 | Practitioners spend substantial effort on chunking and retrieval, not only model choice | Make chunker/retriever/reranker first-class versioned candidates |
| [RAG that actually works?](https://www.reddit.com/r/LocalLLaMA/comments/1ps6txq/rag_that_actually_works/) | 2025-12 | Long technical PDFs produce missed facts; discussion points to structure-aware parsing, hybrid retrieval, and gold questions | Section-aware parsing, hybrid search, and corpus-specific recall tests |
| [Monitoring ML APIs](https://www.reddit.com/r/mlops/comments/ze1ddc/) | 2022-12 | Drift monitoring is operationally separate from the production request path | Shadow/sample evaluation must not destabilize production |
| [MLOps platform comparison](https://www.reddit.com/r/MachineLearning/comments/m1fnrs/) | 2021-03 | Teams combine orchestration, tracking, and deployment systems; no single tool owns the lifecycle | Adapter architecture; avoid inventing another monolithic MLOps platform |
| [Model artifacts with MLflow and DVC](https://www.reddit.com/r/mlops/comments/13np3tk/) | 2023-05 | Users struggle with overlapping artifact ownership and deployment retrieval | One canonical artifact ID plus explicit replicas/lineage |
| [vLLM documentation and dependency friction](https://www.reddit.com/r/LocalLLaMA/comments/1mn98w0/vllm_documentation_is_garbage/) | 2025-08 | CUDA/backend/context defaults cause repeated installation and OOM failures | Preflight compatibility matrix, container/pin capture, and fail-closed memory budget |
| [Stable-Baselines3/PettingZoo compatibility](https://www.reddit.com/r/reinforcementlearning/comments/1098un0/) | 2023-01 | Gym-to-Gymnasium API changes broke environment interoperability | Record API generation and run adapter conformance tests |
| [Best evaluations for reasoning and instruction following](https://www.reddit.com/r/LocalLLaMA/comments/16txkg8/) | 2023-09 | Users cannot infer which benchmark answers their deployment question | Evaluation registry must map a capability claim to a justified task suite |
| [Prompt injection with production agent access](https://www.reddit.com/r/cybersecurity/comments/1sx66fe/how_is_your_org_handling_prompt_injection_now/) | 2026-05 | Practitioner concern remains despite filters and OWASP guidance | Defense in depth plus least privilege; regex is never the sole control |
| [Agent framework comparison: debug latency](https://www.reddit.com/r/AI_Agents/comments/1tp335p/i_compared_8_opensource_ai_agent_frameworks_so/) | 2026-06 | Framework feature lists hide abstraction and debugging cost | Measure failure diagnosis time and trace completeness before dependency adoption |
| [PEFT multiple-adapter switching issue](https://github.com/huggingface/peft/issues/1802) | 2024-06 | Active and merged adapters produced surprising differences | Validate base/adapter state, active set, merge state, and golden outputs before serving |
| [PEFT merged-versus-enabled issue](https://github.com/huggingface/peft/issues/1226) | 2023-10 | Enabled adapter and merged adapter behavior differed in a reported configuration | Merge equivalence is a testable property, never assumed |
| [HF Datasets Arrow/Feather mismatch](https://github.com/huggingface/datasets/issues/2377) | 2021-01 | Users treated a streaming Arrow representation as an Arrow IPC/Feather file | Format adapters declare exact variant and run cross-reader conformance |
| [HF Datasets memory growth](https://github.com/huggingface/datasets/issues/4883) | 2022-08 | Resident memory increased in a data-loader workload | Stream tests measure peak RSS, file descriptors, cache, and cleanup |
| [SearXNG JSON API discussion](https://github.com/searxng/searxng/discussions/1789) | 2022-09 | JSON is disabled unless the instance enables the format | Preflight source capabilities and fall back explicitly |
| [Diffusers per-image seed issue](https://github.com/huggingface/diffusers/issues/208) | 2022-07 | Reproducing images in batched generation needed per-image generator state | Record generator/seed per output, not one request-level seed |
| [Lean “No goals” diagnostic issue](https://github.com/leanprover/lean4/issues/4190) | 2024-05 | A local success-looking message could distract from errors elsewhere | Formal proof status comes only from the complete pinned build/kernel result |
| [Stale vector index production incident](https://www.reddit.com/r/LocalLLaMA/comments/1r69w5y/rag_failure_in_production_our_vector_store_served/) | 2026-02 | Derived embeddings contradicted current structured state | Track index watermarks/tombstones and prioritize authoritative current state |
| [AI Scientist reproducibility question](https://www.reddit.com/r/learnmachinelearning/comments/1fuw2yb/) | 2024-10 | Users struggled to reproduce the demonstrated system in other fields | Domain transfer and setup reproducibility are explicit evaluation gates |

### 16.1 Failure themes that persist across years

1. Dependency and accelerator combinations are fragile.
2. Agent loops fail at state transitions, not only at language reasoning.
3. Tool-call schemas drift across model/provider/framework boundaries.
4. Retrieval quality, chunking, and document structure dominate many RAG
   failures.
5. Offline metrics miss feedback loops, operational workarounds, and semantic
   objective drift.
6. Artifact/data/model ownership becomes ambiguous when several MLOps tools are
   combined.
7. Benchmark selection and prompt formatting materially change conclusions.
8. Security controls remain incomplete when agents receive broad permissions.
9. Adapter/checkpoint names conceal base-model and serving incompatibilities.
10. Dataset “format” names conceal variants, schemas, streaming semantics, and
    memory behavior.
11. Media seeds are insufficient provenance without the full pipeline and
    component state.
12. Formal or experimental verification must be read from the authoritative
    checker, not a plausible explanation or partial UI message.

## 17. Existing Gludd capability map

The new work must extend these implementations rather than create parallel
systems.

| Existing component | Reuse | Verified limitation relevant to this spec |
|---|---|---|
| `agents/researcher.py::ResearcherAgent` | Search orchestration and structured report models | One search pass; URL validation is syntactic; confidence is domain/recency/lexical-overlap heuristic rather than claim entailment |
| `retrieval/research_index.py::ResearchIndex` | Topic/source/citation persistence and freshness | Default path is user-global; no project/tenant namespace, content hash, DOI/version, license, correction, or retraction fields |
| `retrieval/agentic_context.py::AgenticContextInjector` | Token-bounded, confidence-labeled context | Retrieved text is inserted into a prompt; it needs an explicit untrusted-content boundary and injection classification |
| `memory/embedding_store.py::MemoryEmbeddingStore` | Deterministic local vector fallback | Not a complete hybrid scholarly index or production ANN backend |
| `memory/procedural.py::ProceduralMemory` | Learned workflow representation | Procedures need evidence, version, outcome, expiry, and safety scope before reuse |
| `scoring/router.py::AdaptiveRouter` | Historical quality/cost/health routing | Needs capability/risk/calibration dimensions and immutable evaluation identities |
| `models/gateway.py::ModelGateway` | Provider-neutral model calls | Expert logic must stay outside provider-specific prompts |
| `eval/harness.py::EvalHarness` | Small deterministic offline test seam | Currently patch-centric and too narrow for retrieval, research, agent, safety, and statistical evaluation |
| `ag15_benchmarks/benchmark_harness.py::BenchmarkSuite` | External benchmark result shape | Needs run isolation, environment hashes, seeds, raw artifacts, confidence intervals, and contamination metadata |
| `compaction/arena.py::SelfImprovingCompactor` | Champion/challenger and no-regret promotion pattern | Domain-specific; generalization must prevent candidate/judge coupling and persist promotion evidence |
| `ag13_dspy` | Existing prompt-spec/optimizer seam | Upstream DSPy is mature; retain the local deterministic fallback but benchmark an upstream adapter before expanding custom optimizer code |
| `ag14_reflexion` | Existing episodic reflection loop | Reflections are untrusted hypotheses until external outcome checks validate them |
| `self_improve/harness.py::SelfImprovementHarness` | Recurring-failure ingest and gap proposal seam | Model output can become findings without an evidence graph or experiment; current write path audit shows live-tree mutation risk |
| `projects.workspace` and `git_automation` | Isolated per-project workspaces and commits | Must become mandatory for Gludd self-targeting |
| `security/capability_lattice.py` | Default-deny dispatch/self-modification checks | Current self-improvement roles are broad; new capabilities must separate research, experiment, propose, promote, and deploy |
| `physics/mechanistic_interpretability.py` | Existing interpretability primitives | Expose through the collection; do not duplicate |
| `config/ai_sdlc.yml` and `agent.sdlc_gate` | Evidence-token and lifecycle-stage pattern | Extend with dataset/model/eval/promotion evidence rather than creating a competing lifecycle |
| `skills/skill.py`, `loader.py`, and `registry.py` | Existing skill discovery, tools, triggers, model profile, project precedence | Skill metadata lacks input/output schema, capabilities, budgets, evidence policy, and eval suite; a skill body must remain guidance rather than authority |
| `ansible/paths.py::resolve_collections_paths` | Project/user/bundled collection precedence | The ML/AI expert should be a normal bundled `general_ludd.ml_ai_expert` collection, overridable through the existing project tier |
| `models/model_registry.py` and `models/quantization.py` | Model revision downloads and quantization metadata | No adapter artifact/base-compatibility registry, merge state, or adapter routing contract exists |
| Generic artifact/run/eval/sandbox seams | Isolation, recording, and evaluation foundations | Source search found no dedicated dataset-format, PEFT-training, image generation/editing, or formal-proof subsystem; add adapters instead of hiding these jobs in prompts |

## 18. Mature dependency decisions

| Need | Decision | Reason |
|---|---|---|
| Scholarly metadata | Adapt Crossref and OpenAlex; Semantic Scholar optional | Mature APIs provide DOI/version/citation metadata; no custom crawler |
| HTML extraction | Evaluate `trafilatura` behind an adapter | Mature extraction and metadata support; retain original bytes/hash |
| PDF parsing | Evaluate PyMuPDF plus optional GROBID service | Reuse mature parsers; tables/equations still require quality checks |
| Lexical retrieval | PostgreSQL full-text search | PostgreSQL is already a Gludd system dependency |
| Vector retrieval | `pgvector` production adapter; existing `MemoryEmbeddingStore` deterministic fallback | Avoid a second mandatory database; keep offline tests network-free |
| Embeddings | Existing `HashEmbedder` for tests; optional sentence-transformers/provider adapters | Determinism locally, semantic quality when explicitly configured |
| Standard LM evaluation | Adapter to lm-evaluation-harness | Mature task catalog and reproducibility practices |
| Safety/agent evaluation | Adapter to Inspect AI | Maintained evaluation/transcript framework |
| RAG metrics | Adapter to RAGAS plus deterministic retrieval metrics | Do not depend solely on LLM-as-judge |
| Experiment metadata | Extend Gludd IDs/events; optional MLflow/OpenLineage export | Gludd remains authoritative while avoiding custom external-platform clones |
| Prompt/program optimization | Benchmark upstream DSPy; reuse current local seam only as fallback | Mature upstream project already solves the general optimization problem |
| Distributed training/serving | Adapt PyTorch FSDP/DeepSpeed/vLLM/KServe/Ray as chosen per environment | Never implement custom collectives or serving schedulers |
| Parameter-efficient tuning | Hugging Face PEFT first, AdapterHub optional | Reuse LoRA/QLoRA/DoRA/IA3/prompt adapter implementations; Gludd owns manifests, policy, experiments, and promotion |
| Adapter serving | Existing model gateway plus optional vLLM; evaluate S-LoRA/Punica patterns | Keep one authoritative compatibility/routing layer and avoid a custom GPU kernel runtime |
| Tensor artifacts | `safetensors` by default | Avoid executable pickle checkpoints; still validate shape/dtype/digest/size |
| Dataset interface | Hugging Face Datasets optional with Arrow/Parquet/JSONL/WebDataset adapters | Keep a Gludd logical dataset manifest and deterministic JSONL fallback |
| Large tabular processing | PyArrow/Polars/DuckDB adapters selected by workload | Do not write a columnar engine; require bounded memory and cross-format conformance |
| Web metasearch | Existing/self-hosted SearXNG first | Preserve privacy/control and multiple engines; vendor APIs remain policy-configured fallbacks |
| Web archives/bulk discovery | Internet Archive and Common Crawl adapters | Use indexes before content and isolate bulk resource budgets |
| Learned retrieval/reranking | Sentence Transformers, FAISS/pgvector, optional ColBERT/SPLADE | Do not write embedding, ANN, or cross-encoder frameworks |
| Graph analysis | PostgreSQL/RDF/NetworkX for bounded local work; GraphRAG/graph-store adapters only when evaluated | Graph-derived facts retain source spans; no mandatory graph database |
| Image generation/editing | Hugging Face Diffusers adapter | Reuse pipelines/schedulers/LoRA/ControlNet; Gludd supplies authorization, provenance, reproducibility, safety, and evals |
| Media provenance | C2PA SDK/tool adapter | Use the standard signature/manifest model; missing credentials remain `unknown`, not `authentic` |
| Symbolic mathematics | SymPy/SageMath/Z3 adapters | Prefer exact tools over model arithmetic and retain assumptions/tool proofs |
| Formal theorem proving | Lean 4 + pinned mathlib/LeanDojo first | Trust the kernel; other provers join through the same protocol |
| Scientific workflows | Existing scientific Python/R tools inside isolated project environments | Gludd orchestrates and records; it does not replace domain solvers, laboratory safety, or peer review |

## 19. Design hypotheses to test, not assume

| Hypothesis | Minimum experiment |
|---|---|
| Hybrid retrieval beats vector-only for Gludd documentation | Frozen query/evidence set; compare recall@5/10, nDCG, latency, and storage over at least three corpus versions |
| Query decomposition improves complex answer correctness | Blinded paired evaluation on multi-hop questions with identical source/tool budgets |
| Independent verification reduces unsupported claims | Compare unsupported-claim and abstention rates with/without verifier; judge order swapped |
| Procedural memory reduces repeated failures | Randomized shadow routing; measure success, steps, cost, and new failure classes |
| Task-specific routing improves quality/cost | Champion/default versus adaptive policy on the same held-out task stream |
| Prompt optimization improves generalization | Separate optimizer train/dev and untouched holdout; scan for leakage and overfitting |
| Model-based gap proposals find useful improvements | Human-blinded precision of accepted proposals plus implemented outcome lift |
| Self-improvement promotion is net positive | Lower confidence bound above required margin, zero safety/gate regressions, and successful canary window |

## 20. Research refresh and provenance protocol

Each ingested source record must store:

- canonical URL, DOI or repository identity, title, authors/owner;
- publication, update, retrieval, and last-validation timestamps;
- source type, venue, peer-review state, version, correction/retraction state;
- original content hash and normalized extraction hash;
- license/terms and allowed uses;
- parser and parser version;
- claims/evidence spans derived from the source;
- citations and contradictory evidence;
- project/tenant namespace and retention policy; and
- the query and policy revision that admitted the source.

Refresh defaults:

- model/provider/pricing/security advisories: seven days;
- living libraries and standards: thirty days;
- benchmark implementations: before every comparative run;
- papers: ninety days, plus DOI/correction/retraction event;
- foundational books/papers: annually unless a correction appears.

On refresh failure, retain the last snapshot with a visible stale status. Never
silently replace evidence or increase confidence because a source disappeared.

## 21. Conclusion

The research does not support building a static encyclopedia prompt or a
self-editing model. It supports an evidence-oriented expert system with typed
roles, task-specific tools, calibrated uncertainty, reproducible experiments,
independent evaluation, least privilege, and reversible champion/challenger
rollout. The companion specification converts that conclusion into stable Gludd
feature IDs, acceptance tests, rollout phases, and implementation seams.
