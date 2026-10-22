# Make result guidance

S98.4, 2026-10-22 UTC. Agent-facing Make results should teach the model what to
do with the actual result. Current owners include check-make-target-contract,
check-gate-failure-promotions, git-merge-nc, development-merge-forward and
release-proof-admission and human-todo-module. Existing native owners, statuses and artifacts remain
authoritative; no second observer, runner, cache or proof schema is introduced.

## HumanTodo results require identity and reconciliation, 2026-10-22 UTC

### Separate check policy from native exit status

Exact843fe156cd3a2bf6449456bc8365c5740e70cb9c has independent APPROVE SOURCE ONLY
against374, fully read at /tmp/gludd-v012-human-todo-843-source-review.md.
Its normal guarded hooks collect129008selected/129032collected,24deselected/
zero errors47.56s/native52.927s; native receipt:
.gate-logs/observed/test-count/20261022T124815Z-1429-8cc1bca6.json.
Final four-owner documentation receipts pass54 per runtime,3117.29s/native9.008s
and3128.49s/native10.296s:
.gate-logs/observed/human-todo-docs{311,312}/v012-human-todo-docs{311,312}.json.

The review catches ambiguous SESSION wording "check mode0/native0.8s/id=null."
The intended0 is an exit status; literal HUMAN_TODO_VALIDATE_ONLY=0 applies.
Apply the exact source-only review's receiving patch:
HUMAN_TODO_VALIDATE_ONLY=1 selects check mode; exit0/native0.8s/id=null reports
its native result and no filed request. This documentation clarification changes
no source, Make policy, assertions, rosters, budgets or candidate proof state.
Retain accepted source suites; check affected documentation and normal collection
without repeating662 unchanged tests or adding a receipt-only commit.

The frozen review packet includes its exact17-path roster, thirteen historical
native evidence copies and two unchanged843 imports: the canonical HTTP fixture
and module client/result helper. Include those direct support boundaries when
preparing review exports; a changed-path list alone cannot prove restoration or
error-schema compatibility. They are frozen review evidence, never current
observations or another cache/runner. Root consumes the complete decision/patch
and all reader sessions are quiescent before the receiving mutation.

Latest native status retains exact843, gateFAIL/no running gate, seven open
milestone tasks,132 excluded backlog items and zero observed workers; model-agent
status is separate. Disk remains97percent/7.5GiB against95. Operator endpoint/
credential binding and persistent daemon/store ownership remain unverified.
No original60907/final-gate/API replay, startup, cleanup or promotion follows.

### Retained implementation evidence

This coupled S98.4 repair starts from reviewed local
37454900137c21e4d051b9c1fd65cc6c450ac514 on
release-candidate/v012-git-mutex-20261022. Development remains
82db11b57b254c1ca1e035975b5cc86253e2fed5; no integration or push follows.
Root fully consumes APPROVE SOURCE ONLY againstfe2 at
/tmp/gludd-v012-proof-admission-374-source-review.md. Normal374 hooks retain
128913selected/128937collected,24deselected,zero errors49.67s/native55.036s:
.gate-logs/observed/test-count/20261022T120527Z-77538-f06c3086.json.
The earlier630-per-runtime acceptance remains source-specific. Carry these
receipts into this substantive repair; do not create a receipt-only commit.

The actual HumanTodo module accepts HTTP200/201 with a missing, null, blank,
padded or nonstring ID, and accepts a resolution response for a different ID.
That is a false verification boundary even when transport succeeds. The existing
API router serializes the committed record's actual ID; PATCH returns the
requested record. Require a nonempty unpadded string, preserve its exact bytes
and require matching identity for done/dismissed. Do not invent a UUID grammar
or normalize a returned ID. Native transport/HTTP error messages and statuses,
valid response payloads, explicit client binding and check-mode shapes remain.

A malformed successful response fails with its original HTTP status and
write_outcome=unverified. This does not prove the daemon made no change.
Reconcile the real record before retrying; a blind POST could create a duplicate.
No returned ID, fake service, check-mode changed=true or prompt can verify filing.
The existing Make owner emits stderr AGENT_NEXT for refused inputs, temporary/
runtime failure, successful check-mode plan, returned apply response and module
failure. Preserve original stdout, native failure status, literal JSON/policy,
selected interpreter, one fork and owned temporary cleanup. Terminal guidance
follows the native child. A returned apply response still requires capturing the
real ID and verifying requested state; it supplies no milestone/gate authority.

