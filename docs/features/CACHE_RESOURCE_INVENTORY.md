# Cache Resource Inventory and Exact-Child Cleanup

## Purpose

Gludd must be able to explain disk pressure without creating more pressure while
it inspects the machine. The cache resource interface inventories bounded,
allowlisted user-cache roots and removes only one explicitly named immediate
child after a validate-first pass. Both operations run through tracked Make
targets and the system Python; they do not create a project virtual environment.

The initial dogfood run found three distinct resource problems: read-only Make
diagnostics could bootstrap a large virtual environment, nested feature
worktrees escaped the old one-level cleanup glob, and inactive local container
state remained after its Podman machine definition disappeared. The tracked
interface replaced the temporary diagnostic shortcut and made each decision
reviewable in Git.

## Operator contract

- `make cache-resource-inventory` defaults to `~/.cache` and emits bounded
  JSON lines for immediate children. It also exposes the project gate-artifact
  root and the existing project-temp namespace inventory through this named
  owner. The temp root plus its immediate project-state/resource children and
  exact namespace children remain inventory-only; this grants no general home
  traversal or deletion. Library/Caches and local container state are refused.
- Measurement failures remain visible as `status=error` entries and make the
  terminal inventory incomplete/nonzero; partial rows never claim completeness.
- `make cache-resource-remove ... CACHE_RESOURCE_VALIDATE_ONLY=1` validates
  one exact immediate child of `~/.cache` without deletion. Project gate logs,
  temporary namespaces, Library/Caches and container state grant no generic
  removal authority. Managed tool caches retain their native lifecycle policy.
- `cache-disk` and `disk-user-caches` are recipe-free compatibility aliases
  for the same bounded inventory owner. Their contract examples exercise safe
  defaults; the canonical owner's contract lists the actual parameter inputs.
  Parameterized operations should use that canonical owner with explicit values.
- The broad `cache-clean`, `clean-gh-run-logs` and `clean-all-caches`
  entrypoints are retired, including executable/help/phony routes. They cannot
  stand in for ownership-checked resource cleanup.
- Roots and candidates must be canonical, non-symlink paths. Parent roots,
  nested descendants, missing paths, project-temp removal, and paths outside
  the operation-specific allowlist fail closed before mutation.
- Worktree cache cleanup recursively finds generated `.venv`, pytest, mypy,
  Ruff, and coverage directories without following symlinks, including nested
  branch-name directories.
- Live OpenCode data and active Lima virtual machines are outside this feature's
  deletion boundary.

