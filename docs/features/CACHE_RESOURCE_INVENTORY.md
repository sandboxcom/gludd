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
