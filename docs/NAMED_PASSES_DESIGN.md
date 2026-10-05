# AG.9 — Named Single-Purpose Passes Design

**Version:** 1.0 (2026-07-13)
**Status:** Design Proposal
**Dependencies:** AgentRegistry, PromptRegistry, ToolRouter
**Inspiration:** Amazon Strands Agents — named, composable specializations

---

## 1. Problem Statement

### 1.1 Current State

gludd agents are monolithic. Every agent — whether doing a security audit,
writing code, reviewing a PR, or running research — gets the same tool set
(read, write, edit, grep, glob, bash) and the same system prompt. The only
differentiation is the dispatch prompt text. There is no structural division
between "an agent that reviews" and "an agent that writes code."

This has concrete consequences:
- **Prompt bloat.** Every agent prompt carries the full AGENTS.md policy,
  including TDD rules, commit policy, and enforcement plugin descriptions —
  even when the agent is doing a read-only research task.
- **Tool set waste.** A review agent receives write/edit/bash tools it will
  never use. The prompt tells it not to use them; the tools are still
  registered, consuming context and risking accidental misuse.
- **No output contracts.** When a subagent returns "done," the orchestrator
  has no structured way to verify what was produced. A review pass should
  return a structured findings dict; a test pass should return pass/fail
  counts. Today, everything is free-text — the orchestrator parses ad-hoc.
- **No specialization reuse.** The pattern "send an agent to review code" is
  repeated in dozens of dispatch prompts with slight variation. There is no
  catalog, no shared definition, no improvement velocity.
- **Chaining is manual.** The orchestrator manually composes review → test →
  fix cycles by dispatching sequential subagents and parsing each result.
  There is no formal composition layer.

### 1.2 Target State

A **Named Pass** system: each pass is a named, versioned, composable
specialization that bundles its own tool set, system prompt, output schema,
and validation function. The orchestrator composes passes declaratively
instead of writing dispatch prompts by hand. Pass chaining is first-class:
the output of one pass flows as input to the next with schema validation at
each boundary.

---

## 2. Core Concepts

### 2.1 NamedPass

The atomic unit: a named specialization that constrains what an agent can do
and defines what it must produce.

```python
@dataclass
class NamedPass:
    name: str                    # unique pass identifier, e.g. "code-review-pass"
    version: str                 # semver — passes are versioned artifacts
    description: str             # human-readable purpose

    # Tooling constraint
    allowed_tools: set[ToolName] # exact set — no more, no less
                                 # ToolName = "read" | "write" | "edit" |
                                 #            "grep" | "glob" | "bash"

    # Behavioral constraint
    system_prompt: str           # injected into the agent's system prompt
                                 # replaces or augments the default AGENTS.md prompt

    # Output contract
    output_schema: dict          # JSON Schema for the expected return value
                                 # the pass MUST produce output matching this schema

    # Validation
    validation_fn: str           # import path to a callable(str) -> ValidationResult
                                 # or None if no post-hoc validation is needed

    # Metadata
    input_requirements: Optional[dict]  # schema the input must satisfy
    timeout_seconds: int         # per-pass timeout (default 300)
    model_preference: Optional[str]     # "sonnet" | "opus" | None (use default)
    tags: list[str]              # discoverability: "review", "test", "research"
```

Tool names are constrained to the opencode tool surface: `read`, `write`,
`edit`, `grep`, `glob`, `bash`. A pass registering `write` when its purpose
is "read-only review" fails pass validation at register time.

### 2.2 Pass Outcomes

Every pass execution produces a typed result. The orchestrator receives a
`PassOutcome` — not raw subagent text.

```python
@dataclass
class PassOutcome:
    pass_name: str
    pass_version: str
    status: PassStatus           # SUCCESS | FAILED | TIMEOUT | VALIDATION_ERROR
    output: dict                 # structured, schema-conformant output
    raw_output: str              # full subagent response (for debugging)
    validation: ValidationResult # from validation_fn, if configured
    duration_ms: int
    token_usage: TokenUsage      # prompt + completion tokens
    agent_id: str                # which agent executed it
    started_at: datetime
    finished_at: datetime

class PassStatus(enum.Enum):
    SUCCESS = "success"
    FAILED = "failed"            # agent returned but output didn't match schema
    TIMEOUT = "timeout"          # exceeded timeout_seconds
    VALIDATION_ERROR = "validation_error"  # validation_fn returned non-passing
```

