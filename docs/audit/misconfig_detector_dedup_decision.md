# MisconfigDetector Dedup Decision

Pair #4 from `batch3_dedup_coherence.md`.
Analysis date: 2026-06-16.

---

## 1. Verdict

**Canonical: `src/general_ludd/infra/model_deploy_check.py`**
**Delete: `src/general_ludd/infra/misconfig_detector.py`**

Rationale: `model_deploy_check.py` is stdlib-only, implements 11 rules (a-g + h/i/j/k/l superset)
vs 7 rules in `misconfig_detector.py`, has 50+ tests vs ~27, and its `MisconfigDetector.check()`
interface is simpler (no typed profile objects required). Neither module is imported by daemon or
any live/role code — both are test-only. The richer object model in `misconfig_detector.py`
(Signal enum, PatchFn callable, MisconfigRule registry) is portable and can be merged as a
follow-on if needed. The decisive tiebreaker is test coverage and rule completeness.

---

## 2. Importer Census

Search scope: all of `src/` and `tests/`. Method: Read + Glob (no shell).

| Importer | Module imported | Purpose |
|---|---|---|
| `tests/unit/test_model_deploy_check.py` | `general_ludd.infra.model_deploy_check` | Unit tests for canonical |
| `tests/unit/test_misconfig_detector.py` | `general_ludd.infra.misconfig_detector` + `deployment_optimizer` | Unit tests for loser |
| `src/general_ludd/infra/__init__.py` | (neither) | Does not re-export either module |
| `src/general_ludd/daemon.py` | (neither) | No reference to either module |
| All other src/ and tests/ files | (neither) | No references found |

Both modules are unreachable from the infra package public API (`infra/__init__.py` exports
`ComputeConfig`, `DeploymentManager`, etc. — neither misconfig module is listed). Neither is
wired to the daemon event loop, preflight, or any role/orchestration file.

---

## 3. Capabilities in the Loser That Must Be Ported First

The following exist only in `misconfig_detector.py` and are absent from `model_deploy_check.py`.
They MUST be evaluated before deletion and ported if needed.

### 3a. Rule d — CPU offload / swap thrash (MUST PORT)

`misconfig_detector.py` implements rule `d` which fires when `cpu_offload_gb > 0` or
`swap_space > 0` AND runtime signals show throughput collapse or host RAM/swap pressure.
Patch: `{"cpu_offload_gb": 0, "swap_space": 0, "quantization": "fp8"|"awq"}`.

`model_deploy_check.py` has NO equivalent rule. This is the only rule gap; all other
`misconfig_detector.py` rules (a-g) are covered by `model_deploy_check.py` (a-g in addition
to the h-l superset). Port rule d into `model_deploy_check.py` before deleting the loser.

Port specification:
- Add a `_check_vllm_offload` sub-method (or inline in `_check_vllm`) that checks
  `dep.get("cpu_offload_gb", 0) > 0 or dep.get("swap_space", 0) > 0` AND optionally
  accepts a `metrics` dict for the signal-based pressure check.
- Finding shape: `rule_id="d"`, `severity="warn"`, `engine="vllm"`, message describing
  offload thrash, remediation string, evidence with `cpu_offload_gb`/`swap_space` values.
- Add `_patch_d` in `_REMEDIATIONS`: return `{"cpu_offload_gb": 0, "swap_space": 0}` +
  `"quantization": "awq"` (conservative; no arch check needed for the canonical).
- Add tests analogous to `TestRuleD` in `test_misconfig_detector.py`:
  `test_rule_d_offload_fires`, `test_rule_d_no_offload_silent`.

### 3b. Signal enum and typed-profile API (DEFER, no immediate port required)

`misconfig_detector.py` exposes:
- `Severity` (IntEnum: INFO=10, WARNING=20, ERROR=30, FATAL=40) — ordered for threshold checks
- `Signal` (StrEnum: 14 named observability signals)
- `PatchFn` / `PredicateFn` type aliases
- `Remediation` dataclass (`config_patch: PatchFn` callable, `requires_restart`, `summary`)
- `MisconfigRule` dataclass (pluggable rule registry with `predicate`, `remediation`, `signal`)
- `detect(config, hardware: HardwareProfile, model: ModelProfile, metrics)` function
- `DEFAULT_RULES` list

None of these are used outside `test_misconfig_detector.py`. `model_deploy_check.py` uses
`severity: str` ("info"/"warn"/"critical") and callable `_patch_*` builders stored in
`_REMEDIATIONS`. The typed-profile API (`HardwareProfile`, `ModelProfile`) is richer but
requires the `deployment_optimizer` dependency; the canonical avoids this by using raw dicts.

Decision: DEFER. Do not port these abstractions unless a future caller specifically needs
the typed-profile interface or `MisconfigRule` pluggability. The simpler raw-dict interface
in `model_deploy_check.py` covers all current test scenarios.

### 3c. `deployment_optimizer.py` fate

