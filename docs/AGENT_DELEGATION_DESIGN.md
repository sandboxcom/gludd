# AG.7 — Agent Delegation and Handoff Design

**Version:** 1.0 (2026-07-13)
**Status:** Design Proposal
**Dependencies:** EventLoop, AgentRegistry, PromptRegistry, HandoffBus

---

## 1. Problem Statement

### 1.1 Current State

gludd agents operate independently. The EventLoop dispatches a `JobSpec` to a
single worker running a single agent. That agent owns the task from claim to
completion. There is no mechanism for one agent to transfer work to another.

Subagent dispatch (when an agent delegates via the orchestrator's Task tool)
creates a fresh subagent with zero context from the parent aside from the
dispatch prompt. The subagent starts cold — no awareness of files the parent
read, decisions the parent made, or state the parent accumulated.

### 1.2 Why This Matters

- **Wrong-agent problem:** A code-writing agent encounters a security audit
  need. It has no way to hand off to a security-specialist agent. It either
  does a poor job itself or drops the sub-task entirely.
- **Context starvation:** When a long-running agent hits a wall (stuck,
  looping, context window full), it cannot pass its accumulated state to a
  fresh agent. The replacement starts from scratch.
- **Specialization wasted:** gludd has role-based agents (reviewer, tester,
  coder, security-auditor) but no routing fabric between them. Each agent is
  a silo.
- **Escalation non-existent:** An agent that discovers it lacks permissions
  or expertise has no escalation path — it logs an error and the task stalls.
- **Continuity gap:** If an agent is killed mid-task (OOM, timeout, API
  error), all its context is lost. No agent can resume from its state.

### 1.3 Target State

A structured handoff protocol that lets one agent transfer a task (or sub-task)
to another agent with a context bundle — selective state transfer so the
receiving agent starts informed, not cold. The handoff is observable (logged,
audited) and the ownership transfer is clear (exactly one agent is responsible
at any time).

---

## 2. Core Concepts

### 2.1 HandoffRequest

The structured envelope for an inter-agent task transfer.

```python
@dataclass
class HandoffRequest:
    from_agent_id: str          # originating agent UUID
    to_agent_role: str          # target role (coder, reviewer, security, ...)
    to_agent_id: Optional[str]  # specific agent UUID, or None for role-based routing
    task: str                   # human-readable task description
    context: ContextBundle      # selective state (see §5)
    priority: Priority          # HIGH | MEDIUM | LOW
    deadline: Optional[datetime]
    handoff_reason: HandoffReason  # ESCALATION | SPECIALIZATION | CONTINUITY | SUBTASK
    parent_todo_id: Optional[str]  # links to originating TodoModel
    handoff_id: str             # UUID, generated at creation
    created_at: datetime
```

`HandoffReason` enum:
- **ESCALATION** — agent lacks permissions/expertise, needs a different role.
- **SPECIALIZATION** — task has a sub-problem better suited to another agent type.
- **CONTINUITY** — current agent is dying (context full, killed, OOM); hands off
  to a fresh agent to resume.
- **SUBTASK** — agent is splitting work: it owns the parent, dispatches a child.

### 2.2 HandoffProtocol

The protocol governs the lifecycle of a handoff from initiation to completion.
Every transition is recorded as an `AuditEvent`.

```text
States:  PENDING → ACCEPTED → IN_PROGRESS → COMPLETED
              ↘ REJECTED  (agent refused or unavailable)
              ↘ EXPIRED    (deadline passed with no acceptance)
              ↘ FAILED     (receiver crashed mid-task)
```

Transitions:
1. **Initiation** (sender): `POST /api/handoffs` — sender creates a `HandoffRequest`.
   The daemon writes it to the `handoffs` table, publishes to the HandoffBus.
2. **Routing** (daemon): the HandoffBus delivers to matching agents (role filter
   or direct ID). If `to_agent_id` is set, route directly. If `to_agent_role`,
   broadcast to all agents of that role; first to accept wins.
3. **Acceptance** (receiver): `PUT /api/handoffs/{id}/accept` — receiver claims
   the handoff. State → ACCEPTED. The parent todo (if linked) transitions to
   `blocked_on_handoff` until completion.
4. **Execution** (receiver): receiver runs the task using the context bundle.
   Updates the handoff with progress / artifacts. State → IN_PROGRESS.
5. **Completion** (receiver): `PUT /api/handoffs/{id}/complete` with
   `HandoffResult` (output, artifacts, decision). State → COMPLETED. Parent
   todo unblocked with the result injected as `handoff_output`.
6. **Rejection** (receiver or system): if no agent accepts within
   `accept_timeout` (default 60s) or all target agents reject, state → EXPIRED.
   Sender is notified; handoff is re-routed or failed.
7. **Failure** (system): if receiver crashes or times out during execution,
   state → FAILED. Handoff is re-queued for another agent (max 3 retries).

### 2.3 ContextTransfer

The mechanism for selective context bundling. NOT a full conversation dump —
only the state the receiving agent needs.

**Design principle:** transfer the minimal set. More context = more tokens =
slower startup and higher cost. The sender curates; the receiver can request
more via `HandoffContextRequest`.

**Transfer policy:**
- Default: sender selects via `ContextSelector` heuristic (rule-based, ~80% accurate).
- Opt-in: receiver can pull additional context from the sender's trace via
  `GET /api/handoffs/{id}/context` (returns full `AgentTrace` if authorized).
- Configurable: per-agent-role `context_template` in the agent registry defines
  what fields are mandatory for that role (e.g., reviewer always gets the diff).

### 2.4 HandoffBus

Pub/sub message bus for agent-to-agent handoff messages. Built on the existing
daemon WebSocket infrastructure (`ws_manager` in `daemon.py`).

```text
Publisher (sender agent) → HandoffBus (daemon) → Subscribers (receiver agents)
```

**Bus features:**
- **Topic-based routing:** `handoff.role.{role}` and `handoff.agent.{id}` channels.
  Direct handoffs use the agent-id channel; role-based use the role channel.
- **At-most-once delivery:** no persistence replay. If no agent is subscribed
  when the message fires, the handoff goes to the DB table and is served via
  poll (`GET /api/handoffs/pending`).
- **Acceptance racing:** when multiple agents of the same role are subscribed,
  the first to `POST /api/handoffs/{id}/accept` wins. Others see 409 Conflict.
- **Observability:** every publish, accept, reject, and expiry is an `AuditEvent`
  with `category=handoff`. The handoff lifecycle is fully traceable.
- **Heartbeat:** agents subscribed to a role channel send periodic pings.
  Unresponsive agents are removed from the pool; their pending handoffs are
  re-routed.

---

## 3. Patterns from Existing Frameworks

### 3.1 CrewAI — Hierarchical Delegation

CrewAI models delegation as a manager→worker hierarchy. The manager receives a
task, decomposes it, delegates sub-tasks to workers, and synthesizes results.

**Relevant patterns for gludd:**
- **Explicit delegation:** the delegating agent defines the task, the expected
  output, and the context. The worker returns a structured result.
- **Role-based routing:** workers register with a role string. The manager
  dispatches by role, not by ID.
- **Synthesis:** manager merges worker outputs into a final answer. gludd's
  equivalent is the `parent_handoff_id` chain — a handoff can produce a child
  handoff whose result is ingested by the parent.

**What gludd adds:**
- **Non-hierarchical handoff:** CrewAI is manager→worker only. gludd supports
  peer-to-peer (agent A → agent B) and autonomous self-handoff (agent decides
  it needs help).
- **Continuity handoff:** CrewAI has no concept of a dying agent handing off
  mid-task. gludd's `CONTINUITY` reason handles this.
- **Observability:** CrewAI delegation is opaque (print/log only). gludd
  handoffs are `AuditEvent`-backed, queryable, and surfaced in the TUI.

### 3.2 AutoGen — Conversation-Based Handoff

AutoGen models handoff as one agent initiating a conversation with another.
Agents are conversational entities; handoff is `initiate_chat(recipient,
message, context)`.

**Relevant patterns for gludd:**
- **Structured context in the handoff message:** AutoGen's `clear_history` flag
  and `summary_method` control how much context transfers — analogous to gludd's
  `ContextBundle` selectivity.
- **Speaker selection:** AutoGen's `GroupChat` has a `speaker_selection_method`
  that picks the next agent. gludd's role-based routing + first-to-accept
  pattern is analogous but simpler (no LLM call to select the speaker).
- **Carryover:** AutoGen's `carryover` parameter passes context between agents.
  gludd's `ContextBundle` is the same concept, formalized.

**What gludd does differently:**
- **Not conversation-based:** gludd agents are task-executors, not chatbots.
  The handoff is a structured event, not a chat message. This avoids the
  AutoGen problem of agents getting stuck in infinite chat loops.
- **Ownership transfer is explicit:** AutoGen agents can talk to each other
  without clear ownership. gludd enforces exactly-one-owner via the handoff
  state machine — only the `owner` agent can transition the handoff.
- **Deadline and priority:** AutoGen has no concept of task deadlines or
  priorities in handoff. gludd's `HandoffRequest` carries both.

### 3.3 Key Design Decisions

| Decision | CrewAI | AutoGen | gludd (this design) |
|---|---|---|---|
| Topology | Hierarchical (manager→worker) | Peer-to-peer (conversation) | Any-to-any (peer + hierarchy + self) |
| Context transfer | Implicit (manager decides) | Summary or full history | Selective bundle (ContextBundle) |
| Ownership | Manager owns, workers execute | Ambiguous (chat is shared) | Exactly-one-owner state machine |
| Routing | Manager dispatch by role | Speaker selection (LLM) | Role-channel pub/sub + first-to-accept |
| Observability | Print/log only | Print/log only | AuditEvent per transition |
| Continuity | Not supported | Not supported | CONTINTUITY handoff reason |

---

## 4. Implementation Phases

### 4.1 Phase 1 — Direct Handoff (Minimal Viable)

**Goal:** Agent A can hand off a task to agent B with context.

**Scope:**
- `HandoffRequest` dataclass + DB model (`handoffs` table).
- `POST /api/handoffs` — create a handoff (sender).
- `PUT /api/handoffs/{id}/accept` — claim a handoff (receiver).
- `PUT /api/handoffs/{id}/complete` — deliver result (receiver).
- `ContextBundle` with file paths, task state, decisions (see §5).
- `HandoffBus` with direct-agent channel only (`handoff.agent.{id}`).
- HandoffReason: `ESCALATION`, `SUBTASK`, `CONTINUITY`.
- EventLoop integration: handoff PENDING → allocated to receiving agent's queue.

**Deliverables:**
- `src/general_ludd/handoff/models.py` — `HandoffRequest`, `HandoffResult`, `ContextBundle`.
- `src/general_ludd/handoff/bus.py` — `HandoffBus` (WebSocket-based pub/sub).
- `src/general_ludd/handoff/router.py` — direct + role-based routing logic.
- `src/general_ludd/db/migrations/versions/XXXX_handoffs_table.py` — `handoffs` table.
- `daemon.py` — `/api/handoffs` routes + bus startup wiring.
- `tests/unit/test_handoff_models.py`, `tests/unit/test_handoff_bus.py`,
  `tests/integration/test_handoff_e2e.py`.

**Acceptance criteria:**
- Agent A creates a handoff → appears in receiver's queue → receiver executes →
  result flows back to A's linked todo.
- Handoff state transitions are audited (6+ `AuditEvent` entries per lifecycle).
- Expiry: unaccepted handoff after 60s → `EXPIRED` state → sender notified.

### 4.2 Phase 2 — Manager-Coordinated Handoff (Role-Based Routing)

**Goal:** A manager agent (or the EventLoop) can route tasks to the best-fit
agent role without knowing specific agent UUIDs.

**Scope:**
- Agent role registration at startup (`coder`, `reviewer`, `security_auditor`,
  `tester`, `docs_writer`, `infra_engineer`).
- Role-channel pub/sub on `HandoffBus` (`handoff.role.{role}`).
- First-to-accept racing with conflict resolution (409 on late accept).
- `ContextSelector` heuristic: per-role template defines mandatory context fields.
- Manager agent prompt template with handoff syntax: `HANDOFF role=security_auditor
  priority=HIGH <task>...`.
- EventLoop `HandoffAllocator`: when a todo's `work_type` maps to a role with
  no active agents, creates a pending handoff rather than failing the dispatch.

**Deliverables:**
- `src/general_ludd/handoff/selector.py` — `ContextSelector` with per-role templates.
- `src/general_ludd/handoff/allocator.py` — `HandoffAllocator` (EventLoop integration).
- Agent registry (`src/general_ludd/agent/registry.py`) extended with role field.
- Manager prompt template (`templates/prompts/manager_with_handoff.md.j2`).
- `tests/unit/test_context_selector.py`, `tests/unit/test_handoff_allocator.py`,
  `tests/integration/test_handoff_manager_coordinated.py`.

**Acceptance criteria:**
- Manager dispatches `HANDOFF role=tester task="Run regression suite on commit X"` →
  a tester agent picks it up within 10s → result flows back to manager.
- Two tester agents subscribed: first to accept wins, second gets 409.
- `ContextSelector` for role `reviewer` always includes `diff`, `files_changed`,
  `base_branch` — verified in test.

### 4.3 Phase 3 — Autonomous Handoff (Self-Initiated Transfer)

**Goal:** An agent autonomously decides it needs help and initiates a handoff
without a manager prompt. The agent detects its own limitation (wrong role,
context full, stuck) and creates a handoff to offload or escalate.

**Scope:**
- `HandoffDecision` prompt injection: every agent's system prompt includes
  handoff guidance — when to escalate, how to format a `HANDOFF` command.
- `AgentMonitor`: daemon-side watchdog that detects stuck agents (no progress
  for N seconds, repeated tool-call patterns) and injects a handoff suggestion
  or forces a `CONTINUITY` handoff.
- `HandoffLoopGuard`: prevents infinite handoff chains (max depth = 3;
  circular handoff A→B→A detected and broken).
- Self-handoff for context-window management: agent detects context near limit,
  creates a `CONTINUITY` handoff with a context bundle, new agent resumes the
  task with a fresh context window.
- `HandoffAuditTrail`: full trace of every handoff in the handoff chain, stored
  on the parent todo so operators can trace "who did what."

**Deliverables:**
- Handoff guidance injected into all agent prompt templates (via
  `base_harness_aware.md.j2`).
- `src/general_ludd/handoff/monitor.py` — `AgentMonitor` (stuck-agent detection).
- `src/general_ludd/handoff/loop_guard.py` — `HandoffLoopGuard` (depth + cycle detection).
- `src/general_ludd/handoff/continuity.py` — context-window-aware self-handoff.
- `tests/unit/test_handoff_monitor.py`, `tests/unit/test_handoff_loop_guard.py`,
  `tests/integration/test_autonomous_handoff.py`.

**Acceptance criteria:**
- Agent with 190K-token context window (near 200K limit) self-initiates a
  `CONTINUITY` handoff → new agent resumes with a compact context bundle (5K tokens)
  and completes the task.
- Agent stuck in a loop (>5 repeated tool calls with no progress) → `AgentMonitor`
  forces a `CONTINUITY` handoff within 30s of detection.
- Circular handoff A→B→A is detected → `HandoffLoopGuard` breaks the chain and
  escalates to the operator as a `HumanTodo`.

---

## 5. Context Bundle Specification

### 5.1 Structure

```python
@dataclass
class ContextBundle:
    """Selective state transfer — only what the receiver needs."""

    # Task identity
    task_description: str              # what needs to be done (1-3 sentences)
    task_type: WorkType                # code, test, review, audit, ...
    parent_todo_id: Optional[str]

    # Current state
    current_phase: str                 # e.g. "implementing", "testing", "stuck"
    progress_summary: str              # what's been done so far (≤200 words)
    decisions_made: list[Decision]     # key decisions and their rationale

    # File context
    files_read: list[str]              # paths the sender has read
    files_modified: list[str]          # paths the sender has modified
    current_diff: Optional[str]        # unified diff of uncommitted changes
    relevant_paths: list[str]          # files the receiver should look at first

    # Environment
    working_branch: str
    commit_hash: Optional[str]
    python_version: str
    env_vars_relevant: dict[str, str]  # only env vars needed for the task

    # Artifacts
    test_output: Optional[str]         # last test run output (truncated)
    lint_output: Optional[str]         # last lint output (truncated)
    error_trace: Optional[str]         # stack trace if the sender hit an error

    # Handoff metadata
    handoff_reason: HandoffReason
    urgency: str                       # why this transfer now (1 sentence)
    receiver_needs_to: str             # explicit: "run the test suite" / "review the diff"
    sender_notes: str                  # freeform notes (≤500 chars)

    # Constraints
    max_tokens_budget: Optional[int]   # token budget for the receiver
    skip_steps: list[str]              # steps the receiver should NOT repeat
    required_tools: list[str]          # tools the receiver must have

@dataclass
class Decision:
    what: str       # the decision (≤80 chars)
    why: str        # rationale (≤200 chars)
    alternatives: list[str]  # alternatives considered and rejected
    made_at: datetime
```

### 5.2 Per-Role Templates (Phase 2)

Each role defines which fields are mandatory and which are optional. The
`ContextSelector` enforces this at handoff creation time.

```yaml
# config/handoff_context_templates.yml
roles:
  reviewer:
    mandatory: [task_description, files_modified, current_diff, decisions_made]
    optional: [test_output, lint_output, error_trace]
  tester:
    mandatory: [task_description, files_modified, relevant_paths, working_branch]
    optional: [current_diff, test_output, decisions_made]
  security_auditor:
    mandatory: [task_description, files_modified, current_diff, env_vars_relevant]
    optional: [decisions_made, error_trace]
  coder:
    mandatory: [task_description, files_read, decisions_made, working_branch]
    optional: [current_diff, test_output, relevant_paths]
  docs_writer:
    mandatory: [task_description, decisions_made, files_read]
    optional: [progress_summary]
  infra_engineer:
    mandatory: [task_description, env_vars_relevant, working_branch, commit_hash]
    optional: [files_modified, test_output]
```

### 5.3 Size Limits

| Field | Max size | Enforcement |
|---|---|---|
| `task_description` | 500 chars | truncate at creation |
| `progress_summary` | 200 words | summarize if over |
| `current_diff` | 10,000 lines | truncate with marker `[...truncated...]` |
| `test_output` / `lint_output` / `error_trace` | 5,000 chars each | tail of last N lines |
| `sender_notes` | 500 chars | truncate |
| `decisions_made` | 10 entries | drop oldest if over |
| `files_read` / `files_modified` | 50 paths each | drop least recent |
| `relevant_paths` | 20 paths | trim to most relevant |
| Entire `ContextBundle` JSON | 64KB | enforced at serialization |

### 5.4 Context Selector Heuristic (Phase 2)

The `ContextSelector.select(sender_agent, handoff_reason, to_role) → ContextBundle`
algorithm:

1. **Start with mandatory fields** from the role template (yaml above).
2. **Add handoff-reason fields:**
   - `ESCALATION` → always include `error_trace`, `decisions_made`, `sender_notes`.
   - `SPECIALIZATION` → always include `task_description`, `relevant_paths`, `receiver_needs_to`.
   - `CONTINUITY` → always include `progress_summary`, `files_read`, `files_modified`,
     `current_phase`, `decisions_made` (the most complete bundle).
   - `SUBTASK` → always include `task_description`, `receiver_needs_to` (the leanest bundle).
3. **Add optional fields** if they exist and fit within the 64KB budget.
4. **Deduplicate:** remove duplicate file paths, merge overlapping decisions.
5. **Validate:** check all mandatory fields are present and non-empty. If a mandatory
   field is empty, set it to `"<not available>"` rather than omitting — the receiver
   needs to know it was expected but absent.

---

## 6. EventLoop Integration

### 6.1 Handoff Lifecycle in the Event Loop

```text
Tick:
  1. Claim pending todos (existing flow).
  2. Allocate todos to agents (existing flow).
  3. Scan handoffs table for PENDING → broadcast to HandoffBus.
  4. Scan for ACCEPTED but stale (no heartbeat from receiver) → re-route or FAIL.
  5. Scan for COMPLETED → inject result into parent todo, unblock.
  6. Dispatch new jobs (existing flow).
```

### 6.2 Handoff Table Schema

```sql
CREATE TABLE handoffs (
    id UUID PRIMARY KEY,
    from_agent_id UUID NOT NULL,
    to_agent_id UUID,
    to_agent_role VARCHAR(64),
    handoff_reason VARCHAR(32) NOT NULL,
    priority VARCHAR(16) NOT NULL DEFAULT 'MEDIUM',
    deadline TIMESTAMP,
    parent_todo_id UUID,
    state VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    -- PENDING | ACCEPTED | IN_PROGRESS | COMPLETED | REJECTED | EXPIRED | FAILED
    context_bundle JSONB NOT NULL,
    result JSONB,
    retry_count INT DEFAULT 0,
    max_retries INT DEFAULT 3,
    accept_timeout_seconds INT DEFAULT 60,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    accepted_at TIMESTAMP,
    completed_at TIMESTAMP,
    CONSTRAINT fk_parent_todo FOREIGN KEY (parent_todo_id)
        REFERENCES todos(id) ON DELETE SET NULL
);

CREATE INDEX idx_handoffs_state ON handoffs(state);
CREATE INDEX idx_handoffs_role ON handoffs(to_agent_role, state);
CREATE INDEX idx_handoffs_parent ON handoffs(parent_todo_id);
```

### 6.3 Handoff Audit Events

Every state transition emits an `AuditEvent` with:
- `category`: `handoff`
- `event_type`: `handoff.{created,accepted,rejected,completed,expired,failed,routed}`
- `resource_type`: `handoff`
- `resource_id`: `{handoff_id}`
- `metadata`: `{from_agent, to_agent, to_role, reason, state_before, state_after}`

This makes every handoff fully queryable: "show me every handoff from agent X,"
"how many handoffs expired in the last hour," "what's the average handoff
acceptance latency by role."

---

## 7. API Surface

### 7.1 REST Endpoints (Phase 1)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/handoffs` | Create a handoff request |
| `GET` | `/api/handoffs` | List handoffs (filter by state, role, agent) |
| `GET` | `/api/handoffs/{id}` | Get a single handoff with full context |
| `PUT` | `/api/handoffs/{id}/accept` | Accept a handoff (claim ownership) |
| `PUT` | `/api/handoffs/{id}/reject` | Reject a handoff (with reason) |
| `PUT` | `/api/handoffs/{id}/complete` | Mark handoff completed with result |
| `POST` | `/api/handoffs/{id}/context` | Request additional context from sender |
| `GET` | `/api/handoffs/pending` | List pending handoffs for the calling agent |

### 7.2 WebSocket Channels (Phase 2)

| Channel | Direction | Description |
|---|---|---|
| `handoff.agent.{id}` | daemon→agent | Direct handoff to a specific agent |
| `handoff.role.{role}` | daemon→agents | Role-based broadcast |
| `handoff.accepted.{id}` | agent→daemon | Agent claims a handoff |
| `handoff.status.{id}` | daemon→sender | Status updates to the originating agent |

### 7.3 CLI Commands (Phase 2)

```text
gludd handoff list [--role R] [--state S]   # list handoffs
gludd handoff show <id>                      # show handoff with context
gludd handoff accept <id>                    # accept a pending handoff
gludd handoff reject <id> --reason="..."     # reject a handoff
gludd handoff watch                          # live stream of handoff events
```

---

## 8. Edge Cases and Guardrails

### 8.1 Infinite Handoff Chains

- **Depth limit:** max 3 handoffs in a chain (A→B→C→stop). Exceeding depth
  → handoff is rejected with reason `max_depth_exceeded`. The task is escalated
  to a `HumanTodo`.
- **Cycle detection:** `HandoffLoopGuard` tracks every `from_agent_id → to_agent_id`
  pair in the current chain. A→B→A is detected and blocked.

### 8.2 Orphaned Handoffs

- **Sender dies:** if the sender agent is killed while a handoff is in flight,
  the handoff is NOT cancelled — it remains PENDING and can be accepted by a
  receiver. The receiver's result goes into the parent todo directly.
- **Receiver dies:** if the receiver accepts but crashes before completing,
  state → FAILED, retry_count incremented. After `max_retries` (3), the handoff
  is re-routed to a different agent role or escalated to `HumanTodo`.
- **Daemon restart:** on daemon restart, all ACCEPTED and IN_PROGRESS handoffs
  are scanned. If the receiver agent is no longer connected, state → FAILED
  and re-routing logic applies.

### 8.3 Concurrent Handoffs

- **Same sender, multiple handoffs:** allowed. Each is independent. The parent
  todo tracks all child handoff IDs; completion is when ALL children are COMPLETED
  (or explicitly cancelled).
- **Multiple receivers for same handoff:** first-to-accept wins. Others get 409.
  There is no negotiation — acceptance is atomic.

### 8.4 Context Size Attacks

- **Sender floods receiver:** `ContextBundle` is capped at 64KB. Senders that
  exceed this get 413 Payload Too Large.
- **Receiver requests excessive context:** `POST /api/handoffs/{id}/context` is
  capped at 3 requests per handoff. Further requests return 429.
- **Context amplification:** a chain of 3 handoffs could grow context 3×. Each
  receiver should summarize, not accumulate. `ContextSelector` deduplicates and
  summarizes on each hop.

---

## 9. Success Metrics

| Metric | Target | Measurement |
|---|---|---|
| Handoff acceptance latency | p95 < 10s | `AuditEvent` timestamps |
| Handoff completion rate | > 90% | COMPLETED / total created |
| Context bundle adequacy | > 80% of receivers don't request more context | count of `POST .../context` calls |
| Continuity handoff recovery | > 70% of stuck-agent tasks rescued | COMPLETED after CONTINUITY / total CONTINUITY handoffs |
| Loop guard false positives | < 1% | operator-dismissed `HumanTodo` escalations |
| Handoff O11y coverage | 100% of state transitions audited | `AuditEvent` count per handoff ≥ number of transitions |

---

## 10. Risks and Open Questions

| Risk | Mitigation |
|---|---|
| Receivers ignore handoffs (starvation) | `accept_timeout` + expiry → re-route or escalate |
| Context bundle too sparse → receiver fails | Per-role mandatory fields; receiver can pull more |
| HandoffBus overload (1000s of agents) | Topic-based routing limits fan-out; direct-to-ID bypass |
| Agent misuses handoff to offload all work | `HandoffLoopGuard` depth limit; audit trail makes this visible |
| Security: agent reads another agent's context | Context access gated by `PermissionSpec` intersection (see AGENTS.md §Human Permission Subjects) |

**Open questions:**
1. Should a receiver be able to **modify** the context bundle (add notes, correct
   misconceptions) before passing it further? → Likely yes, as a `context_annotations`
   field on `HandoffResult`.
2. Should handoffs support **broadcast** (one sender → all agents of a role, not
   first-to-accept)? → Defer to Phase 2 evaluation. The current design handles
   first-to-accept only; broadcast is a different use case (notifications, not
   task assignment).
3. Should the EventLoop **require** a handoff when `work_type` doesn't match the
   claiming agent's role? → Phase 3. The current behavior (agent claims any todo)
   would be unchanged in Phase 1-2; Phase 3 adds role-enforced routing.

---

## 11. File Map

| File | Purpose |
|---|---|
| `src/general_ludd/handoff/__init__.py` | Package init |
| `src/general_ludd/handoff/models.py` | `HandoffRequest`, `HandoffResult`, `ContextBundle`, `Decision` |
| `src/general_ludd/handoff/bus.py` | `HandoffBus` — WebSocket pub/sub |
| `src/general_ludd/handoff/router.py` | Direct + role-based routing |
| `src/general_ludd/handoff/selector.py` | `ContextSelector` with per-role templates |
| `src/general_ludd/handoff/allocator.py` | `HandoffAllocator` — EventLoop integration |
| `src/general_ludd/handoff/monitor.py` | `AgentMonitor` — stuck-agent detection (Phase 3) |
| `src/general_ludd/handoff/loop_guard.py` | `HandoffLoopGuard` — depth + cycle detection (Phase 3) |
| `src/general_ludd/handoff/continuity.py` | Context-window-aware self-handoff (Phase 3) |
| `src/general_ludd/db/migrations/versions/XXXX_handoffs_table.py` | `handoffs` table migration |
| `daemon.py` | `/api/handoffs` routes + HandoffBus startup |
| `templates/prompts/manager_with_handoff.md.j2` | Manager prompt with HANDOFF syntax (Phase 2) |
| `config/handoff_context_templates.yml` | Per-role context field templates (Phase 2) |
| `tests/unit/test_handoff_models.py` | Model validation tests |
| `tests/unit/test_handoff_bus.py` | Bus pub/sub tests |
| `tests/unit/test_handoff_router.py` | Routing logic tests |
| `tests/unit/test_context_selector.py` | Context selection tests |
| `tests/unit/test_handoff_allocator.py` | Allocator logic tests |
| `tests/unit/test_handoff_monitor.py` | Agent monitor tests (Phase 3) |
| `tests/unit/test_handoff_loop_guard.py` | Loop guard tests (Phase 3) |
| `tests/integration/test_handoff_e2e.py` | End-to-end handoff lifecycle tests |
| `tests/integration/test_handoff_manager_coordinated.py` | Manager-coordinated tests (Phase 2) |
| `tests/integration/test_autonomous_handoff.py` | Autonomous handoff tests (Phase 3) |