The `PassOutcome` is the bridge between passes in a chain: the orchestrator
feeds `pass_N.outcome.output` as input to `pass_N+1`.

### 2.3 Pass Chaining

Passes compose into a `PassChain` — an ordered sequence of passes where each
pass's output becomes the next pass's input.

```python
@dataclass
class PassChain:
    name: str                    # e.g. "review-then-fix"
    passes: list[NamedPass]      # ordered — executed sequentially
    context: dict                # shared context carried across passes
    abort_on: PassStatus         # which statuses halt the chain (default: FAILED)
    max_retries: int             # per-pass retry on TIMEOUT (default: 1)
```

```python
@dataclass
class ChainOutcome:
    chain_name: str
    status: ChainStatus          # COMPLETED | ABORTED | PARTIAL
    pass_outcomes: list[PassOutcome]  # one per pass, in order
    final_output: dict           # output of the LAST pass in the chain
    total_duration_ms: int
    aborted_at_pass: Optional[str]    # which pass caused the abort
    aborted_reason: Optional[str]
```

Chain execution is sequential (not parallel) because each pass depends on the
prior pass's output. The orchestrator runs them in order, aborting the chain
if a pass returns a status in `abort_on`. On completion, the chain result
(`ChainOutcome`) is returned — not the raw subagent text.

### 2.4 Pass Lifecycle

```text
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ REGISTER │───▶│ VALIDATE │───▶│ DISPATCH │───▶│ VALIDATE │
│   pass   │    │  schema  │    │ to agent │    │  output  │
└──────────┘    └──────────┘    └──────────┘    └──────────┘
                                                     │
                     ┌───────────────────────────────┘
                     ▼
              ┌──────────┐
              │ RETURN   │
              │ PassOut- │
              │ come     │
              └──────────┘
```

1. **Register.** Pass definition is stored in the `PassRegistry`. At register
   time, the registry validates: tool names are known, output_schema is valid
   JSON Schema, validation_fn is importable (if set), allowed_tools is a
   subset of the full tool surface.

2. **Validate (pre-dispatch).** Before dispatch, the pass's
   `input_requirements` are checked against the incoming context. If the
   context fails the schema, the pass is rejected before any agent is spun up.

3. **Dispatch.** The orchestrator creates a subagent with the pass's trimmed
   tool set and specialized system prompt. The agent runs to completion.

4. **Validate (post-dispatch).** The raw output is parsed against
   `output_schema`. If parsing fails, the outcome is `FAILED` (or retried).
   If `validation_fn` is set, it runs. If validation fails, the outcome is
   `VALIDATION_ERROR`.

5. **Return.** The `PassOutcome` is returned to the orchestrator or passed to
   the next pass in the chain.

---

## 3. Pass Library

### 3.1 Built-in Passes

The following passes ship with gludd. Each is a concrete `NamedPass` definition
stored in `src/general_ludd/passes/library/`.

#### code-review-pass

| Field | Value |
|---|---|
| `allowed_tools` | `{read, grep, glob}` |
| `timeout_seconds` | 120 |
| `output_schema` | `{"type": "object", "properties": {"findings": {"type": "array", "items": {"$ref": "#/definitions/finding"}}, "summary": {"type": "string"}, "is_clean": {"type": "boolean"}}, "required": ["findings", "summary", "is_clean"]}` |
| `validation_fn` | `general_ludd.passes.validators.review:ReviewValidator` |

Produces a structured findings list. Each finding has `severity` (BLOCKER,
HIGH, MED, LOW, INFO), `file`, `line`, `message`, and `suggestion`. The
`is_clean` flag is `true` only when findings is empty. No write tools — the
pass physically cannot modify files, only report on them.

#### test-writing-pass

