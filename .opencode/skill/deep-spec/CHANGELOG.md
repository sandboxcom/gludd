# Changelog

All notable changes to the **deep-spec** skill are documented in this file.
The active version lives in the `SKILL.md` frontmatter (`version` field).

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project follows [Semantic Versioning](https://semver.org/): **major** = breaking workflow/commands; **minor** = compatible features; **patch** = docs/clarifications only.

## [3.0.0] — 2026-06-19

### Added

- **CLI:** `npx spec.md init <agent>` scaffolds `.spec.md/` idempotently (templates, hooks, pipeline dirs, agent commands).
- **Command split:** 8 operational prompts in `spec/commands/spec.md.*.md` (init, create-task, approve-task, discard-task, complete-task, revise-task, list, repair).
- **Bootstrap templates:** `AGENTS.template.md`, `memory.template.md` plus existing A-B-C templates in `spec/templates/`.
- **Hooks:** deterministic `list`, `track`, `repair`, `validate` compiled to `.spec.md/hooks/*.mjs`.
- **Multi-agent providers:** registry + transform layer (skill, copilot-prompt, markdown, forge, gemini-toml, goose-yaml).
- **Tests:** Poku e2e init + integration (frontmatter, tracking).
- **CI:** GitHub Actions lint + test workflows.
- **Docs:** root `AGENTS.md`, `architecture` skill, Docusaurus site in `website/`.
- **Dogfooding:** `.spec.md/` in the spec.md repo with archived migration task.

### Changed

- `spec.md/SKILL.md` is now a **thin orchestrator** (~80 lines) routing to `spec/commands/`; operational detail moved to commands.
- Recommended install path is CLI init; manual copy of `spec.md/` remains supported for retrocompat.

### Preserved (unchanged semantics)

- A-B-C flow, 3-stage pipeline, Review Gate, approve=execute same turn, task sizing, immutability of Execution Plan.

## [2.0.1] — 2026-06-01

### Fixed

- `"Approve task"` now explicitly requires **same-turn** handoff to Active Execution (§4); agents must not stop at “active / ready for implementation”.

## [2.0.0] — 2026-05-31

### Added

- Mandatory **Review Gate** after execution (`[IN REVIEW]`); `Complete task` only after user approval.
- `## Review Rounds` section in `APPROACH.md` for post-implementation iteration.
- `## Review Gate` section in `COMPLETION_REPORT.md`; `[IN REVIEW]` status.
- Optional `Revise task` / `Refinar tarefa` commands as feedback aliases at the gate.
- Guards on `Complete task` (reject if `[IN PROGRESS]` or a review round is in progress).

### Changed

- Pipeline: `active/` → execute → Review Gate → `Complete task` → `archive/`.
- Immutability scoped to `## Execution Plan`; mid-flight pivots go in `## Deviations`.

## [1.0.0] — 2026-05-28

### Added

- `drafts` → `active` → `archive` pipeline with A-B-C flow.
- Commands: Initialize, Create task, Approve task, Discard task, Complete task.
- APPROACH, BUSINESS_CONTEXT, and COMPLETION_REPORT templates.
