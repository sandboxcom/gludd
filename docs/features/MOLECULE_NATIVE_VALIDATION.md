# Native Molecule input validation

## Batching research and acceptance boundary

The [2021 maintainer discussion](https://github.com/ansible/ansible-lint/discussions/1256)
identifies repeated Ansible startup as a major cost and explains why changed-file
validation alone misses dependent inputs. Its historical CLI limitation is not
current API truth: the [supported CLI](https://docs.ansible.com/projects/ansible/latest/cli/ansible-playbook.html)
accepts multiple playbooks. Real bounded probes nevertheless reject using either
CLI batching or import aggregation here: both expose an earlier playbook's local
module to a sibling that fails when checked alone. Import aggregation also loses
late diagnostics and still starts native checks for imported inputs.

Six retained acceptance cases require late syntax, argument, Jinja and schema
errors, both early and late syntax diagnostics, and sibling module isolation.
No input is removed and no skip flag or mock rule is introduced by these
batching experiments. The [native syntax prerequisite](https://docs.ansible.com/projects/lint/rules/syntax-check/)
remains mandatory. Earlier complete repository acceptance timed out at 45 seconds;
these tests do not close S98.4 or establish a speed improvement. The separately
selected complete-workload budget below is a larger allowance, not a speedup.

The [static/runtime split discussed by practitioners](https://forum.ansible.com/t/feedback-wanted-ansible-static-lint-static-ansible-linting-without-the-ansible-runtime/46213)
may help editor feedback, but cannot replace runtime-dependent release checks.
No additional linter dependency is adopted for this release. An independent
[adjacent-collection report](https://github.com/ansible/ansible/issues/85357)
also reinforces the need to preserve input-local resolution semantics; it is not
the cause asserted for the locally reproduced module leakage.


The existing check-molecule-yaml owner validates configuration and playbook inputs; it is not a Molecule test run, convergence receipt, verifier receipt, release proof, or a substitute for ZDD lifecycle execution.

## Maintained owners and retained scope

Native CLI controls expose a project-root boundary: a malformed dynamically
included child outside discovery is missed in a marker-free fixture using the
private lint configuration's inferred root. The same native CLI reports the
child when given `--project-dir` explicitly. The checker now supplies its exact
ROOT; private state, every original input and the native closure remain intact.
Both controls pass on Python 3.14 and 3.11. This is not a claim that every
Git-backed repository previously selected the wrong root.
[Native project selection](https://docs.ansible.com/projects/lint/usage/)

The installed Runner's checked-files set is not a syntax-result cache.
[Documented caching](https://docs.ansible.com/projects/lint/usage/#caching)
covers dependencies; it does not justify bypassing fresh validation. The
maintained CLI remains the correctness owner rather than a replacement runner.

Installed Molecule Config performs YAML loading, default/migration handling and native schema checks; its playbook resolver owns effective paths. Configuration errors are collected with source identity, with each diagnostic limited to 500 characters. The old check-molecule-integrity target references an ephemeral /tmp script and is not reused as validation authority. [Molecule configuration](https://docs.ansible.com/projects/molecule/configuration/)

Discovery retains root molecule scenarios, collection molecule scenarios and collection role task YAML, resolves each unique path once and includes both YAML suffixes. It neither samples cases nor excludes nested scenarios. UTF-8/read failures remain errors. Each scenario receives a distinct SHA-256-named child of private state, avoiding collisions between separate scenarios named default.

An active convergence stage requires its native resolved converge playbook. An explicitly requested active verification stage or explicit verification playbook reference requires its real native-resolved file. A backend name alone does not require the optional default verifier: an omitted implicit verifier emits MOLECULE_VALIDATION_VERIFY_SKIPPED with proof=none, never a verification-completed claim. Empty and limited sequences remain valid; existing explicit macOS gather_facts=false assertions remain unchanged.

Installed ansible-lint runs its min correctness profile with args and jinja enabled, offline, no color and empty ignore/skip/warn lists. This leaves native malformed YAML, unknown modules, syntax, argument and Jinja errors authoritative; it does not impose formatting policies from the project's separate full yaml-lint target. No Molecule actions, installation, convergence or verifier execution is invoked. [Lint configuration](https://docs.ansible.com/projects/lint/configuring/), [syntax-check](https://docs.ansible.com/projects/lint/rules/syntax-check/), [Jinja](https://docs.ansible.com/projects/lint/rules/jinja/)

## Action option limitation

Native args inspection can return no findings for action plugins without main. A thin fallback uses native Lintable/task metadata, plugin path resolution, ansible.utils.plugin_docs.get_plugin_docs with documentation fragments, sidecars and aliases, and ArgumentSpecValidator. Module metadata takes precedence; native action metadata is a fallback. It validates only documented option names for closed option domains. Raw values are not rendered, coerced or falsely type-validated when they require runtime variables. It imports no action class and runs no action or module body.

Genuinely missing native options emit ACTION_OPTIONS_UNSUPPORTED with proof=none, not a claim that options were validated. Malformed documentation, chained parser failures and invalid option maps remain errors. All closed-option failures are collected with source identity in one run, rather than stopping at the first input. The existing native lint still owns remaining correctness checks.

The documented key_value pseudo-option declares dynamic variable names; that domain remains with native validation. Documented free-form/free_form instead admits only unnamed _raw_params: its other option names remain closed. This preserves set_fact variable names and include_vars free-form semantics without a blanket module-name allowlist. Closed copy/assert/include_vars unknown-option regressions remain failures. This limited fallback is not complete runtime argument validation. [set_fact](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/set_fact_module.html), [include_vars](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/include_vars_module.html), [args](https://docs.ansible.com/projects/lint/rules/args/)

## Workload-sized bounded lint budget

The public CLI accepts `--lint-timeout-seconds` only as an integer from 1 through
120; malformed or out-of-range values fail before discovery or native work.
Its default remains 45 seconds, as does a direct `main()` call. The canonical
`make check-molecule-yaml` target explicitly passes its selected budget, defaulting
`MOLECULE_YAML_LINT_TIMEOUT_SECONDS=120` for all 211 configurations and 1,014 lint
inputs. This is a larger bounded complete-work allowance, not faster validation.
The historical 45-second failures remain retained evidence.

`MOLECULE_YAML_VALIDATE_ONLY=1` selects `--validate-only`: argument admission and
a plan reporting `proof=none`, with no discovery, native validator, temporary
state or environment mutation. The documented safe example is
`make check-molecule-yaml MOLECULE_YAML_LINT_TIMEOUT_SECONDS=120 MOLECULE_YAML_VALIDATE_ONLY=1`.
Actual execution explicitly selects `MOLECULE_YAML_VALIDATE_ONLY=0`.

Budget selection does not remove an input, skip a rule, lower a profile or raise
the eight-worker cap. Native closure, project-root selection, all diagnostics
and the existing process-group cleanup owner remain authoritative. Focused real
fixtures prove default 45, explicit 120, argument refusal and non-proof plan
behavior; only a measured complete native run can prove the full workload fits
120 seconds. The first complete run with this allowance resolved all 211
configuration inputs and processed 1,348 lint/closure files in 65.8 seconds of
lint (86.7 seconds including the watched configuration phase). It failed with
47 violations; fitting the allowance is not successful validation or release
proof. All reported failures must be repaired before another complete run.

Native `kinds` configuration identifies the two shared daemon lifecycle includes
as task lists, not playbooks; neither file is excluded and invalid native module
arguments still fail. The canonical resource-free phases contain valid localhost
plays with no facts, roles or tasks instead of invalid empty YAML lists.
[Native file-kind configuration](https://docs.ansible.com/projects/lint/configuring/)

Real Ansible rendering regressions cover report category iteration, defaulted
bounded HSM diagnostics, and all eleven SQL-log classifications. The SQL template
uses supported Jinja iteration instead of Python comprehensions, preserves
ordering and severity, omits unsuccessful/missing scans, and returns `unknown`
when web-log metadata does not match. Existing search-result preservation tests
remain unchanged. These source checks do not constitute deployment acceptance.

Coverage.py's maintained `[paths]` configuration combines byte-identical copied
CLI fixtures with their source owner; no measured production file, line, branch
or threshold is excluded. The final focused replay passes all 109 cases at 95%
combined checker coverage. Two incomplete role fixtures initially failed the new
diagnostic guard: they copied callers without their actual module/client files.
The fixtures now copy and verify the exact production dependency bytes, retaining
their original success assertions and actual native validation.
[Native source-path aliases](https://coverage.readthedocs.io/en/7.10.7/config.html#paths)

The final complete native replay resolves all 211 configurations and checks
1,349 closure files in 65.9 lint seconds (88.0 watched seconds). It reports zero
lint-rule violations but emits 78 native validation warnings: 68 unresolved
module-validation diagnostics and ten ignored argument-rule exceptions. The
owned diagnostic guard correctly rejects that run. The relative renderer import
restores one previously omitted child; no missing-playbook error remains.
Unsupported platform/dependency validation is not release acceptance, and the
unchanged failing observation must not be rerun without repairing its inputs or
validation environment. S98.4 remains pending.

The normal duplicate-code guard rejects two newly repeated assessment/mapping
blocks before commit. Data-driven native Jinja iteration removes those blocks
without changing detector policy. Four additional failing-first rendering cases
expose undefined `null`/missing scans and a changed advanced-SQL label. The
assessment emits native `none`, safely defaults absent scans, preserves every
category/template/breach field, and restores the original `Advanced exploitation`
classification. All fifteen template cases pass on Python 3.14 and 3.11;
the original field assertions are retained and the native clone guard reports
zero new clones. This is source acceptance, not a new deployment proof.

## Resource and offline ownership

One gludd-molecule-yaml TemporaryDirectory owns Ansible state, local temp files, scenario state, native lint configuration, an empty ignore file, log and observer artifacts. Context exit removes the complete private subtree; original environment and working directory are restored. Existing repository/config inputs are never rewritten by validation, and shared home/venv caches are not selected as lint workspace.

The checker calls the existing scripts.stream_command.stream_command owner, not a second scheduler. It streams native output, publishes namespaced heartbeats every ten seconds, enforces the admitted 1..120-second lint deadline (default 45; canonical complete-workload target 120) and terminates only its start_new_session process group with TERM then KILL after the grace period. Cleanup does not stop merely because the group leader exits; real regressions cover a TERM-ignoring grandchild and an unrelated session that must survive. Fixture outer subprocesses remain bounded at 60 seconds.

CPython 3.13+ provides -X cpu_count=8 and PYTHON_CPU_COUNT=8. The child also checks installed ansible-lint's actual threads() result against its native-classified syntax workload before calling the unchanged native CLI. On 3.11/3.12 the interpreter override has no effect: a small workload is still admitted, while native potential concurrency over eight fails closed and requires a supported constrained execution environment. Linux cgroup limits may override CPython's CPU count and are checked rather than assumed. The bound covers native syntax workers; the checker/stream supervisor and lint interpreter are separate owned processes. [CPython CPU-count control](https://docs.python.org/3.14/using/cmdline.html#cmdoption-X), [multiprocessing.cpu_count](https://docs.python.org/3/library/multiprocessing.html#multiprocessing.cpu_count)

## Long-lived practitioner findings

The 2023 Ansible forum discussion reports that native args inspection can execute module initialization and cause syslog side effects. The fallback therefore reads documentation rather than executing plugin code, while retaining the upstream args rule for supported modules; offline syntax/lint acceptance is not a claim of arbitrary-plugin sandboxing. [Practitioner discussion](https://forum.ansible.com/t/ansible-lint-causes-massive-amount-of-debug-logging-to-syslog/853)

A separate 2023 developer discussion explains paired module stubs as action-plugin documentation owners. Native assigned DOCUMENTATION and adjacent YAML metadata remain authoritative; a heading embedded only in __doc__ is not assumed equivalent. [Action-plugin documentation discussion](https://forum.ansible.com/t/creating-my-first-action-plugin-and-have-a-few-questions/38275), [native module documentation](https://docs.ansible.com/projects/ansible-core/devel/dev_guide/developing_modules_documenting.html).

A 2023 user thread reports warn becoming an unsupported shell/command parameter after upgrading old playbooks. Real repository fixtures reproduce two such options and a task environment incorrectly nested under command; those fields are repaired, preserving the intended environment. [Upgrade report](https://www.reddit.com/r/ansible/comments/14no23s/).

A 2019 Molecule report documents fact-gathering failures on Alpine depending on the target's Python setup. That is an environment-dependent execution issue, not justification for blanket rejection of intentional gather_facts=true. The maintained default driver replaces the removed delegated label without changing playbooks or lifecycle sequences. [Facts report](https://github.com/ansible/molecule/issues/2164), [upstream driver rename](https://github.com/ansible/molecule/pull/3960)

## Acceptance and release proof

Fail-first real native fixtures cover valid facts/expressions/script options, empty/limited sequences, required/optional verifier semantics, malformed/schema/module/argument/expression/read failures, documented aliases/runtime values/dynamic domains, discovery deduplication, state isolation and complete config-error reporting. Additional bounded-owner fixtures cover native capacity refusal and owned-grandchild cleanup. The subprocess coverage profile remains config/coverage_molecule_yaml.ini.

Focused/source acceptance does not close S98.4. Complete canonical gate evidence, 85% aggregate and 75% per-file coverage, zero collection errors and required exact-SHA hosted proofs remain mandatory. Actual optional verification skips never count as feature verification evidence.

The focused native replay passes 50 cases at 93% branch-aware checker coverage. The shared observer/trace replay meets unchanged 85/75 floors at 90% combined coverage. The previous complete repository lint timed out at 45 seconds on 1,014 inputs after resolving 211 scenario configurations; this unsuccessful acceptance remains retained. The canonical target now selects a bounded 120-second allowance while preserving all inputs and native checks. Complete measured native execution under that selected budget, the canonical gate, coverage floors and required exact-SHA hosted evidence remain necessary before S98.4 can close.

## Native diagnostics and incomplete argument proof

Native `--strict` is retained as the maintained control for rule warnings. Its
installed outcome code examines `result.matches`; logger diagnostics are not
necessarily rule matches. The installed `BaseRule.getmatches` catches rule
exceptions, logs a warning and continues. A real nested integer option fixture
reproduces `ArgsRule.matchtasks` raising `KeyError('port')` while native lint
exits zero and reports zero rule warnings. A separate missing-module fixture
also emits a warning while native lint exits zero. These are incomplete
validation, so the existing checker now rejects warning, error, critical and
fatal severity records in its existing streamed native log even after exit
zero. A missing or unreadable successful-run log fails closed. Full diagnostics
remain visible; no logger levels, filters, skip lists, warning lists or enabled
rules are changed. [Maintained strict CLI](https://docs.ansible.com/projects/lint/usage/)

The shipped system-facts renderer used a templated `import_playbook` that native
Ansible resolves at syntax-check time, while ansible-lint's child resolver treats
it as a literal path and logs an error. A real control reproduces native exit
zero while a child's invalid Jinja expression is missed. Its portable relative
import now resolves to the same shipped playbook and allows native lint to
detect the child's invalid expression. This repairs the input rather than
changing the resolver or excluding the child.

A [2023 practitioner report](https://stackoverflow.com/questions/76135553/ansible-lint-warning-for-custom-module-argument-exception)
describes the same ignored ArgsRule exception for a nested `port` option.
The installed parser looks up the reported leaf option at the top-level action,
which explains the local reproduction; the historical report is corroboration,
not proof that every ignored exception has that cause. The
[2026 upstream reproduction](https://github.com/ansible/ansible-lint/issues/5065)
separately documents logger warnings alongside exit zero. Its module-import
registration repair already exists in the installed owner and does not repair
the nested-option lookup. Changing a valid nested argument to avoid inspection
would not establish validation.

The [2023 Windows module report](https://github.com/ansible/ansible-lint/issues/3200)
led to native ArgsRule explicitly skipping `.ps1` modules on POSIX. Installing a
missing collection can restore resolution and remove a warning without proving
Windows argument types. Such platform argument proof remains `proof=none` here
and requires the applicable supported validator/execution environment. A clean
static result is not Windows execution acceptance. Missing external collections,
incorrect module namespaces and other ArgsRule exceptions in the retained
complete-run log still require their actual owning-input or prerequisite repair.
The [2023 Ansible forum discussion](https://forum.ansible.com/t/ansible-lint-causes-massive-amount-of-debug-logging-to-syslog/853)
also documents module initialization side effects during args inspection; the
existing native rule and metadata-only action fallback are preserved.

Focused TDD observed 13 failures and 2 passes before these repairs, then 15
passes under one pytest worker and warnings-as-errors. Native fixtures retain
the eight-worker admission, bounded deadlines, child syntax/Jinja checks and
existing stream owner. The complete retained native run, canonical gate,
85/75 coverage floors and exact-SHA hosted proofs remain separate requirements;
this focused result does not close S98.4.
