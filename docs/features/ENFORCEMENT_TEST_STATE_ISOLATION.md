# Enforcement test state isolation

Recorded 2026-10-26 UTC for S98.4 on
feature/v012-enforcement-state-20261026, based on
ec17ce3fcbeb23b5d2dec01648058675d9247997. These are working-source acceptance
receipts; normal source commit, Main receiving and release certification remain
separate.

## Actual boundary

The runtime invocation tests inherited live plugin state. Their read-tool case
also wrote the default main-thread streak file directly. The reload tests
created, removed and asserted on shared enforcement files while invoking native
Make targets that hardcode those defaults. An xdist group cannot isolate these
operations from another pytest process or an active session.

Self-review of the first repair found three missed parent-side literals. The
corrupt-state case wrote and removed the shared tool-streak and block-counter
files; floor-streak setup also attempted to remove the shared disengage file.
The earlier 131-pass/1-skip receipts therefore fail to prove isolation. They are
retained below without being promoted into acceptance for the corrected source.
No manual shared-state restoration was attempted.

The safe native RED used Make dry-run output from the actual complete recipes.
Both ordinary Make and environment-override mode exposed the shared disengage
and floor paths before any target write executed. A separate real Node child
showed that all eight independently asserted state carriers were absent. These
measurements establish the local input defect.

## Fixture repair

The runtime module assigns a separate pytest-owned state directory to each test
with monkeypatch.setenv. The actual Node subprocess receives a copied
environment, followed by explicit case overrides. Temporary TypeScript programs,
streaks, locks, deadline state and other owned state use that directory.
The explicit override wins in the child while the parent value and an unrelated
environment carrier remain intact. A guard binds only this test module's open
helper and cleanup helper to its owned state directory. It leaves Python's
builtins, pathlib, Node and Make unchanged; the two existing dirty-tree input
files are limited to this registered checkout. Separate safe RED controls use
an outside-state decoy within tmp_path and prove that writes and unlinks must
refuse before changing its bytes. Gate autoscheduling is disabled in this
fixture so a hook-contract test cannot start another gate.

The reload module composes the logical Makefile with the existing layout owner.
Its private Makefile retains complete native recipe blocks, the native PHONY
declarations and shared inspection providers. Only the literal default state
prefix is rebound to the private fixture directory. The native cleaner still
executes its actual implementation; its candidate-root input is confined to a
pytest-owned cleanup directory. A matching candidate outside that root remains
byte-identical. The verifier uses the actual source script.

Every child receives an explicit copied environment. The fixture removes only
its recursive Make control carriers, keeps unrelated values, selects the
installed runtime, and retains ordinary and environment-override controls.
A private link to this checkout's own Git metadata supplies the recipe's
read-only source identity without exporting common-repository paths.

Original allow/deny, corruption, reset, disengage, status and verification
assertions remain. Missing annotations in the existing reload tests were added
for strict typing. The existing session-start corruption skip remains explicit;
this change introduces no skip or production enforcement exception.

## Checkout witness ownership after independent review

Independent review held source approval because two necessary Git-dirty inputs
still used fixed checkout filenames. The earlier 192-pass/1-skip serial receipts
cover the parent-state repair but do not establish checkout witness ownership.

Each test fixture now plans two paths containing its process PID and a fresh
UUID. The module IO allowlist contains exactly those planned inputs. Actual
dirty-tree cases use exclusive file creation, verify that read-only native Git
reports their own input as untracked, and remove only an input they created.
Original clean-tree Node hooks and their allow/deny assertions remain.

A safe two-case RED used an owned checkout under tmp_path. It demonstrated both
identical plans and an allowed overwrite of an unowned legacy-name decoy.
No real checkout witness was written in that RED. The repaired controls retain
the legacy decoy and another owner's planned-path decoy byte-identically.
The narrow six-case GREEN also executes all four actual dirty-tree cases and
their native Git observation.

The actual normal two-worker LoadGroupScheduling run retains the native
conftest grouping owner. That owner conservatively assigns any module containing
a literal /tmp/gludd- string to enforcement-shared-state, including comments and
negative witnesses. The two enforcement modules therefore remain in one native
scheduler group. The run proves ordinary two-worker harness interaction; it
does not prove overlapping execution of the dirty-tree cases or a performance
improvement. PID-plus-UUID ownership and the independent foreign-path controls
establish the collision repair without weakening that existing grouping guard.

## Firsthand research