| Field | Value |
|---|---|
| `allowed_tools` | `{read, write, edit, grep, glob}` |
| `timeout_seconds` | 300 |
| `output_schema` | `{"type": "object", "properties": {"test_file": {"type": "string"}, "test_count": {"type": "integer"}, "collection_ok": {"type": "boolean"}, "pass_count": {"type": "integer"}, "fail_count": {"type": "integer"}}, "required": ["test_file", "test_count", "collection_ok", "pass_count", "fail_count"]}` |
| `validation_fn` | `general_ludd.passes.validators.test:TestPassValidator` |

Writes test files following TDD conventions. The `allowed_tools` include
`write` but exclude `bash` — the pass writes tests but never executes them.
Execution happens in a subsequent `test-runner-pass` (enforcing the
write-before-run discipline). The validator confirms the test file exists,
has valid syntax, and has the reported number of test functions.

#### test-runner-pass

| Field | Value |
|---|---|
| `allowed_tools` | `{read, bash}` |
| `timeout_seconds` | 600 |
| `output_schema` | `{"type": "object", "properties": {"test_file": {"type": "string"}, "passed": {"type": "integer"}, "failed": {"type": "integer"}, "errors": {"type": "integer"}, "collection_ok": {"type": "boolean"}, "output_summary": {"type": "string"}}, "required": ["test_file", "passed", "failed", "errors", "collection_ok"]}` |
| `validation_fn` | None |

Runs tests via `make test TESTFILE=...`. Can only use `read` and `bash`
(bash is restricted to `make test*` targets). Returns structured pass/fail
counts. This is the canonical "run the test suite" pass.

#### research-pass

| Field | Value |
|---|---|
| `allowed_tools` | `{read, grep, glob}` |
| `timeout_seconds` | 180 |
| `output_schema` | `{"type": "object", "properties": {"topic": {"type": "string"}, "findings": {"type": "array", "items": {"$ref": "#/definitions/research_finding"}}, "files_examined": {"type": "array", "items": {"type": "string"}}, "conclusion": {"type": "string"}, "confidence": {"type": "string", "enum": ["high", "medium", "low"]}}, "required": ["topic", "findings", "files_examined", "conclusion", "confidence"]}` |
| `validation_fn` | None |

Read-only research. Cannot write or execute. Returns structured findings
with source file references and a confidence level. The `files_examined`
field lets the orchestrator verify that relevant files were actually read.

#### fix-pass

| Field | Value |
|---|---|
| `allowed_tools` | `{read, write, edit, grep, glob, bash}` |
| `timeout_seconds` | 300 |
| `output_schema` | `{"type": "object", "properties": {"files_changed": {"type": "array", "items": {"type": "string"}}, "description": {"type": "string"}, "test_file": {"type": "string"}, "test_passed": {"type": "boolean"}}, "required": ["files_changed", "description"]}` |
| `validation_fn` | `general_ludd.passes.validators.fix:FixValidator` |

Full-tool pass for code changes. Has all tools (including bash for `make`
targets). The output contract requires listing every file changed — the
orchestrator can cross-check this against the git diff.

#### security-audit-pass

| Field | Value |
|---|---|
| `allowed_tools` | `{read, grep, glob}` |
| `timeout_seconds` | 180 |
| `output_schema` | `{"type": "object", "properties": {"scope": {"type": "string"}, "vulnerabilities": {"type": "array", "items": {"$ref": "#/definitions/vulnerability"}}, "secrets_found": {"type": "boolean"}, "findings_count": {"type": "integer"}}, "required": ["scope", "vulnerabilities", "secrets_found", "findings_count"]}` |
| `validation_fn` | None |

Read-only security review. Each vulnerability has `cwe_id`, `severity`,
`file`, `line`, `description`, and `remediation`.

### 3.2 User-Defined Passes

Users define custom passes in `<project>/.gludd/passes/`. A pass is a YAML
or JSON file conforming to the `NamedPass` schema:

