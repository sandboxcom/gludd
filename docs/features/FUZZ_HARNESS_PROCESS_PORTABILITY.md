# Fuzz harness process portability

## Contract

The deep parser fuzz harness proves that adversarial JSON, YAML, regular
expressions, syslog, skill frontmatter, tool calls, and extra-vars inputs either
complete or fail through controlled parser outcomes. Its ReDoS probes must run
outside the pytest process so a catastrophically backtracking expression can be
terminated at a bounded deadline without hanging a worker.

Each regex probe uses the standard-library `spawn` context and an importable,
module-level worker. Completion means the child exited successfully before the
deadline. A still-running child is terminated, joined, and closed before the
probe returns failure. The harness deliberately avoids inherited fork state,
process-shared queues, third-party serializers, and helper scripts.

The JSON encoding fixture is valid JSON containing an escaped Unicode string;
it asserts that JSON escape decoding produces `é`. Encoding error tolerance and
all parser safety assertions remain unchanged.

## Practitioner evidence

- The [CPython multiprocessing documentation](https://github.com/python/cpython/blob/main/Doc/library/multiprocessing.rst)
  records that spawn and forkserver require process targets and arguments to be
  picklable, and specifically describes failures when a target is not defined in
  an importable module. Python 3.14 also makes fork non-default on every
  platform, so a nested worker is not a portable test primitive.
- A long-lived [pyABC user report about local callables under macOS
  spawn](https://github.com/ICB-DCM/pyABC/issues/286) shows the same failure from
  Python 3.8 onward: a nested target works under inherited fork state but cannot
  be serialized by spawn. This harness uses the mature stdlib rule directly by
  moving the target to module scope rather than adding a serializer dependency.
- CPython's [Python 3.14 release notes](https://github.com/python/cpython/blob/main/Doc/whatsnew/3.14.rst)
  direct users who encounter multiprocessing pickling failures to the
  forkserver restrictions and state that macOS and Windows continue to use
  spawn. The harness explicitly requests spawn so every supported host exercises
  the stricter portable contract.

## Safety, resources, and zero-downtime delivery

The deadline remains fail-closed: a live child is evidence that the regex did
not complete, never a passing outcome. A child that crashes or cannot execute is
also failure because its nonzero exit code is checked. Each child handle is
closed after joining, and no semaphore-backed result queue is created, avoiding
resource-tracker leaks during repeated fuzz runs.

This is test-only infrastructure. It changes no parser, wire format, runtime
process, database, or deployment artifact, so rollout requires no migration or
restart. The focused harness is the promotion check; rollback is a source revert.
The full parser assertions remain available throughout either version.

## Verification

- `tests/unit/test_fuzz_harness_deep.py` exercises 71 adversarial contracts,
  including known-hanging and known-fast regex cases under `spawn`.
- Ruff and strict mypy must report zero findings for the harness.
- The harness plus the skill-loader family provides at least 85% aggregate and
  75% per-file production coverage; the current focused result is 93.75% for
  `general_ludd.skills.loader`.
