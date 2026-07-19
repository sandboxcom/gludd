# CLAUDE.md — root quick reference

Claude Code auto-loads this file at session start. Full harness quick-reference
(bash policy, plugin list, known traps, key documents) lives at
`docs/CLAUDE.md` — read it too. Binding agent policy (TDD, completion rules,
guardrail integrity) is `AGENTS.md` — that is the authority; this file is a
pointer plus the loop you'll use most often.

## Bash: make-only

Every Bash command in this repo MUST be `make <target>`. No `ls`/`git`/`find`/
`python`/`uv`/pipes/`&&`/`;`/`$()`/backticks — even inside quotes. Denied by
`.opencode/plugin/enforce-make.ts` and mirrored hook rules. File reads/edits
use the Read/Edit/Write tools, never shell.

**Never use `make task CMD='...'`.** It wraps an arbitrary command, so it
prompts the operator on *every* invocation and stalls the session — and it is an
escape hatch around the rule below. Use the direct named target instead; if none
fits, **design a new Makefile target** and run that. That is the point of
make-only: every recurring facility becomes a named, reviewable target
(e.g. `git-restore-from`, `release-upload-assets`, `release-set-prerelease`).

## The canonical loop

1. Targeted tests while iterating: `make test-iso TESTFILE=path::Test::test_name`
   (plain `make test TESTFILE=...` is a no-op; `make test`/`make gate` block the
   foreground for 30+ minutes — never run them directly).
2. Full gate: `make gate-async` (launches detached, writes `.gate-status`), then
   poll with `make gate-status`.
3. Commits only via gated make targets (e.g. `make test-and-commit MSG='...'`,
   or `make git-add FILES='...'` + `make git-commit MSG='...'`). Never bypass
   with raw git commands. Commit messages are single-line, no `;`/`|`/`&&`/
   `$()`/backticks.
4. Every checked box in `TASKS.md` needs a trailing `| evidence: ...` citing a
   test count, CI run id, commit hash, or gate output — never claim done/fixed/
   shipped without pasting that measurement.

See `docs/CLAUDE.md` for the plugin roster, agent-floor policy, known traps, and
a **"Non-obvious project facts"** section you should read before touching
releases, config, `dist/`, or concurrent worktree agents. The short version:

- `dist/` is **half-tracked** (build inputs are committed, outputs are ignored) —
  deleting it breaks `make dist`.
- The repo's own `config/` is **not** on the config discovery path, so a daemon
  run from a checkout without `GLUDD_CONFIG_DIR` loads no model profiles and
  **silently no-ops every dispatch** while still reporting healthy.
- `make verify-release-artifact` only proves "≥1 asset"; the real gate is
  `make verify-release-completeness`. A release poll timeout means *still
  building*, not failure.
- Git's cross-process lock is currently **skipped inside worktrees** (`.git` is a
  file there, not a dir), so concurrent worktree agents doing git mutations are
  unserialized. Specced, not yet fixed.
- **Verify design docs against the code before planning from them** — they have
  drifted stale in both directions (claiming built things are dead, and dead
  things are built).
