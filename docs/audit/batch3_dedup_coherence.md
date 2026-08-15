# Batch-3 Dedup & Coherence Audit

Commit audited: **4314a6c** (batch-3, 295 files).
Method: Read + `make grep` only. No test runs.
Date: 2026-06-16.

---

## 1. Confirmed Duplicate Pairs

### Table

| Duplicate pair | Canonical (keep) | Remove / merge | Evidence | Risk |
|---|---|---|---|---|
| `orchestration/pipeline_controller.py` VS `pipeline/controller.py` | `pipeline/controller.py` | Remove `orchestration/pipeline_controller.py` | Daemon imports `pipeline.controller`; unit test `test_pipeline_controller.py` still imports `orchestration.pipeline_controller` | `test_pipeline_controller.py` will break on removal; must be re-pointed first |
| `issue_sources/csv_excel.py` (`CsvExcelSource`) VS `issue_sources/excel_csv.py` (`ExcelCsvSource`) | `issue_sources/csv_excel.py` | Remove `issue_sources/excel_csv.py` | `CsvExcelSource` subclasses the `IssueSource` base; `ExcelCsvSource` is standalone with different config keys (`root` required) and a different public API (`fetch_issues`/`update_status` vs `fetch`/`write_back`) | Both have their own test files; removal of `excel_csv.py` means killing `test_issue_source_excel_csv.py` too, or merging its path-safety/confinement tests into csv_excel tests |
| `issue_sources/markdown_source.py` (`MarkdownSource`) VS `issue_sources/markdown_todo.py` (`MarkdownTodoSource`) | `issue_sources/markdown_todo.py` | Remove `issue_sources/markdown_source.py` | `MarkdownTodoSource` has a test (`test_issue_source_markdown.py`); `MarkdownSource` has **zero references** anywhere in the codebase — no test, no import, no registry entry | Zero-risk removal of `markdown_source.py`; it is completely dead code |
| `infra/model_deploy_check.py` (`MisconfigDetector`) VS `infra/misconfig_detector.py` (`MisconfigDetector` + `detect()`) | `infra/misconfig_detector.py` | Remove `infra/model_deploy_check.py` | Both define a class named `MisconfigDetector`. `misconfig_detector.py` is the richer implementation: uses `deployment_optimizer.HardwareProfile`/`ModelProfile`, defines a `detect()` module-level function, and uses a `PatchFn`/`Remediation` object graph. `model_deploy_check.py` is self-contained stdlib-only with a simpler rule set (rules a-l, no profiles). Both have their own test files. | `test_model_deploy_check.py` imports `model_deploy_check.MisconfigDetector` — will break; must migrate tests |
| `connectors/windows_event.py` (`WindowsEventSource`) VS `connectors/windows_event_log.py` (`WindowsEventLogSource`) | `connectors/windows_event_log.py` | Remove `connectors/windows_event.py` | `WindowsEventLogSource` has richer test coverage (26 tests vs 12), validates channels, uses a `(returncode, stdout, stderr)` runner tuple. `WindowsEventSource` uses a simpler `argv -> str` runner. Neither is registered in the connector registry currently — both are test-only-exercised. | Separate test files; `test_connector_windows_event.py` must be removed or merged |
| `connectors/docker_api.py` (`DockerApiSource`) VS `connectors/docker_engine.py` (`DockerEngineSource`) | `connectors/docker_engine.py` | Remove `connectors/docker_api.py` | `DockerEngineSource` has richer test coverage (22 tests), a `Response` dataclass transport abstraction, and an `is_safe_endpoint` SSRF guard. `DockerApiSource` has a simpler protocol-based transport (12 tests), supports both socket and TCP endpoints via same class. Neither is registered in the connector registry. | Two test files exist; `test_connector_docker_api.py` must be removed or merged |
| `connectors/tempo.py` (`TempoSource`) + `connectors/zipkin.py` (`ZipkinSource`) VS `connectors/tempo_zipkin.py` (`TempoZipkinSource`) | `connectors/tempo_zipkin.py` | Remove `tempo.py` and `zipkin.py` (or make them thin re-exports) | `TempoZipkinSource` unifies both in one class via `spec['flavor']`. `TempoSource` and `ZipkinSource` are standalone with their own tests. All three are functional; none is registered in the registry. | Three test files exist (`test_connector_tempo.py`, `test_connector_zipkin.py`, `test_connector_tempo_zipkin.py`); the standalone tests have more edge cases — merge before removing |
| `issue_sources/github_issues.py`: two classes in one file | `GitHubIssuesSource` (IssueSource subclass) | Remove `GitHubIssueSource` (standalone class, kept as "backward compat") | File comment at line 344 says "older GitHubIssueSource is kept for backward compatibility." But there is no external caller — only `test_issue_source_github.py` imports it. `GitHubIssuesSource` is the standard adapter. | Low: `test_issue_source_github.py` must be retired or migrated to `GitHubIssuesSource` |

