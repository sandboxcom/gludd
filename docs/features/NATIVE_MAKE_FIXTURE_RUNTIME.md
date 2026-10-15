# Native Make Fixture Runtime

Hosted run `38812181930`, job `116694026843`, exposes three failures in the
native Make execution-boundary fixture: both successful mixed-goal cases create
a disposable `.venv`, and the runtime-policy case indexes an absent
`UV_PROJECT_ENVIRONMENT`. The fixture inherited activation pointers from its
caller; hosted setup-python does not promise those variables. These failures
remain historical evidence, not green results after a source change.

The fixture now explicitly selects the already-running interpreter using
`UV_PROJECT_ENVIRONMENT=sys.prefix`, `VIRTUAL_ENV=sys.prefix` and
`UV_PYTHON=sys.executable`. This works with the installed Python prefix rather
than assuming a virtualenv layout or a symlink named `.venv`. The real Make and
uv owners still execute. No production owner repair is needed: the defect was
the fixture's missing runtime input, not failure to honor an explicit input.
The fixture creates or synchronizes no environment and installs no package or
Python interpreter. Existing no-sync, offline, frozen and no-download policies remain
unchanged, including Make's refusal of a caller's `UV_NO_SYNC=0` override.

The fixture copies its input environment, retains Git-local and recursive-Make
isolation, and does not mutate its caller. Its deliberately runtime-free read
cases remove all three selection variables, including `UV_PYTHON`. Every original
assertion remains: successful registration is real and exactly once, invalid
input and unavailable uv still fail, active-gate checkout is refused, Git/source
authority is preserved, and no fixture `.venv` or `uv.lock` appears.

## Native regression and admission

Two additive cases supply an environment with all runtime selectors absent or
pointing at an unrelated directory and unavailable interpreter. Before repair,
the selection assertions fail with the missing-key error and inherited foreign
prefix. Those assertions run before uv, so failing-first reproduction does not
create another environment. After repair, both cases execute the original
successful real-owner assertions and preserve a foreign-directory sentinel.

A third failing-first case invokes the existing admission recipe through its
native Make capture helper and finds the execution-boundary file missing. The
existing `_pytest-runtime-compatibility-admission` now selects that entire file
exactly once, retaining all original owner cases and the new ambient-input cases.
The promotion manifest and both exact-roster consumers keep every prior entry.
There is no new runner, admission owner, duplicated selector or replay. The
two-worker cohort, separate fresh-help child, fatal warnings and 90-second total /
60-second quiet bounds are unchanged.

The focused behavioral example is:

```sh
make test-files TESTFILES=tests/unit/test_make_uv_execution_boundary.py PYTEST_ARGS='-q -n 0 -W error'
```

Use the already-installed locked Python 3.11 environment as both
`UV_PROJECT_ENVIRONMENT` and `VIRTUAL_ENV`, with `UV_NO_SYNC=1`,
`UV_PYTHON_DOWNLOADS=never`, `NPROC=1` and `GLUDD_XDIST_WORKERS=1`; do not install or
sync dependencies for this check. The three new cases fail before repair. After
repair, the full native-owner file, exact-roster check and promotion suite pass
82 cases on Python 3.11.14 with warnings fatal. Ruff, strict typing, all 236 Make
target contracts, the 85-family promotion validator and Make duplicate/parity
checks pass.

The actual existing admission owner passes all 1,797 cohort cases plus its
separate fresh-help case in 81.7 observed seconds, within the unchanged 90/60
bounds. The retained observer receipt is
`native-make-fixture-runtime/repair-20261015`. This is a local repair check beside
one existing shard worker, not a measured hosted scheduling improvement.

## Upstream context and proof boundary

The official [uv project-environment documentation](https://docs.astral.sh/uv/concepts/projects/config/#project-environment-path)
allows an existing Python installation prefix, including system Python, as
`UV_PROJECT_ENVIRONMENT`. Its warning that synchronization can remove unrelated
packages is why this fixture retains the existing no-sync boundary. It does not
construct a virtualenv or copy an interpreter to satisfy the tests.

The long-lived [November 2024 user report](https://github.com/astral-sh/uv/issues/9067)
describes `UV_SYSTEM_PYTHON` still creating `.venv`; the
[February 2025 user report](https://github.com/astral-sh/uv/issues/11206)
describes working system-prefix selection alongside misleading error wording.
These reports are related runtime-selection context, not evidence for this
repository's root cause or a reason to suppress warnings. The native failing
inputs and real-owner assertions establish the fixture defect and repair.

Focused checks are not a complete gate, hosted acceptance, coverage-floor proof
or release authority. Hosted CI must exercise the same prefix-independent cases
on its installed interpreter. ZDD, 85% aggregate / 75% per-file coverage, full
gate, exact-SHA CI and branch/promotion requirements are unchanged. Required
commit collection retains the inherited Python `-B` child behavior. No service,
deployment, cleanup authority or generated observation is modified.
