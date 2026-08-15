# Backlog-Audit System — design (#65)

Status: DESIGN ONLY (no code in this change). Implementation-ready.
Scope: a verbose, highly-functional **backlog-audit system** for gludd that

1. audits every prior task for completeness against real evidence,
2. when it finds a defect, fixes it **system-wide as a bug-class** (never a point
   fix) and installs a **recurrence guard** so the class cannot slip through a
   future task,
3. is exposed as a first-class gludd **role** + daemon endpoint, and
4. auto-files follow-up todos for every gap it finds.

This document is grounded in the code that already exists. Every cross-reference
below was read from the live tree:

- `src/general_ludd/validation/backlog_auditor.py` — `BacklogAuditor`,
  `TaskVerdict`, `BacklogAuditReport`, verdict constants `FALSE_CLAIM` /
  `INCOMPLETE` / `VERIFIED_COMPLETE`, `_STUB_MARKERS`, `_COMPLETED_STATUSES`.
- `src/general_ludd/quality/bug_class_registry.py` — `BugClass`, `Occurrence`,
  `sweep`, `verify_guards`, `DEFAULT_BUG_CLASSES`, `SEED_MARKER`, `SWEEP_ROOT`.
- `scripts/backlog_audit.py` — the verbose CLI report (`run_report`,
  `collect_test_ids`, `_print_occurrences`, `_print_guard_gaps`,
  `_print_backlog_verdicts`).
- `src/general_ludd/validation/runner.py` — `ValidationRunner`,
  `_validate_command`, `create_child_todos_for_failures`.
- `Makefile` `gate:` target (5 phases: lint / typecheck / collect / test /
  smoke) + `.gate-status` contract, and `audit-evidence:` target.
- Role pattern: `collections/ansible_collections/general_ludd/agent/roles/`
  (`feature_audit`, `backlog_groom`), `gludd_*` modules under
  `.../agent/plugins/modules/`, PSK auth + `POST /api/todos`
  (`src/general_ludd/routers/todos.py`, `src/general_ludd/daemon.py`).

---

## 0. Defects this design already surfaces (dog-fooding)

Designing the system forced a read of the two collaborating files, and they do
not currently compose. These are recorded here because the auditor's whole point
is to catch exactly this class of "claimed-done but the wiring is broken":

