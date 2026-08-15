# Conversation-Driven Orchestration (AG.13/16)

> AutoGen-style chat-based control flow for agent coordination.

## 1. Problem

Batch dispatch (Task tool waves) requires the orchestrator to pre-plan all work
up front.  When subagent results change the plan (a PR review reveals a deeper
bug, a research task surfaces a missing dependency), the orchestrator must
re-plan between waves — a serial step the pipeline model cannot parallelize.

## 2. Solution: Conversation as Control Flow

Each agent participates in a structured **Conversation** — a sequence of typed
turns where agents speak, delegate, request, or terminate.  The conversation IS
the orchestration; there is no separate dispatch planner.

### Primitives

| Primitive | Meaning |
|-----------|---------|
| `Turn` | One message from a speaker to one or more listeners |
| `SpeakerSelector` | Decides who speaks next (round-robin, priority, LLM-driven) |
| `TerminationCondition` | Predicate that ends the conversation (max turns, keyword, consensus) |
| `ChatOrchestrator` | Drives the conversation loop: select → speak → check termination → repeat |

### Message Types

- **DELEGATE** — speaker assigns work to another agent
- **REPORT** — agent returns results to the delegator
- **REQUEST** — agent asks another agent for information
- **BROADCAST** — speaker shares context with all participants
- **TERMINATE** — speaker declares the conversation complete

## 3. Comparison: Batch Dispatch vs. Conversation

| Dimension | Batch Dispatch | Conversation |
|-----------|---------------|--------------|
| Plan horizon | Fixed (one wave) | Adaptive (each turn) |
| Error handling | Failed task = replacement subagent | Failed task = speaker reacts next turn |
| Context sharing | Via orchestrator (main thread) | Via broadcast messages |
| Dynamic reprioritization | Between waves only | Any turn |
| Complexity | Low (stateless dispatch) | Medium (stateful conversation graph) |

## 4. Implementation

```text
src/general_ludd/ag16_orchestration/
  __init__.py          — exports (Turn, Conversation, ChatOrchestrator)
  conversation.py      — Turn, Conversation, SpeakerSelector, TerminationCondition
  orchestrator.py      — ChatOrchestrator: drives the conversation loop
```

`ChatOrchestrator` is the main entry point.  It holds a `Conversation` graph,
selects the next speaker via `SpeakerSelector`, collects turns, and checks
`TerminationCondition` after each turn.

## 5. Usage Pattern

```python
from general_ludd.ag16_orchestration import (
    ChatOrchestrator, Turn, Conversation, RoundRobinSelector,
    MaxTurnsTermination,
)

conv = Conversation(participants=["planner", "coder", "reviewer"])
orch = ChatOrchestrator(
    conversation=conv,
    speaker_selector=RoundRobinSelector(),
    termination=MaxTurnsTermination(max_turns=10),
)

async for turn in orch.run():
    match turn.kind:
        case "DELEGATE":
            await dispatch_subagent(turn.payload)
        case "REPORT":
            ingest_result(turn.payload)
```

## 6. Design Decisions

- **Minimal dependency**: No external AutoGen/CrewAI dependency.  Core
  primitives only; adapters for external frameworks can be layered later.
- **Async-first**: `ChatOrchestrator.run()` is an async generator yielding
  turns for the caller to act on.
- **Pluggable selectors/termination**: `SpeakerSelector` and
  `TerminationCondition` are abstract base classes; built-in implementations
  cover common patterns.
- **No agent implementation**: This module provides orchestration PRIMITIVES,
  not agent implementations.  Agents are external callables the orchestrator
  invokes via the conversation protocol.
