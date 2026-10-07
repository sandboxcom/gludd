# Tracked-file line limit

Gludd requires every Git-tracked UTF-8 text file to contain fewer than 2,500
physical lines. `make check-file-line-limits
FILE_LINE_LIMIT_POLICY=config/file_line_limits.json` inventories the complete
Git index, counts blank and comment lines, and reports every violation in one
deterministically sorted pass. `make lint` and the gate preflights invoke the
same target.

The policy is deliberately fail-closed. Missing or unreadable tracked paths,
malformed Git output, unlisted binary data, stale exclusions, duplicate paths,
and changes to the fixed threshold are configuration errors. Non-text entries
may be skipped only by an exact path and a non-empty reason in
`config/file_line_limits.json`; patterns and implicit extension-based skips are
not supported. The sole initial exception is the `external/llamacpp` Git
submodule entry, which is a gitlink rather than a file.

## Makefile layout

The root `Makefile` remains the only entrypoint and names every fragment under
`make/` explicitly in parse order. Wildcards, variable-expanded paths,
`-include`, and nested includes are rejected by the layout tests. The split
tool proves that its fragments reproduce the previous monolith byte-for-byte
and compares both `make help` and `make -n help` output before accepting the
rewrite. Quality tools compose the explicit fragments when they inspect target
definitions, so duplicate-target, help, target-contract, and gate-parity checks
continue to see one logical Makefile.

## Long-lived user reports considered

- A 2014 GNU Make user question confirms that an included file is processed as
  though its contents appeared at the include location. That supports preserving
  the monolith's exact order rather than redesigning dependencies during the
  split: [execution flow of a Makefile that includes another](https://stackoverflow.com/questions/23300718/execution-flow-of-makefile-which-includes-other-makefile).
- A 2016 question specifically asks whether a large Makefile can be divided at
  arbitrary positions and documents the same inline-inclusion model. Although
  one answer suggests globbing, Gludd uses an explicit list so order changes are
  visible in review: [how to split a Makefile into separate modules](https://stackoverflow.com/questions/36422375/how-to-split-makefile-into-separate-modules).
- A 2021 `-include` discussion explains that the optional form suppresses a
  missing-file failure. Gludd uses strict `include` because a missing fragment
  is configuration drift, not an optional dependency artifact: [what `-include`
  means in GNU Make](https://stackoverflow.com/questions/66118766/what-is-include-in-gnu-make-and-how-it-works).
- A Sonar community report describes whole-file size rules disappearing from
  pull-request feedback when analysis is limited to changed lines. Gludd scans
  the complete tracked inventory on every invocation so an old oversized file
  cannot evade the gate: [whole-file rules and new-code-only pull-request
  analysis](https://community.sonarsource.com/t/some-rules-do-not-work-in-mrs-prs-decoration-because-of-newcode-focus/152734).
- The accepted ESLint `max-lines` proposal counts blank lines and comments by
  default. Gludd follows that easily audited physical-line model across all text
  formats instead of maintaining language-specific counting rules: [ESLint
  max-lines rule proposal](https://github.com/eslint/eslint/issues/6078).

## Remediation workflow

Run the checker to get the complete largest-first inventory. Split a file along
cohesive ownership boundaries, preserve its public imports or entrypoint, add
focused regression tests, and rerun the checker. Do not add a text-file
exception to make the gate pass; the exception policy exists only for tracked
entries that cannot be decoded as text.

## Initial post-split remediation inventory

The first complete inventory after splitting the Makefile contains 31 tracked
text files. Counts below are physical lines, including blanks and comments.

### Production source and maintenance scripts (11)

- `src/general_ludd/event_loop/loop.py` — 5,494
- `src/general_ludd/cli.py` — 5,129
- `src/general_ludd/models/gateway.py` — 4,083
- `src/general_ludd/daemon.py` — 4,076
- `scripts/test_hook_runtime.py` — 3,885
- `scripts/agent_watchdog.py` — 3,884
- `src/general_ludd/self_improve/codex_comparison.py` — 3,674
- `src/general_ludd/db/repository.py` — 3,513
- `src/general_ludd/self_improve/managed_runner.py` — 3,246
- `src/general_ludd/self_improve/runtime.py` — 3,230
- `src/general_ludd/pricing_intel/sources.py` — 2,500

### Tests (9)

- `tests/e2e/test_game_building_deepseek.py` — 4,034
- `tests/unit/test_self_improve_codex_comparison.py` — 3,712
- `tests/unit/test_ci_named_shard_files.py` — 3,546
- `tests/unit/test_automatic_disk_cleanup.py` — 3,113
- `tests/unit/test_behavioral_enforcement.py` — 2,993
- `tests/unit/test_self_improve_codex_runner.py` — 2,923
- `tests/unit/test_probabilistic_deep.py` — 2,864
- `tests/e2e/test_connectors_batch5_workflows.py` — 2,540
- `tests/unit/test_routers_endpoints.py` — 2,521

### Documentation, specifications, and skills (9)

- `docs/specs/BEHAVIORAL_SPECS.md` — 21,495
- `docs/internal/sprint0.md` — 3,970
- `.opencode/skills/go-expert/SKILL.md` — 3,581
- `docs/MCP_TOOLS_TOPICS.yml` — 3,581
- `AGENTS.md` — 3,540
- `docs/specs/FEATURE_EXPERT_SYSTEM_INTEROPERABILITY.md` — 3,303
- `docs/features/BETA4_DUAL_TRACK_CI.md` — 2,877
- `docs/design/specs/SPEC_ML_AI_EXPERT_AND_SAFE_SELF_IMPROVEMENT.md` — 2,758
- `.opencode/skills/java-expert/SKILL.md` — 2,613

### Generated and lock artifacts (2)

- `.secrets.baseline` — 208,277
- `uv.lock` — 9,090
