# Ripgrep Search Root Confinement

## Purpose

Gludd uses ripgrep as an optional fast path for structured code search. The
search boundary must launch the mature `rg` binary with the same canonical root
that passed authorization. Validating one path and later launching a lexical
alias creates a time-of-check/time-of-use ambiguity; admitting a missing root
defers policy failure to the child process and produces environment-dependent
errors.

This feature resolves the requested root once, requires an existing directory,
checks that canonical path against the deny list and canonical allowed roots,
and passes that exact canonical value as the final `rg` operand. Query text is
still separated by `--`, optional flags remain allowlisted, and no shell is
involved.

## Resource and result contract

Ripgrep searches with `--threads 1`. This preserves every match while bounding
ripgrep's parallel per-file output buffering. The existing 30-second subprocess
deadline bounds runtime. Python still captures the complete JSON result before
parsing it: this change does not claim a total stdout byte limit, and callers
must not expose arbitrary repository-wide searches as an unmetered live API.
A later streaming/result-budget feature must specify partial-result semantics
before replacing the current complete-result contract.

## Practitioner evidence

Long-lived upstream reports show why the boundary must address both path identity
and the mature tool's actual resource behavior:

- [ripgrep issue #2831](https://github.com/BurntSushi/ripgrep/issues/2831)
  documents a large-file search being killed under memory pressure. The
  maintainer explains that parallel search buffers complete per-file output and
  recommends `-j1`; the report also warns that huge individual lines can still
  require substantial memory.
- [ripgrep issue #608](https://github.com/BurntSushi/ripgrep/issues/608)
  records user demand for pagination because `--max-count` applies per file,
  not to the total search. Gludd therefore does not mislabel that flag as a
  global output bound or silently truncate results.
- [CPython issue #99334](https://github.com/python/cpython/issues/99334)
  documents that `Path.is_relative_to()` is lexical and that callers needing
  symlink-aware confinement should resolve paths first. Gludd resolves both the
  requested root and each allowed root before containment checks.

## Security and observability

Canonical containment rejects traversal aliases and symlink escapes. The shared
deny list remains authoritative for sensitive locations, and non-directories
fail before binary lookup or process creation. Errors remain structured in
`RgResult`: unavailable roots, binary failures, timeouts, and operating-system
failures are distinguishable from ripgrep's normal no-match exit code. Signal
termination and other nonzero errors retain return codes and sanitized stderr.

## Zero-downtime deployment and rollback

The change modifies no database schema, network listener, background service, or
stored search result. Existing callers using valid directories keep the same
complete-match response shape. During a rolling deployment, old and new workers
can coexist; only missing roots and lexical aliases differ, with new workers
failing earlier or reporting the canonical path to ripgrep. Rollback is a normal
Git revert of the source, tests, coverage profile, task evidence, and this
contract. No data migration or service interruption is required.

## Verification

Failing-first tests pin canonical operand reuse and pre-launch rejection of a
missing directory. Adjacent tests cover symlink escape, query separation,
flag allowlisting, one-thread execution, timeout and process failures, signal
return codes, binary fallback, and structured JSON parsing. Branch-enabled
coverage must remain at least 85% aggregate and 75% for the source file. Ruff,
strict mypy, production docstrings, Markdown, task/spec validation, collection,
and the full project gate are required before release promotion.
