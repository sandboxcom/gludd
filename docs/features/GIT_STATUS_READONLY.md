# Read-only native Git status

## Observed boundary and decision

The first SESSION-boundary ship-commit-files attempt from
ad4f92abc025cd416d0c72d822270336901ba00a stops in the native read-only secrets
pre-commit guard, before collection or a commit. A stash restoration reports
.git/index.lock already exists, child Git128/enclosing Make2. Root's exact
exec82740 is terminal. Native worktree-state retains the same15dirty paths and
eight staged paths; ps shows only the two existing watchdogs. The originating
lock writer was not captured. Do not attribute it to a model or delete the lock.

Existing agent_watchdog.check_push_status calls make git-status. That advertised
read-only owner runs ordinary Git status, which may refresh the index, and its
echo fallback hides native failures. Controlled real-Git fixtures reproduce
both defects:5FAIL/1PASS1.32s/native2/2.3s at
.gate-logs/observed/git-status-readonly-red/v012-git-status-readonly-red311.json.
The held competing-lock/dirty-output predecessor control already passes.

Reuse Git's existing --no-optional-locks option, already used by the native merge
acceptance helper. No lock/parser/observer/dependency is added. Keep stdout's
short status data and return the native Git exit through Make. AGENT_NEXT goes
to stderr: observed0 means observation only; failed preserves child128/17 and
directs reconciliation through worktree-state. Neither supplies gate, commit,
CI or release authority. Watchdogs remain running; no enforcement is disabled.

## Native controls and early admission

The complete six-case owner preserves index bytes/mtime/inode despite forced
stale stat metadata, including inherited GIT_OPTIONAL_LOCKS=1. A subsequent
ordinary Git status must actually refresh that same private fixture, so the
read-only assertion has a real write witness. Other controls retain staged/
working/untracked output and an existing lock's bytes/mode/mtime/inode, actual
outside-repository128, child17/exact argv and the zero-variable contract.
The source owner plus complete fixture-collision and read-only classification
owners passes130/5.80s/native0/6.6s:
.gate-logs/observed/git-status-readonly/v012-git-status-readonly311.json.
Ruff and separate strict complete-owner checks pass.

Append one exact native inherited-one regression as family176
git-status-native-index-readonly in the existing first compatibility cohort.
All175 predecessors, independent seeds/literal rosters, three2/1/1 children,
90total/60quiet,21phases/2850 and the required full gate remain.
Routing RED retains4FAIL/4PASS1.28s/native2/2.8s at
.gate-logs/observed/git-status-routing-red/v012-git-status-routing-red311.json.
The first positive attempt retains1FAIL/7PASS1.75s: a second independent literal
consumer was missing. Correct that consumer, and compact only an unchanged
three-line expected mapping to two lines to retain the strict<2500 limit.
All eight required pins then pass1.80s/terminal0. Complete changed owners,
both routing owners, fixture/classification/Make contract/layout/deep and handoff/
link consumers pass449 per runtime:31162.93s/native0/63.9s and
31285.53s/native0/86.7s. Receipts remain
.gate-logs/observed/git-status-complete-receiving/v012-git-status-complete-receiving{311,312}.json;
raw/XML are /tmp/gludd-v012-git-status-complete-receiving{311,312}.{log,xml}.
Contract282, the actual zero-variable make git-status example, task registration,
line limits,1160targets/zero duplicates and ten Markdown files pass. No production
Python source changes or coverage exclusions are introduced by this status repair.
The earlier trace coverage retains its own unchanged four-source scope; full gate,
a guarded commit, clean-SHA repair and hosted/deployment proof remain pending.

## Resume without losing owned work

Use git-unstage-files with the exact eight owned FILES to clear only their index
entries before staging this small status repair first. It preserves working
bytes. Staging the changed Make status owner first keeps it present while native
pre-commit temporarily stashes other repairs. Preserve the failed hook evidence;
do not clear locks, force, skip hooks or repeat an unchanged failure. Reconcile
native worktree-state and quiesce all model/read/test owners before Git hooks.
Commit the SESSION, URI and trace repairs separately afterward, with their
mandatory native collection. Use ci-failure-repair on the clean successor,
then normal guarded publication, remote verification and native pipeline-status.
All seven release tasks and required deployment/exact-SHA proofs remain pending.
This control-plane repair changes no serving process or data.
