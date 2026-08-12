# Make Target Contract

Agent work is driven through Make targets. Before any tool call, read `make help`,
select the narrowest appropriate target, and set every required variable explicitly.
The target's usage line and `config/make_target_contract.json` are the source of truth.

Every agent-facing target has a behavioral smoke example in the contract. Run that
behavioral smoke after changing a target or its variables; a successful parse is not
enough. Keep long-running work observable through the target's normal output.

Do not issue bare shell commands when a Make target exists. If a needed operation has
no target, add the target, document its variables and safe example, and add a behavioral
test before using it. `make check-make-target-contract` enforces this contract and is
part of the release gate.

For status claims, use `make ps` for auditable test/audit PIDs. `make ps-gludd` covers
only namespaced Gludd daemons; it is not evidence that delegated model work is idle.
Use `make active-work-status` for one JSON snapshot combining PIDs, gate state, git
hashes, and unchecked task IDs. Each process is tagged with a logical `task`, and
the `workstreams` map groups controller and child PIDs so a second terminal can
verify whether one target has spawned real parallel workers. The snapshot
intentionally reports `agent_pids: false`: model-agent turns are not OS processes;
only their spawned Make/pytest work can have auditable PIDs.

Release-candidate pushes use `make ci-push-committed-head`, whose
`ci-trigger-committed-head` step is the idempotent exact-SHA signal documented
in [CI_EXACT_SHA_SIGNAL.md](CI_EXACT_SHA_SIGNAL.md). It must return a confirmed
`GHA_RUN_URL`; a successful push by itself is not evidence that CI started.

## Integration temporary paths

`make integration-health` gives pytest a short, process-unique `/tmp/gi-*`
`--basetemp`. The name includes a stable hash of the project namespace, so
parallel checkouts cannot clear each other's test directories, and the process
suffix keeps concurrent runs isolated. The target removes only its own directory
on exit. Keep this prefix short: pytest and xdist append worker and sanitized test
names before an AF_UNIX socket filename is added.

This follows long-lived upstream reports rather than weakening socket tests.
CPython users documented that AF_UNIX tests fail once a temporary root pushes the
socket name beyond the platform limit ([CPython #93852](https://github.com/python/cpython/issues/93852)).
Pytest documents that its default layout adds `pytest-of-{user}/pytest-{num}` and
that `--basetemp` directly controls the root ([pytest temporary-directory guide](https://docs.pytest.org/en/stable/how-to/tmp_path.html#temporary-directory-location-and-retention)).
A pytest user report also shows xdist adds a `popen-gwN` layer under the chosen
base ([pytest #10679](https://github.com/pytest-dev/pytest/issues/10679)).