- **D-1 — auditor API mismatch (blocks the CLI's backlog section).**
  `scripts/backlog_audit.py::_print_backlog_verdicts` calls
  `BacklogAuditor(repo_root=repo_root)` then `auditor.audit()` with **no
  arguments**. The real `BacklogAuditor.__init__` *requires* a `test_runner`
  callable and the real `audit(tasks)` *requires* a `tasks` sequence. The CLI
  only survives because the call is wrapped in a broad `except Exception` that
  prints "BacklogAuditor present but failed to run". So the backlog section is
  silently dead. Section 1.6 specifies the adapter that closes this.
- **D-2 — no task source.** Nothing in the tree assembles the `tasks` list the
  auditor consumes (`evidence_test_ids`, `touched_files`, `acceptance_criteria`,
  `status`). `make audit-evidence` only re-runs node ids scraped from
  `TASKS.md`; it never adjudicates per-task completeness. Section 1.2 specifies
  the ingest.
- **D-3 — no `make backlog-audit` target.** `scripts/backlog_audit.py`'s
  docstring tells the user to "Invoke via the integrator-added `make
  backlog-audit` target", but that target does not exist in the Makefile.
  Section 4 adds it and wires it into the gate.
- **D-4 — guard gaps already live.** `DEFAULT_BUG_CLASSES` ships two classes
  with empty `guard_test_id` (`overlay_shadow`, `non_idempotent_reconcile`);
  `verify_guards` correctly reports them as prevention gaps. The system must turn
  every such gap into a filed todo (Section 3.4) rather than a printed line that
  scrolls away.

---

## 1. The auditor pipeline

### 1.1 Data model

The existing dataclasses are the spine; we extend them, we do not replace them.

Existing (keep verbatim):

```markdown
# backlog_auditor.py
FALSE_CLAIM, INCOMPLETE, VERIFIED_COMPLETE        # verdict label constants
@dataclass TaskVerdict:   id; verdict; reasons:list[str]
@dataclass BacklogAuditReport:
    total_audited; verified_complete; false_claim; incomplete; verdicts:list[TaskVerdict]
```

New, additive (proposed `validation/backlog_audit_model.py`):

```python
@dataclass(frozen=True)
class TaskRecord:
    """One backlog task, normalised from whatever source produced it."""
    id: str
    status: str                       # raw; lower()'d against _COMPLETED_STATUSES
    title: str = ""
    source: str = ""                  # "TASKS.md" | "daemon" | "guide:GLM_...md"
    evidence_test_ids: tuple[str, ...] = ()
    touched_files: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    commit_hashes: tuple[str, ...] = ()   # NEW evidence channel (Section 1.4)
    raw: dict = field(default_factory=dict)   # provenance for the report

@dataclass(frozen=True)
class EvidenceMatch:
    """Why one evidence item did or did not ground a claim."""
    kind: str                         # "test" | "file" | "commit" | "criterion"
    ref: str                          # node id / path / sha / criterion text
    found: bool
    detail: str                       # "passed" | "absent" | "stub:TODO" | sha-msg
```

`TaskVerdict` gains an additive optional `matches: list[EvidenceMatch] = []` so
the verbose report can explain *every* evidence decision, not just the failing
reasons. Defaulted ⇒ no break to existing callers/tests.

A task is **auditable** iff `status.strip().lower()` is in
`_COMPLETED_STATUSES = {"complete","completed","done"}` (already enforced by
`BacklogAuditor.audit`). Non-completed tasks are skipped — we only re-adjudicate
*claims of doneness*.

### 1.2 Ingest (task source) — closes D-2

A new `validation/backlog_sources.py` produces `list[TaskRecord]` from three
adapters; each is independent and any subset can be empty.

1. **Daemon backlog** (authoritative live state): `GET /api/todos`
   (`routers/todos.py::api_list_todos`). A todo is mapped to a `TaskRecord` with
   `status=todo["status"]` and evidence pulled from the description if it carries
   the structured markers below. This is the source the *role* uses
   (Section 3) because the daemon is the running product's truth.

2. **`TASKS.md`** (the historical work log). Reuse the exact extraction already
   shipped in `make audit-evidence`:
   `re.findall(r'tests/[^\s:]+(?:::[A-Za-z0-9_]+)+', text)` for evidence test
   ids, plus the per-task block parser specified in 1.4. This is the source that
   re-adjudicates the round-1/2/3 remediation ticks the CLAUDE.md history warns
   are untrustworthy.

3. **Remediation guides** (`GLM_REMEDIATION_GUIDE*.md`,
   `GLM_IMPLEMENTATION_GUIDE.md`): each `Wx.y`/`Vx.y` checklist item with a
   ✅/done marker becomes a `TaskRecord(status="done")` whose evidence is the
   node ids, file paths and commit hashes named in the same bullet.

Adapters are pure `(text|json) -> list[TaskRecord]` functions so they unit-test
with fixed strings (no IO), matching the hermetic style of `backlog_auditor.py`.

### 1.3 Claimed-deliverable derivation

For each `TaskRecord`, the "claimed deliverable" is the union of its evidence
channels — this is exactly what the auditor already treats as the claim:

- the **tests** it says prove it (`evidence_test_ids`),
- the **files** it says it touched (`touched_files`),
- the **acceptance criteria** it states (`acceptance_criteria`, e.g.
  `must export <symbol>` — the one structured grammar
  `BacklogAuditor._criterion_met` understands today), and
- (NEW) the **commits** it cites (`commit_hashes`).

No NL guessing: a claim is only as strong as the structured evidence attached.
A task that asserts "done" with *no* evidence is, by construction, a
`FALSE_CLAIM` ("status claims done but there are no evidence tests" — already the
first reason `_adjudicate` appends).

### 1.4 Evidence matching

This is the core. It extends the three checks `BacklogAuditor._adjudicate`
already performs and adds the commit channel.

**(a) Tests** — re-run via the injected `test_runner`
(`run(node_ids) -> {node_id: passed}`). Any node id that is absent from the
result map or maps to `False` is a failure ⇒ contributes a `FALSE_CLAIM` reason
`"evidence test fails on re-run: <nid>"`. The production `test_runner` wraps
`ValidationRunner` semantics — build a pytest argv (`["pytest", *node_ids,
"-q"]`), run it under `_validate_command`'s allowlist + shell-metachar rejection
(`runner.py`), and parse pass/fail with `_parse_pytest_output`. Critically the
runner must **distinguish "not collected" from "failed"**: a node id that pytest
reports as *no tests ran / errors during collection* is treated as **not passed**
(fail-closed), mirroring `collect_test_ids`'s fail-loud default. A test that the
task names but that *does not exist* is the strongest FALSE_CLAIM signal.

**(b) Files** — resolved against `repo_root` (abs-join when relative, already
done) and read via the injected `file_reader` (`read(path)->str|None`, `None` ⇒
absent). Absent ⇒ `FALSE_CLAIM` reason `"referenced file is absent/missing"`.
Present ⇒ cached for the stub-marker pass.

**(c) Stub markers** — `_STUB_MARKERS = ("raise NotImplementedError",
"NotImplementedError","TODO","FIXME","pass  # stub","@pytest.mark.xfail")`. A
present-but-stubbed file is `INCOMPLETE`, not FALSE_CLAIM — the work exists but
isn't finished. (Design note: today the marker scan is a substring over the
whole file, so a `TODO` in a docstring/comment trips it. Section 5 calls for an
AST-aware refinement — flag only markers in executable positions — but the
substring form is kept as the conservative default.)

**(d) Acceptance criteria** — `must export <symbol>` is satisfied iff some
touched file contains `<symbol>` (`_criterion_met`). Free-text criteria fail
*open* (can't be disproved). This asymmetry is deliberate and preserved.

**(e) Commits (NEW channel)** — for each cited sha, the commit must (i) exist
and (ii) actually touch at least one of the task's `touched_files`. The
production matcher shells nothing directly; it goes through a `commit_reader`
callable injected the same way as `test_runner`/`file_reader`
(`exists(sha)->bool`, `files_in(sha)->frozenset[str]`). Default impl wraps the
repo's `make git-history-file` / `git show --name-only` semantics. A cited sha
that does not exist, or that touches none of the claimed files, is a
`FALSE_CLAIM` reason `"cited commit <sha> does not touch any claimed file"`.
Injection keeps unit tests hermetic — no real git in the unit suite.

### 1.5 Classification (unchanged precedence)

`BacklogAuditor._adjudicate`'s order is correct and is preserved:

1. Collect all FALSE_CLAIM reasons (missing tests, absent files, failing tests,
   bad commits). **If any ⇒ `FALSE_CLAIM`** and stop.
2. Else collect INCOMPLETE reasons (stub markers, unmet `must export`).
   **If any ⇒ `INCOMPLETE`.**
3. Else **`VERIFIED_COMPLETE`**.

Mapping to the requested three buckets:

| this design       | requested bucket   | meaning                                  |
|-------------------|--------------------|------------------------------------------|
| VERIFIED_COMPLETE | **GROUNDED**       | every evidence channel checks out        |
| INCOMPLETE        | **PARTIAL**        | exists but stubbed / criterion unmet     |
| FALSE_CLAIM       | **UNSUBSTANTIATED**| evidence missing, absent, or failing     |

The report keeps the internal verdict names (back-compat with
`BacklogAuditReport` counters) and exposes the GROUNDED/PARTIAL/UNSUBSTANTIATED
aliases only in the human/markdown rendering.

### 1.6 Driver — closes D-1

A thin `validation/backlog_audit.py::run_backlog_audit(repo_root, *, sources,
test_runner, file_reader, commit_reader) -> BacklogAuditReport` that:

1. assembles `TaskRecord`s from the requested `sources` (1.2),
2. constructs `BacklogAuditor(repo_root, test_runner, file_reader)` **with its
   real signature**,
3. calls `auditor.audit([asdict-ish view of each record])`, and
4. returns the `BacklogAuditReport`.

`scripts/backlog_audit.py::_print_backlog_verdicts` is then fixed to call this
driver (passing a real `test_runner` + default readers) instead of the broken
no-arg `BacklogAuditor(...).audit()`. The lazy/guarded import stays — the script
must still run standalone when the module is absent.

---

## 2. System-wide bug-class fix

When an audit finds a *defect* (a FALSE_CLAIM/INCOMPLETE rooted in a code error,
not just missing paperwork), the fix is never the one site. The flow:

### 2.1 (a) Generalise the defect to a bug-CLASS

A found defect is described as a `BugClass` (existing dataclass, frozen):
`id` (snake_case), `description`, `detector`, `guard_test_id`, `remediation`.
The detector is either a compiled `re.Pattern` matched per line, **or** a
predicate `(path, text)->bool` over the whole file for multi-line shapes — both
forms are already supported by `_scan_file`. Generalisation rule of thumb: strip
the incident's incidental specifics (this variable, this module) and keep the
*shape* (e.g. "f-string interpolated into a subprocess argv" → the existing
`unvalidated_subprocess_argv` predicate). The D-1 mismatch above generalises to a
new class **`auditor_api_drift` / `stale_call_signature`**: a call site that
constructs/ invokes a project class with keyword/arity that the current
definition does not accept — detectable as a predicate (parse the file, resolve
the callee in-repo, compare against its signature) and guarded by a contract
test.

### 2.2 (b) Sweep the whole codebase

Reuse `bug_class_registry.sweep(repo_root, classes)`:

- It walks `repo_root / SWEEP_ROOT` (= `src/general_ludd`), every `*.py`, and
  returns `{class_id: [(path, lineno, line), ...]}` — **every** occurrence, with
  an entry for every class (empty list when clean). That dict *is* the
  system-wide proof that a fix is total.
- The registry's own seed file is excluded wholesale (`_iter_source_files` skips
  `_REGISTRY_FILENAME`); any other line that legitimately embeds a pattern as
  data opts out with the `SEED_MARKER` sentinel
  (`"bug-class-seed" ":exclude"`). `_scan_file` honours the marker for both the
  regex and predicate forms. **New seeds must carry the marker on any line that
  embeds the literal pattern**, or they self-flag — pinned by the existing
  `test_registry_does_not_self_flag_its_own_definitions`.
- Regex detectors are line-scoped (fast, precise line numbers); AST/predicate
  detectors get whole-file text (`lineno=0` ⇒ "file-level"). Choose regex when
  the shape fits one line; choose a predicate when it needs context (the
  `_detect_unvalidated_subprocess_argv` / `_detect_ssrf_unvalidated_url`
  precedent). For "stale call signature" the predicate should do a real `ast`
  parse rather than a regex, to avoid false positives on comments/strings.

Remediation discipline: the fix is not done until `sweep` returns an **empty
list** for that class across `src/general_ludd` — the CLI's banner says it
outright: "a fix is never a point fix".

### 2.3 (c) Register it

Append the new `BugClass` to `DEFAULT_BUG_CLASSES` in
`bug_class_registry.py`. The new entry MUST set `guard_test_id` to the node id
of the test added in (d). If a guard is not yet written, `guard_test_id=""`
flags it as a known prevention gap (the `overlay_shadow` /
`non_idempotent_reconcile` precedent) — acceptable only transiently; Section 3.4
auto-files a todo to close it.

### 2.4 (d) Add a recurrence guard

A guard is a **test/lint/gate check that fails iff the class reappears**.
Canonical home: `tests/unit/test_guardrails.py` (every shipped
`guard_test_id` already points there, e.g.
`tests/unit/test_guardrails.py::test_no_shell_true_in_src`). The standard guard
body is a three-liner that calls the registry itself:

```python
def test_no_<class>_in_src():
    occ = sweep(REPO_ROOT, [by_id["<class>"]])
    assert occ["<class>"] == [], f"bug-class <class> recurred: {occ['<class>']}"
```

This makes the registry self-enforcing: add a seed + this guard and the class
can never silently come back. `verify_guards(classes, known_test_ids)` then
returns it as *covered* (its `guard_test_id` is now in the collected set),
closing the gap it previously reported.

---

## 3. Exposure as a gludd role + endpoint

### 3.1 The `backlog_audit` role

New role `collections/ansible_collections/general_ludd/agent/roles/backlog_audit/`
following the `feature_audit` / `backlog_groom` shape exactly:

```text
backlog_audit/
  defaults/main.yml      # daemon_url, psk, artifact_dir, file_followups, fail_on
  meta/main.yml          # galaxy_info; dependencies: []  (composes, not duplicates)
  tasks/main.yml         # the workflow below
  README.md              # contract: inputs, outputs, artifacts
```

`defaults/main.yml` (mirrors `feature_audit/defaults`):

```text
daemon_url: "http://localhost:8000"
psk: ""
artifact_dir: "/tmp/gludd-backlog-audit"
file_followups: false        # REPORT-ONLY by default (matches backlog_groom)
fail_on: "unsubstantiated"   # none | partial | unsubstantiated  (gate severity)
include_sweep: true          # run the bug-class sweep + guard-coverage section
```

### 3.2 Workflow (`tasks/main.yml`)

Audit-only by default, exactly like `feature_audit` ("never mutates the repo").

1. **Create `artifact_dir`** (`ansible.builtin.file`, mode 0755).
2. **Fetch the backlog** via a `gludd_*` module. Two viable wirings:
   - reuse `gludd_facts` (the `backlog_groom` precedent:
     `live_facts.ansible_facts.gludd.todos['items']`), or
   - a new `state: audit` verb on a `gludd_backlog` module that calls the
     daemon endpoint in 3.3 and returns the full `BacklogAuditReport` JSON.
   The module path is preferred so the heavy auditing runs *in the daemon*
   (where `test_runner`/`commit_reader` have repo + venv), not in Ansible.
   `no_log: "{{ psk | length > 0 }}"` on any PSK-bearing task (the
   `feature_audit` precedent).
3. **Classify** the verdicts into GROUNDED / PARTIAL / UNSUBSTANTIATED counts
   and compute a `status` fact: `clean` iff `unsubstantiated==0 and partial==0`,
   else `gaps_detected` (mirrors `feature_audit`'s `_fa_status`).
4. **(optional) Bug-class section** when `include_sweep`: include the sweep
   occurrence table + guard-coverage gaps from `scripts/backlog_audit.py`'s
   report (or the same data over the endpoint).
5. **File follow-ups** when `file_followups | bool` (Section 3.4).
6. **Write artifacts** `backlog_audit.json` + `backlog_audit.md` to
   `artifact_dir` (the two-artifact JSON+MD convention every audit role uses).
7. **Report completion** via `ansible.builtin.debug` with the summary line.

A playbook `playbooks/backlog_audit.yml` wires it with `include_role: name:
general_ludd.agent.backlog_audit`, matching `playbooks/system_report.yml`.

### 3.3 Endpoint + capability grants

New daemon route (`src/general_ludd/routers/`), e.g.:

- `POST /api/audit/backlog` → runs `run_backlog_audit` against the live backlog +
  repo and returns the serialised `BacklogAuditReport` (+ optional sweep
  section). Body selects `sources`, `fail_on`, `include_sweep`, `file_followups`.
- `GET  /api/audit/backlog/last` → last report (read-only).

**Capability grants** follow the existing auth model (`daemon.py`): `POST` is a
**mutating** method, so it is **not** in `_PUBLIC_PATHS` and is **not** a
`_SAFE_METHOD` — it goes through the PSK auth gate like `POST /api/todos`. The
read-only `GET .../last` MAY be added to `_PUBLIC_PATHS` (public only for
GET/HEAD/OPTIONS via `_is_public`). The role passes `psk` exactly as
`feature_audit` does. The audit *itself* needs no extra repo-write capability in
report-only mode; only `file_followups=true` exercises the todo-create
capability (3.4), which is already an authenticated `POST /api/todos`.

### 3.4 Feeding the todo backlog (auto-file follow-ups)

This is where the audit becomes a forcing function instead of a scrolling
report. For every gap, file a todo via the existing contract
(`routers/todos.py::AddTodoRequest` / `POST /api/todos`), reusing the *shape*
`ValidationRunner.create_child_todos_for_failures` already establishes
(`parent_todo_id`, `title`, `description`, `category`, `status`). Mapping:

| audit finding                         | todo                                                            |
|---------------------------------------|-----------------------------------------------------------------|
| `FALSE_CLAIM` (UNSUBSTANTIATED)       | `title: "Substantiate or revert: <task id>"`, `priority:high`, `queue:audit`, body = the `reasons` list |
| `INCOMPLETE` (PARTIAL)                | `title: "Finish stubbed work: <task id>"`, `priority:medium`, body = stub markers / unmet criteria |
| bug-class occurrence (`sweep` hit)    | `title: "Sweep bug-class <class_id> system-wide"`, body = every `(path,lineno,line)`, `priority:high` |
| guard gap (`verify_guards`)           | `title: "Add recurrence guard for <class_id>"`, body = `remediation`, `priority:high` |

Idempotency: each follow-up carries a deterministic dedup key in its title/body
(`audit:<task_or_class_id>`); the filer first `GET /api/todos?queue=audit` and
skips any open todo whose key already matches, so re-running the audit does not
spawn duplicates. (`AddTodoRequest` constraints: `title` ≤512, `description`
≤4096, `queue` matches `^[a-z0-9_\-]+$`, `priority` ∈ low|medium|high|critical —
the filer must truncate/escape to satisfy these or the POST 422s.)

---

## 4. Recurrence-guard catalog

### 4.1 Storage

The catalog is `DEFAULT_BUG_CLASSES` in `bug_class_registry.py` — the single
source of truth pairing every class's `detector` with its `guard_test_id`. No
parallel registry; the same tuple drives the sweep, the guard-coverage check,
and the report. New classes are appended here (Section 2.3), with the
`SEED_MARKER` discipline on any pattern-bearing line.

### 4.2 Running in the gate

`verify_guards(classes, known_test_ids)` returns every class whose
`guard_test_id` is empty or not in the **currently-collected** pytest node ids.
`scripts/backlog_audit.py::collect_test_ids` supplies that set via `pytest
tests/ --co -q` (fail-loud: on any collection failure it returns `set()` so
every guard reads as a gap rather than a false all-clear).

Add a `make backlog-audit` target (closes D-3) and wire it into `make gate` as a
new phase between `collect` and `test` (it depends on a clean collection but
should fail the gate before the long full-suite run):

```text
backlog-audit:
    @$(UV) run python scripts/backlog_audit.py --repo-root .
```

`run_report` already returns exit `1` when there are **occurrences OR guard
gaps**, so the target is gate-ready as-is. In the gate, append a 6th phase that
writes `backlog-audit PASS 0` / `FAIL <n>` to `.gate-status` and `touch
.gate-failed` on non-zero — identical to the lint/typecheck/collect phases, so
the existing `git-commit` freshness+green guard (which greps `^<check> PASS` for
each phase) extends to it by adding `backlog-audit` to that phase list.

The guard *tests* themselves (Section 2.4) run inside the normal `test` phase —
they are ordinary pytest functions in `tests/unit/test_guardrails.py`. So a
recurred bug class fails the gate **twice**: once in the `backlog-audit` phase
(non-empty `sweep`) and once in the `test` phase (the guard assertion).

### 4.3 Reporting

`scripts/backlog_audit.py` is already the verbose reporter and is the model for
all output:

- `_print_occurrences`: per-class banner, count, description, remediation, then
  every `path:lineno: line` — "every occurrence, system-wide (a fix is never a
  point fix)".
- `_print_guard_gaps`: per-gap class with its (possibly empty) `guard_test_id`,
  description, and fix.
- `_print_backlog_verdicts`: per-task verdicts (once D-1 is fixed).
- final `SUMMARY: N occurrence(s) across M class(es); K guard gap(s).`

The role's `backlog_audit.md` artifact renders the same three sections plus the
GROUNDED/PARTIAL/UNSUBSTANTIATED counts table (the `feature_audit.md` table
style), and the JSON artifact carries the machine-readable
`BacklogAuditReport` + sweep dict for downstream tooling and the `GET
.../last` endpoint.

---

## 5. Test strategy (described — no tests written here)

All unit tests stay **hermetic** via the DI seams the code already exposes
(`test_runner`, `file_reader`, and the new `commit_reader`), so no real pytest /
git / daemon is spawned in the unit suite — matching `backlog_auditor.py`'s
stated design and the repo's collection-error discipline (`make test-count`
before any commit).

**Auditor (`backlog_auditor.py` + driver).**
- Verdict matrix: one task per outcome — VERIFIED_COMPLETE (all green),
  FALSE_CLAIM × {no evidence tests, absent file, failing test, bad commit},
  INCOMPLETE × {each `_STUB_MARKER`, unmet `must export`}.
- Aggregation: `BacklogAuditReport` counters equal the sum of per-verdict
  counts; non-completed-status tasks are skipped (assert they never appear in
  `verdicts`).
- Driver/D-1 regression: a test that constructs the system through
  `run_backlog_audit` and asserts the backlog section produces real verdicts
  (this is the guard for the `auditor_api_drift`/`stale_call_signature` class —
  it fails if the CLI↔auditor signatures drift again).
- Commit channel: injected `commit_reader` fakes {exists, files_in}; assert a
  sha that touches none of the claimed files ⇒ FALSE_CLAIM.

**Ingest (`backlog_sources.py`).** Pure string/JSON fixtures: a `TASKS.md`
snippet yields the expected node ids (reuse the `audit-evidence` regex), a guide
bullet with ✅ yields a `done` `TaskRecord`, a `GET /api/todos` JSON payload maps
fields correctly. No file IO.

**Registry + sweep (`bug_class_registry.py`).**
- `sweep` returns every occurrence and an entry per class (empty when clean).
- `SEED_MARKER` discipline: keep/extend
  `test_registry_does_not_self_flag_its_own_definitions` — assert the registry
  file never appears as an occurrence and any new seed line carries the marker.
- `verify_guards`: empty `guard_test_id` ⇒ gap; present-but-uncollected ⇒ gap;
  present-and-collected ⇒ not a gap.

**Recurrence guards (`tests/unit/test_guardrails.py`).** Each new class gets the
three-line `sweep`-based guard (Section 2.4). Meta-test: every class in
`DEFAULT_BUG_CLASSES` with a non-empty `guard_test_id` resolves to a real,
collected test node id (so the catalog can't reference a deleted guard).

**CLI report (`scripts/backlog_audit.py`).** `run_report` with injected
`classes` + `known_test_ids` (its existing test seams) — assert exit code is `1`
on occurrences/gaps and `0` when clean, and that the standalone path
(`include_backlog=False`) prints the notice without importing the auditor.

**Role / endpoint (integration, daemon-backed).**
- Molecule/playbook scenario invoking `backlog_audit` against a mock daemon
  (the `molecule/` precedent) asserting the JSON+MD artifacts are written and the
  summary line is emitted (the `feature_audit` test pattern).
- Endpoint: `POST /api/audit/backlog` requires PSK (asserts 503/401 without it,
  mirroring the `POST /api/todos` auth test), `GET .../last` is public-readable.
- Follow-up filing: with `file_followups=true` against a backlog containing a
  known UNSUBSTANTIATED task, assert exactly one `POST /api/todos` per gap and
  that a second run files **zero** new todos (idempotency/dedup key).

**Gate wiring.** A test asserts `make backlog-audit` exits non-zero when the
registry has occurrences or guard gaps, and that `.gate-status` gains a
`backlog-audit PASS/FAIL` line that the commit-freshness guard checks.

---

## Appendix — implementation checklist (ordered)

1. `validation/backlog_audit_model.py` — `TaskRecord`, `EvidenceMatch`; extend
   `TaskVerdict` with defaulted `matches`.
2. `validation/backlog_sources.py` — three pure ingest adapters.
3. Extend `BacklogAuditor` with the injected `commit_reader` + commit channel
   (additive, defaulted ⇒ no break).
4. `validation/backlog_audit.py::run_backlog_audit` driver (closes D-1).
5. Fix `scripts/backlog_audit.py::_print_backlog_verdicts` to call the driver.
6. Add the `auditor_api_drift`/`stale_call_signature` `BugClass` + its guard;
   sweep `src/` clean.
7. `make backlog-audit` target + gate phase 6 + `.gate-status` line (closes D-3).
8. Auto-file follow-ups (dedup-keyed) reusing `POST /api/todos`.
9. `backlog_audit` role + `playbooks/backlog_audit.yml` + `POST
   /api/audit/backlog` endpoint (PSK-gated) + `GET .../last`.
10. Tests per Section 5; `make test-count` then `make gate` green before commit.
```text
```
