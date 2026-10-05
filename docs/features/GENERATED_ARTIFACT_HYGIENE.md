# Generated Artifact Hygiene

**Status:** implemented 2026-08-30

Gludd keeps three generator-owned MCP documentation artifacts in Git:

- `docs/MCP_TOOL_REFERENCE.md`
- `docs/MCP_TOOLS_MANIFEST.json`
- `docs/MCP_TOOLS_TOPICS.yml`

`scripts/check_generated_artifact_hygiene.py` is a read-only postcondition
checker for that canonical set. It asks the Git index for the exact paths using
NUL-delimited output, then requires UTF-8, LF line endings, no NUL bytes, no
trailing spaces or tabs, and a terminal newline. JSON is loaded with the standard
library parser and YAML is loaded as a complete stream with PyYAML's safe loader.
CommonMark permits arbitrary Unicode text, so strict UTF-8 decoding is the
Markdown parseability boundary; the repository's Markdown linter remains the
syntax/style authority.

The check passes only when all three paths are tracked and clean. A missing path,
Git failure or timeout, malformed inventory, symlink, unreadable or oversized
file, unsupported serialization, decoding error, or parse error fails closed.
Exit status `0` is clean, `1` is artifact drift, and `2` is an inventory failure.
Every result is printed with a path and, when available, a source line; a final
summary always reports the artifact and violation counts.

`make check-generated-artifact-hygiene` is the sole public entry point and
delegates directly to that checker; the Make recipe contains no second
implementation. The target runs before expensive work in `gate-fast`, `gate`,
and `gate-refresh`. Because `gate-full` depends on `gate-refresh`, the release
path receives the same fail-closed prerequisite without duplicate logic.

## Operational Use

Run `make check-generated-artifact-hygiene` after `gen-mcp-tools` and
`gen-mcp-tool-ref`, before packaging or publishing the generated files. Its
target contract has no variables and uses that same command as the safe,
read-only behavioral example. Fix the owning generator or source data and
regenerate; do not hand-edit the output merely to silence the check. The focused
test suite also checks the real repository artifacts, so an artifact that no
longer satisfies the contract is visible immediately.

The checker deliberately owns only whole-file generated artifacts. The generated
section inside `README.md` remains under `gen-status-table` because treating a
partially generated document as a whole-file artifact would create false
ownership and false positives.

## Zero-Downtime Delivery and Rollback

Rollout is additive and read-only: no running daemon, collection, role, database,
model, or generated artifact is changed. Existing generation continues while the
checker reads the committed snapshot. A drift result leaves the last known-good
artifacts untouched and blocks the caller from presenting malformed output as a
successful generation.

Rollback removes the checker, its focused tests, and this document. There is no
schema migration, persisted state, background service, lock, cache, or data-plane
handoff to reverse. If a checker release is rolled back, retain the previously
validated generated files until its replacement passes the same postconditions.

## Resource Ownership

The process owns one bounded `git ls-files` child for at most 10 seconds and waits
for it synchronously. It reads at most 4 MiB from each artifact, scans at most 32
artifacts, closes every file with a context manager, creates no temporary files,
and starts no workers or services. Symlinks are rejected before reading so the
canonical inventory cannot redirect the bounded scan outside its ownership
boundary.

## Evidence and Long-Lived Practice

- The [Git `ls-files` manual](https://git-scm.com/docs/git-ls-files.html), reviewed
  2026-08-30, specifies that `-z` emits verbatim paths terminated by NUL. That is
  the mature index inventory mechanism used here instead of parsing quoted text.
- The [EditorConfig specification](https://spec.editorconfig.org/), reviewed
  2026-08-30, defines `trim_trailing_whitespace`, `end_of_line`, and
  `insert_final_newline`. The checker enforces those stable serialization
  postconditions without mutating files.
- [RFC 8259](https://www.rfc-editor.org/info/rfc8259/) (December 2017) requires
  UTF-8 for interoperable JSON. The
  [YAML 1.2.2 specification](https://github.com/yaml/yaml-spec/blob/main/spec/1.2.2/spec.md)
  (2021-10-01) defines parsing a presentation stream and explicitly allows that
  parsing to fail on ill-formed input. Those standards support parser-backed
  validation rather than extension-only checks.
- The long-running PyYAML practitioner report
  [#450](https://github.com/yaml/pyyaml/issues/450), opened 2020-10-23 and still
  receiving real-world references in 2023, records hard-to-see trailing-tab
  failures and an upstream maintainer's recommendation to lint repository YAML.
  This is the failure class caught before generated YAML reaches downstream
  consumers.
- EditorConfig discussion
  [#336](https://github.com/editorconfig/editorconfig/issues/336), opened
  2017-12-08, distinguishes terminal-newline/trailing-whitespace guarantees from
  policies about multiple empty lines. Accordingly, the checker accepts a valid
  blank line before EOF and does not invent a content-formatting rule.
- The archived EditorConfig
  [cross-editor newline survey](https://github.com/editorconfig/editorconfig/wiki/Newline-at-End-of-File-Support),
  last revised 2016-07-03, documents years of differing editor defaults. A
  deterministic repository check avoids relying on any contributor's editor.
