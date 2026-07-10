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

See `docs/CLAUDE.md` for the plugin roster, agent-floor policy, and known
traps (e.g. `make test-failures` history of masking collection errors).
