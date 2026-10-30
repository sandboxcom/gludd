# Legacy bulk worktree cleanup retirement

S98.4,2026-10-23. The compatibility CLI and worktree-cleanup-merged Make target
are retired. They return status 2 with bounded next-action guidance. They do not
claim cleanup, health, acceptance, gate, hosted or release success.

## Confirmed unsafe boundary

The previous executor listed worktrees through the health reader, tested branch
ancestry and tracked cleanliness, then used forced worktree removal and deleted
the branch ref. It did not protect ignored release evidence or the active logical
owner, gate, process and checkpoint lifecycle. It shortened the displayed source
identity to eight characters and ignored the branch-only unregister result.
The tracked-clean and merged predicates did not establish retirement authority.

Actual native CLI and complete Make-body controls reproduce destruction inside
private real repositories, under ordinary and -e Make execution. Every case
deleted ignored proof and the selected branch while a live controller-held
native gate-run-lock still blocked mutation. The real native unregister then
failed with PermissionError during atomic replacement in an owned read-only
registry directory; legacy cleanup hid native Make 2 and still returned 0.
The frozen peer, its checkpoint and lock were retained in the captured snapshot.
No live Main cleanup occurred.

RED: 3 failures in 5.16s, observed 6.2s, Python 3.11.14:
legacy-worktree-cleanup/legacy-cleanup-native-red311-54413f1.json.
Its -e invocation also exposed an ambient UV override/no-project warning.
The unsafe deletion remains genuine; subsequent receiving pins the owned UV
command-line selector explicitly, preserving ordinary/-e carrier boundaries.

## Retirement and next action

The Python compatibility module now contains only its stdlib refusal. The Make
recipe uses shell printf/exit and starts no UV, Python, recursive Make, Git or
other owner child. Existing general Make startup is outside that recipe claim.
All unsafe inventory, forced-removal, branch-deletion and unregister helpers
were removed rather than retained behind an unproved guard. No alternative
pruner, merger, observer, cache or lifecycle API was introduced.

The safe existing zero-variable contract example is deliberately a refusal:

~~~sh
make worktree-cleanup-merged
~~~

The existing contract keeps the same behavior command and zero-variable list;
its expected_result now explicitly records native 2 and no mutation.

Expected result: native status 2, outcome=refused, gate_evidence=false and
release_ready=false. No cleanup happens. The direct Python CLI has the same
status and guidance; unsupported legacy arguments cannot authorize cleanup.

Reconcile the exact branch, canonical path, active logical owner and retained
evidence through the existing native status/lifecycle owners. Plan selected
retirement with agent-cleanup only after native exact branch/path/full-SHA
reconciliation and released logical ownership. Preserve the configured registry:
never pass an empty registry override or guess a registry file/path. Follow the
existing selected owner's documented inputs with AGENT_CLEANUP_VALIDATE_ONLY=1.
For genuinely missing-path registrations, inspect
the existing wt-prune-safe validate-only plan. Neither route authorizes force
removal, branch-ref deletion, automatic ownership release or gate bypass.
Keep the native archive/process/checkpoint checks and full source identities.

The native prune/retirement/registry owners and worktree-health-check were not
changed. Existing health violations and all original health assertions remain;
a retired compatibility command cannot make them healthy. Integration/merge
aliases belong to their separate owning packet.

## Tests and source scope

Narrow GREEN: all 5 new cases pass in 1.90s, observed 2.8s:
legacy-worktree-cleanup/legacy-cleanup-native-green311-54413f1.json.
They execute the actual CLI, full Make rule, module main and real __main__ entry.
CLI/Make controls preserve the complete private refs/index/working bytes,
ignored proof, active registry and native gate/peer lock snapshot. Direct module
controls reject process creation. The corrected -e control emits no UV warning. This narrow receipt precedes the
final registry-preservation/full-SHA guidance and contract wording refinement;
those frozen bytes still require whole-profile/consumer receiving.

Fixtures replace only the old literal checkout/root constants in private copies.
They use real Git and actual registry/unregister code with an explicit private
registry, not a fake failure code. The native lock has a real live PID/start
identity; it is a fixture mutation refusal, not an executed release gate.
Only owned fixture permissions/lease are restored at teardown. No default Main
registry, foreign worktree or actual live cleanup is used.

The whole changed production module is included by
config/coverage_legacy_worktree_cleanup.ini, with branch measurement and unchanged
85% aggregate/75% each-file line AND branch floors. Root's staged-content
guard passed before the final frozen-source runs: whitespace/EOF, secrets and
line checks passed for all eight paths; clone baseline 3510, new 0. Receipt:
legacy-cleanup-staged-preflight/legacy-cleanup-staged-preflight311-54413f1.json.

Both complete module profiles measure all 8 statements and 2 branches at 100%
lines AND branches, not a restricted function slice:

| Runtime | Profile cases / pytest | Observed | Native coverage-files run |
| --- | --- | --- | --- |
| 3.11.14 | 5 passed / 2.42s | 3.9s | 20261023T221558Z-23119-07bfc26f |
| 3.12.15 | 5 passed / 3.06s | 5.2s | 20261023T221612Z-23377-2486290d |

Audit artifacts coverage-20261023T221601Z.json and
coverage-20261023T221617Z.json each pass unchanged 85/75 line/branch floors.
The ordinary union retains the complete health owner, seven complete safe
audit classes and its original zero-inventory native smoke, complete M13,
target-contract, Make-help audit and script-reference owners:

| Runtime | Ordinary cases / pytest | Observed | Native legacy-worktree-cleanup run |
| --- | --- | --- | --- |
| 3.11.14 | 109 passed / 5.37s | 6.3s | legacy-cleanup-union311-54413f1 |
| 3.12.15 | 109 passed / 6.08s | 7.3s | legacy-cleanup-union312-54413f1 |

All four exact sessions drained terminal 0 under original 90/60 bounds, fatal
warnings and native serial/PID-owned temporary inputs. Fresh native load checks
passed before each cohort. Source, tests and configuration were fixed throughout;
this final receipt paragraph is a later documentation update.
The health audit's three unrelated live-Main/remote query nodes remain unchanged
in the canonical gate. No old assertion or gate node was removed.

After those runs, the exact no-variable example was also executed in the real
checkout. It returns native Make 2/Error 2 and the prescribed retired/refused
guidance while doing no cleanup. That expected refusal is not a failed repair
or a successful cleanup. Root owns final Markdown validation, independent review,
normal collection and commit; no Main receiving, CI or deployment is claimed.

## Long-lived ownership context

The [official Git worktree contract](https://git-scm.com/docs/git-worktree)
distinguishes existing branch/path ownership, missing-path registrations and
forced overrides. Those overrides supply no project retirement permission.
The previously retrieved [2017 ownership question](https://stackoverflow.com/questions/41545293/branch-is-already-checked-out-at-other-location-in-git-worktrees)
has API activity through 2025-09-07. In
[torek's answer](https://stackoverflow.com/a/41546512), pruning advice depends on
the old worktree actually having been removed; an existing worktree remains owned.
[Tom Ellis's 2023 firsthand rebase report](https://stackoverflow.com/a/76664209)
also shows why a superficial branch observation can miss ongoing ownership.
The accepted native research bodies are recorded in the
[creation feature](MAKE_HELPER_BOOTSTRAP_DIAGNOSTICS.md#firsthand-worktree-ownership-reports).
No new HTTP or exact-version inference from unfetched linked commits was used.

This is control-plane ZDD. No deployed component is restarted. Independent
review, normal collection, the complete canonical gate and required exact-SHA
hosted/deployment evidence remain separate from this retirement repair.