---

## 2. Detailed Findings Per Pair

### 2.1 Pipeline Controller

**`orchestration/pipeline_controller.py`** — 426 lines. Pure synchronous protocol-based model. `PipelineController.tick()` takes explicit `backlog`/`completed`/`integrated` lists; uses Protocol-injected fakes. No asyncio, no daemon coupling. Self-contained; good for algorithm unit tests.

**`pipeline/controller.py`** — 223 lines (+ `pipeline/lanes.py` 445 lines). Real async implementation: `asyncio.create_task`, `asyncio.Lock`, `DispatchLane`/`IntegrateLane`/`GateLane` with `step()`/`run()` loops. PID-provider integration. **This is what `daemon.py` actually uses** (`_build_pipeline_controller` at line 441 imports `general_ludd.pipeline.controller`).

**Verdict:** `orchestration/pipeline_controller.py` is a design-time model that was superseded by the real async implementation in `pipeline/`. The test `tests/unit/test_pipeline_controller.py` still imports from `orchestration.pipeline_controller` and must be updated to point at `pipeline.controller` or deleted (the `test_pipeline_lanes.py` covers the real implementation).

**Missing `__init__.py`:** `src/general_ludd/pipeline/` has no `__init__.py`. It imports fine as a package on CPython (namespace package) but many toolchains (mypy, pyright, some editable installs) require an explicit `__init__.py`. **Missing `__init__.py`.**

---

### 2.2 CSV / Excel Source

**`csv_excel.py` (`CsvExcelSource`):** Subclasses `IssueSource`. Config key is `path`. Column mapping via `config['columns']` dict (header-name → field). Uses `Transition.CLAIM`/`Transition.DONE`. `fetch()` + `write_back()` API. Tested by `test_issue_source_csv_excel.py` (52 tests).

**`excel_csv.py` (`ExcelCsvSource`):** Standalone (no base class). Requires `config['root']` (path-confinement) + `config['path']`. Column mapping via positional config keys (`id_col`, `title_col`, …). `fetch_issues()` + `update_status()` + `add_comment()` API. Tested by `test_issue_source_excel_csv.py` (17 tests). Has the stronger security feature: realpath-confinement to a root directory.

**Verdict:** `CsvExcelSource` is the canonical IssueSource-family adapter; `ExcelCsvSource` was written in parallel with a more restrictive (root-confined) security model and a different public API. The path-confinement logic from `ExcelCsvSource` is absent from `CsvExcelSource` — this is a **feature gap**, not just duplication. The cleanup pass must merge the `_confine(root, path)` guard into `CsvExcelSource` before deleting `ExcelCsvSource`.

---

### 2.3 Markdown Source

**`markdown_source.py` (`MarkdownSource`):** Subclasses `IssueSource`. Config key `path`. ID derived from `ordinal:text` hash. CLAIM is a no-op; DONE ticks checkbox. **Zero references anywhere — no import, no test, no registry entry.**

**`markdown_todo.py` (`MarkdownTodoSource`):** Standalone (no base class). Has path-confinement. IDs extracted from HTML comment `&lt;!--id:ABC--&gt;` or `(#42)` pattern first, falling back to SHA1. `fetch_issues()` / `update_status()` / `add_comment()` API. Tested by `test_issue_source_markdown.py` (20 tests).

**Verdict:** `markdown_source.py` is dead code. Delete it. `markdown_todo.py` is the canonical implementation.

---

### 2.4 Misconfig Detector / Model Deploy Check

**`misconfig_detector.py`:** Depends on `deployment_optimizer.HardwareProfile`/`ModelProfile`/`kv_cache_bytes`. Defines `Severity` (IntEnum), `Signal` (StrEnum), `Finding`, `Remediation`, `MisconfigRule`, `detect()` + `DEFAULT_RULES`. Rich machine-readable config patches via `PatchFn`. Tested by `test_misconfig_detector.py` (imports from both `deployment_optimizer` and `misconfig_detector`).

