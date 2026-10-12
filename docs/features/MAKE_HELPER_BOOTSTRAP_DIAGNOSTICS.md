# Make helper bootstrap diagnostics

## Quoted observed commands

`run-watched` preserves ordinary quoted child arguments, including quoted
`TESTFILES` and `PYTEST_ARGS` assignments passed to a child Make invocation.
Its admission guard evaluates command presence using GNU Make's `strip` and
`if` functions and sends only a fixed presence marker to the shell test. It
does not embed command text inside another shell-quoted emptiness test.
The existing child command, observer, streaming output, retained receipts,
timeouts, and exit propagation remain unchanged. No stderr is suppressed.

The previous guard could print a shell argument-count error while still
executing a successful child when `CMD` contained normal double-quoted
arguments. Actual-recipe regressions first reproduced that diagnostic, then
verified both single and double quotes, rejection of empty commands before
observation starts, and failed child codes 7 and 143 in terminal receipts.
GNU Make itself reports recipe failure as 2; the observer retains the child
code. A passing child never conceals an invalid admission check.

This bounded smoke uses the existing target and one test worker:

```text
make run-watched CMD='make test-files TESTFILES="tests/unit/test_run_watched_make_recipe.py" NPROC=1 GLUDD_XDIST_WORKERS=1 PYTEST_ARGS="-W error -q --tb=short"' OBSERVED_LABEL=make-helper-smoke RUN_ID=quoted-command STALL_SECS=30 MAX_SECS=120 LOG=.gate-logs/make-helper-smoke.log OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=10 OBSERVED_RETAIN_RUNS=20
```

Empty commands still fail before starting a child. This target does not certify
a release or replace the full gate and its unchanged 85/75 coverage floors.

## Read-only source version metadata

Packaging-name defaults expand `VERSION` during Make parsing, even when the
requested target is only `git-show-commit`. The previous runtime import
allocated an empty `.venv` in a fresh checkout and emitted two application
import failures before the otherwise successful Git diagnostic.

`VERSION` now uses the existing offline `uv version --short` source-metadata
reader. It passes no replacement value or bump option, does not import
`general_ludd`, and does not need an installed application or a Python
interpreter. The native shell's Python 3.9 therefore does not need an added
TOML dependency. This is an existing tool interface, not a new version loader.
The metadata operation does not depend on the runtime `UV` shim used by other
read-only targets, and introduces no new `UV` override.

The reader binds its project explicitly to the current checkout, so an ambient
`UV_PROJECT` setting cannot substitute another project's version.

The existing command-line `VERSION` precedence, tarball name, macOS DMG name,
and Windows installer name are preserved. Explicit `make version` still uses
the application runtime and exposes its import failures. Missing, malformed,
or empty source metadata fails with the original diagnostic and a Make error;
there is no silent fallback to an installed package's possibly stale version.

The read-only behavioral example is:

```text
make git-show-commit C=HEAD
```

Regressions exercise the full actual Make entrypoint in a fresh temporary Git
checkout with an intentionally broken runtime module. The diagnostic must be
silent on stderr, leave `.venv` and `uv.lock` absent, and preserve the exact
source metadata bytes. The explicit runtime command must still fail. Additional
actual packaging-default cases check the precise project field rather than an
unrelated table's version, command-line precedence, and invalid source metadata.

The workflow-state, writable-cache, and dependency-profile contracts inspect
only the `VERSION` definition when pinning this offline, checkout-bound reader.
They reject runtime loading in that definition while retaining their existing
guard-goal, cache-sandbox, and no-sync assertions. The actual-recipe regressions
above still enforce lockfile and environment nonmutation; conforming the legacy
structural checks does not relax these behavioral checks or runtime validation.

Isolated launcher fixtures that invoke the full external Make entrypoint copy
the source `pyproject.toml` before invocation. They do not override `VERSION` or
weaken missing-metadata rejection. Duplicate-owner tests retain the live PID;
dry-run tests compare directory contents, and both retain unchanged metadata
without allocating an environment. The second sealed integration's two absent
fixture-metadata failures remain recorded rather than treated as gate success.

## Research and resource boundary

Research reviewed on 2026-10-12:

- The [GNU Make conditional-function reference][make-if] defines functional
  presence testing; its [string-function reference][make-strip] defines
  whitespace normalization. The recipe uses those established facilities rather
  than adding a command parser or shell-escaping subsystem.
- A [Unix & Linux practitioner discussion from 2017-10-20][make-forum] records
  the long-lived confusion between Make expansion and shell parsing, including
  the same shell argument-count diagnostic. This feature's regression tests
  exercise the real recipe and runner rather than assuming shell quoting alone
  protects text expanded by Make.
- The [uv CLI reference][uv-version] provides a built-in source version reader;
  the [PyPA metadata specification][project-version] defines the static project
  version field. These established interfaces were selected after inspecting
  existing repository version helpers and the native interpreter boundary.
- A [packaging practitioner discussion begun in 2010][version-forum] records
  the bootstrap problem caused by importing application code before its runtime
  dependencies are installed. Its installed-distribution approaches address a
  different lifecycle from a fresh source checkout; this helper reads the
  checkout's metadata instead.

ZDD is nonmutation of deployments: these helpers do not promote branches, tag,
publish, or restart services. The implementation adds no persistent process and
retains the existing observed-command owner, bounded lifecycle, heartbeat,
run-namespaced logs, atomic terminal receipt, and cleanup. Tests use isolated
temporary directories and at most one worker; their child commands never run
the full suite or a gate.

[make-if]: https://www.gnu.org/software/make/manual/html_node/Conditional-Functions.html
[make-strip]: https://www.gnu.org/software/make/manual/html_node/Text-Functions.html
[make-forum]: https://unix.stackexchange.com/questions/399451/makefile-sqare-brackets-built-in-variable-expansion-and-command-substitution
[uv-version]: https://docs.astral.sh/uv/reference/cli/#uv-version
[project-version]: https://packaging.python.org/en/latest/specifications/pyproject-toml/#version
[version-forum]: https://stackoverflow.com/questions/2058802/how-can-i-get-the-version-defined-in-setup-py-setuptools-in-my-package
