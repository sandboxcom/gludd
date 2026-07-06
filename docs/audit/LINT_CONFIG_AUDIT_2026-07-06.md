# Lint Configuration Audit — 2026-07-06

**Scope:** Every file in `/Users/shawnwilson/gludd/` where lint, type, or coverage rules are configured or could be bypassed.
**Mode:** Read-only audit — no files modified.

---

## 1. Lint / Type / Coverage config locations

| File | Status | Notes |
|---|---|---|
| `pyproject.toml` | **PRIMARY** | All ruff, mypy, coverage, and pytest config lives here (lines 145–209). |
| `.ruff.toml` | not present | — |
| `ruff.toml` | not present | — |
| `mypy.ini` | not present | — |
| `.coveragerc` | not present | — |
| `setup.cfg` | not present | — |
| `tox.ini` | not present | — |
| `.flake8` / `flake8` | not present | — |
| `.pylintrc` / `pylintrc` | not present | — |
| `.pre-commit-config.yaml` | **not present** | Hooks installed via `make install-hooks` but the config file is not in the repo (relies on pre-commit defaults; see §5). |

**Verdict: single source of truth (`pyproject.toml`).** No competing config files; no shadow configs that could weaken the gate.

---

## 2. Effective rules from `pyproject.toml`

### `[tool.ruff]` (lines 145–154)
```toml
target-version = "py311"
src = ["src", "tests"]
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "I", "W", "UP", "B", "SIM", "RUF"]   # pycodestyle-E, pyflakes-F, isort-I, pycodestyle-W,
                                                          # pyupgrade-UP, bugbear-B, simplify-SIM, ruff-RUF
[tool.ruff.lint.isort]
known-first-party = ["general_ludd"]
```

- **No `ignore = [...]` block.** No global rule disables.
- **No `per-file-ignores` table.** No centralized allowlist for specific files.
- **No `extend-exclude` / `exclude`.** Ruff uses its default exclusion list (`.git`, `.venv`, `__pycache__`, `build`, `dist`, etc.) — no project paths are opted out.

### `[tool.mypy]` (lines 156–178)
```toml
python_version = "3.11"
strict = true
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
exclude = ["src/general_ludd/security/sandboxes/.*"]

[[tool.mypy.overrides]]
module = ["ansible.*", "hvac.*", "psycopg.*", "yaml.*", "tree_sitter.*",
          "tree_sitter_python.*", "huggingface_hub.*", "diskcache.*",
          "watchdog.*", "croniter.*", "jsonschema", "googleapiclient.*",
          "google.*", "azure.*"]
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = "general_ludd.ansible.core_runner"
disable_error_code = ["misc"]

[[tool.mypy.overrides]]
module = "general_ludd.observability.metrics_exporter"
disable_error_code = ["type-arg"]
```

⚠ **Two escape hatches in mypy config** (see §6 recommendations):
1. `exclude = ["src/general_ludd/security/sandboxes/.*"]` — an entire subsystem bypasses strict mypy. Documented as in-flight parallel work; re-enabling is the completion gate for that work.
2. `disable_error_code` for two specific modules (`core_runner`, `metrics_exporter`).

The `ignore_missing_imports` list is appropriate (third-party stubs missing for ansible/hvac/psycopg/yaml/etc.).

### `[tool.coverage.run]` and `[tool.coverage.report]` (lines 197–209)
```toml
[tool.coverage.run]
source = ["general_ludd"]
omit = ["tests/*"]

[tool.coverage.report]
fail_under = 70
show_missing = true
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "raise NotImplementedError",
    "pass",
]
```

