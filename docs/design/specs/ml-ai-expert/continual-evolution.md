## 8. Continual research and governed capability evolution

These units operationalize the dossier's primary evidence on
[horizon scanning](https://arxiv.org/abs/2202.13480),
[scientific research agents](https://arxiv.org/abs/2404.07738),
[idea-evaluation limits](https://arxiv.org/abs/2409.04109),
[negative/missing evidence](https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-13),
[human authority and independent assessment](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/),
[provenance](https://www.w3.org/TR/prov-o/),
[poisoning](https://arxiv.org/abs/2302.10149),
[reward overoptimization](https://arxiv.org/abs/2210.10760), and
[silent drift](https://proceedings.neurips.cc/paper/2019/hash/846c260d715e5b854ffad5f70a516c88-Abstract.html).
The persistent
[AI Scientist rate-limit](https://github.com/SakanaAI/AI-Scientist/issues/84)
and
[PaperQA resumability](https://github.com/Future-House/paper-qa/issues/381)
reports are mandatory failure fixtures, not anecdotes used to claim general
performance.

### MLCONT.1 — Versioned horizon-scan scope and cadence

**Status:** Not implemented

**Contract:** Define each recurring or one-shot scan by immutable scope revision:
questions, concepts/synonyms, Gludd components, source/domain/language/time
slices, inclusion/exclusion policy, cadence, freshness, expected outputs, risk,
resource profile, owner, and expiry. Record the prior watermark and do not start
a duplicate overlapping run with the same idempotency key.

**Acceptance:** Unit tests reject an unversioned or unowned scope; scheduler tests
prove exact-once logical admission across restart and duplicate delivery;
changing scope creates a new digest without rewriting prior runs; an expired or
disabled scope performs no fetch and records a terminal reason.

### MLCONT.2 — New issue, topic, idea, and weak-signal discovery

**Status:** Not implemented

**Contract:** Discover new and materially changed papers, standards, official
documentation, releases, code, issues/discussions, security advisories,
practitioner reports, benchmarks, datasets, patents/grants when configured, and
local Gludd outcomes. Normalize each observation into a signal with source
identity/version, first/last seen, change digest, affected capability, source
class, trust label, and uncertainty.

**Acceptance:** Time-sliced fixtures detect a new issue, changed API, corrected
paper, new code release, resurfaced duplicate, and deleted page exactly once;
source and language strata appear in coverage; popularity alone cannot admit or
rank a signal; unavailable strata remain visible missing coverage.

### MLCONT.3 — Reproducible deep web, paper, code, and forum research

**Status:** Not implemented

**Contract:** Execute the dossier section 12.3 process as a typed state machine:
preregister, registry refresh, search, immutable capture, identity resolution,
screening, extraction, disconfirmation, gap mapping, hypothesis generation,
ranking, agenda publication, and proposal. Preserve queries, cursors/pages,
ranks, fetch/parse outcomes, inclusion/exclusion reasons, locators, hashes,
fallbacks, and budget use.

**Acceptance:** A frozen mixed web/paper/code/forum corpus reproduces the same
admitted identities and claim graph from the run manifest; interrupted search
resumes from durable per-source checkpoints without duplicate fetch/admission;
429, parser failure, paywall, deletion, and archive fallback appear in the
report; a second implementation replays the trace without provider-private CoT.

### MLCONT.4 — Evidence-linked knowledge-gap detection

**Status:** Not implemented

**Contract:** Generate typed gaps only from unsupported, contradicted, stale,
out-of-domain, unreplicated, low-calibration, or repeatedly failing claims and
from missing capability/evaluation/source slices. Store the searched evidence,
blind spots, operational consequence, uncertainty, falsifier, cheapest next
check, expiry, and supersession graph.

**Acceptance:** Fixtures distinguish unsupported from disproved, absent evidence
from negative evidence, stale from retracted, and model uncertainty from source
coverage; synthetic recurring failures produce one deduplicated gap; resolving,
superseding, or invalidating a gap never erases its prior evidence.

### MLCONT.5 — Transparent novelty, impact, feasibility, and risk ranking

**Status:** Not implemented

**Contract:** Compare every idea to nearest prior art across identity, lexical,
semantic, citation, repository, issue/forum, and archive evidence. Report
novelty, expected impact against declared project outcomes, feasibility,
information gain, evidence strength, uncertainty, safety, cost, reversibility,
and source diversity separately; expose a Pareto frontier and configurable human
priorities rather than an opaque universal scalar.

**Acceptance:** Exact and paraphrased duplicates lose novelty; an idea with high
citation/social velocity but no project impact cannot outrank solely on
popularity; adversarial judge-order and self-citation fixtures do not change
deterministic dimensions; unavailable novelty sources yield `unknown`, not
`novel`; blinded human review can reproduce the displayed ranking inputs.

### MLCONT.6 — Negative, null, contradictory, and failed evidence

**Status:** Not implemented

**Contract:** Run explicit searches for contradiction, correction, withdrawal,
retraction, null/negative results, failed replication, security incident,
regression, abandoned project, and maintainer/user failure evidence. Preserve
these records in the claim and agenda graphs with the same identity, provenance,
screening, and freshness treatment as supporting evidence.

**Acceptance:** Gold fixtures cover separate Crossmark retraction records,
in-place correction, null result, failed replication, issue regression, and
searched-but-not-found; the first five can lower or block a claim while the last
only lowers coverage; deleting an unfavorable source from the current index does
not delete its immutable evidence or increase confidence.

### MLCONT.7 — Human-governed research agenda lifecycle

**Status:** Not implemented

**Contract:** Convert signals and gaps into a versioned agenda item containing
question/hypothesis, evidence graph, alternatives including no change, expected
information gain/outcome, minimum experiment, falsifier, budget, dependencies,
risks, owner, review/expiry date, and state. Only an authorized human may accept,
rescope, defer, reject, or close an item; automation may recommend a transition.

**Acceptance:** State-machine tests reject skipped, self-approved, unsigned, and
stale transitions; concurrent updates use compare-and-swap and retain both
proposals; rejected/failed items remain searchable negative evidence; agenda
ordering can be recomputed from recorded inputs without changing human decisions.

### MLCONT.8 — Source-registry discovery and zero-downtime refresh

**Status:** Not implemented

**Contract:** Periodically revalidate each source adapter's API/schema version,
capabilities, authentication, terms/robots, rate and concurrency observations,
pagination/cursor semantics, freshness, corrections/retractions, parser,
health, and fallbacks. Build a candidate registry beside the champion, replay
contract fixtures, shadow/dual-read, reconcile, then atomically move a versioned
pointer; retain the prior registry for rollback.

**Acceptance:** Fixtures cover GitHub API version sunset, Semantic Scholar 429,
SearXNG JSON disabled, Crossref cursor interruption/update relation, schema
addition/removal, credential expiry, and primary/fallback disagreement; active
research continues on the champion during refresh; failed reconciliation leaves
the pointer unchanged; rollback is lossless and does not restart admitted runs.

### MLCONT.9 — Evidence-backed Gludd core proposal synthesis

**Status:** Not implemented

**Contract:** Convert an accepted agenda item into a core-system proposal with
problem/outcome, affected existing seams, source and local-outcome evidence,
alternatives/OSS reuse decision, threat model, schemas/APIs, compatibility and
data migration, ZDD rollout/rollback, resource model, atomic requirement IDs,
first failing acceptance tests, evaluation plan, dependencies, and non-goals.
The output is a proposal artifact, not a repository mutation.

**Acceptance:** Schema tests reject a proposal without nearest existing seams,
OSS comparison, negative evidence, measurable outcome, tests, rollout, or
rollback; a fixture proposes an improvement to an existing Gludd seam without
inventing duplicate infrastructure; no proposal role has live-tree, promotion,
or deployment capability.

### MLCONT.10 — Collection, role, and skill proposal generation

**Status:** Not implemented

**Contract:** Propose new or changed collection/role/skill artifacts as explicit
versioned diffs containing input/output schemas, capabilities, tools/sources,
resource profile, evidence policy, evaluation suite, triggers, compatibility,
deprecation/migration, safety boundaries, and content digest. The skill body
cannot grant authority, and project overrides cannot weaken hard policy.

**Acceptance:** Golden proposals add a role, split an overloaded role, update a
skill, and retire a skill through side-by-side compatibility; tests reject
implicit tools, capability escalation, missing digest/eval, hidden policy
changes, and direct incumbent edits; unchanged inputs produce the same proposal
digest.

### MLCONT.11 — Isolated candidate materialization and compatibility

**Status:** Not implemented

**Contract:** Materialize only a human-accepted proposal in a project-isolated,
namespaced candidate workspace with immutable champion/proposal/source/eval
digests, least privilege, network policy, secret isolation, storage quota,
deadline, and cleanup. Build new schemas/artifacts beside old versions and
exercise forward/backward/rollback compatibility before shadow use.

**Acceptance:** Escape, symlink, cross-tenant, secret, network, budget, and
champion-write fixtures fail closed; interruption leaves the champion serving
and a resumable or safely disposable candidate; expand/migrate/contract tests
prove old and new readers during the transition; cleanup cannot remove the
champion or evaluation evidence.

### MLCONT.12 — Evaluation-driven champion/challenger promotion

**Status:** Not implemented

**Contract:** Evaluate candidate core/collection/role/skill artifacts against the
immutable champion on frozen capability, regression, transfer, adversarial,
security, resource, and rollback suites. Use deterministic oracles first,
independent blinded evaluators second, confidence intervals and predeclared
thresholds; require material improvement with no safety, compatibility, or
per-file coverage regression.

**Acceptance:** Candidate identity/order swaps do not change deterministic
outcomes; missing/timed-out cases count by the predeclared fail-closed rule;
holdout contamination, evaluator disagreement, insignificant lift, safety
regression, or resource breach prevents promotion; the complete per-case result
and decision replay from immutable artifacts.

### MLCONT.13 — Human authority and separation of duties

**Status:** Not implemented

**Contract:** Separate scanner, proposer, implementer, evaluator, policy owner,
approver, and deployer identities/capabilities. Require explicit human approval
for source-policy expansion, new external data/tool access, collection/role/skill
activation, core changes, holdout replacement, threshold change, or promotion.
No candidate or model judgment may mint, delegate, or satisfy that approval.

**Acceptance:** Authorization tests reject self-approval, model-generated
signature, expired/replayed approval, role collision forbidden by policy,
approval for a different digest, and post-approval mutation; emergency human
deny/stop overrides every automated recommendation; the audit proves who knew
which artifacts before the decision.

### MLCONT.14 — Pointer rollback, kill switch, and recovery

**Status:** Not implemented

**Contract:** Keep the last known-good core/collection/role/skill/source-registry
revision runnable and switch authority through an atomic versioned pointer.
Define automatic rollback on safety, correctness, latency, cost, resource,
drift, or error-budget breach plus an independent human kill switch and bounded
recovery procedure.

**Acceptance:** Fault-injection under concurrent traffic exercises each trigger,
stale compare-and-swap, partial migration, evaluator outage, and control-plane
restart; rollback meets the declared recovery objective without destructive
migration or request-schema break; the kill switch works when the candidate and
model providers are unavailable.

### MLCONT.15 — End-to-end research and evolution provenance

**Status:** Not implemented

**Contract:** Link signal, source snapshot, query, screening decision, claim,
gap, idea, agenda decision, proposal, code/config/data/model artifact,
experiment, evaluation, approval, rollout, observation, and rollback as
immutable entity/activity/agent records. Export W3C PROV-O and
SLSA/in-toto-compatible attestations while retaining compact Gludd IDs and
redacting secrets/private data.

**Acceptance:** A promoted and a rejected candidate each traverse back to exact
source/eval versions and responsible identities; digest tamper, missing edge,
wrong tenant, revoked signer, clock skew, and redaction fixtures return explicit
invalid/incomplete states; export/import round-trips preserve derivation and ZDD
pointer history.

### MLCONT.16 — Research poisoning and indirect-instruction defenses

**Status:** Not implemented

**Contract:** Treat all fetched papers, pages, repositories, issues, forums,
metadata, models, and generated critiques as untrusted data. Pin snapshots and
hashes, compare mutable/archive views, require source diversity for consequential
claims, detect duplicate/coordinated records and indirect prompt injection,
quarantine suspect evidence, and keep retrieval content out of policy,
instruction, capability, and approval channels.

**Acceptance:** Split-view, frontrunning, PoisonedRAG, malicious PDF/HTML,
repository-instruction, metadata-spoofing, coordinated-source, and stale-cache
fixtures cannot trigger a tool/policy action or verified claim; quarantine is
traceable and reversible; removing suspect evidence recomputes affected claims,
gaps, and rankings without rewriting the original run.

### MLCONT.17 — Reward hacking, evaluator capture, and contamination controls

**Status:** Not implemented

**Contract:** Prevent a candidate from reading hidden tests, modifying cases,
labels, metrics, judge prompts/models, thresholds, policy, provenance, resource
accounting, or promotion state. Use deterministic external checks, multiple
independence classes, blinded order, canaries, leakage/near-duplicate scans,
metric-component reporting, and periodic human audits; never optimize a single
model-judge score as the promotion objective.

**Acceptance:** Fixtures attempt answer-key discovery, metric tampering,
verbosity/style gaming, self-preference, sycophancy, benchmark memorization,
judge collusion, failure relabeling, cost hiding, and delayed trigger behavior;
each is detected or prevents promotion; optimizing the proxy while ground-truth
quality declines is a hard rejection with preserved evidence.

### MLCONT.18 — Evidence, behavior, evaluator, and objective drift

**Status:** Not implemented

**Contract:** Monitor source/topic coverage, claim validity, retrieval and answer
quality, calibration, task mix, costs/resources, safety, evaluator agreement,
human override, proposal acceptance, and post-promotion outcomes against
versioned baselines. Distinguish data, concept, schema, policy, objective, and
feedback-loop drift and route each to revalidation, shadow evaluation, rollback,
or a human agenda item rather than automatic retraining.

**Acceptance:** Controlled gradual, abrupt, seasonal, benign, malign, schema,
judge, policy, and objective shifts produce typed alerts with exemplars and
uncertainty; low-power/no-label conditions stay `unknown`; drift detection alone
does not mutate a model or threshold; post-promotion regression triggers
MLCONT.14 within its recovery objective.

### MLCONT.19 — Bounded, observable, release-aware research scheduling

**Status:** Not implemented

**Contract:** Enforce the `horizon_scan` profile and per-source quotas across
query/fetch/token/model/CPU/RAM/accelerator/disk/network/time/money dimensions.
Namespace every run and process, expose phase progress and heartbeats, checkpoint
long work, cap retries/concurrency, and yield admission to higher-priority release
and production work. A role cannot increase its own profile.

**Acceptance:** Boundary tests hit each ceiling with a typed partial/cancelled
result and intact checkpoint; 429/timeout retries honor headers and remain
bounded; restart and duplicate delivery do not double spend; load tests show a
release gate retains its declared resources and latency while a scan is paused
or throttled; no orphan process, lock, cache, or temporary artifact remains.

### MLCONT.20 — Safe self-evolution ceiling

**Status:** Not implemented

**Contract:** Allow the expert to research, answer, derive solutions, discover
gaps, synthesize Gludd core proposals, and propose improvements to its own
collection/roles/skills. Forbid autonomous live edits, authority expansion,
policy/evaluator/holdout mutation, deployment, or recursive spawning outside the
approved DAG. Progression is proposal -> human acceptance -> isolated
implementation -> independent evaluation -> human promotion -> ZDD canary, with
rollback available at every mutable stage.

**Acceptance:** An end-to-end fixture discovers a persistent Gludd issue,
reproduces deep research, proposes a new skill and core change, builds candidates
in isolation, and reaches a reviewable promotion record without touching live
state; adversarial requests to skip any stage, grant tools, reveal holdouts,
rewrite policy, self-approve, or suppress rollback fail closed; denial leaves the
champion unchanged and all useful research preserved.

### MLCONT.21 — Atomic claim and citation quality gate

**Status:** Not implemented

**Contract:** Decompose externally meaningful answer and proposal assertions
into atomic claims and evaluate citation locator validity, atomic entailment,
support completeness, contradiction, source quality, source independence,
version/integrity, and temporal scope. A citation URL or model-based entailment
score alone never verifies a claim; consequential claims require primary or
official evidence plus deterministic or authorized human verification when
available.

**Acceptance:** ALCE/FActScore-style fixtures include supportive, partially
supportive, irrelevant, contradictory, circular, mirrored, stale, retracted,
wrong-locator, and inaccessible citations; every unsupported clause remains
visible; dependent copies count as one independence group; evaluator
disagreement returns `partial` or `unknown`; the answer cannot report a higher
verified-claim count than the serialized claim/citation records reproduce.

### MLCONT.22 — Bitemporal validity, supersession, and as-of answers

**Status:** Not implemented

**Contract:** Store observation time separately from claimed valid-from/valid-to
time, with unknown bounds explicit. Append correction, supersession,
withdrawal, retraction, and deletion records instead of overwriting history.
Resolve current and as-of queries against one immutable knowledge snapshot and
prevent expired or future-invalid evidence from satisfying a present claim.

**Acceptance:** Fixtures cover an undated fact, scheduled API sunset, retroactive
correction, overlapping conflicting versions, retraction, deletion, late-arriving
older observation, and false-premise FreshQA-style question; current/as-of
answers select the expected versions and cite their temporal basis; rollback
reproduces the exact former answer without resurrecting evidence outside that
snapshot.

### MLCONT.23 — Practitioner, forum, and maintainer signal lifecycle

**Status:** Not implemented

**Contract:** Search canonical issue trackers/discussions and approved
practitioner forums for recurring failures, workarounds, regressions, operational
costs, and unmet needs. Preserve thread/comment identity, author/maintainer role,
timestamps, edits, issue state, affected versions/environment, reproducer,
reactions only as metadata, archive/digest, and corroboration. Default these
records to `practitioner_signal`; they may prioritize investigation but cannot
alone verify a general technical claim.

**Acceptance:** Fixtures distinguish maintainer confirmation, reproducible user
bug, duplicate reports, bot response, anecdote, edited/deleted post, coordinated
spam, popularity, resolved version, and still-open issue; a reproduced failure
links a local observation without rewriting the thread; search coverage includes
negative/closed results; likes or repetition cannot raise verification state.

### MLCONT.24 — Zero-downtime candidate knowledge snapshot

**Status:** Not implemented

**Contract:** Apply admitted source/claim changes to a versioned candidate
knowledge namespace, then reconcile source identities, temporal records,
tombstones, dependent chunks, embeddings, lexical index, graph edges, caches,
citations, access/deletion policy, and source/index watermarks. Run frozen
retrieval, answer, injection, privacy, resource, temporal, and rollback suites
before an authorized atomic pointer swap; retain champion and snapshot leases
for in-flight requests and replay. Enforce the `knowledge_refresh` profile; the
curator cannot expand it.

**Acceptance:** Concurrent update/query tests never observe a mixed snapshot;
insert/update/delete/retraction/parser/embedding/schema fixtures remove orphaned
derived state and preserve history; failed reconciliation or evaluation leaves
the champion pointer unchanged; in-flight champion requests finish while new
requests select the candidate; rollback restores the prior snapshot without
reindex or downtime.

### MLCONT.25 — Exposure-aware outcome and causal-learning ledger

**Status:** Not implemented

**Contract:** Record eligibility/context, assignment rule and probability,
selected revision, delivered intervention, immediate/delayed outcomes,
observation window, missingness, safety/cost/latency, human override, concurrent
changes, known confounders, prior Gludd-output exposure, and permitted learning
use. Prefer randomized shadow/canary evidence when safe; otherwise report
support/overlap, estimator assumptions, sensitivity, uncertainty, and
non-identifiability. Enforce the `outcome_analysis` profile. Outcome analysis
emits candidates, never live updates.

**Acceptance:** Fixtures cover randomized comparison, selection bias, zero/weak
propensity support, delayed/censored outcome, policy change, seasonality,
duplicate user, self-generated feedback, engagement proxy, override, rollback,
and no-effect result; causal claims appear only when the predeclared design
identifies them; off-policy estimates fail closed on inadequate support; raw
outcomes cannot directly mutate knowledge, procedure, role, skill, or thresholds.

### MLCONT.26 — Evidence-gated procedural and outcome memory

**Status:** Not implemented

**Contract:** Convert repeated validated outcomes into a candidate procedure or
calibration memory containing scope, preconditions, action, expected outcome,
counterevidence, provenance, independence class, confidence/calibration,
applicable versions, privacy/license, expiry, invalidation triggers, evaluation,
and rollback. Deduplicate semantically, preserve failures, and test old, new,
transfer, adversarial, and no-memory baselines to detect forgetting or harmful
overgeneralization.

**Acceptance:** A repeated successful procedure with independent outcomes can
reach human review; one anecdote, poisoned feedback, self-authored outcome,
expired version, privacy-disallowed record, or proxy-only gain cannot; tests
detect catastrophic forgetting and domain leakage; shadow lookup records whether
the memory would have changed an outcome; pointer rollback removes its authority
while preserving the evidence and rejected candidate.

### MLCONT.27 — Progressive role and skill revision rollout

**Status:** Not implemented

**Contract:** Version role and skill schemas, content, tools, capabilities,
budgets, triggers, evidence policy, and evaluation identity as one immutable
artifact. Install a candidate beside the champion; run static/schema/security
checks, replay, shadow decisions, bounded canary by eligible task slice, and
post-canary observation before pointer promotion. Existing in-flight DAGs pin
their revision. Tool/capability expansion requires separate human authority and
cannot ride a content-only approval.

**Acceptance:** Fixtures cover compatible addition, trigger change, schema
migration, tool addition, capability escalation, budget change, cyclic handoff,
bad shadow route, canary regression, mid-DAG pointer change, and rollback;
content-only changes cannot smuggle authority; champion and candidate outcomes
are attributed by exact revision; rollback affects new DAGs without corrupting
in-flight work.

### MLCONT.28 — Continual-loop health, staleness, and anti-drift audit

**Status:** Not implemented

**Contract:** Publish per-scope service objectives and distributions for source
coverage/diversity, registry health, scan/checkpoint age, source-to-index lag,
tombstone/orphan reconciliation, claim/citation support, stale-answer rate,
gap/agenda aging, proposal novelty/acceptance/outcome, evaluator agreement,
human override, memory use/lift, promotion/rollback, safety, resource cost, and
release interference. Compare against immutable baselines and detect metric
definition, objective, source, evaluator, and selection drift.

**Acceptance:** Synthetic outages, silent source loss, stale index, changed
metric denominator, evaluator upgrade, agenda starvation, popularity capture,
survivorship bias, rising cost, failed rollback, and release contention each
produce a typed alert with lineage and uncertainty; a green aggregate cannot
hide a failed required slice; the audit is read-only and cannot auto-relax its
SLO, change weights, retrain, promote, or suppress negative results.

### MLCONT.29 — Autonomous new Internet source discovery and onboarding

**Status:** Not implemented

**Contract:** Discover sources that are absent from the champion registry by
following typed Web links, API descriptions, repository metadata, scholarly
citations, archive/repository discovery records, standards registries, canonical
issue/forum references, and explicit coverage gaps. Normalize each result into
the section 3.5 candidate-source manifest, resolve mirrors and ownership, and
measure unique coverage, authority, independence, freshness, correction support,
terms/license, robots policy, privacy, authentication, rate/cost limits,
schema/parser stability, availability, archival fallback, SSRF/redirect/TLS
risk, untrusted-content risk, and required capabilities. A candidate remains
quarantined until sandboxed contract tests, shadow/dual-read comparison,
security and policy review, and explicit human approval succeed; only then may
`MLCONT.8` atomically promote the new registry revision. Discovery never grants
network or tool authority, creates an account or secret, installs source code, or
changes live source policy.

**Acceptance:** Fixtures discover a useful OAI-PMH/OpenAPI source and distinguish
it from a mirror, fork, search-result wrapper, paywall, abandoned endpoint, and
already registered source; robots denial, ambiguous terms/license, private-IP or
redirect SSRF, broken TLS, malicious metadata/instructions, schema drift,
pagination loss, 429, authentication request, and disappearing source remain
quarantined with typed reasons. A candidate with genuinely independent coverage
can reach human review through a reproducible discovery/probe ledger; denial,
timeout, failed shadow comparison, or absent approval leaves the champion
registry and active research unchanged.

### MLCONT.30 — Unattended autonomous research and improvement cycle

**Status:** Not implemented

**Contract:** Run registered research scopes without a live user question on a
versioned schedule or approved event trigger, including source/standard/library
changes, recurring Gludd failures, evaluation or outcome regressions, stale
coverage, and new issue/topic/idea signals. Each cycle preregisters scope,
budgets, source classes, policy, stop conditions, and outputs; invokes
`MLCONT.1`–`.7`, `.21`–`.23`, and `.29` as applicable; and may emit evidence,
gaps, agenda items, candidate-source manifests, or Gludd/expert improvement
proposals. Triggers and deliveries are deduplicated and idempotent. The cycle is
namespaced, checkpointed, heartbeat-visible, pausable, release-aware, and bounded
by `MLCONT.19`; it cannot autonomously accept its agenda, implement a candidate,
alter a champion, expand authority, or promote any result.

**Acceptance:** An end-to-end time/event replay with no user prompt discovers one
new topic, one recurring issue, one falsifiable idea, and one previously unknown
Internet source; performs reproducible deep and negative-evidence research;
deduplicates a repeated trigger; and produces reviewable source, expert, and
Gludd proposals with complete provenance. Empty scans remain valid negative
evidence rather than invented findings. Release contention pauses and resumes
from the same checkpoint without duplicate spend; cancellation, restart, stale
trigger, unavailable sources, insufficient evidence, and lack of human approval
leave every champion pointer unchanged.

### MLCONT.31 — Unified artifact promotion, dependency bundle, and rollback

**Status:** Not implemented

**Contract:** Apply one explicit transition protocol to source-registry adapters,
knowledge snapshots, procedure/outcome memories, collections, roles, skills,
Gludd core/config/schema/API changes, model adapters, routers, and evaluation
assets:
`proposed -> human_accepted -> materialized -> evaluated -> human_approved ->
shadow -> canary -> champion`, with terminal `rejected`, `rolled_back`, and
`retired` states. Every transition binds immutable artifact, dependency,
policy, capability, evaluation, approval, compatibility, migration, and rollback
digests. A multi-artifact improvement is one compatible dependency bundle:
prepare and evaluate all members beside their champions, then use versioned
compare-and-swap pointers or an atomic manifest pointer so partial promotion
cannot expose a mixed generation. In-flight work leases its admitted bundle.
Automatic rollback may select only a preapproved last-known-good bundle on a
declared breach; policy, evaluator, holdout, capability, or approval changes
always require their own human authority and cannot ride another artifact's
approval.

**Acceptance:** A table-driven conformance suite exercises every artifact class
and every legal/illegal transition, including missing or mismatched digests,
stale approval, skipped shadow/canary, capability smuggling, policy/holdout
mutation, partial materialization, failed migration, concurrent promotion,
stale compare-and-swap, mixed bundle revisions, in-flight lease, canary breach,
control-plane restart, rollback dependency ordering, and retirement. No failing
member changes an authoritative pointer; successful promotion exposes exactly
one compatible generation; rollback restores the complete last-known-good
bundle within its recovery objective while preserving provenance and rejected
evidence.
