# Read-only native Git status

## Introduced fixture representations (2026-10-27 UTC)

The original-source audit compares published
a403ed858bb561f7bbc8353c54ad057be4020f89 with development
82db11b57b254c1ca1e035975b5cc86253e2fed5. It parses all122 changed unit
owners without executing them and identifies six introduced automatic fixture
representations containing copied environment values. Root already repaired
_OwnedRelease separately; this packet owns the other five:

| Original a403 owner | Exposed construction |
| --- | --- |
| tests/unit/test_git_status_readonly.py:19–22 | StatusFixture NamedTuple environment field |
| tests/unit/test_git_merge_nc_safety.py:42,96 | repository returns root/environment tuple |
| tests/unit/test_make_commit_transaction.py:232–235 | NativeTransaction dataclass environment field |
| tests/unit/test_make_checker_guidance.py:46,67 | native_repository returns root/environment tuple |
| tests/unit/test_run_watched_command_boundary.py:73–75 | Probe dataclass environment field |

These five files are absent at the development baseline. HookFixture in
test_collection_hook_environment.py and NativeMake in
test_make_uv_execution_boundary.py are byte-identical to their baseline
containers and stay outside this introduced-source repair. Ordinary helper
returns that are locally unpacked are not pytest fixture-argument values.
The bounded audit establishes no repository-wide claim about every object or
future fixture. The historical private proposal and original122 inventory stay
under /tmp/gludd-v012-a403-env-container-proposal-20261027.

### Construction boundary and retained native behavior

Each regression constructs only a synthetic two-key mapping with owned public
markers. No ambient environment is poisoned, printed or intentionally rendered
on a failing path. For the two tuple fixtures, the tests-first stage extracts
only the existing root/environment construction into a typed helper; the
ordinary fixture calls that same construction boundary and still returns the
original tuple during RED. Synthetic controls therefore exercise its actual
representation without creating a native Git fixture or copying credentials.

GREEN gives those two states explicit frozen dataclasses and field(repr=False).
Their fixture/test annotations and existing setup unpacking become truthful
root/environment attribute access. The merge-guidance consumer receives the
same mechanical adaptation. StatusFixture also becomes a frozen dataclass;
NativeTransaction and Probe retain their existing dataclass mutability while
excluding only their environment field from generated repr. The complete
mapping and object identity remain available for ordinary native child use.
No Any, mapping wrapper, custom environment repr, key filtering or parent-global
environment mutation is introduced.

Independent private source comparison preserves all516 original assertion
expressions/messages and137 original qualified functions across the six source
owners. Both real native tuple-fixture bodies before final construction remain
exact. Real Git/Make/subprocess, source identity, signal/status/cleanup, inherited
carrier handling, original selectors and fatal warnings retain their contracts.
This source comparison is separate from pytest, normal collection and gates.
The first private comparison incorrectly compared global breadth-first AST
order after appending tests; it stopped before repository mutation. Comparing
each original qualified function's assertion sequence resolves that checker
input without altering a source assertion.

### Actual RED, GREEN and process boundary

The first sandbox AST child passes one case in19.20s, then the unchanged
observer fails native125/20.585s on actual psutil/sysctl and killpg EPERM;
enclosing Make returns2. Its failed/inactive receipt remains
v012-fixture-privacy-ast/v012-fixture-privacy-ast311-a403-20261027.json.
The changed permission-context native retry passes1/18.57s/native0/19.858s:
v012-fixture-privacy-ast/v012-fixture-privacy-ast-native311-a403-20261027.json.
No observer repair, suppression or performance claim follows that clearance.
All subsequent watched pytest owners use the approved native process boundary.

Actual synthetic RED fails all five intended repr assertions in1.06s,
native2/2.416s at
v012-fixture-privacy-red/v012-fixture-privacy-red311-a403-20261027.json.
Its trace records18events/5 complete pairs/5 expected failure reports and zero
issues or unfinished cases. Every failure contains owned fake inputs only.