**`model_deploy_check.py`:** Stdlib-only, no profile dependency. Defines `MisconfigDetector` class with `check()` + `remediate()`. `Finding` shape: `rule_id, severity: str, engine: str, message, remediation, evidence`. Implements rules a-l (vllm + llamacpp). Tested by `test_model_deploy_check.py` (50+ tests).

**`deployment_optimizer.py`:** Defines `HardwareProfile`, `ModelProfile`, `kv_cache_bytes`, `recommend_config`, `GPU_TABLE`, `CLOUD_INSTANCE_TABLE`. Used by `misconfig_detector.py` only. Tested by `test_deployment_optimizer.py`.

**Verdict:** Two separate implementations of the same feature (`MisconfigDetector` class name collision). `model_deploy_check.py` is the more complete, standalone, better-tested implementation. `misconfig_detector.py` is richer in rule object model (Remediation, PatchFn) but less tested directly. The canonical should be decided by the team — but whichever wins, the two `MisconfigDetector` classes cannot coexist. Both `deployment_optimizer.py` and `misconfig_detector.py` together form one cohesive unit (misconfig references optimizer); `model_deploy_check.py` is the divergent standalone. Recommended canonical: **`model_deploy_check.py`** (self-contained, more tests), but it needs the `PatchFn`/`Remediation` object model from `misconfig_detector.py` merged in.

---

### 2.5 Windows Event Log Connector

**`windows_event.py` (`WindowsEventSource`):** Runner signature `(argv: list[str]) -> str`. Defaults to `wevtutil`. Normalizes to gludd record shape. 12 tests.

**`windows_event_log.py` (`WindowsEventLogSource`):** Runner signature `(argv) -> (returncode, stdout, stderr)` tuple. Supports both `powershell` and `wevtutil`. More thorough injection validation. 26 tests. Has shell-injection guard with metacharacter set.

**Verdict:** `windows_event_log.py` is the canonical. The `WindowsEventSource` runner API is a subset and lacks the `returncode` signal. Delete `windows_event.py` + `test_connector_windows_event.py` after verifying no registry references.

---

### 2.6 Docker Engine Connector

**`docker_api.py` (`DockerApiSource`):** Protocol-based transport (`transport.get(path, params)`). Supports socket and TCP. Exports `KIND_LOGS`/`KIND_EVENTS`. 12 tests. Simpler transport interface.

**`docker_engine.py` (`DockerEngineSource`):** Transport callable `(method, path, query, base_url, timeout) -> Response`. `Response` dataclass. Default stdlib socket/TCP transport built in. More complete SSRF guard. 22 tests. Has `Connector` alias for registry compatibility.

**Verdict:** `docker_engine.py` is the canonical (richer transport, more tests, `Connector` alias). Delete `docker_api.py` + `test_connector_docker_api.py` after verifying no registry references.

---

### 2.7 Tempo + Zipkin vs TempoZipkinSource

**`tempo.py`** and **`zipkin.py`** are independent connectors, each with their own SSRF guard, normalization, and test suite. **`tempo_zipkin.py`** is a unified class that dispatches on `spec['flavor']`.

**Verdict:** This is *consolidation without deletion* — all three are present. The standalone `TempoSource`/`ZipkinSource` classes are functional and tested. `TempoZipkinSource` was added as a combined alternative. The risk is that the registry accepts three module names for what is logically one capability. Decide: keep the standalone pairs (easier independent config) or the combined class (less duplication). The standalone pair has 63 total tests vs 28 for `tempo_zipkin`. **Recommend keeping standalones and deleting `tempo_zipkin.py`**, or making `tempo_zipkin.py` a thin adapter over them.

---

### 2.8 GitHubIssueSource (singular) vs GitHubIssuesSource (plural)

Both classes live in `issue_sources/github_issues.py`. `GitHubIssueSource` (singular, line 135) is the standalone non-IssueSource adapter. `GitHubIssuesSource` (plural, line 350) subclasses `IssueSource` and is the standard-family adapter. The file comment at line 344 says the singular is kept for backward compat, but it is only used by `test_issue_source_github.py` — no production path imports it.

**Verdict:** Remove `GitHubIssueSource` (singular) from the file and delete `test_issue_source_github.py`. The `GitHubIssuesSource` path is the correct one.

---

## 3. Missing `__init__.py` (package wiring gaps)