The genuine module RED retains58FAIL/33PASS0.93s/native1.910s, with all nine
predecessor assertions intact. Make guidance retains9FAIL/20PASS2.90s/native4.038s.
Routing retains2FAIL/65deselected0.95s/native2.0s before promotion. Receipts:
.gate-logs/observed/human-todo-id-red311/v012-human-todo-id-red311.json,
.gate-logs/observed/human-todo-guidance-red311/v012-human-todo-guidance-red311.json
and .gate-logs/observed/human-todo-routes-red311/
v012-human-todo-routes-red311.json. Logs/XML use the corresponding
/tmp/gludd-v012-human-todo-{id,guidance,routes}-red311 names.
The82 new module cases reuse the canonical physical module and existing HTTP
adapter fixture. A scoped PATCH recorder restores the shared fake afterwards;
the complete legacy HTTP owner runs with it. Nine additive real-Make controls
invoke all original literal-policy/argv/status/temporary-cleanup assertions.
Two further native controls retain temporary-directory and interpreter failures,
original stderr/status9, no request and one failure prompt. The initial native
registration guard refuses four unregistered HumanTodo paths; explicit S98.4
path registration repairs the real input without adding or ticking a task.
The new throughput cross-reference exceeds the strict ceiling at2501 then2500;
compact that prose, preserving all older content/history and the strict limit.

The complete106-case module/legacy cohort passes1.63s/native3.1s with fatal
warnings and no skips/errors. Whole physical module coverage is97.6line/95.5branch,
with no exclusions and unchanged85aggregate/75each-file BOTH:
.gate-logs/observed/coverage-files/20261022T123757Z-89274-df0d3a26.json,
.gate-logs/v012-human-todo-module-coverage311.json and
.gate-logs/coverage-20261022T123800Z.json. This is generic source coverage,
not E2E execution or global release coverage. The only missed line/arc is the
main guard. Strict scopes for the module and both changed test owners pass;
Ruff passes without suppressions. The actual registered check-mode example
returns0, changed=true/id=null and planned guidance, with no HTTP filing.

Exactly two new cheap nodes append as families165/166 after all164 predecessors.
Both literal runtime rosters and the independent ordered manifest pin retain the
same node order. The routing assertions preserve first-child counts1/0/0, all
eight existing pins, three children2/1/1,90total/60quiet,21phases/2850 and full gate.
Literal-only adjacent-string compaction keeps the existing promotion test below
2500 physical lines without dropping a byte of its expected selection/assertions.
All eight native pins pass1.77s/terminal0;278 target contracts and the documented
actual example pass. The complete16-owner receiving union passes all662 cases
per runtime, fatal warnings and zero skips/errors. Python311 takes104.84s/
native106.129s; Python312 takes110.10s/native111.470s. Exact receipts:
.gate-logs/observed/human-todo-receiving{311,312}/
v012-human-todo-receiving{311,312}.json; logs/XML are
/tmp/gludd-v012-human-todo-receiving{311,312}.{log,xml}. Both native commands bind
unchanged tested sourceea4bdfc207be5ab9ce89b83a1c30685843bea89a0f454e0515c4735c6e954673.
The native receipt retains every deduplicated owner and installed runtime tuple.
These diagnostic240total/180quiet limits never replace original90/60 proof.
Final documentation/registration checks and normal guarded collection retain
their own native results; do not add a receipt-only commit or replay accepted
source tests merely to restate that collection. No hosted speed claim follows.

Capacity remains the measured97percent/7.6GiB against95, not cleared by a new
candidate. Preserve original60907 and the refused/unfiled capacity request.
The updated request configuration is
/tmp/gludd-v012-capacity-human-todo-request-374.json. Its bounded check-mode
receipt .gate-logs/observed/capacity-request-input374/
v012-capacity-request-input374.json returns0/native0.8s/id=null: no request filed.
An unanswered operator question requests the existing endpoint and credential
binding name only, never its value. Do not repeat the same refused API request.