`deployment_optimizer.py` is imported ONLY by `misconfig_detector.py`. Once
`misconfig_detector.py` is deleted, `deployment_optimizer.py` has no remaining importer in
`src/` (confirmed: `infra/__init__.py` does not import it; daemon does not import it).
It is exercised by `test_deployment_optimizer.py`. Options:
- Keep it: low harm, has its own tests, may be useful standalone.
- Delete it: it becomes dead code once `misconfig_detector.py` is removed.
Recommended: KEEP `deployment_optimizer.py` and `test_deployment_optimizer.py` intact. The
optimizer is a standalone useful module. Only `misconfig_detector.py` is deleted.

---

## 4. Migration Plan (ordered steps)

Execute these steps in sequence. Do NOT delete before porting.

### Step 1 — Port rule d into `model_deploy_check.py`

File to edit: `src/general_ludd/infra/model_deploy_check.py`

Add to `_check_vllm()` (after the existing rule l block, before `return findings`):

```python
# Rule d: CPU offload / swap thrash.
cpu_offload = _as_float(dep.get("cpu_offload_gb")) or 0.0
swap_space = _as_float(dep.get("swap_space")) or 0.0
if cpu_offload > 0 or swap_space > 0:
    findings.append(
        Finding(
            rule_id="d",
            severity="warn",
            engine="vllm",
            message=f"cpu_offload_gb={cpu_offload} / swap_space={swap_space}: "
            "CPU-offload or swap-backed KV cache collapses throughput",
            remediation="set cpu_offload_gb=0 and swap_space=0; use a more aggressive "
            "quantization (awq / gptq-marlin) to fit the model in VRAM",
            evidence={"cpu_offload_gb": cpu_offload, "swap_space": swap_space},
        )
    )
```

Add to `_REMEDIATIONS`:

```python
def _patch_d(f: Finding) -> tuple[dict[str, Any], bool]:
    return {"cpu_offload_gb": 0, "swap_space": 0, "quantization": "awq"}, True
```

And register: `"d": _patch_d` in the `_REMEDIATIONS` dict.

### Step 2 — Add tests for rule d in `test_model_deploy_check.py`

File to edit: `tests/unit/test_model_deploy_check.py`

Add two test functions after the existing rule c tests:

```python
def test_rule_d_cpu_offload_fires() -> None:
    cfg = _good_vllm()
    cfg["cpu_offload_gb"] = 40
    findings = MisconfigDetector().check(cfg, _good_vllm_gpu())
    assert "d" in _rule_ids(findings)


def test_rule_d_swap_space_fires() -> None:
    cfg = _good_vllm()
    cfg["swap_space"] = 16
    findings = MisconfigDetector().check(cfg, _good_vllm_gpu())
    assert "d" in _rule_ids(findings)


def test_rule_d_silent_on_good() -> None:
    findings = MisconfigDetector().check(_good_vllm(), _good_vllm_gpu())
    assert "d" not in _rule_ids(findings)
```

### Step 3 — Re-point `test_misconfig_detector.py` to canonical OR delete it

`test_misconfig_detector.py` tests the richer `detect()` / `DEFAULT_RULES` / `Signal` /
`Severity` / `Remediation` / `MisconfigRule` object model that is NOT being ported.
Since those abstractions are deferred (section 3b), the cleanest path is:

**Delete `tests/unit/test_misconfig_detector.py`** — its coverage is subsumed by
`test_model_deploy_check.py` after rule d is ported. The only unique coverage it provides
(typed-profile API, `detect()` function, `DEFAULT_RULES` list, Signal enum) tests code
that will no longer exist.

If the `detect()` / typed-profile API is ported in a future task, a new test file should
be created at that time against the canonical.

### Step 4 — Delete `src/general_ludd/infra/misconfig_detector.py`

After steps 1-3 are complete and tests pass (run `make test-unit`):

Delete: `src/general_ludd/infra/misconfig_detector.py`

### Step 5 — Verify no remaining references

After deletion, confirm no import of `misconfig_detector` remains:
Run `make grep Q="misconfig_detector"` — should return zero matches outside deleted files.

### Step 6 — No changes needed to `infra/__init__.py`

Neither module was exported from `infra/__init__.py`. No change required.

---

## 5. Five-Line Summary

Canonical is `model_deploy_check.py`: stdlib-only, 11 rules (a-l), 50+ tests, simpler
raw-dict interface; `misconfig_detector.py` is the loser (7 rules, typed-profile dependency,
~27 tests, no live importers). Neither is wired to daemon or any production path. Before
deletion, port rule d (CPU offload/swap thrash) from the loser into the canonical — it is
the only rule gap. Delete `test_misconfig_detector.py` alongside the loser since its unique
coverage (Signal/Severity/detect() typed-profile API) tests code that will not be ported.
Keep `deployment_optimizer.py` intact: it becomes a standalone module once its only importer
(`misconfig_detector.py`) is gone.