```yaml
# .gludd/passes/custom-lint-pass.yml
name: custom-lint-pass
version: 1.0.0
description: "Run project-specific lint rules"
allowed_tools: [read, bash]
timeout_seconds: 120
output_schema:
  type: object
  properties:
    violations: {type: integer}
    details: {type: string}
  required: [violations, details]
validation_fn: null
model_preference: sonnet
tags: [lint, quality]
```

User-defined passes are loaded at daemon startup alongside the built-in
catalog. A user pass with the same name as a built-in pass shadows the
built-in (project-overrides-system pattern, consistent with the
project-collection precedence system).

---

## 4. Pass Composition

### 4.1 Common Chains

Chains are named, pre-configured sequences of passes. They live in the same
`PassRegistry` but have type `PassChain`.

#### review-then-fix

```json
[code-review-pass] → conditional? → [fix-pass] → [test-runner-pass]
       │                                  │              │
       │ is_clean: true                   │              │
       └──→ skip (green)                  └──────────────┘
```

The canonical "find problems, fix them, verify" cycle. The orchestrator
inspects the review pass's `output.is_clean` flag. If clean, the chain
completes after step 1. If not clean, the findings are passed to `fix-pass`,
which makes changes, then `test-runner-pass` verifies.

```python
CHAIN_REGISTRY["review-then-fix"] = PassChain(
    name="review-then-fix",
    passes=[review_pass, fix_pass, test_runner_pass],
    abort_on=PassStatus.FAILED,
    context={"_conditional_skip": {"at_index": 1, "condition": "prev.output.is_clean"}},
)
```

#### research-then-apply

```json
[research-pass] → [fix-pass] → [test-runner-pass]
```

Research the codebase first, then apply changes based on findings, then
verify. Used for tasks like "find all callers of X and update them."

#### test-write-then-run

```json
[test-writing-pass] → [test-runner-pass]
```

Write tests, then run them. The test-writing pass produces the test file
path; the test-runner pass executes it. This enforces the TDD
write-before-run discipline at the pass level.

#### full-gate-pass

```json
[lint-pass] → [typecheck-pass] → [test-runner-pass]
```

A composed gate: lint, then typecheck, then test. Each pass has its own
tool set (lint and typecheck are `{read, bash}`, test is `{read, bash}`).
The chain aborts on the first failure.

### 4.2 Chain DSL (Future)

A declarative syntax for composing passes:

```yaml
name: review-fix-verify-loop
max_iterations: 3
passes:
  - pass: code-review-pass
  - condition: "output.is_clean == false"
    then:
      - pass: fix-pass
      - pass: test-runner-pass
  - goto: 0  # loop back to review
```

This enables iterative refinement: review → fix → test → review again, up
to `max_iterations`. The chain terminates early if `is_clean` becomes `true`,
or after the max iteration count, whichever comes first.

---

## 5. Implementation Plan

### 5.1 Phase 1: Registry + Dispatch (core)

- `PassRegistry` class in `src/general_ludd/passes/registry.py`
  - Loads built-in passes from `passes/library/`
  - Loads user passes from `<project>/.gludd/passes/`
  - Handles shadowing (project overrides system)
  - Validates passes at register time (tools, schemas, importable validators)

- `PassDispatcher` class in `src/general_ludd/passes/dispatcher.py`
  - `dispatch(pass: NamedPass, context: dict) -> PassOutcome`
  - Trims agent tool set to `pass.allowed_tools`
  - Injects `pass.system_prompt` into agent context
  - Parses raw output against `pass.output_schema`
  - Runs `pass.validation_fn` if set
  - Returns `PassOutcome`

- Wire into the orchestrator's subagent dispatch path:
  `dispatch_subagent(...)` gains an optional `pass_name: str` parameter.
  When set, the dispatcher resolves the pass from the registry and applies
  its constraints.

### 5.2 Phase 2: Chaining

- `ChainRunner` class in `src/general_ludd/passes/chain.py`
  - `run(chain: PassChain, context: dict) -> ChainOutcome`
  - Sequential execution with abort-on-failure
  - Passes output from step N as input to step N+1
  - Conditional skip support (e.g., skip fix if review is clean)

- Wire into the orchestrator for multi-pass workflows.