⚠ **Coverage `fail_under = 70` is low.** `AGENTS.md` references an 85% per-file threshold via `make gate-audit`; the global gate only enforces 70%. Coverage is also **only collected for `general_ludd` source** (tests/* omitted — correct), but production code can fall to 70% without failing `make gate`.

### `[tool.pytest.ini_options]` (lines 180–195)
```toml
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-v --tb=short"
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
timeout = 180
timeout_method = "signal"
markers = [
    "xdist_group: serialize tests that share a fixed resource",
]
```

No skip/xfail allowlist via markers; timeout guard is universal (180s).

---

## 3. What `make lint`, `make typecheck`, and `make gate` actually run

From the `Makefile`:

| Target | Recipe | What it actually lints |
|---|---|---|
| `lint` | `uv run ruff check src tests` | **ALL of `src/` and `tests/`** — no path exclusions. |
| `lint-fix` | `uv run ruff check --fix --unsafe-fixes src tests` | Same scope. |
| `typecheck` | `uv run mypy src` | **Only `src/`**. `tests/` is **NOT** type-checked. |
| `gate` | `ruff check src tests` + `mypy src` + `collect-check` + `pytest tests/` + `smoke` | Lints src+tests, typechecks src only, runs all tests. Writes `.gate-status`. |

⚠ **Gap:** `tests/` is never type-checked by `make typecheck` or `make gate`. Test code can contain type errors that mypy strict mode would catch in production code. Common practice, but worth noting as a coverage hole.

---

## 4. Every per-file ignore / inline `noqa` (file + rule + reason)

### 4.1 File-level directive (`# ruff: noqa: ...`) — **1 occurrence**

| File:line | Directive | Reason |
|---|---|---|
| `tests/unit/test_openrouter_discovery.py:3` | `# ruff: noqa: E501` | File-wide suppression of line-too-long. No inline justification. **Recommend investigate — likely long URL/data lines that should be split or moved to fixtures.** |

### 4.2 Inline `# noqa` in `src/` — **3 real occurrences** (1 string literal excluded)

| File:line | Rule | Reason |
|---|---|---|
| `src/general_ludd/security/sandboxes/windows_appcontainer.py:90` | `F401` | `win32security` imported for side-effect detection; pywin32 is Windows-only and guarded by try/except. |
| `src/general_ludd/security/sandboxes/detect.py:119` | `F401` | `win32` imported for side-effect detection; pywin32 is Windows-only and guarded by try/except. |
| `src/general_ludd/security/sandboxes/linux_landlock.py:115` | `F401` | `landlock` imported for side-effect detection; pylandlock is Linux-only and guarded by try/except. |

> Note: `src/general_ludd/security/fix_not_disable.py:25` contains the literal string `"# noqa"` (it is data — a pattern the fix-not-disable policy detects). **NOT a real suppression.**

All three real `src/` occurrences are in the `security/sandboxes/` subtree — the same subtree that mypy excludes entirely (§6).

### 4.3 Inline `# noqa` in `tests/` — **20 real occurrences** (4 string-literal test-subjects excluded)

| File:line | Rule | Reason |
|---|---|---|
| `tests/integration/test_daemon_mcp_dispatch.py:97` | `F401` | Importability is the test — import only for side effect. |
| `tests/integration/test_daemon_mcp_dispatch.py:342` | `F401` | Importability is the test. |
| `tests/integration/test_rules_healthgate_integrity.py:100,140,164,201` | `F401` | `routing_roles` imported for side effects (warms import cycle). 4 occurrences. |
| `tests/integration/test_multimodel_routing.py:21` | `F401` | `routing_roles` for side effects. |
| `tests/unit/test_classify_agent_error.py:32` | `E402` | Module-level import after path manipulation (script-style test). |
| `tests/unit/test_scoring_integrity.py:22` | `F401` | `routing_roles` for side effects. |
| `tests/unit/test_git_locking.py:227` | `SIM117` | Nested `with` deliberately not collapsed (test clarity). |
| `tests/unit/test_zai_secrets_resolution.py:31` | `SIM112` | Lowercase env var name is the test subject. |
| `tests/unit/test_gen_status_table.py:29,43` | `E402` | Path manipulation before import. 2 occurrences. |
| `tests/unit/test_gateway_circuit_breaker.py:46` | `F401` | `routing_roles` for side effects. |
| `tests/unit/test_ansible_env_scrub.py:12` | `E402` | Path manipulation before import. |
| `tests/unit/test_circuit_breaker_fallback.py:24` | `F401` | `routing_roles` for side effects. |
| `tests/unit/test_tf_provider_cache.py:21` | `E402` | Path manipulation before import. |
| `tests/unit/test_multitasking_backlog.py:30` | `E402` | Path manipulation before import. |
| `tests/unit/test_skill_embeddings.py:218` | `F401` | `openai` import probe — importability is the assertion. |

> Note: `tests/unit/test_type_safety_guardrails.py:14–24` references `noqa` as a **test subject** (the test enforces that source files contain no `# noqa`). It is **not** a suppression itself. This test is a structural guardrail against the very pattern this audit catalogs.

**All test noqa occurrences are narrowly scoped line-level suppressions with documented reasons.** No file is wholesale exempted.

### 4.4 `# type: ignore` in `src/` — **40 occurrences** (mypy, not ruff)

| Category | Count | Justification |
|---|---|---|
| `[import-not-found]` for OS-specific / optional deps (`boto3`, `win32`, `landlock`, `selinux`, `pysnmp`, `redis`, `pymongo`, `pymysql`, `paho-mqtt`, `pynvml`, `openpyxl`, `msgpack`, `opentelemetry`, `langgraph.checkpoint`) | 35 | All are lazy imports inside `try/except ImportError` guards — the runtime semantics match the type-check suppression. |
| `[import-untyped]` for libraries without py.typed markers (`pymysql`, `msgpack`) | 3 | Library limitation, not project gap. |
| `[assignment]` / `[assignment, misc]` for stub fallbacks (`payment_vault.py` — `hashes = None` when `Hash` import fails) | 3 | Module-level sentinel pattern when optional dep absent. |
| `[attr-defined]` for `langchain.hub` dynamic attribute | 1 | Library API surface; standard escape hatch. |

All `type: ignore` calls are documented with inline comments naming the optional dependency and the guard pattern. None are bare `# type: ignore` without a code or justification.

---

## 5. Every `pytest.mark.skip` / `pytest.mark.xfail` / `@skip` / `@unittest.skip` (file + line + reason)

### 5.1 `pytest.mark.skipif` — **59 occurrences**

All 59 are **conditional `skipif` markers**, not unconditional skips. Breakdown by reason:

| Reason category | Count | Examples |
|---|---|---|
| Missing live API key (`_ZAI_KEY`, `_DEEPSEEK_KEY`, `_zai_api_key()`, `_get_zai_api_key()`) | 25 | `tests/e2e/test_pipeline_live_zai.py`, `tests/live/test_zai_*.py`, `tests/e2e/test_game_building_deepseek.py` |
| Missing optional binary on PATH (`git`, `terraform`, `docker buildx`) | 6 | `tests/unit/test_engine_git_hardening.py:190`, `tests/unit/test_terraform_validate.py:58,93`, `tests/unit/test_terraform_modules.py:177`, `tests/unit/test_local_model_templates.py:152`, `tests/unit/test_accounting_loc_provider.py:23` |
| Platform-specific (`sys.platform == "win32"` / `!= "linux"` / `!= "freebsd"` / `!= "darwin"` / Windows-only) | 10 | `tests/unit/test_sandbox_backends.py:79,106,115,146,166,190`, `tests/unit/test_abtest_runner.py:113`, `tests/unit/test_filestore_overlay_security.py:38` |
| Missing generated artifact (`dist/general-ludd.service`, installer paths) | 4 | `tests/unit/test_installer.py:11,14,19,57` |
| Missing optional env / dist service / ansible collection | 8 | `tests/integration/test_project_init_role.py:59`, `tests/integration/test_langgraph_decision_feature.py:71`, `tests/integration/test_agent_orchestrate_e2e.py:86`, `tests/integration/test_local_inference_integration.py:143`, `tests/integration/test_hf_model_integration.py:23,36,48,63,77`, `tests/unit/test_onboard_aws.py:322` |
| TUI/headless display env | 2 | `tests/e2e/test_tui_daemon_start.py:100`, `tests/e2e/dogfood/test_dogfood_todo_site.py:47` |
| Local model runtime env (`_RUN_LOCAL`) | 1 | `tests/e2e/test_local_model_discovery_eval.py:694` |
| Controllers/pause-store conditionals | 2 | `tests/controllers/test_pause_store.py:118,130` |
| ZAI model suitability scenario | 1 | `tests/e2e/test_model_suitability_scenarios.py:712` |

**All skipif markers gate on an environmental condition (key, binary, platform, artifact). None are unconditional skips.** This is the appropriate pattern — these tests run when their environment supports them and are visibly skipped otherwise.

### 5.2 `pytest.mark.xfail` — **0 real occurrences**

`tests/unit/test_backlog_auditor.py:169` contains the **string literal** `"@pytest.mark.xfail\n"` — it is test data fed into the backlog auditor (the auditor detects xfail markers in user code). **Not an actual xfail marker.**

### 5.3 `@skip` / `@unittest.skip` — **0 occurrences**

---

## 6. Recommendations — configurations that weaken the gate

Sorted by severity (highest first).

### HIGH — `mypy exclude` of `src/general_ludd/security/sandboxes/.*`

**Location:** `pyproject.toml:166`
**Impact:** An entire subsystem bypasses strict type checking. All three real `# noqa: F401` markers in `src/` live in this subtree — i.e. the same code that escapes ruff (for legitimate F401 reasons) also escapes mypy entirely.
**Documented?** Yes — inline comment says "Sandbox backends … are in-flight parallel work that references API surface not yet shipped on PermissionSpec/Capability (agent_id, constraint_value, Constraint class). Excluded until that schema lands; re-enabling is the parallel task's completion gate."
**Action:** Track the re-enablement as a task in `TASKS.md` if not already. The audit cannot verify whether the schema has since landed.

### MEDIUM — `fail_under = 70` for coverage

**Location:** `pyproject.toml:202`
**Impact:** Production code can drop to 70% coverage without failing the gate, even though `AGENTS.md` references an 85% per-file threshold via `make gate-audit`.
**Action:** Either raise `fail_under` to match `gate-audit`'s per-file threshold, or document why the two thresholds differ.

### MEDIUM — `tests/` not type-checked

**Location:** `Makefile:231–232` (`typecheck: uv run mypy src`)
**Impact:** Test code can carry type errors mypy strict mode would catch in production code. No `[[tool.mypy.overrides]]` for test modules exists; mypy simply never sees them.
**Action:** Consider `mypy src tests` (with per-module overrides for fixtures that intentionally misuse types). At minimum, document the omission as a deliberate scope choice.

### MEDIUM — File-level `# ruff: noqa: E501` in `tests/unit/test_openrouter_discovery.py:3`

**Location:** `tests/unit/test_openrouter_discovery.py:3`
**Impact:** Entire test file evades the line-length rule. No inline justification.
**Action:** Investigate the file. If it carries long URL/data lines, move them to fixtures or wrap them; otherwise remove the file-level suppression.

### LOW — `mypy disable_error_code` for two specific modules

**Location:** `pyproject.toml:172–178`
- `general_ludd.ansible.core_runner` — `["misc"]`
- `general_ludd.observability.metrics_exporter` — `["type-arg"]`
**Impact:** Narrow; each disables a single error code for a single module.
**Action:** Document the reason inline (no comment currently). The `type-arg` disable for `metrics_exporter` likely hides a missing type parameter on a generic — worth re-evaluating.

### LOW — Coverage `exclude_lines` includes `"pass"`

**Location:** `pyproject.toml:208`
**Impact:** Any line consisting solely of `pass` is excluded from coverage. This is appropriate for abstract method bodies, but can hide incomplete implementations.
**Action:** Audit `src/` for `pass` statements that are not in abstract-method / except-and-rethrow contexts.

### LOW — No `.pre-commit-config.yaml` in repo

**Location:** missing
**Impact:** `make install-hooks` runs `pre-commit install --install-hooks` but the config that defines *which* hooks run is not in the repo. Hooks rely on pre-commit defaults or a config generated out-of-band. A fresh clone has no lint hook until someone runs `make install-hooks`, and even then the config is implicit.
**Action:** Commit a `.pre-commit-config.yaml` that wires `ruff check`, `ruff format --check`, `detect-secrets`, and the collection-check hook into the standard pre-commit flow. This makes the gate reproducible outside `make`.

---

## 7. Summary verdict — does the gate actually lint everything?

| Question | Answer |
|---|---|
| Does `make lint` cover ALL of `src/`? | **YES.** `ruff check src` — no path excludes in config. |
| Does `make lint` cover ALL of `tests/`? | **YES.** `ruff check src tests`. |
| Does `make typecheck` cover ALL of `src/`? | **NO — one exclude.** `src/general_ludd/security/sandboxes/.*` bypasses mypy. |
| Does `make typecheck` cover `tests/`? | **NO.** `mypy src` only. |
| Are there global rule ignores (ruff `ignore = [...]`)? | **NO.** |
| Is there a `per-file-ignores` table? | **NO** (good). |
| Are there file-level `# ruff: noqa` directives? | **1** (`tests/unit/test_openrouter_discovery.py`). |
| Are inline `# noqa` justified? | **YES** — every real occurrence has an inline reason or obvious test-pattern context. |
| Are `skipif` markers unconditional? | **NO** — all 59 gate on environment / platform / artifact presence. |
| Any `pytest.mark.xfail` or `@unittest.skip`? | **0** real. |
| Any bare `# type: ignore` without a code? | **0** — all 40 carry an error code and an inline reason. |

**Bottom line: the lint gate (`make lint`) is airtight over `src/` + `tests/` — no path excludes, no per-file-ignores table, one file-level `noqa` to investigate. The typecheck gate has one documented subsystem exclude (`security/sandboxes/`) and does not check `tests/`. Coverage threshold (70%) is materially lower than the `gate-audit` threshold (85%). No silent escape hatches; the one exclude is documented as in-flight work.**

---

## Counts (for the audit return value)

- **Per-file ignores found:** 4 (1 file-level `# ruff: noqa` + 3 inline `# noqa` in `src/`). Inline `noqa` in tests/ add 20 more line-level suppressions (all justified).
- **Skip/xfail markers found:** 59 `skipif` (all conditional, no unconditional); 0 `xfail`; 0 `@unittest.skip`.
