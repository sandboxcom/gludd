# BINDING_RULES — 10 Mechanical Invariants

1. **10-dispatch floor**: Every dispatch wave while `TASKS.md` has unchecked items or `config/ratchet.yml` has entries MUST contain exactly 10 task/agent/workflow dispatches in one message. Verifiable: count dispatches in the message.

2. **≥3 topic areas per wave**: Each dispatch wave MUST span at least 3 distinct TASKS.md sections or topic areas (e.g., pipeline + tests + docs). Verifiable: map each dispatch to its TASKS.md entry area.

3. **≥1 continuation slot per wave**: Every wave MUST include at least 1 task from a pre-existing unchecked `TASKS.md` item (not a new item added this session). Verifiable: grep `TASKS.md` for the task ID before the wave.

4. **No text-only response with pending work**: If `TASKS.md` has unchecked items or `config/ratchet.yml` is non-empty, every response MUST include at least one tool call. Verifiable: check for tool call in response.

5. **Gate status at session start and after each wave**: Run `make git-status` at session start; run `make gate-status-check` after each dispatch wave completes. Verifiable: bash invocation present at those boundaries.

6. **Commit after each logical unit of work**: After any coherent change (a test file, a feature, a fix) passes tests, run `make ship-commit` or `make git-commit`. Verifiable: `make git-log` shows a commit matching the completed work.

7. **Push when batch threshold met AND CI idle**: Push via `make batch-push` only when ≥3 unpushed commits exist AND `make ci-verdict BRANCH=<branch>` returns `conclusion: success` or no run in flight. Verifiable: count unpushed commits; check CI verdict before push.

8. **Write tests first (TDD)**: Before writing any `src/general_ludd/**/*.py` file, the corresponding test file in `tests/unit/` MUST exist on disk. Verifiable: `ls tests/unit/test_<module>.py` must succeed before `edit src/general_ludd/<module>.py`.

9. **Lint clean before every commit**: `make lint` MUST exit 0 before any commit lands. Zero warnings allowed. Verifiable: run `make lint`; exit code must be 0.

10. **Read actual files, never assume from memory**: Before any claim about file contents, task status, or code state, read the relevant file. Verifiable: every factual claim about code is traceable to a read/grep/glob call in the same session.
