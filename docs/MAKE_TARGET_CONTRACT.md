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