| Package | `__init__.py` exists | Impact |
|---|---|---|
| `src/general_ludd/pipeline/` | **Missing** | mypy/pyright may refuse to resolve; namespace-package behaviour differs across tools |
| `src/general_ludd/issue_sources/` | **Missing** | Same risk; the many new modules are unreachable via `from general_ludd.issue_sources import X` |
| `src/general_ludd/orchestration/` | **Missing** | The orchestration package was used for the duplicate `pipeline_controller`; once that is removed, this package may be empty |
| `src/general_ludd/infra/` | Present | `__init__.py` exists but does NOT re-export `misconfig_detector`, `model_deploy_check`, or `deployment_optimizer` — they are accessible only via direct module path |

---

## 4. Modules with No Test

| Module | Status |
|---|---|
| `issue_sources/markdown_source.py` | **Zero references** — no test, no import. Dead code. |
| `issue_sources/excel_csv.py` | Has `test_issue_source_excel_csv.py` but no wiring to the IssueSource registry |
| `issue_sources/markdown_todo.py` | Has `test_issue_source_markdown.py`; not in registry |
| `connectors/windows_event.py` | Has `test_connector_windows_event.py` but duplicate of `windows_event_log.py` |
| `connectors/docker_api.py` | Has `test_connector_docker_api.py` but duplicate of `docker_engine.py` |
| `connectors/tempo_zipkin.py` | Has `test_connector_tempo_zipkin.py` but overlaps `tempo.py`+`zipkin.py` |
| `infra/misconfig_detector.py` | Has `test_misconfig_detector.py`; not wired to daemon |
| `infra/model_deploy_check.py` | Has `test_model_deploy_check.py`; not wired to daemon |
| `infra/deployment_optimizer.py` | Has `test_deployment_optimizer.py`; used only by `misconfig_detector.py` |

---

## 5. Tests with No Canonical Module

| Test file | Imports | Issue |
|---|---|---|
| `tests/unit/test_pipeline_controller.py` | `general_ludd.orchestration.pipeline_controller` | Imports the **orphan** controller, not the real async one in `pipeline/`; will fail if `orchestration/pipeline_controller.py` is deleted |
| `tests/unit/test_issue_source_github.py` | `general_ludd.issue_sources.github_issues.GitHubIssueSource` | Tests the "backward compat" standalone class; should be migrated to `GitHubIssuesSource` |
| `tests/unit/test_connector_windows_event.py` | `general_ludd.connectors.windows_event.WindowsEventSource` | Tests the orphan connector |
| `tests/unit/test_connector_docker_api.py` | `general_ludd.connectors.docker_api.DockerApiSource` | Tests the orphan connector |

---

## 6. Cleanup Priority Order

1. **Immediate / zero-risk:** Delete `issue_sources/markdown_source.py` (zero refs).
2. **Low-risk:** Delete `connectors/windows_event.py` + `test_connector_windows_event.py` (no registry refs; `windows_event_log.py` is the richer replacement).
3. **Low-risk:** Delete `connectors/docker_api.py` + `test_connector_docker_api.py` (no registry refs; `docker_engine.py` is richer).
4. **Moderate — update test first:** Migrate `test_pipeline_controller.py` to import from `pipeline.controller`/`pipeline.lanes`, then delete `orchestration/pipeline_controller.py`.
5. **Moderate — feature merge first:** Merge `ExcelCsvSource`'s `_confine(root, path)` security guard into `CsvExcelSource`, then delete `excel_csv.py` + `test_issue_source_excel_csv.py`.
6. **Moderate:** Decide `GitHubIssueSource` (singular) fate; migrate `test_issue_source_github.py` to `GitHubIssuesSource`, then remove the class.
7. **Hard — design decision:** Consolidate `model_deploy_check.py` vs `misconfig_detector.py` + `deployment_optimizer.py`. They implement the same contract with different depth. Need an explicit "winner" before deletion.
8. **Hard — design decision:** Decide `tempo.py`+`zipkin.py` vs `tempo_zipkin.py`. Standalone pair has more tests.
9. **Infra:** Add `__init__.py` to `src/general_ludd/pipeline/` and `src/general_ludd/issue_sources/`.

---

## 7. Summary Counts

- Confirmed duplicate pairs: **8** (7 module-level pairs + 1 two-class-in-one-file)
- Dead code (zero refs): **1** (`markdown_source.py`)
- Missing `__init__.py` in committed packages: **2** critical (`pipeline/`, `issue_sources/`), 1 likely empty (`orchestration/`)
- Tests pointing at orphan modules: **4**
- Modules not wired to daemon or registry despite having tests: **6**