Root fully consumes the independent existing-core-daemon proposal and stdio
patch in /tmp/gludd-v012-core-daemon-proposal/. Decision: HOLD startup/apply.
The public foreground CLI already owns Popen wait and signal forwarding; reuse
that lifecycle if a thin Make entrypoint is later needed. Its --project does not
isolate the store; an optional numeric PID file is not ownership/readiness.
Source child stdio is suppressed, and external binding prints a PSK. No proposed
stdio patch or daemon target is integrated here. Actual persistent config,
database/schema/runtime ownership and explicit safe credential transport are
unverified. GLUDD_DB_PATH cannot override configured database host/url; do not
substitute temporary SQLite or a fake API to manufacture an ID. The shared client
intentionally never scavenges GLUDD_AUTH_PSK. Raw psk in HUMAN_TODO_ARGS_JSON
would enter Ansible --args despite no_log; keep it out of argv/logs/receipts.
A verified native secret binding needs its own confined controls before apply.
No new supervisor, observer, cache, outbox, daemon or cleanup is introduced.

The existing [firsthand Make report](https://stackoverflow.com/questions/10559064/terminate-makefile-command-when-subcommand-throws)
retained in the throughput feature motivates explicit local failure handling.
It does not establish this API defect's cause or authorize a retry. Native
receipts establish the repair; original full-gate, exact-SHA hosted/deployment/
reviewed-head/promotion proofs and all seven milestone checkboxes stay pending.

## Release admission teaches bounded next actions, 2026-10-22 UTC

The next coupled S98.4 change starts at reviewed local
fe2d6a53611a6e1fb38adf96d7fb1392a1069a5d on the same release-candidate branch;
development remains82db11b57b254c1ca1e035975b5cc86253e2fed5. Root fully reads
independent APPROVE SOURCE ONLY against65cb at
/tmp/gludd-v012-merge-forward-fe2-source-review.md. That review accepts the
twelve frozen integration-guidance paths, six native receipts and retained
predecessor assertions. It grants no APPLY=1, cleanup, final gate or release
authority. Normal fe2 hooks retain128897selected/128921collected,24deselected,
zero collection errors48.47s/native53.899s at
.gate-logs/observed/test-count/20261022T114053Z-60590-67fe245a.json.
The first write-tree128/index.lock failure above stays unexplained; serial
guarded retry follows actual ENOENT, never lock deletion or a cause assertion.

The existing release-proof-admission owner now emits one short stderr result
guidance line for planned, refused, blocked or passed outcomes. All original
stdout markers, reasons, native status propagation, phase ordering and policies
remain. The plan explicitly leaves live capacity, configuration, ownership,
source contracts and runtime unexecuted. Input refusals identify mode/task data;
a blocked child names its actual phase and native repair owner, retains failed
evidence and requires a changed input before retrying. Native focus refusal
states that the runtime child has not started. Terminal guidance follows every
successful child and still requires one proof owner, a frozen exact candidate,
independent source review, combined acceptance, canonical gate and exact-SHA
hosted/deployment/reviewed-head proof. No prompt supplies release authority.

Capacity guidance uses existing disk/lifecycle and HumanTodo owners only.
Keep protected resources when no cleanup is eligible. A check-mode
null, request configuration or refused API request is not a filed HumanTodo;
a refused service needs a changed prerequisite before another request. The
same-input original60907/final-gate replay stays blocked. The separate read-only
native cache inventory examines seven children with zero errors in0.161s;
largest pre-commit is77049856 allocated bytes. Its native receipt is
.gate-logs/observed/user-cache-inventory/v012-user-cache-inventory-fe2.json,
terminal0/native0.3s. These measurements cannot clear GiB-scale capacity or
authorize managed-cache removal; no new lifecycle, scanner or daemon follows.

Sixteen additive behavioral controls preserve all42 predecessor cases. They
retain16FAIL/42deselected4.09s/native5.2s before the emitter exists:
.gate-logs/observed/proof-admission-guidance-red311/
v012-proof-admission-guidance-red311.json and
/tmp/gludd-v012-proof-admission-guidance-red311.{log,xml}.
Private real-Make cases cover plan, both literal input refusals, all nine
failed owners, two real focus refusals and both ordinary/native-focus success.
They assert exact original markers/statuses, stderr-only prompts, native child
order and absence of gate/attestation writes. The complete test owner already
runs in the first existing admission child; no new family, pin, phase or
whole-file widening is needed. All164 families/eight pins, three2/1/1 children,
90total/60quiet,21phases/2850 and85/75 BOTH remain unchanged.

One cheap invocation attempts to combine lint-files with two other goals; its
existing strict goal contract refuses before execution. The corrected separate
lint-files invocation and separate strict scope both pass. Do not combine
file-scope owners with extra goals to save calls. No assertions, scope checks,
warnings or guardrails are weakened. Markdown catches a heading-level gap;
the strict line owner also refuses BUGS2515 and TASKS2509 before replay. Only
new tracking notes are compacted into existing paragraph prefixes; all old
history and full decisions in this feature remain. A literal transport first
fails to match the Unicode task heading before mutation; UTF-8 encoding fixes
that exact input. Complete receiving and normal per-commit collection remain
distinct from source review and final release proof.

The complete 17-owner receiving cohort passes all 630 cases per runtime, with
fatal warnings and zero skips/errors. Python 3.11 takes115.07s/native116.282s;
Python 3.12 takes117.69s/native118.985s. Native
receipts are .gate-logs/observed/proof-admission-guidance-receiving{311,312}/
v012-proof-admission-guidance-receiving{311,312}.json; each
progress_identity.command retains the complete deduplicated paths, argv and
installed interpreter. Logs/XML are
/tmp/gludd-v012-proof-admission-guidance-receiving{311,312}.{log,xml}.
Both native receipts bind unchanged tested source
00e2824364e37c92206078c4a0c0dda861f3edbc1aef76ad4b6815c5cfb4397c.
The diagnostic240total/180quiet limits never become original runtime90/60 proof.

All eight existing pins pass1.78s/terminal0;278 contracts, the actual documented
mode1 plan(exit0),1157 targets/zero duplicates/parity, six Markdown files, eight-path
registration, native SESSION mode1, strict scope/Ruff and line ceiling pass.
This production delta changes only Make; no Python owner or coverage profile
changes. Historical6bc95.2line/90.2branch remains source-specific two-checker
coverage, never this candidate's global audit. Final native make disk measures
97percent/7.6GiB against95; make ps shows only the two existing Python controllers,
which is not a Git/model-agent idleness proof. No new daemon or cleanup runs.
Normal guarded collection remains mandatory. No original60907/full-gate,
exact-SHA CI/deployment, integration, release promotion or milestone tick follows.

## Development integration results, 2026-10-22 UTC

The next coupled S98.4 delta starts at local65cb7fb481f95fc3ef18a9808927140ed2ebd128;
development stays82db11b57b254c1ca1e035975b5cc86253e2fed5. The native watched plan
v012-development-integration-plan65cb returns0 and resolves SOURCE to exact65cb,
but executes no merge, branch/clean check, collection or gate. That successful
plan therefore cannot clear capacity96percent/7.9GiB against95 or authorize an
unchanged failed publication replay. Actual APPLY=1 retains its native collection,
secrets, lint, merged-tree gate-refresh, pre-commit and gate-freshness sequence.

The existing development-merge-forward owner now prints planned, refused, failed,
unchanged or passed guidance on stderr. Original reasons/stdout and explicit
refusal2/failure1/success0 stay intact; enclosing Make retains its native failure
meaning. A plan names SOURCE/mode validation after APPLY validation and the
unverified branch/clean/capacity/test inputs. It directs worktree-state and the
documented release-proof-admission mode0 only after repairing failed prerequisites.
An unchanged-ancestry result says no source applied/no new gate. Failed guidance
asks the model to wait for native transaction cleanup and inspect state; it never
claims cleanup succeeded. Applied guidance follows actual git commit and still
requires exact-SHA CI/deployment/reviewed-head evidence. PHONY prevents a namesake
file from suppressing this administrative owner.

Fourteen additive real-Make/Git controls retain all33 predecessor cases/assertions:
four clean/staged plans across content/ancestry-only on a feature branch, seven
original refusal reasons/statuses with exact state, an already-ancestor no-op,
a namesake file and a real external-driver conflict. The first13 fail in4.75s/
native6.0s; the separate native conflict fails0.99s/native1.9s before its guidance.
No configured external driver is installed in Main. The private conflict verifies
its real rollback state without promising arbitrary hooks/concurrent-writer safety.
This change preserves the existing merge options, transaction trap and gates;
it is not a new certification of those other boundaries.

Two routing controls fail0.95s/native1.8s before additive families163/164. Both
new exact nodes run once in the existing first child after162 predecessors;
independent counts, ordered IDs and both literal rosters retain every old entry.
Eight cheap pins, three2/1/1 children,90total/60quiet,21phases/2850 and required
full gate stay intact. No new phase, script, runner, daemon or proof cache exists.

Cheap feedback catches one127-character line and a2502-line fixture before broad
acceptance. Wrapping the new expression and compacting unchanged literal data
preserve all assertions and the strict less-than2500 ceiling. Native exact-once
edit guards reject ambiguous matches before mutation. Native diff inspection
also catches a literal JavaScript replacement collapsing Make dollar escaping;
the exact native byte replacement restores the original SOURCE refspec before
execution. Literal transports must encode the intended bytes: JavaScript string
replacement interprets double dollars unless its replacement is returned literally.

The complete18-owner receiving union passes747 per runtime with fatal warnings,
zero skips/errors:311131.59s/native133.094s and312124.25s/native125.917s. Native
receipts are .gate-logs/observed/merge-forward-receiving{311,312}/
v012-merge-forward-receiving{311,312}.json; each progress_identity.command retains
the entire deduplicated test-path roster, argv and installed runtime. Logs/XML
are /tmp/gludd-v012-merge-forward-receiving{311,312}.{log,xml}. Both native
receipts bind identical tested source99f8c2fbe3ea051eae32b9a1db98fccf78dd31ec35e2f388b2ebc66810721958.
The240total/180quiet diagnostic envelope is not the original runtime90/60 proof.

All three separate strict scopes/Ruff,278 target contracts,1157 targets/zero
duplicates/parity, six Markdown files,12-path registration, SESSION structure
and strict line ceiling pass. Eight current promotion pins pass1.83s/terminal0;
the actual documented SOURCE=HEAD/MODE=content/APPLY=0 behavior returns0 with
explicit non-authority guidance. Source/test/manifest inputs remain stable.
The subsequent documentation additions record only these received measurements.
The first normal ship-commit-files attempt returns Make2 before collection:
pre-commit returns3 because native git write-tree returns128 with index.lock
already present. The existing show-lines owner refuses the external pre-commit
cache-log path; no alternate external read is attempted. The actual console
failure is retained here rather than fabricated as a generated observation.
Exact make list-files DIR=.git/index.lock later returns native find1/Make2 with
No such file or directory. Native worktree-state still names65cb and all12 owned
paths staged, no unstaged/untracked changes. This observed absence permits
normal guarded re-evaluation; it proves no original cause or stale/dead owner.
No lock deletion, abort, restoration, checkpoint removal or bypass occurs.
The next guarded invocation uses make -j1 to serialize its Make jobs; this
scheduling choice is not a demonstrated cause repair. Do not repeat it if the
same failure recurs without a newly observed prerequisite change.

Normal guarded collection remains mandatory and its native hook receipt owns
the next commit's result; no recursive receipt-only commit or unchanged
acceptance replay follows. Normal65cb hooks retain128881selected/128905collected,
24deselected0errors54.53s/native59.849s at
.gate-logs/observed/test-count/20261022T110241Z-40296-fc8e9206.json.

Historical6bc source approval/840-case acceptance/95.2line90.2branch canonical
checker coverage retain their actual source; no Python production owner changes
here and those figures never become this delta's final/global coverage proof.
New exact-source review and final release proof remain separate. Latest native
disk is97percent/7.7GiB against95; only the two existing controllers remain after
the terminal test owners. No new daemon or cleanup is started. Original60907,
complete canonical/global coverage, exact-SHA hosted/deployment/reviewed-head/
promotion and all seven release tasks remain pending.

## Result-to-action contract

Keep the original summary/reasons on their established stream. Follow with a
short AGENT_NEXT line that names the relevant input, next Make action and missing
proof. A refusal needs a prerequisite repair; it never authorizes an unchanged
expensive retry. A pass names only what that owner measured.

Contract PASS directs the changed target's registered behavioral example, every
documented variable explicit and any expected refusal/status preserved. Contract
FAIL names target/help/variable/example repair; ERROR names input/parse repair.
Both failures retain script1; success retains0. Missing or unsafe Makefile
composition now enters the existing ERROR branch through MakefileLayoutError,
retaining its original native cause and failure1 instead of an unowned traceback.

Promotion script PASS says STRUCTURAL ONLY. Its native summary can precede eight
child tests (six predecessors plus two Make consumers), so the prompt requires all eight pins and terminal Make
exit0 before a broader cohort. The Make target emits outcome=passed only after
that child returns0. Manifest/input/child failure emits its failed phase and
repair action, preserves native reasons/status and prevents terminal passed.
All six predecessor pins, their exact argv, worker count and fatal-warning flags remain; two consumer pins add early failure feedback.

Promotion manifest/validation errors retain script1. Native argparse usage and
argument errors retain2; help retains0 and makes no result claim. Guidance does
not suppress errors or execute behavioral examples. The enclosing Make retains
its usual nonzero behavior. Git merge guidance is specified with its actual
state/abort boundaries in [the owning feature](GIT_MERGE_NO_COMMIT.md).

Prompts are advisory context for models. Source review, focused acceptance,
normal collection, a structural pass and a terminal target success supply none
of the missing canonical full gate, global coverage, exact-SHA hosted/deployment
or guarded release promotion proof.

This is a control-plane ZDD change: no serving process is restarted. An active
operation retains its loaded code; later invocations use the updated owner.
Rollback follows the existing reviewed branch flow while retaining failed
receipts, owned checkpoints and cleanup/retention/promotion gates.

## Permanent controls and source coverage

The confined two-script proposal retains25FAIL/2PASS2.25s/native3.007s before
27PASS1.75s/native2.468s. Root fully read its decision, literal production patch,
postimages and terminal receipt under /tmp/gludd-v012-make-guidance-proposal.

Canonical receiving tests retain27 cases and every original assertion:
25FAIL/2PASS2.98s/native3.9s precedes27PASS2.96s/native4.0s at
.gate-logs/observed/checker-guidance-{red,green}311.
The two composition failures expose the real uncaught helper error. Native
disposable CLI execution and the canonical in-process function must have exactly
equal stdout, stderr and native status for each tested result. Canonical real
code objects are measured; a copied fixture's coverage cannot replace them.

The fixture uses the existing synthetic manifest schema, nodes and budgets,
rather than copying another fixture implementation. It keeps ordinary help,
relative/absolute manifests, multiple simultaneous reasons, missing/malformed/
UTF-8/oversized inputs, expected refusal, usage and native output ordering.
The actual external pytest child asserts project config and equal physical
rootdir/confcutdir. It creates no gate observation and runs no example or gate.

Eight real Make-stanza controls retain8FAIL1.62s/native2.6s before
8PASS1.53s/native2.498s at make-result-guidance-{red,green}311 under the same native
observed root. They reuse the existing validator/separate test-child fixture:
each of six pins failing, validator7 and success. Native child5 and enclosing
Make2 remain; terminal passed must follow the child marker and is absent on all
failures. Existing validator/argv/order/worker/assertion checks remain intact.

Coverage reuses config/coverage_gate_failure_promotions.ini, adds the complete
contract checker alongside the complete promotion checker, and retains branch
measurement/subprocess patching and85aggregate/75each-file line AND branch.
Both include paths bind to physical GLUDD_COVERAGE_SOURCE_ROOT, automatically
exported by the existing coverage-files owner. This measures the two canonical
production files; temporary copies prove CLI parity but cannot fragment or
substitute production coverage. No source file or arc is excluded.

Ruff caught a126-character guidance literal and a constant getattr in the new
test. Literal reflow preserves output; direct typed module access preserves the
native calls. The target contract also exceeded the strict ceiling at2501lines;
compacting only its new git-merge-nc entry preserves every field and278 entries.

## Consumer repairs before another broad replay

The first complete614-case coverage run retains2FAIL/612PASS205.47s/native207.0s:
.gate-logs/observed/coverage-files/20261022T102641Z-270-1ff07c48.json.
The guide lost make check-make-target-contract, and the variable guard stripped
recipe indentation before counting Python assignments. Native failure correctly
prevents coverage publication/audit; this run grants no green coverage claim.

Six controlled real-Make inputs retain a separate fixture6FAIL/IndentationError.
After correcting only the fixture's initial command,2FAIL/4PASS1.09s reproduce
the semantic error. The repair checks the leading tab before stripping; the
existing20-limit assertion, exceptions and messages stay. Recipe-only and20-plus-
recipe pass, while genuine21 and leading-space genuine21 still refuse. Actual
native Make executes every fixture before the guard checks it. Guide restoration
plus these controls pass8/0.77s/native1.7s.

This is guard conformance, not a larger allowance or a source-format escape.
[GNU recipe syntax](https://www.gnu.org/s/make/manual/html_node/Recipe-Syntax.html)
distinguishes default-tab commands from Make variables. A custom RECIPEPREFIX is
not certified here. No parser dependency, suppression, weaker assertion or
production merge-formatting workaround is added.

Two native routing controls fail before families161/162 bind both real consumer
nodes to the existing check-gate-failure-promotions owner. Its eight current pins
retain all six predecessors first and pass8/1.87s terminal0. Existing real-child
controls grow from eight to ten outcomes; terminal passed must follow the child
and remain absent on every failure. Current prompts say eight; older six-pin
receipts above retain their historical scope.

Instrument complete source owners and required coverage callers under the same
two-source85/75 BOTH profile. Run remaining complete consumers through ordinary
native acceptance once per required runtime. This retains the deduplicated union
without instrumenting unrelated repository-wide AST traversal. It is a scheduling
choice; no measured speedup or omitted scope is claimed.

## Measured throughput decision

The complete441-case receiving owners take native78.668s on311 and84.474s on312
at exact41b. One unnecessary repeat of both would consume163.142s by arithmetic;
this is not a measured new pipeline saving. Required normal41b collection takes
native57.111s and remains mandatory for every logical source commit. Approval
waiting, optimal batch size, a future two-item speedup and original60907 cause
are unmeasured. The reviewed throughput decision is retained at
/tmp/gludd-v012-throughput-decision/decision.md; source timings remain in the
[admission record](INTEGRATION_ADMISSION.md#git-task-handoff-admission-and-confined-duration-fixtures-2026-10-22-utc).
The current mutex/admission/helper work is coupled S98.4 repair, not another
completed backlog item. Do not rerun a covered subset after its whole owner
passes or repeat accepted41b mid-repair; changed shared foundation inputs still
require affected consumers at stable receiving. The [throughput research decisions](RELEASE_PROOF_THROUGHPUT.md#research-decisions-for-this-pipeline)
support fast feedback/WIP limits, not a proven optimum. Native result prompts
in [the result contract](MAKE_RESULT_GUIDANCE.md) name that next action while
preserving statuses, all eight current child pins and pending final proof.

## Complete receiving measurements

The deduplicated complete receiving union retains these terminal results;
each row names distinct complete test owners, with no failed/skipped cases:

| Scope | Python3.11 | Python3.12 | Native receipt owner |
| --- | --- | --- | --- |
| Six source/Make owners |209PASS27.11s/native29.535s, with coverage |209PASS14.13s/native15.0s | coverage-files/20261022T103756Z-14806-70941393 and make-guidance-source312/v012-make-guidance-source312 |
| Eleven remaining consumers |417PASS111.45s/native112.6s |417PASS100.05s/native101.060s | make-guidance-consumers{311,312}/v012-make-guidance-consumers{311,312} |
| Two merge owners |92PASS81.20s/native83.0s |92PASS81.76s/native83.9s | merge-nc-final{311,312}/v012-merge-nc-final{311,312} |
| Four operating/handoff owners |122PASS5.68s/native6.5s |122PASS9.68s/native10.8s | make-guidance-operating{311,312}/v012-make-guidance-operating{311,312} |

All receipt prefixes above are under .gate-logs/observed; named logs/XML remain
under /tmp/gludd-v012-* except the native coverage log/trace. They are source/
receiving proof, not exact-SHA hosted or final-gate evidence. Diagnostic180/60
and240/180 envelopes do not alter the original runtime90/60 requirement.

The unchanged85aggregate/75each-file BOTH audit is
.gate-logs/coverage-20261022T103826Z.json:95.2line/90.2branch overall, promotion
checker98.0line/95.1branch and contract checker89.7line/82.0branch. It measures
the two complete canonical production files with no below-threshold file and
pytest0. The raw two-source report is
.gate-logs/coverage-v012-make-guidance-final311.json.

Cheap checks retain278 target contracts/actual empty-BR refusal2,162 families/
eight pins1.87s terminal0,1157Make targets/zero duplicates/parity,10 Markdown
files,26-path registration, native SESSION mode1, strict scopes/Ruff and the
less-than2500 limit. Node compatibility uses its existing installed3.14 owner:
six cases pass9.46s; it is separate from the311/312 acceptance above.

## Normal guarded source receipt

Exact6bc6981413c5c1f5b042043cc04f793f17b6841c is committed locally on
release-candidate/v012-git-mutex-20261022. Normal hooks retain zero new clones,
six owned checkpoints and128881selected/128905collected,24deselected0errors
48.65s/native53.765s. The native receipt is
.gate-logs/observed/test-count/20261022T104837Z-32263-e7ef01c1.{json,log,pytest.jsonl}.
Independent APPROVE SOURCE ONLY against41b is fully read at
/tmp/gludd-v012-native-make-review-decision.md. The review accepts all26 frozen
paths and ten native receipts; it preserves literal/native merge state, original
statuses, terminal child ordering, canonical full-file coverage,162 additive
families/eight pins and unchanged runtime/full-gate floors. It does not recertify
prior mutex source or arbitrary hooks/filters/concurrent writers. Its two exact
nonblocking six-to-eight wording corrections are applied in this documentation
receiving; historical six-pin measurements stay intact. The reviewer and all
reader sessions are quiescent. This record supplies no full-gate, exact-SHA
hosted/deployment or release-completion claim.

The documentation receiving runs only its five complete operating/guide owners,
not the accepted840-case source union:133PASS on3117.23s/native8.138s and312
7.85s/native8.820s. Native receipts are
.gate-logs/observed/make-guidance-handoff{311,312}/v012-make-guidance-handoff{311,312}.json;
logs/XML are /tmp/gludd-v012-make-guidance-handoff{311,312}.{log,xml}. Both preserve
fatal warnings/no skips or errors. Seven-file Markdown, registration, SESSION,
line-limit and278-contract checks pass. Normal per-commit collection remains
mandatory; consume its native hook result without a recursive receipt-only commit.

## Receiving sequence and release limits

Run cheap lint, separate strict scopes, target contract, documented behavior,
line checks and all eight current promotion pins before the complete receiving union.
Families157-160 append the staged-test preservation, literal Make branch,
terminal prompt and layout-error witnesses after all156 predecessors. Four
routing controls fail first before additive manifest/ordered-seed/count/literal
roster changes. Each node runs once in the first existing child with no file
widening; three2/1/1 children,90total/60quiet,21phases/2850 and required full gate
remain. Script structural output alone never starts a broader owner.

At stable receiving inputs run the deduplicated complete changed owners and
required consumers on each installed runtime, then normal per-commit collection
and independent exact-source review. The earlier41b441-case acceptance and
APPROVE SOURCE ONLY remain history, not new proof of changed Make inputs.
The native Make guide and AGENTS.md codify the same sequence.

The source review retains capacity97percent/7.8GiB as its frozen input. Latest
native disk96percent/7.9GiB still exceeds95. Original60907/124/90.554s/nulltrace/no transport,
original runtime replay, canonical full gate/global coverage and all exact-SHA
hosted/deployment/reviewed-head/promotion proofs remain pending. The seven S98
items stay open. A new prompt, source commit or elapsed time repairs none of
those inputs.