### 5.3 Phase 3: Observability

- Every pass execution logs a `PassExecutionEvent` to the audit log.
- Pass metrics: duration, token usage, success rate per pass type.
- Chain metrics: end-to-end duration, abort rate, which pass aborted.
- Dashboard: pass library health (which passes are used, which fail most).

### 5.4 Phase 4: Composition DSL (Future)

- YAML-based chain definitions with conditionals and loops.
- `ChainCompiler` that compiles a YAML chain into a `PassChain` instance.
- UI in the TUI / CLI for browsing the pass library and composing chains.

---

## 6. Integration Points

### 6.1 With Agent Delegation (AG.7)

A `HandoffRequest` can specify a `target_pass` instead of (or in addition to)
a `to_agent_role`. The handoff router resolves the pass, selects an agent
capable of executing it, and dispatches. This lets a code-writing agent
hand off a review sub-task as `HandoffRequest(to_agent_role="reviewer",
target_pass="code-review-pass")`.

### 6.2 With Tool Router

The `allowed_tools` constraint is enforced by the `ToolRouter`. When a pass
dispatches with `allowed_tools={read, grep, glob}`, the tool router
deregisters `write`, `edit`, and `bash` from the agent's tool surface. The
agent physically cannot call them — not just "should not."

### 6.3 With Prompt Registry

Pass `system_prompt` strings are stored in the `PromptRegistry` as versioned
prompt templates. A pass references its prompt by name + version, not by
inline string, so prompts can be A/B tested and rolled back independently
of pass definitions.

### 6.4 With Permission System

Each pass carries an implicit permission intersection: the pass's
`allowed_tools` intersect with the agent's granted permissions and the
human's permission spec. A pass that requests `write` when the human has
`write: deny` fails at dispatch time (before any agent runs).

---

## 7. Design Decisions

### 7.1 Why not just prompt engineering?

Prompt-only specialization is fragile. An agent told "do not write files"
can still accidentally call `write` if the prompt is long or ambiguous. Tool
set restriction (removing `write` from the agent's tool surface) makes it
structurally impossible. The prompt tells the agent *how* to use its tools;
the tool set tells it *which* tools exist.

### 7.2 Why sequential chaining instead of parallel?

Passes in a chain feed output to the next pass. The review pass produces
findings; the fix pass consumes them. This is inherently sequential.
Parallelism happens at the orchestrator level: multiple independent chains
run concurrently.

### 7.3 Why a pass library instead of ad-hoc dispatch prompts?

Shared definitions create improvement velocity. When the code-review pass is
refined (better schema, stricter validation), every dispatch that uses it
improves. Ad-hoc prompts improve zero other dispatches. The library is the
single source of truth for "how does gludd review code?"

### 7.4 Why versioned passes?

Passes change. A new version of `code-review-pass` may add new fields to the
output schema or change the validation rules. Versioning lets the orchestrator
pin a specific pass version or accept `>=1.2.0`. Old chains continue to work
with their pinned versions until explicitly upgraded.

---

## 8. Open Questions

- **Should passes be model-aware?** A pass might require `sonnet` for cost
  efficiency or `opus` for complex reasoning. The `model_preference` field
  captures this, but enforcement depends on model availability at dispatch
  time.
- **Should pass outcomes be cacheable?** If the same pass with the same input
  runs twice, should the second invocation return a cached result? Cache
  invalidation on code changes is the hard problem.
- **How do passes interact with the floor-enforcement plugins?** A pass dispatches
  as a subagent. The floor enforcement counts it like any other dispatch. The
  pass's timeout interacts with the deadline plugin — a 600s test-runner-pass
  exceeds the 300s default task timeout and must be configured.

---

## 9. References

- Amazon Strands Agents: [strands-agents](https://github.com/strands-agents/strands) —
  named, composable agent specializations with constrained tool sets.
- AG.7 Agent Delegation Design: `docs/AGENT_DELEGATION_DESIGN.md`
- Tool Router: `src/general_ludd/agent/tool_router.py` (planned)
- Prompt Registry: `src/general_ludd/prompts/registry.py` (planned)