After the narrow field/construction repair, the logical-Make AST plus all five
privacy regressions pass6/18.79s/native0/20.000s at
v012-fixture-privacy-green/v012-fixture-privacy-green311-a403-20261027.json.
Its trace records15events/6 complete pairs and zero issues, unfinished cases or
failures. Exact sessions63468,92437,7388 and86282 are drained, and each actual
terminal receipt is verified through observed-status. Receipt paths above are
relative to this checkout's .gate-logs/observed.

Ruff, the environment-write guard, working-file line limits and six separate
complete strict typing scopes on EACH installed Python3.11/3.12 pass. No
production Python source or coverage profile changes. Existing85aggregate/
75each-file LINE AND BRANCH, canonical full gates, ZDD and promotion remain.

The first native registration check refuses three current paths. A prospective
call to the unchanged native registration function identifies five missing
paths across the full intended source/feature roster; that plan supplies no
execution proof. Root initially authorizes one inline append of exactly those
five paths to existing TASKS line8. Actual registration then passes6 changed
paths, and passes7 after GREEN adds the merge-guidance consumer. That line8
placement is historical: before final live review, Root authorizes moving only
these five privacy references into the existing canonical unchecked S98.4 task
scope, immediately before its Node security scope. The supporting line8 returns
to this branch's original bytes, so Main can retain the typing packet's separate
two-path support append when combining branches. This changes exactly two TASKS
lines without adding paragraphs or changing existing task text. Native ledger
validation retains188 checked/140 unchecked, all328 IDs/states/order and2499
lines; no task is ticked. The administrative move adds no pytest or gate proof.

### Remaining proof ownership

The six changed complete test owners and three existing consumers form one
nine-owner receiving union: git_status_readonly, git_merge_nc_safety,
make_commit_transaction, make_checker_guidance, run_watched_command_boundary,
git_merge_nc_guidance, commit_observer_loading, gate_failure_promotion and
runtime_admission_scheduling. Each denotes its complete tests/unit/test_*.py
owner. Root owns staged content admission before broad receiving; both installed
runtimes run sequentially in one reserved width1 cohort with explicit selected
Python/SYSTEM_PYTHON/environment/pinned UV0.13.1 on watcher and actual CMD,
checkout-owned observations/ledger and literal ID=.

### Complete receiving on both installed runtimes

The unchanged complete nine-owner union passes693 cases EACH, with no skips:

| Installed runtime | Pytest seconds | Native seconds/status | Immutable receiving run |
| --- | --- | --- | --- |
| Python3.11.14 | 446.18 | 450.978 / 0 | v012-fixture-privacy-receiving311-a403-20261027 |
| Python3.12.15 | 476.79 | 481.823 / 0 | v012-fixture-privacy-receiving312-a403-20261027 |

Both JSON receipts are under this peer's
.gate-logs/observed/v012-fixture-privacy-receiving/ with the corresponding
run name and .json suffix. Exact sessions65930/59095 are drained; native
observed-status verifies inactive/passed/exit0. Each real trace has1475events/
720 complete protocol pairs, zero issues and zero unfinished cases. Two expected
worker/controller failure reports belong to the owned test_warning.py::
test_original_warning child. The preserved
test_two_worker_native_admission_keeps_warnings_fatal requires that child's
nonzero status, FAILED output and original UserWarning; its parent passes.
These retained negative controls are separate from the693 passing outer cases.

Each outer receiving invocation uses -n0 and one reserved cohort. Original
nested one/two-worker controls remain unchanged; the reservation is not a
global process-count claim. Native600total/180quiet, per-case bounds, exact
selectors and all original assertions remain. No code/test edit follows the
first staged content guard or either receiving run.

Before broad receiving, Root's exact eight-path staged guard passes
native0/3.811s at
v012-fixture-privacy-content/v012-fixture-privacy-content311-a403-20261027.json.
Formatting, secrets and staged line limits pass; changed-production=0 correctly
selects the existing clone owner's skip branch. This is not a repository-wide
clone claim. Root's native Main Rumdl checks the exact peer feature/TASKS
snapshot in26ms. That guard and lint precede this receipt/research-only update;
Root must restage/check the final prose before normal collection/commit.
The normal per-commit collection, independent final live review, complete
canonical global85/75 LINE AND BRANCH gate, exact-SHA hosted/artifact/ZDD/
deployment, promotion and release proofs remain pending.