The 2026-09-28 release gate exposed an idempotence defect in that boundary. Pass
one legitimately ran `uv cache clean`, which removed the now-empty shared cache
root; pass two then treated that successful postcondition as an inspection
failure and stopped before tests. The convergent automatic caller now identifies
the exact approved absent root as `uv-cache-absent` and continues, while direct
or differently rooted cleanup calls still fail closed. Regression coverage pins
both halves so an absent cache cannot authorize a broader path and a successful
cleanup cannot make the next bounded pass fail. This is the local counterpart to
the long-lived cache-growth pressure reported in
[uv issue #5731](https://github.com/astral-sh/uv/issues/5731): reclamation must be
both explicit and safely repeatable.

The same gate cycle found roughly 735 MiB of provider binaries in two destroyed
Azure proof workspaces. Automatic cleanup now examines only the two exact
Gludd-owned Terraform roots, accepts only a 24-character digest directory with
the protected v1 ownership marker, checks process ownership twice, and
revalidates the marker and canonical provider path immediately before mutation.
It removes only `.terraform/providers`; Terraform state, plans, module source,
and release evidence remain intact. Missing caches are converged postconditions,
while malformed markers, broad or symlinked paths, process-inspection failures,
and more than 16 candidate workspaces fail closed. The narrow boundary responds
to long-lived practitioner reports of per-workspace provider duplication in
[Terraform issue #26144](https://github.com/hashicorp/terraform/issues/26144)
and accumulated obsolete providers in
[Terraform issue #28286](https://github.com/hashicorp/terraform/issues/28286)
without adopting an unsafe broad-directory deletion.

## Practitioner evidence

Long-lived reports show that this is an operational contract rather than a
one-off workstation issue:

- Astral users reported self-hosted uv caches growing to roughly 40 GB and asked
  for a bounded size policy in [uv issue #5731](https://github.com/astral-sh/uv/issues/5731).
  The upstream CI guidance recommends deliberate post-job cleanup such as
  `uv cache prune --ci`, supporting Gludd's explicit tracked cleanup boundary.
- [Podman issue #15742](https://github.com/containers/podman/issues/15742)
  documents machine metadata disappearing while local images and state remain,
  matching the residual local container cache found during dogfood.
- [Lima issue #1427](https://github.com/lima-vm/lima/issues/1427) records the
  operational need to control cache placement. Gludd therefore inventories the
  download cache separately and never treats the active VM directory as a
  regenerable cache.
- Terraform users have reported project trees reaching 20 GB from duplicated
  providers and multi-version provider histories reaching 1.8 GB in a single
  provider namespace. Gludd treats that evidence as justification for reclaiming
  only verified provider binaries after a proof run, never its state or plans.

## Security and resource limits

Inventory is capped at 100 results, each `du` subprocess has a 30-second
deadline, and output is stable JSON suitable for audit logs. Canonical
allowlisting and immediate-child ownership prevent path traversal, symlink
escape, broad home-directory deletion, and accidental mutation of active
service data. Read-only diagnostics are in the no-uv-sync goal set so measuring
resource use cannot silently consume hundreds of megabytes.

## Zero-downtime deployment and rollback

The interface changes no running service, database schema, network route, or
release artifact. It is safe to deploy before callers adopt it. Cache cleanup
may cause a later tool invocation to redownload or rebuild data, but it does not
restart services or remove durable project state. Rollback is the normal Git
revert of the Make target, script, tests, and contract. Removed cache contents
are intentionally not recoverable from Gludd; operators must regenerate or
redownload them from their authoritative source. Azure proof workspaces remain
usable after provider-cache cleanup because their pinned lock files and module
source allow the normal initialization phase to restore the exact provider.

## Verification

The focused test suite covers ordering, limits, unreadable children, canonical
path rejection, validate-only and applied removal, file and directory children,
system-Python startup, JSON observability, nested worktree cleanup, and the
no-bootstrap Make contract. Branch-enabled coverage must remain at least 85%
aggregate and at least 75% for the script. The Make target contract, behavioral
examples, Ruff, strict mypy, docstrings, Markdown, task integrity, collection,
and the full project gate are required before release promotion.

## Native cache-policy alignment (2026-10-21)

Source base is development3964002be2b039fc81d94989bf0e818fdc9a6144.
This is bounded S98.4 support, not a release verdict. The preceding trace-facade
commit has independent SOURCE ONLY approval at
/tmp/gludd-v012-trace-summary-3964002-source-review.md; Root fully read it.
Normal hooks retain128347selected/128371collected,24deselected/zeroerrors,
46.67s/native51.9s, with zero new clones and all six checkpoints. No actual
publication input was repaired by that diagnostic support.

### Existing owners and precise policy boundary

The old default and contract examples pointed at prohibited Library/Caches.
Two legacy diagnostic recipes also traversed broad home roots and ran unbounded
du/find loops. Generic removal admitted the gate-evidence root, and three bulk
entrypoints directly deleted shared caches or all GitHub log zips. Source review
identified these paths; no prohibited host path or legacy cleanup was invoked.

The repair reuses scripts/cache_resource_manager.py, its canonical allowlist,
native du/scandir, monotonic30-second deadline,1000-entry cap,1..100 output limit,
five-second progress and complete/incomplete JSON. No parser, scanner, process
observer, daemon, cache, dependency or resource manager is added. System Python
still runs the helper without UV/bootstrap. Existing symlink/depth checks,
validate-first behavior, native child failures and bounded timeout/reaping remain.

Early literal capture/unexport applies to the two canonical cache goals and
their two explicit diagnostic aliases before startup/export expansion. Separate
private exports and recursive target-context bindings preserve inherited inputs,
ordinary/-e Make and mixed goals on installed GNU Make3.81. Empty ROOT selects
the child's HOME/.cache without evaluating a caller's Make/shell data. Boolean
validation precedes any child; quoted private data becomes native argv. This
measured direct-goal guarantee does not establish hostile arbitrary-parent alias
startup safety or a GNU4.4 binary replay.

Recipe-free aliases have no independent input implementation. The first new
alias contract incorrectly assigned the canonical owner's three variables to
each alias and the unchanged checker correctly refused all six direct-reference
claims. The corrected alias entries contract their safe default behavior;
canonical inventory/remove entries retain every real documented parameter.
No fake variable reference, parser change or checker weakening was introduced.
The native contract then passes274 targets, and Make lint retains1157 unique
targets/zero duplicates,9CI12local parity and the actual make-n-help parse.

The existing path policy supplies the restriction; this aligns native owners,
safe defaults, examples and agent guidance with that policy. It does not grant a
new home prefix. Generic removal of gate logs is refused even for an immediate
child; existing lifecycle owners retain proof/active-resource cleanup authority.

### Failing-first and complete receiving controls

The first53-case owner reports43FAIL/10PASS in5.46s/native6.6s before source
repair. It covers prohibited-root inventory/plan/apply before measurement,
protected gate evidence, safe defaults, bounded alias delegation, literal fields,
private carrier spoofing, retired entrypoints and late/mixed target context.
Retirement cases assert the old recipe is absent before any native invocation,
so a RED cannot execute a legacy destructive prerequisite chain.

The first repaired replay retains4FAIL/49PASS9.72s/native11.1s. A bounded
single-case diagnostic with native locals still fails; it is diagnostic-only.
An explicit stdout/stderr assertion then records the actual mismatch: GNU Make
appends its redundant-goal notice after valid JSON. A separate synthetic fixture
representation check also fails first, using only a controlled sentinel value.
The two-case RED is2FAIL/0.79s/native1.6s. Mixed-goal fixtures now request native
--silent; this leaves child JSON, stderr, argv and failure status intact. Frozen
stdlib dataclass fixtures use field(repr=False) for environment mappings; no
real environment is needed in a privacy assertion. All53 original IDs/assertions
remain, with one additive representation guard.

The old helper fixture changes only Library/Caches to allowed .cache. Its former
generated-gate-root deletion case conforms to the explicit proof policy: native
inventory still reports both resources, generic apply refuses, and both survive.
All other original38-owner controls remain. The complete two owners pass92 on311
in8.94s/native10.1s; raw log/XML:
 /tmp/gludd-v012-cache-policy-complete-green311.*.
The new policy owner is tests/unit/test_cache_resource_policy.py. Preserve
 /tmp/gludd-v012-cache-policy-{red,green}311.* and
 /tmp/gludd-v012-cache-policy-fixture-red311.*.
The green311 name retains the failed4-case attempt; it is not a passing receipt.

Three independent manifest/wiring/roster assertions fail first in0.89s/native1.7s.
One prohibited-root inventory node is then appended after all141 predecessor
families: cache-policy-home-and-proof-boundary,142 total. The actual canonical
checker passes all6 in2.35s; original3children2/1/1,90total/60quiet,21phases2850,
fatal warnings and full-gate authority remain. The other53 controls at that
postimage stay in full acceptance; later additions retain that same routing.
No whole policy owner enters the tight admission.

Nine complete compatibility/environment/readonly owners pass472 on3.12.15 in
33.25s/native34.8s, fatal warnings/no skips/errors:
 /tmp/gludd-v012-cache-policy-complete-compat312.{log,xml}.
They include both cache owners and every previous seven-owner trace-facade
receiving owner. Whole routing/operating and any later lifecycle postimage own
their fresh results; focused source acceptance is not an original runtime receipt.

Using unchanged config/coverage_cache_resource_manager.ini, both complete cache
owners pass92/10.23s/native12.0s. The full script measures98.7line/94.1branch;
both85aggregate/75each-file floors pass with no excluded covered line/file.
Immutable audit .gate-logs/coverage-20261021T142125Z.json and named
.gate-logs/coverage-v012-cache-policy-complete.json retain that source measurement.
Ruff and separate strict checks pass all five changed source/test scopes.
A new typed JSON-size assertion first fails one strict operator check, then
narrows the value with isinstance; no suppression or original assertion is lost.
Later source bytes must obtain their own full-scope coverage, not reuse this one.

### Managed lifecycle admission and model-facing examples

Independent research produces /tmp/gludd-v012-cache-managed-boundary-decision.md,
a minimal production patch and an additive24-case test postimage. Root reads the
complete decision, receives tests first and retains all prior54 IDs/assertions.
The single permitted external fixture probe records1FAIL/1PASS0.56s: managed UV
apply does not raise, while explicit unknown uv-extra apply retains its valid
behavior. Canonical whole additions reproduce22FAIL/2PASS0.75s/native1.5s in
/tmp/gludd-v012-cache-managed-canonical-red311.{log,xml}; no real cache is touched.

The finite namespace map comes from the retired native catalog: uv, gh,
opencode, huggingface, pip, pre-commit, claude, gludd and general-ludd. Casefold
protects equivalent namespace spelling, without a uv* prefix ban. After every
existing canonical-root/symlink/exact-child/existence check, the same existing
CacheResourceError refuses both plan and apply, with the namespace and concrete
lifecycle guidance. Inventory remains available. Explicit unknown-child plan
returnsFalse without mutation; apply returnsTrue after removing that exact child.
Unknown does not prove idle, unowned or disposable; existing authorization and
ZDD requirements still govern real cleanup, including custom managed locations.

Keep each existing lifecycle boundary precise. disk-cleanup-preflight's native
UV cleanup admits only the exact shared /tmp/gludd-uv-cache-public-v2 root after
idleness checks; it does not admit arbitrary ~/.cache/uv. Legacy uv-cache-prune's
force/swallowed wait status is not a replacement proof. opencode-clean maintains
bounded offline data, not the entire shared cache; clean-hf-cache admits only
verified Gludd-owned unleased artifacts. No verified whole-namespace Gludd apply
owner is supplied for gh/pip/pre-commit/claude. Retain/inventory that data instead
of inventing deletion authority. Proof and active Gludd leases remain protected.

Root adds12 actual native Make controls: positive apply with managed-sibling
retention under ordinary/-e Make, both managed plan/apply modes under both Make
modes, and child exit7/124/143 through each canonical recipe. The first complete
nine-owner replay retains6FAIL/502PASS32.24s/native33.6s: the status fixture tries
to replace SYSTEM_PYTHON from argv, which production correctly pins to
/usr/bin/python3. The fixture now includes the unchanged full Makefile and adds
a later explicit fixture-only override; no production interpreter guard or
assertion is weakened. Retain /tmp/gludd-v012-cache-managed-complete311.* as a
failed receipt. These child numeric exits do not claim actual signal delivery.

The executable contract and mandatory guide make the real managed UV plan's
expected Make2 refusal explicit. The JSON example carries an ordinary shell
comment describing that expected outcome; the actual command retains native
stderr/status. Positive plan/apply use the existing test-files owner with an
automatic synthetic HOME, never a manual host-cache fixture. A seven-owner
routing replay first retains1FAIL/450PASS140.10s/native141.3s because the new
guide code block omitted its declared UV_PROJECT_ENVIRONMENT. The corrected
self-contained block includes the installed runtime tuple; the original contract
assertion stays unchanged. Full current-source results follow separately.

### Upstream context and actual safe behavior

The existing stdlib/native owner is the mature implementation for this repair.
Current [UV cache safety](https://docs.astral.sh/uv/concepts/cache/#cache-safety)
separates supported concurrent UV operations from direct filesystem edits;
its native cleanup keeps the cache lock. Gludd retains its stronger ownership
and evidence prerequisites around resource reclamation.

The firsthand [UV issue5731](https://github.com/astral-sh/uv/issues/5731), opened
2024-08-02, describes approximately40GB growth on self-hosted runners. A
2026-02-09 operator reports more than200GiB and a manual deletion still running
after2.5hours. These reports justify explicit placement and bounded lifecycle
operations; they do not establish this workstation's pressure cause or authorize
a shared-cache purge. No new cache-size limiter is introduced.

The actual changed-target examples run with SYSTEM_PYTHON=/usr/bin/python3 and
the installed aligned UV/VIRTUAL_ENV tuple. Both default aliases and explicit
canonical inventory report the same seven allowed entries,0 errors:
cache-disk0.161s, disk-user-caches0.042s, canonical0.042s. Allocated bytes are
pre-commit76947456,uv8355840,opencode5857280,gh1937408,gludd528384,
general-ludd32768,huggingface0. The actual canonical remove example with
ROOT=/Users/shawnwilson/.cache, CANDIDATE=/Users/shawnwilson/.cache/gludd and
VALIDATE_ONLY=1 returned removed=false/status=complete before the lifecycle
repair below. Nothing was removed. That historical plan is not current deletion
eligibility; the final contract deliberately exercises a managed-cache refusal.

These small caches do not clear the retained97percent/6.8GiB capacity failure
against95. The actual publication90.554s timeout remains preserved; no changed
cause is established. HumanTodo remains UNFILED after API refusal and host-space
input unanswered. Do not repeat unchanged cleanup/admission/publication because
HEAD or time changes. Full canonical gate/global85-75 BOTH, exact-SHA all-shard CI,
deployment acceptance and guarded promotion remain pending; all seven S98 tasks
stay unchecked. ZDD, resource namespacing, retention and cleanup guards remain.


### Final source acceptance and retained limits

The final90-case policy owner plus original38 cache controls are included in nine
complete compatibility/environment/readonly owners:508PASS on311 in33.62s/native
35.2s and508PASS on312 in34.67s/native36.0s, fatal warnings/zero skips/errors.
Native logs/XML are /tmp/gludd-v012-cache-managed-final{311,312}.*. All previous
native input/context/privacy assertions and unchanged production interpreter pin
remain. Every yielded owner is drained before another sequential command or Git
hooks; model owners are quiescent separately from OS process observations.

The complete production docstring check discovers D107 on the existing partial
inventory exception initializer. Adding its precise storage docstring passes the
unchanged checker. Fresh full-file coverage after that docstring passes all128
cache cases in11.93s/native13.3s, unchanged branch/subprocess configuration:
98.8line/94.4branch, both85aggregate/75each-file floors. Immutable audit is
.gate-logs/coverage-20261021T143607Z.json; named report is
.gate-logs/coverage-v012-cache-managed-docstrings.json. No file/line exclusion or
lowered floor is introduced. The earlier128/12.38s/native14.1s pre-docstring
receipt and .gate-logs/coverage-20261021T143529Z.json remain historical evidence.
Ruff passes all five changed Python scopes; helper/policy strict checks and
production docstrings pass. The unchanged other three scopes retain their strict
receipts. Native Make lint again passes1157unique/zero duplicates,9CI12local and
make-n-help. Contract274 and task registration15 pass before SESSION integration.

Actual final-source alias/canonical examples again report the same seven allowed
entries and no error, native helper elapsed0.166/0.049/0.056s respectively. The
managed-UV plan emits the precise lifecycle ERROR and native Make2, no success
JSON and no mutation. The contract explicitly expects that negative result.
No real apply or prohibited-root scan runs. All positive mutation/status controls
remain confined to automatic test homes, with sibling/managed payloads retained.

The legacy namespace plan already records39candidates/0reclaimable at the
unchanged24-hour grace in GATE_RESOURCE_LIFECYCLE. Re-reading that authoritative
record avoids another identical scan or weakening age/ownership limits. Neither
these small .cache measurements nor source acceptance changes the actual97percent
capacity input or the original publication timeout. Full gate/global coverage,
exact-SHA hosted/deployment and all seven release checklist items stay pending.

Whole routing after the guide's runtime correction passes451 on311 in127.84s/native128.9s and451 on312 in121.26s/native122.5s. The actual canonical142-family checker passes6/2.21s; Node compatibility passes6/12.42s. The normal current-source operating/commit/review owners still supply their own terminal receipts; no original runtime/publication/gate is inferred.

## Native temporary-size attribution (2026-10-21)

This bounded S98.4 support starts from development
a4f894c29f982281470d07bf4d78ace5114320cc. It changes the diagnostic display;
it does not clear the release capacity prerequisite or authorize removal.

### Reuse the existing owner

The old disk recipe ended with unsorted du output and tail5. Shell name order
could discard the largest entry. The existing tmp-gludd-usage owner already
uses one native du measurement, sort -h and tail40. Disk now delegates to that
owner with recursive Make; the original workspace-volume, generated-file,
gate-log, workspace and Terraform sections remain. There is no new parser,
scanner, dependency, resource manager, observer or background process.

The executable contract records both public targets without new variables:

~~~console
make disk
make tmp-gludd-usage
~~~

These are human diagnostics for the fixed project/temp scope. The forty-row
view is not an exhaustive inventory: existing error suppression may omit paths,
and entries below the retained forty are absent. A zero diagnostic exit does
not certify complete measurement, disposable ownership or safe cleanup.
Use the canonical JSON inventory when its complete/incomplete status matters.

### Failing-first native boundary and actual behavior

The additive fixture runs the actual two recipes through native Make, sort
and tail, replacing only du at a confined process boundary. Forty-five rows put
a 2G entry first by name and 1M..44M entries later. It requires exactly the forty
largest rows in size order, exactly one temporary measurement, and native
MAKELEVEL2 for disk delegation versus1 for the standalone owner. Synthetic
HOME/TMPDIR and a private environment representation keep fixtures confined.

Before source repair, the three new cases retain1FAIL/2PASS in1.76s/native2.8s:
disk prints only40M..44M, losing the2G entry; the existing canonical owner and
privacy guard pass. Receipt: /tmp/gludd-v012-disk-native-red311.{log,xml}.
After delegation, the complete unchanged observability owner plus all three
additions passes22 on311 in1.86s/native2.8s and312 in1.97s/native3.1s.
Receipts: /tmp/gludd-v012-disk-native-green{311,312}.{log,xml}.
The selected native disk node takes0.55s/0.52s; no whole-file admission is added.

Independent manifest, Make-wiring and native-roster expectations fail first:
3FAIL/1.24s/native2.3s, /tmp/gludd-v012-disk-routing-red311.{log,xml}.
The disk exact node then appends family144 after every143 predecessor.
The canonical six-pin checker passes6/2.14s; original3children2/1/1,
90total/60quiet,21phases2850 and full-gate authority remain. Both target
contract entries pass with276 total. Actual watched examples terminate
disk0/6.4s and tmp-gludd-usage0/3.7s; their logs are
/tmp/gludd-v012-disk-contract-{disk,temp}.log. Later complete/static/collection
and source-review receipts remain separately recorded by their native owners.

### Complete receiving acceptance

All eight complete routing/observability owners pass478 on311 in134.26s/native
135.5s and312 in139.31s/native140.6s, fatal warnings and zero skips/errors.
Receipts: /tmp/gludd-v012-disk-routing-complete{311,312}.{log,xml}.
This includes the complete changed test owners, original publication/CI/contract
controls and all22 observability cases after the final fixture import fix.
Ruff passes all three changed Python scopes; strict mypy passes the complete
observability scope without suppressions. The existing field loop is preserved;
the new dataclass field import uses an explicit alias and native import ordering.

Make lint retains1157 unique targets/zero duplicates,9CI12local phases and the
actual help dry-run. Locked Markdown passes six files; native SESSION structure
and registration13 pass. The line guard first refuses TASKS at2501 lines; removal
of two synopsis blank lines preserves every word/criterion and returns PASS for
all10828 tracked files under the unchanged less-than2500 limit. No checker or
threshold is weakened. The complete eight operating/documentation owners then pass235 in83.24s/native
84.4s, /tmp/gludd-v012-disk-operating311.{log,xml}, including actual collection,
Ansible syntax and all original guardrails. Normal commit/collection and
independent exact-source review remain separate required receipts.

### Resource findings and safe continuation

The actual disk example measures98percent and5.8GiB free, versus the95percent
ceiling. The preceding98percent/5.7GiB reading and historical97percent/6.9GiB
remain evidence, not an attributed growth cause. The temporary view exposes
worktrees1.4G, pinned Playwright browsers853M and retained testfiles115M;
these sizes do not confer deletion authority. No resource is removed.

The existing JSON inventory on /Users/shawnwilson/tmp reaches its unchanged
1000-entry cap after3.316s and returns Make2/native3.4s with twenty partial
rows. Receipt: /tmp/gludd-v012-capacity-temp-inventory-a4f894c.log.
Those rows cannot rank the complete parent or justify repeating that scan with
larger bounds. Use a known allowlisted narrower root when needed, or the
existing fixed-scope temporary diagnostic for its stated human-display purpose.
Preserve the existing39-candidate/0-reclaimable24-hour namespace plan,
proof retention, browser lifecycle, worktree ownership and ZDD. Capacity
HumanTodo remains UNFILED after API refusal; no ID or host-space answer exists.

Actual capacity must clear95 before native release-proof-admission mode0.
The different60907 publication124/90.554s/trace_path:null still has no established
cause and no Git transport. A new display, source SHA or observer ID changes
neither prerequisite. Full canonical gate/global85-75 line AND branch coverage,
exact-SHA all-shard CI, deployment acceptance and guarded promotion remain
pending; all seven release tasks stay unchecked.

### Long-lived practitioner evidence

A firsthand2009 [Server Fault question](https://serverfault.com/questions/62411/how-can-i-sort-du-h-output-by-size)
shows numeric sorting placing508K ahead of64M. The enduring issue is preserving
unit meaning when ordering human-readable sizes. The primary
[GNU sort manual](https://www.gnu.org/software/coreutils/manual/html_node/sort-invocation.html)
defines human-numeric ordering and its use with one du invocation.
This supports reusing the existing native sort -h owner instead of writing a
size parser. It does not attribute this host's disk pressure. Native fixture
and actual-target runs establish the selected environment's behavior; no
cross-platform binary compatibility or package installation is inferred.

## Native capacity cleanup and direct owner (2026-10-21)

S98.4 on development0ab4ae6050728348e53abc19ac248669a57197bf keeps the
protected release evidence and all seven tasks open. The native disk-check
reports97percent against95 and766.5MB generated scratch against100MB.
The preceding39-namespace/0-reclaimable24h plan remains history; it does not
answer the separate canonical scratch/cache cleanup question.

### Preserve the failed plan and actual consumers

Existing run-watched retains plan20261021T225700Z-62649-ccb94ac1 under
.gate-logs/observed/v012-capacity-scratch-plan/,30quiet/60total, exit2/native8.6s.
Native preflight inspects395scratch candidates and reports nine dry-run eligible,
386skipped. These dry-run counts are no removal or capacity proof.
It reports active UV PIDs62648/62722; subsequent exact diagnostics find62648
missing and62722 present as the real five-core detect-secrets command under62707.

The installed agent watchdog owns that separate natural scan,
scan-9a19b82fb09544bdb607a43fb8e418e8, parent57626/child62697.
It starts2026-10-21T22:57:04.134653Z and naturally terminates inactive124/60.518s,
max-runtime-timeout. No scan is forced or cooldown changed. Its timeout has no
established scanner or original-publication cause.

The cleanup owner itself streams bounded phases. A UV-backed observer also
participates in its global UV-idle checks, so use the existing direct Make owner
with installed SYSTEM_PYTHON after actual consumers drain. Do not ignore UV
PIDs, remove the idle gate or create a second observer/cache. Direct dry-run
then reports would-prune shared UV cache rather than active UV consumers:

~~~console
make disk-cleanup-preflight DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0 DISK_CLEANUP_PREFLIGHT_DRY_RUN=1 DISK_CLEANUP_RECEIPT_GRACE_SECONDS=1800 SYSTEM_PYTHON=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11/bin/python3 GATE_TEST_PROFILE=release GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1 UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never
~~~

This direct plan exits Make2/no-progress at97percent and768.1MB; no source,
policy or resource is changed by the dry-run. Neither plan exit is green admission.

### Apply only the existing native policy

After readers/model owners/tests/observers quiesce, actual apply uses the same
native owner and1800-second receipt grace:

~~~console
make disk-cleanup-preflight DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0 DISK_CLEANUP_PREFLIGHT_DRY_RUN=0 DISK_CLEANUP_RECEIPT_GRACE_SECONDS=1800 SYSTEM_PYTHON=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11/bin/python3 GATE_TEST_PROFILE=release GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1 UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never
~~~

Pass1 reports16removed/401skipped/zeroerrors. It removes the three regenerable
cache directories in the cold-render peer,6314bytecode files/244991371bytes
there and16files/167311bytes in the public-review peer. The release-note peer
has zero eligible bytecode. Total bytecode is6330files/245158682bytes.
The same owner removes nine stale scratch entries and its exact shared
Playwright download root. UV prune finds no unused entries; native UV clean
reports11files/12.3KiB and completes the exact shared cache cleanup.
Installed test runtimes and protected refs/evidence/locks/checkpoints remain.

Native pass1 recheck changes97to96percent and768.1to711.7MB scratch. Pass2
removes0/skips403/zeroerrors and stops nonzero with no-progress at96/711.7;
the enclosing Make exit is2. Subsequent make disk measures96percent/8.1GiB free;
make ps shows only registered watchdogs57626/57964, no test/audit/scanner
processes. Partial measured reclamation supplies no green capacity verdict.

These direct-owner phase outputs are manual receiving evidence, not a generated
current observation or release receipt. Preserve both failed plans and the
partial apply result. No broader cleanup, shortened grace, protected-artifact
removal or unchanged cleanup retry follows. Actual hard capacity must clear95;
original60907 publication, full canonical gate/global85aggregate/75each-file line
AND branch, exact-SHA all-shard hosted CI/deployment, ZDD/retention and guarded
promotion remain separate and unproved.

## Scratch permission repair through owned directory descriptors (2026-10-21)

### Measured defect and confined reproduction

After the capacity apply and documentation replay, native worktree-state at
development 0ab4ae6050728348e53abc19ac248669a57197bf reports the seven expected
prose edits plus executable-bit changes to scripts/stream_command.py and
scripts/reviewed_head_inputs.py. Scoped native git-diff-full shows only
100644-to-100755, with no byte changes. The specific deleted entry responsible
is unproved. Preserve /tmp/gludd-v012-cleanup-mode-observation.md as manual
receiving evidence. The native Git owner restores only those measured modes;
subsequent worktree-state no longer includes either script.

The existing scratch helper walks directory and file names, then chmods every
path. Not traversing directory symlinks in os.walk does not prevent chmod from
following a listed link. Confined tests invoke the actual public cleaner with
nested file-link and directory-link variants and retain external target bytes,
modes and contents inside the owned fixture. Both fail against the old code:
2 failures in 0.68s/native 1.6s, v012-scratch-symlink-red311.
A separate readable-directory/unreadable-refusal boundary gives 3 failures and
1 pass in 0.65s/native 1.5s, v012-scratch-boundary-red311.
The complete old 38 cases plus ten additive controls then give 8 failures and
40 passes in 1.42s/native 2.3s, v012-scratch-complete-red311.
Logs/XML remain under the corresponding /tmp/gludd-v012-* names.

### Reuse the standard library and preserve refusal

Root fully reads /tmp/gludd-v012-scratch-symlink-repair-design.md before changing
production. The single existing _remove_tree owner now requires the public
symlink-resistant rmtree capability plus native fwalk/fchmod before mutation.
It uses os.fwalk with follow_symlinks=False and repairs permissions only through
the yielded directory descriptors with fchmod. Regular-file and symlink paths
are never chmodded. Both existing callers retain their original ownership,
age, PID/socket checks, leases, fresh identity validation and result assertions.

Native fwalk validates lstat/open/fstat identity before yielding a descriptor.
Controlled swaps before native open and before descriptor chmod leave the
replacement external bytes and modes unchanged. Public shutil.rmtree retains
its own descriptor-based symlink defenses. No private helper, custom walker,
path-chmod fallback, subprocess or dependency is added.

The registered CPython implementations return a generator; the typing stub
exposes only Iterator. A fully parameterized Generator cast supplies that
verified close interface to contextlib.closing, retaining descriptor cleanup
on normal and exceptional exits without Any or suppression comments.
An unsupported native implementation becomes OSError and the existing public
removal-failed refusal. Traversal errors are raised rather than silently skipped.

Readable 0500 directories still clean. A genuinely unreadable 000 directory
fails safely before an unverified descriptor can be used. Its test measures
actual access first: a privileged process that can traverse it may clean it;
a denied traversal must retain the tree and original data as removal-failed.
No skip or fabricated platform-independent denial replaces either assertion.
The original permission-failure control keeps its assertion and injects at the
actual fchmod seam, matching the repaired adapter.

### Complete source acceptance and early routing

The unchanged coverage_check_disk profile measures the same two source files
with branch data and both 85 aggregate / 75 each-file floors. Four complete
owners run: test_clean_ci_shard_scratch.py, test_check_disk_usage.py,
test_automatic_disk_cleanup.py and test_automatic_disk_cleanup_resources.py.

Python 3.11 reports 193 passes in 5.64s/native 7.7s, run
20261021T231324Z-69341-c4817721, audit .gate-logs/coverage-20261021T231332Z.json.
Python 3.12 reports the same 193 passes in 6.83s/native 9.4s, run
20261021T231429Z-70087-dc083aba, audit .gate-logs/coverage-20261021T231438Z.json.
Both report 86.7% aggregate line / 89.0% branch coverage. Check-disk measures
86.1% line / 87.1% branch; scratch cleanup measures 87.3% / 90.5%.
Every measured file passes 75% for both metrics; this is not global coverage.

The actual existing-owner command for 3.11 is:

~~~console
make coverage-files COVERAGE_TESTFILES="tests/unit/test_clean_ci_shard_scratch.py tests/unit/test_check_disk_usage.py tests/unit/test_automatic_disk_cleanup.py tests/unit/test_automatic_disk_cleanup_resources.py" COVERAGE_CONFIG=config/coverage_check_disk.ini COVERAGE_REPORT=.gate-logs/coverage-v012-scratch-callers-final311.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=5 OBSERVED_QUIET_SECS=60 OBSERVED_MAX_SECS=180 OBSERVED_RETAIN_RUNS=20 GATE_TEST_PROFILE=release GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1 UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never
~~~

The 3.12 invocation uses the installed sibling python-3.12 for both environment
paths, UV_PYTHON=3.12.15 and the separate final312 report path. No environment
acquisition, warning suppression or reduced selector replaces these runs.
Earlier 82-case source-only receipts remain history, separately scoped.

Family 149, scratch-nested-symlink-permissions, appends after all 148 predecessors.
Its real file-link witness joins the existing first child exactly once:
tests/unit/test_clean_ci_shard_scratch.py::test_stale_scratch_nested_symlink_preserves_external_target_modes[file].
The whole owner is not widened into admission. Three children, 2/1/1 workers,
90-total/60-quiet, 21 phases/2850 seconds and required full gate stay unchanged.
The native routing witness first fails in 0.75s/native 1.7s, then three focused
pins pass in 1.73s/native 2.6s. Preserve v012-scratch-route-red311 and
v012-scratch-route-green311.

The first full four-owner routing replay retains 2 failures/295 passes in
54.24s/native 55.2s at v012-scratch-routing-final311: one independent explicit
selector pin still lacks the new node, and its native-check consumer propagates
that failure. Only expected selector data is updated, preserving all prior
selectors, order, assertions and original worker/budget constraints.
The canonical existing check then reports 149 families and six passes/1.83s.
The complete four routing/workflow owners pass 297 on each runtime: Python3.11
42.35s/native43.3s and Python3.12 49.30s/native50.5s, fatal warnings and zero
skips/errors. Preserve /tmp/gludd-v012-scratch-routing-repaired{311,312}.{log,xml}
and their native immutable observed receipts separately from the failed run.

The five complete documentation/handoff/ledger owners pass 98 in4.13s/native5.0s,
v012-scratch-handoff-docs311, fatal warnings and zero skips. The first line-limit
check retains2500 lines in the existing roster test; data-only list formatting
preserves every value/order/assertion and returns it below the strict limit.
No assertion, coverage floor or line-limit policy is weakened.

Ruff checks four Python scopes; production docstrings and four separate strict
typing scopes pass. The existing target contract reports 276, Make validates
1157 targets/zero duplicates with 9 CI/12 local phases, and the full canonical
resource inventory passes unchanged at 264. A wrong inventory filename attempt
is retained as a caller error; the actual documented input is
config/resource_ownership_inventory.json with empty RESOURCE_OWNERSHIP_PATHS.

### Practitioner evidence and release limits

The [Python 3.11 fwalk reference](https://docs.python.org/3.11/library/os.html#os.fwalk)
documents directory descriptors and their lifetime. The registered
[3.11 implementation](https://github.com/python/cpython/blob/v3.11.14/Lib/os.py)
and [3.12 implementation](https://github.com/python/cpython/blob/v3.12.15/Lib/os.py)
retain descriptor identity checks before yield. The public
[rmtree reference](https://docs.python.org/3.11/library/shutil.html#shutil.rmtree)
describes its symlink-resistant capability. Those APIs support the repair;
the confined runtime tests establish this checkout behavior.

A firsthand [2022 cleanup report](https://bugs.python.org/issue46977) reproduces
permission repair following a link out of a read-only directory. Its
[migrated discussion](https://github.com/python/cpython/issues/91133) records
fixes and 2024 closure. It is a relevant historical pattern, not attribution
of the deleted Gludd entry or a claim that installed Python is vulnerable.
No private TemporaryDirectory callback or unsafe path fallback is copied.

Actual capacity remains 96% against 95, with the retained partial apply result.
This source repair supplies no new disposable owner or changed capacity input;
do not repeat that failed cleanup for a badge. Preserve original publication
60907 and all required canonical gate/global coverage, exact-SHA hosted and
deployment proofs, ZDD, retention and guarded promotion. No task is ticked.


### Exact source receiving boundary

Local development41013b302decef572cb119bea5eeffef33c2a6e6, parent0ab4ae6,
has independent APPROVE SOURCE ONLY at
/tmp/gludd-v012-scratch-permissions-41013b3-source-review.md, fully read by Root.
All14 changed paths were exported/read. The reviewer independently read both193
terminal source/caller receipts, both native86.7line/89.0branch audits and the
fresh normal collection:128537selected/128561collected,24deselected,zero errors
49.16s/native54.324s; raw collection also retains4skips. Preserve
.gate-logs/observed/test-count/20261021T232605Z-79835-b3d0bee0.{log,json}.
All six checkpoints remain, zero new clones and one existing22-line clone.
The0ab CHANGES REQUIRED remains history; its stale-PID sentence is corrected in410.

This source decision requires no code repair. Existing native HEAD/worktree-state
owns later documentation receiving boundaries; do not repeat finished source work
because a historical handoff predates this receipt. Capacity96>95 and the original
publication60907 failure still block final admission/proof. All seven S98 tasks
stay unchecked; no push, full gate, exact-SHA hosted/deployment, promotion or
release is supplied by this source review or collection.


## Native UV prune status and model contract (2026-10-22 UTC)

S98.4 found a separate defect in the existing uv-cache-prune facade. Its
background wait and timeout branches ended in ||true. A real child exit17
became Make0, and kill-0 polling misclassified completed children as running.
A SIGTERM-ignoring fixture plus descendant exceeded the fixture's12s bound.
The fixture killed its owned Make session; this was not a returned124.
No real cache was pruned. This defect does not establish the cause of
publication60907 or the natural watchdog timeouts.

### Existing owner and precise bindings

The facade now invokes scripts/stream_command.py directly with the selected
installed PYTHON, then the exact native UV argv cache prune --ci. It reuses
native receipts, merged visible output, heartbeat5s, quiet0, automatic identities,
retention20 and a positive total runtime, default300s. Native termination grace
drains the owned process group after child work stops. Native17 remains failed17
and timeout remains timed_out124 in producer-owned JSON; enclosing Make returns
nonzero, normally2 with Error17/Error124. Do not confuse those two exit layers.
The observer implementation and retention rules are unchanged.

Default FORCE=0 retains the native process guard. An unavailable ps snapshot
or failed classification refuses before pruning; unknown process state cannot
become idle. FORCE=1 overrides only that existing guard. It never appends UV's
native --force and never grants broader ownership or cache-removal authority.
The complete busy control starts a real fixture executable named uv with argv
run, verifies skip with no prune receipt, then verifies forced child17 failure
with exact argv and no added --force. All fixture children are drained.

Keep direct installed Python here. The
[UV cache documentation](https://docs.astral.sh/uv/concepts/cache/#clearing-the-cache)
says cleanup waits for active UV users; the
[native prune reference](https://docs.astral.sh/uv/reference/cli/#uv-cache-prune)
says its own --force skips the lock. Wrapping the observer in UV run adds an
avoidable UV lifetime around pruning. This is a design inference from the
documented lock, not an observed explanation for this host's failures.
Do not add a shell timer, external timeout utility, runner or status cache.

Four target-specific public data fields are captured literally before startup
exports: UV_CACHE_PRUNE_TIMEOUT, UV_CACHE_PRUNE_FORCE,
UV_CACHE_PRUNE_VALIDATE_ONLY and UV_CACHE_PRUNE_OBSERVED_ROOT. Their dedicated
raw exports work for direct goals and native dependency callers. A dedicated
prune root avoids changing the generic OBSERVED_ROOT behavior of other owners.
Timeout must be a positive decimal integer;0 would make the observer unbounded.
Mode and force must be0|1, root nonempty. Invalid policy refuses before a child.
Trusted PYTHON/UV command selectors retain their existing meaning.

Mode1 prints an offline plan before the recipe's ps, cache command or observer.
It creates no cache or observer directory and supplies no idle, cleanup, capacity
or release proof. Normal Make startup still occurs. The executable contract and
help declare all four inputs; the actual safe behavior is:

~~~console
make uv-cache-prune UV_CACHE_PRUNE_TIMEOUT=300 UV_CACHE_PRUNE_FORCE=0 UV_CACHE_PRUNE_VALIDATE_ONLY=1 UV_CACHE_PRUNE_OBSERVED_ROOT=/tmp/gludd-uv-prune-contract/observed
~~~

The measured result is Make0 and one UV_CACHE_PRUNE_PLAN line naming
300/0/heartbeat5/quiet0/retain20 and that exact root. Use this plan for contract
validation, never a real-cache apply example. The contract checker requires the
behavior to start with its named public target; substituting test-files is not
supported. Real lifecycle acceptance uses the existing confined test-files owner:

~~~console
make test-files TESTFILES=tests/unit/test_make_uv_cache_sandbox.py PYTEST_ARGS='-q -W error -n 0 -o verbosity_test_cases=-2' GATE_TEST_PROFILE=release GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1 UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never
~~~

Use the authorized host Make boundary for native process inspection. A sandbox
ps denial remains a failed receipt, never a mocked empty inventory or a reason
to weaken descendant cleanup. Fixtures select their already installed interpreter,
private cache/observer roots and actual executable argv. Success0, failure17,
timeout124, busy skip/force, offline direct/dependency plans, invalid fields and
24 Make/shell/backtick literal controls retain every original assertion. A cache
sentinel's bytes and0600 mode remain intact. A SIGTERM-ignoring parent reaps its
terminated child before the native observer kills the parent; both are checked
absent. The plan poisons recipe UV/PYTHON selectors, proving neither is launched.

### Failing-first and current source receipts

The immutable starting point is development
bd3d2a8235f5e19e9baeff23414cf7275fa44cc0. Root fully reads the concrete independent
design at /tmp/gludd-v012-uv-prune-design.md and receives the implementation inline.
This choice uses its direct-Python/native-observer/literal-root recommendation;
the existing outer run-watched owns milestone progress, while this direct cleanup
target's observations remain cleanup evidence only.

- Original native RED:3FAIL/3PASS22.93s/native24.0s,
  /tmp/gludd-v012-uv-prune-red311.{log,xml}. Child17 was swallowed, child0 lacked
  a receipt and the timeout fixture required its own12s session teardown.
  Native ps then showed only the two controllers, no fixture survivors.
- Initial observer repair:6PASS3.15s/native4.1s,
  /tmp/gludd-v012-uv-prune-green311.{log,xml}.
- Additional public-plan/literal RED:6FAIL/15PASS/6deselected1.73s/native2.9s,
  /tmp/gludd-v012-uv-prune-policy-red311.{log,xml}. It retained ignored/empty root,
  dependency binding, startup injection and a literal backslash-n output defect.
  Dedicated unconditional prune-only capture and the real printf newline repair
  those actual bindings; no assertion was removed.
- Intermediate28PASS4.75s/native5.7s,
  /tmp/gludd-v012-uv-prune-policy-green311.{log,xml}. Extending literal tests to
  dependency callers brings the complete original owner to40cases.
- Mixed canonical/source sandbox attempt:2FAIL/45PASS5.70s/native6.6s,
  /tmp/gludd-v012-uv-prune-canonical311.{log,xml}. Preserve ps Operation not
  permitted and a descendant-presence failure. No empty-inventory substitution.
- Exact same complete source owner at the authorized host boundary:
  40PASS4.96s/native5.8s, /tmp/gludd-v012-uv-prune-host311.{log,xml}.
  Ruff and all three separate strict scopes pass. A transport-only JavaScript
  syntax error performed no edit; native Make edits applied only after correction.
- Routing RED:1FAIL.78s/native1.5s,
  /tmp/gludd-v012-uv-prune-route-red311.{log,xml}, actual first-child count0
  versus required1. Family152 appends one exact child17 witness after all151
  predecessors. It does not select the whole file. The unchanged six canonical
  pins pass1.58s and the promotion owner reports152families/21phases/2850s.
  Original three children2/1/1 and90total/60quiet remain unchanged.
- Native check-make-target-contract passes277 entries; actual mode1 above and
  lint-make pass1157targets/zero duplicates and existing gate parity. Complete
  source/consumer and routing receipts follow below after terminal verification.


The complete nine-owner compatibility RED remains
/tmp/gludd-v012-uv-prune-receiving311.{log,xml}:2FAIL/249PASS34.37s/native35.4s.
The old release-proof fixture now reads its generated unsplit Makefile through
the already captured native composer, retaining the identical exactly-one real
production binding assertion and every missing/duplicate refusal. This conforms
to the existing logical-Makefile rule. Three separating blank lines make existing
trace target-specific assignments conform to the unchanged syntax test; all four
bindings retain their bytes. The dated136-family timing synopsis now points to
the native manifest for the current roster instead of presenting history as current.

The new contract entry initially makes the file2509lines, refused by the existing
strict<2500 checker. Compacting just the prune and prune-status JSON entries
preserves every field and example and passes the unchanged guard. No allowance
or checker is relaxed. Complete12-owner receiving controls add both trace owners
and the production-binding fixture to the original nine; native terminal receipts
are required before this source is committed.

The first normal guarded commit stops before collection when TASKS reaches2501
lines. Removing only the two blank separators introduced with the new startup
pointer preserves all ledger text and restores strict<2500; neither a task nor
historical evidence is dropped. The source remains uncommitted until fresh normal
guards and collection actually finish.

### Complete source and consumer acceptance

Both complete12-file cohorts pass392cases:311 in48.19s/native49.4s and312 in
50.94s/native52.1s. Preserve
/tmp/gludd-v012-uv-prune-complete311.{log,xml} and
/tmp/gludd-v012-uv-prune-complete312.{log,xml}. Native terminal JSON under
.gate-logs/observed/uv-prune-complete311 and uv-prune-complete312 retains exact
commands, roots, original flags and statuses. These are focused180s cohort
envelopes, not a change or pass of the original90/60 publication owner.

The12 complete owners are test_make_uv_cache_sandbox,
test_observability_guardrails, test_automatic_disk_cleanup,
test_automatic_disk_cleanup_resources, test_make_target_contract,
test_makefile_layout, test_makefile_syntax, test_make_help_audit,
test_makefile_audit_deep, test_release_proof_admission_target,
test_trace_summary_make_inputs and test_run_watched_trace under tests/unit.
The last three retain native binding mutation, trace dependency contexts and
CMD positional arguments. No old assertion or worker/runtime bound is removed.

Five complete routing/workflow owners pass373each:311 in92.44s/native93.6s and
312 in83.38s/native84.6s. Preserve
/tmp/gludd-v012-uv-prune-routing311.{log,xml} and
/tmp/gludd-v012-uv-prune-routing312.{log,xml}. Native manifest/checker owns the
current152-family roster; older136/151 figures remain dated history. The first
child adds only the real17 witness; all predecessors, three children2/1/1,
90/60 phase,21phases/2850s and required full gate remain unchanged.

All124 complete handoff/contract/Markdown/branch-policy checks pass7.72s/native8.8s
in /tmp/gludd-v012-uv-prune-docs311.{log,xml}. All four separate strict scopes,
Ruff, seven-file Markdown, native277contracts, status-snapshot validation, task
registration of16changed paths and full264-resource ownership inventory pass.
Normal collection and exact-source independent review remain separate requirements.

Native system-load measurement is3.63 on8CPUs,0.45x/OK; only two testing owners
ran concurrently. Native disk remains96percent with8.0GiB available. No cache
mutation or cleanup authority is inferred from either measurement. Production
Python is unchanged, so no reduced coverage profile is introduced; global85/75
for both line and branch stays required at final proof.

### Practitioner context and retained release boundary

The firsthand [UV issue5731](https://github.com/astral-sh/uv/issues/5731), opened
2024-08-02, reports about40GB on self-hosted CI. Later operators describe
multi-project growth and a July2026 cache exceeding1TB. These reports support
bounded native lifecycle work; they do not identify this machine's pressure or
authorize shared-cache deletion. The upstream thread's evolving automatic-prune
proposal supplies no local policy or permission change.

No actual cache operation was performed for this repair. Last native disk remains
96percent/8.0GiB, above95; earlier cleanup remains711.7MB scratch/no-progress.
Capacity HumanTodo is still UNFILED after API refusal, with host input unanswered.
Original publication60907/124/90.554s/nulltrace/no transport remains unresolved.
New source, plans, focused tests or another SHA permit no unchanged retry.

Source acceptance and independent review remain separate from the complete
canonical gate/global85aggregate75each-file line AND branch floors, exact-SHA
all-shard CI, deployment acceptance, reviewed-head and promotion. No production
Python coverage scope was narrowed or substituted for those requirements.
ZDD, native locks, namespacing, retained failed evidence and cleanup/retention
guards remain. All seven S98 tasks stay unchecked.