A user reported process CWD leaking between tests in
[pytest issue 7228](https://github.com/pytest-dev/pytest/issues/7228), opened
2020-05-18. The maintainer recommended the restoring monkeypatch fixture. This
is historical evidence of a shared-state hazard, not a claim about a current
pytest defect.

The official [tmp_path guide](https://docs.pytest.org/en/stable/how-to/tmp_path.html)
defines per-test unique directories and requires unique basetemp locations for
concurrent invocations because each basetemp is cleared. The
[monkeypatch guide](https://docs.pytest.org/en/stable/how-to/monkeypatch.html)
documents automatic restoration after a test. Those ownership mechanisms support
the fixture repair; the native RED above establishes this project's cause.
Root retrieved these sources as the release wave's sole public research owner.

## Acceptance and retained failures

All observer roots below belong to this feature checkout under
.gate-logs/observed. The installed Python3.11 and Python3.12 toolchains were
selected explicitly in both outer watcher and actual Make child, with no sync
or downloads. The complete owners run serially with fatal warnings and one
execution process.

| Owner | Result | Native observation |
| --- | --- | --- |
| Initial combined RED | Node carrier control failed; two Make controls were refused by checkout confinement | enforcement-isolation-red/enforcement-isolation-red-ec17-312.json |
| Actual native boundary RED after fixture-input reconciliation | 2 failed, ordinary and environment-override Make | enforcement-native-boundary-red/enforcement-native-boundary-red-ec17-312.json |
| Narrow repair controls | 5 passed | enforcement-isolation-green/enforcement-isolation-green-ec17-312.json |
| Earlier Python3.11 receiving, isolation unproved | 131 passed, 1 pre-existing skip; 52.22 seconds | enforcement-isolation-acceptance311/enforcement-isolation-acceptance-ec17-311.json |
| Earlier Python3.12 receiving, isolation unproved | 131 passed, 1 pre-existing skip; 53.23 seconds | enforcement-isolation-acceptance312/enforcement-isolation-acceptance-ec17-312.json |
| Parent IO guard plus complete fixture audit and logical-Makefile node | 62 passed | enforcement-isolation-static-receiving/enforcement-isolation-static-receiving-ec17-312.json |
| Parent-state receiving before witness repair, Python3.11 | 192 passed, 1 pre-existing skip; 60.13 seconds | enforcement-isolation-final311/enforcement-isolation-final-ec17-311.json |
| Parent-state receiving before witness repair, Python3.12 | 192 passed, 1 pre-existing skip; 60.87 seconds | enforcement-isolation-final312/enforcement-isolation-final-ec17-312.json |
| Safe witness collision and ownership RED | 2 failed | enforcement-witness-collision-red/enforcement-witness-collision-red-ec17-312.json |
| Witness controls and all actual dirty-tree cases | 6 passed | enforcement-witness-green/enforcement-witness-green-ec17-312.json |
| Final three-owner Python3.11 receiving | 194 passed, 1 pre-existing skip; 56.22 seconds | enforcement-witness-final311/enforcement-witness-final-ec17-311.json |
| Final three-owner Python3.12 receiving | 194 passed, 1 pre-existing skip; 58.39 seconds | enforcement-witness-final312/enforcement-witness-final-ec17-312.json |
| Actual native Python3.12 two-worker loadgroup | 194 passed, 1 pre-existing skip; 54.55 seconds | enforcement-witness-xdist312/enforcement-witness-xdist-ec17-312.json |

The initial confinement refusal identified fixture Git environment carriers.
It supplies no shared-path execution proof. The subsequent dry-run RED reached
the actual native recipe boundary after that input was reconciled.

The logical-Makefile AST guard passed before receiving. Focused Ruff, strict
typing, the bare-test-environment guard and tracked-file line limits passed.
Strict typing's initial duplicate CLI-module import and existing missing
annotations remain failed diagnostic input; the final fixture uses explicit
state input data and complete annotations instead of changing typing policy.

The final three-owner serial receipts execute:

~~~console
make test-files TESTFILES='tests/unit/test_enforcement_runtime_invoke.py tests/unit/test_enforcement_reload_targets.py tests/unit/test_pytest_param_fixture_collisions.py' PYTEST_ARGS='-q -W error -n 0' ID=
~~~

Append the installed runtime tuple from the
[mandatory Make guide](../../.agents/policy/key-make-targets.md#run-focused-checks-with-explicit-inputs)
to this child and its watcher. MAX_SECS600/STALL_SECS60 bounds these focused
receiving commands; canonical publication/runtime and release budgets remain
unchanged. The actual parallel receiving uses the same owners with:

~~~console
PYTEST_ARGS='-q -W error -n 2 --dist loadgroup --max-worker-restart=0'
~~~

The corrected parent IO guard has a separate 2-failure safe RED observation:
enforcement-parent-io-red/enforcement-parent-io-red-ec17-312.json. Both operations
changed only the deliberately owned decoy in that RED. The final three-owner
receiving passed on each installed runtime after all three missed literals were
replaced and the module-owned IO guard was added. Both exact observer sessions
were drained and their terminal passed status verified. Subsequent independent
review identified the checkout-witness gap above; the final witness-repair
serial and two-worker receipts were separately drained and verified after that
input changed. The earlier 131/1
receipts remain evidence of the failed isolation proof and its actual shared
side effects.

## Proof limits and next owner

This repair confines behavioral test state and native Make cleanup inputs.
Production heartbeat and session diagnostic log defaults retain their native
paths, including /tmp/gludd-plugin-heartbeat-*.json,
/tmp/gludd-plugin-loaded.log, /tmp/gludd-session-debug.log and
/tmp/gludd-session-start-errors.log. This test-only change establishes no
project-wide plugin namespace migration or daemon activation.

No production behavior, coverage exclusion, selector, gate, ZDD rollout,
deployment or release requirement is relaxed. Global coverage must still pass
85 percent aggregate and 75 percent for every file on both lines and branches.
Local test acceptance does not supply a complete canonical gate or exact-SHA
hosted result.

Root owns independent source review, exact-path staging, normal collection and
commit, candidate receiving, real release-proof admission and the required
canonical gate, hosted, deployment and artifact proofs. No release task is
closed by these receipts. One unselected-runtime load check created an empty
local .venv; preserve it for the existing ownership-checked lifecycle cleanup
after this worktree is inactive.