### Primary contracts and bounded firsthand search

[Python3.11 dataclasses](https://docs.python.org/3.11/library/dataclasses.html)
documents generated repr including each field, with fields explicitly marked
repr=False omitted. [Pytest output controls](https://docs.pytest.org/en/stable/how-to/output.html)
document optional --showlocals/-l and --no-showlocals. Optional local-variable
display is separate from the tested container repr; these sources establish no
claim that every default traceback prints every environment.

Root's existing public research owner fetched both primary pages and the first
historical query with HTTP200/native1.420s:
v012-fixture-privacy-research/v012-fixture-privacy-research-a403-20261027.json.
The first query reports11 total results, but only the three oldest bodies were
fetched and triaged; those three are unrelated and are not supporting evidence.
A bounded title/body plus showlocals/secret refinement returns zero items,
incomplete_results=false, HTTP200/native0.750s:
v012-fixture-privacy-research/v012-fixture-privacy-research-refined-20261027.json.
The actual refined body is retained as firsthand-refined.json
under /tmp/gludd-v012-fixture-privacy-research-20261027, alongside primary raw
pages. No absence claim extends to all11 results, other queries or all history.

Research receipt paths above are Root-owned under Main's .gate-logs/observed;
they are separate from this peer's pytest receipts and supply no gate proof.

The actual five owned synthetic REDs establish this repository's introduced
construction defect. No current pytest bug, real credential exposure or
upstream resolution is inferred from this bounded research.

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

The changed-input guarded retry commits the status repair at
32bb6190b882c93af422489b4297d5588f7f6e6a. Native staged-content checks,
zero-new-clone audit and collection pass; the latter selects129268/129292collected,
24deselected/0errors48.68s/native55.0s at
.gate-logs/observed/test-count/20261022T203312Z-84726-266d785b.json.
Six other owned receiving code paths remain unstaged during that working-checkout
collection; this is not a claim about the intermediate committed tree's isolated
test count. The successful retry identifies no original writer and proves no
full gate, CI or deployment. All old failures and protected checkpoints remain.

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

## Main privacy receiving after the native TASKS conflict (2026-10-27 UTC)

Native git-merge-nc of privacy donor
86ba8b8fd263a4acacbf709f04dacad5e97aeff8 into clean Main
e7d814b263c15571f9c457f317a9b9c5ba422652 encounters conflict1 in adjacent
TASKS scope prose and aborts0; Main is independently verified clean afterward.
No successful donor merge or ancestry is claimed. A private read-only preimage
check verifies all seven incoming file preimages equal published a403 and all
seven donor postimages equal86ba8b8. The first checker fails on its own malformed
regex before project mutation; the corrected checker passes. Direct native
copy-file from an absolute peer path refuses before writing; immutable native
git-show-file-to exports through its allowed /tmp/gludd namespace, then native
copy-file receives those seven files. A singleton TASKS prefix insertion keeps
all328 IDs/states/order,2499lines, typing line8 and Node continuation line624.
Independent live eight-path review approves source only and retains all137
original test functions and516 original assertion expressions/messages.

Complete six changed test owners plus the logical-Make AST guard pass465 cases
on installed3.11.14, no skips/errors,371.50s/native376.122s at
.gate-logs/observed/v012-main-privacy-receiving/
v012-main-privacy-receiving311-86ba8b8-20261027.json. This900/180 diagnostic
uses the ordinary Main harness, fatal warnings and one worker; its exact native
session is drained and observed-status confirms terminal0. Native trace summary
is partial:743events,369 paired starts/370finishes, one issue, no unfinished
nodes or failure reports. No complete-protocol, cause, timing or performance
claim follows; the pytest terminal result supplies the465-case acceptance.
The peer's complete nine-owner693 cases on EACH runtime retain their own source
and receipts above. This receiving does not replace final combined acceptance,
original90/60 runtime, canonical21600 gate/global85/75 LINE AND BRANCH BOTH,
canonical exact-SHA push CI, ZDD deployment or signed all-assets release proof.
All seven S98 tasks remain unchecked.
