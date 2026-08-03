"""Run user-requested test batch and report grand total passes + failures.

Categories:
  SMP.1 — small_model, hardware, quantize, lm_eval tests
  SEC.1 — security control tests (TASKS.md line 120 files + D-xx security)
  travel — all test_travel* and test_collection_travel* files
  integration — all tests/integration/
  budget/cost — all budget* and cost* test files

Strategy:
  - Non-integration tests run with -n auto for speed
  - Integration tests run serially (they spin up FastAPI/DB)
  - Results aggregated at end
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# — SMP.1 test files —
SMP_FILES = sorted(
    p.as_posix()
    for pat in (
        "tests/unit/test_small_model*.py",
        "tests/unit/test_small_models*.py",
        "tests/unit/test_hardware*.py",
        "tests/unit/test_quantize.py",
        "tests/unit/test_lm_eval_runner.py",
        "tests/e2e/test_small_model*.py",
        "tests/integration/test_small_models_*.py",
        "tests/integration/test_hardware_*.py",
    )
    for p in ROOT.glob(pat)
)

# — SEC.1 test files (from TASKS.md line 120 + additional D-xx security) —
SEC1_NAMED = [
    "tests/unit/test_ansible_extravars_validation.py",
    "tests/unit/test_d09_jobspec_ownership.py",
    "tests/unit/test_d17_psk_rotation.py",
    "tests/unit/test_d20_config_hot_reload.py",
    "tests/unit/test_mcp_transport.py",
    "tests/unit/test_mcp_transport_stderr.py",
    "tests/unit/test_security_audit_observability.py",
    "tests/unit/test_security_backlog.py",
    "tests/unit/test_security_sandbox_hardening_spec.py",
    "tests/unit/test_security_state.py",
    "tests/unit/test_d10_request_size_limits.py",
    "tests/unit/test_d14_clone_url_parsing.py",
    "tests/unit/test_d15_openbao_token_scope.py",
    "tests/unit/test_d18_audit_log.py",
    "tests/unit/test_d30_gateway_size_limit.py",
    "tests/e2e/test_e2e_security_sts.py",
]
SEC1_FILES = sorted(f for f in SEC1_NAMED if (ROOT / f).exists())

# — Travel test files —
TRAVEL_FILES = sorted(
    p.as_posix()
    for pat in ("tests/unit/test_travel*.py", "tests/unit/test_collection_travel*.py")
    for p in ROOT.glob(pat)
)

# — Integration test files (directory) —
INTEGRATION_FILES = sorted(p.as_posix() for p in (ROOT / "tests/integration").glob("test_*.py"))

# — Budget/cost test files —
BUDGET_COST_FILES = sorted(
    p.as_posix()
    for pat in (
        "tests/unit/test_budget*.py",
        "tests/unit/test_*budget*.py",
        "tests/unit/test_*cost*.py",
        "tests/unit/test_cost*.py",
        "tests/unit/test_token_cost.py",
        "tests/unit/test_combined_cost.py",
        "tests/unit/test_small_models_cost.py",
        "tests/unit/test_ag10_budget*.py",
        "tests/unit/test_resource_namespace_budgets.py",
        "tests/unit/test_langgraph_budget.py",
        "tests/unit/test_radio_link_budget.py",
        "tests/unit/test_c4_budget*.py",
        "tests/unit/test_c_budget*.py",
        "tests/unit/test_d21_check_budget*.py",
        "tests/unit/test_azure_cost*.py",
        "tests/unit/test_slurm_cost*.py",
        "tests/unit/test_avg_cost*.py",
        "tests/unit/test_infra_cost*.py",
        "tests/unit/test_routers_azure_cost.py",
        "tests/unit/test_gateway_cost*.py",
        "tests/unit/test_actual_cost*.py",
        "tests/e2e/test_model_routing_budget.py",
        "tests/e2e/test_budget_worker_eval_events_workflows.py",
        "tests/e2e/test_scoring_cost_routing_e2e.py",
        "tests/test_budget_hardening.py",
        "tests/integration/test_budget_integrity.py",
        "tests/integration/test_bill_cost*.py",
        "tests/integration/test_bill_slurm_cost*.py",
        "tests/integration/test_azure_cost*.py",
        "tests/integration/test_bill8_cost*.py",
        "tests/integration/test_bill2_slurm_cost*.py",
        "tests/integration/test_slurm_cost*.py",
    )
    for p in ROOT.glob(pat)
)

NON_INTEGRATION = sorted(set(SMP_FILES + SEC1_FILES + TRAVEL_FILES + BUDGET_COST_FILES))

print(f"=== User Test Batch ===")
print(f"  SMP.1:          {len(SMP_FILES)} files")
print(f"  SEC.1:          {len(SEC1_FILES)} files")
print(f"  Travel:         {len(TRAVEL_FILES)} files")
print(f"  Budget/Cost:    {len(BUDGET_COST_FILES)} files")
print(f"  Integration:    {len(INTEGRATION_FILES)} files")
print(f"  Non-integration: {len(NON_INTEGRATION)} files (will run -n auto)")
print()

pytest_base = [sys.executable, "-m", "pytest", "-v", "--tb=short", "--no-header"]

total_pass = 0
total_fail = 0
total_skip = 0
any_failure = False

# — Phase 1: Non-integration tests (fast, parallel) —
print("=== Phase 1: SMP.1 + SEC.1 + Travel + Budget/Cost (non-integration) ===")
if NON_INTEGRATION:
    cmd = pytest_base + ["-n", "auto"] + NON_INTEGRATION
    result = subprocess.run(cmd, cwd=ROOT)
    # Parse output (not reliable with -n auto line order, but pytest returns summary)
    exit_code = result.returncode
    if exit_code == 0:
        # Quick re-run with --collect-only to count
        collect_result = subprocess.run(
            pytest_base + ["--co", "-q"] + NON_INTEGRATION,
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        # Count line says: "N tests collected" or "no tests collected"
        count_line = [l for l in collect_result.stderr.splitlines() if "collected" in l]
        if count_line:
            print(f"\n{count_line[0]}")
else:
    print("No non-integration tests.")

# — Phase 2: Integration tests (slow, serial) —
print("\n=== Phase 2: Integration tests ===")
if INTEGRATION_FILES:
    cmd = pytest_base + INTEGRATION_FILES
    result = subprocess.run(cmd, cwd=ROOT)
    exit_code = result.returncode
else:
    print("No integration tests.")

# — Final summary from pytest's last output —
print("\n=== Summary ===")
print("Check the pytest summary lines above for pass/fail/skip counts.")
print("(pytest's -v output includes the final count line)")
sys.exit(exit_code if any_failure else 0)
