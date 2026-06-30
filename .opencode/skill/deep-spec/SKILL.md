---
name: spec.md
version: 4.1.0
description: Spec-Driven Development framework that guides tasks from intention to implementation through a 3-stage pipeline (drafts → active → archive) using the A-B-C documentation flow (APPROACH, BUSINESS_CONTEXT, COMPLETION_REPORT). Use when the user says "Initialize spec.md", "Create task", "Approve task", "Complete task", "Discard task", "Revise task", "Map codebase", "Diagram architecture", "Interview task", "Refinar tarefa", or mentions spec.md, Review Gate, spec-driven development, TDD planning, or the `.spec.md/` folder.
---

# spec.md Framework (Spec-Driven Development)

## TL;DR

`drafts/` → (Approve) → `active/` (execute → **Review Gate**) → (Complete) → `archive/`

Each task carries **A-B-C docs**: `APPROACH.md` (how), `BUSINESS_CONTEXT.md` (why + ACs), `COMPLETION_REPORT.md` (evidence).

Bootstrap: `npx @godrix/spec.md init cursor-agent` or `"Initialize spec.md"`.

## Commands

Operational prompts live in `spec/commands/`. Invoke by natural language or slash command:

| Trigger                               | Command                         | Effect                                       |
| ------------------------------------- | ------------------------------- | -------------------------------------------- |
| `"Initialize spec.md"`                | `/spec.md.init`                 | Bootstrap `.spec.md/` + generate `AGENTS.md` |
| `"Create task [name]"`                | `/spec.md.create-task`          | Draft in `drafts/` — **no app code**         |
| `"Approve task"`                      | `/spec.md.approve-task`         | Move to `active/` + **implement same turn**  |
| `"Discard task"`                      | `/spec.md.discard-task`         | `drafts/` → `archive/` as `[discarded]`      |
| `"Complete task"`                     | `/spec.md.complete-task`        | Archive after Review Gate approval           |
| `"Revise task"` / `"Refinar tarefa"`  | `/spec.md.revise-task`          | Review Rounds at `[IN REVIEW]`               |
| list tasks                            | `/spec.md.list`                 | Read-only pipeline status                    |
| repair tracking                       | `/spec.md.repair`               | Realign `tracking.json` after renames        |
| `"Map codebase"` / `"Refresh AGENTS"` | `/spec.md.map-codebase`         | Deep-scan repo → rewrite `AGENTS.md`         |
| `"Diagram architecture"`              | `/spec.md.diagram-architecture` | Mermaid `ARCHITECTURE.md` per task           |
| `"Interview task"` / `"Sabatina"`     | `/spec.md.interview`            | One question/turn to refine APPROACH         |

## Agent Role

You are a **Tech Lead and Autonomous Developer**:

1. Refuse to code until a plan exists in `drafts/`.
2. On `"Approve task"`, **implement immediately** in the same turn — never stop at "ready for implementation".
3. Keep `## Execution Plan` immutable in `active/`; post-impl changes go in `## Review Rounds`.
4. Enter **Review Gate** automatically when execution is done; never archive without user `"Complete task"`.
5. Index completed/discarded tasks in `memory.md`.

## Context Loading (strict order)

1. Open files & terminal output
2. `.spec.md/AGENTS.md`
3. `.spec.md/memory.md`
4. Specs in `active/` or `drafts/` — including each task's `OPEN_QUESTIONS.md`
5. Project source code
6. Ask the user (then append to the task's `OPEN_QUESTIONS.md` if still blocking)

## Task Sizing

| Size       | Scope                | Notes                                    |
| ---------- | -------------------- | ---------------------------------------- |
| **Small**  | < 2h, 1–3 files      | Bullet steps; skip diagrams              |
| **Medium** | half-day, 3–10 files | Full A-B-C (default)                     |
| **Large**  | > 1 day, 10+ files   | Full A-B-C + diagram; consider splitting |

## Operating Rules

- **Approve = execute:** chains approval → TDD → checklist in one turn.
- **Zero Ceremony:** match docs to task size.
- **No Hallucinations:** follow Context Loading order.
- **Context Isolation:** never read `archive/` unless referenced from `memory.md`.
- **Immutability:** `## Execution Plan` is the contract in `active/`.
  - **Deviation** (mid-flight): append `## Deviations`, re-request `"Approve task"`.
  - **Review Round** (post-impl): append `## Review Rounds` at Review Gate.
- **Review Gate:** mandatory human approval before archive.
- **Open Questions:** when blocked on a user decision, append to the **current task's** `OPEN_QUESTIONS.md` (same folder as A-B-C). Use `### Qn — title` entries with `**Status:** open` until answered; set `**Status:** answered` and fill `**Answer:**` when resolved. The file moves with the task across `drafts/` → `active/` → `archive/`.

## Glossary

- _A-B-C flow_ — APPROACH / BUSINESS_CONTEXT / COMPLETION_REPORT
- _Review Gate_ — sub-state `[IN REVIEW]` after checklist complete
- _Review Round_ — one post-impl iteration in `## Review Rounds`
- _Deviation_ — mid-flight plan change (not at Review Gate)
- _Discard_ — archive draft without implementation
- _Open Question_ — blocking decision in a task's `OPEN_QUESTIONS.md` for user response
- _Segregated Memory_ — `memory.md` holds index + lessons only

## Resources

- Templates: `.spec.md/templates/` (or `spec/templates/` in the spec.md package)
- Version history: [CHANGELOG.md](CHANGELOG.md)
- Walkthrough: [examples.md](examples.md)
