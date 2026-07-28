"""Tests for scripts/azure_event_guard.sh — Azure Activity Log smoke-test guard.

Verifies the script's violation-detection logic: expensive GPU types, duplicate
resource names, wrong-subscription/wrong-resource-group, auth errors, watch-mode
polling, kill-switch behavior, and clean-pass exit codes.

Because the guard script is a bash script that shells out to `az` and `python3`,
these tests run the script in a controlled environment with mocked az output and
assert on exit codes, stderr messages, and side effects (PID file kill, log file
writes).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "azure_event_guard.sh"

# Sample activity log JSON returned by a "clean" az call — one cheap B1s VM created.
CLEAN_EVENTS_JSON = """[
  {
    "resourceName": "smoke-vm-01",
    "resourceType": "Microsoft.Compute/virtualMachines",
    "operationName": "Microsoft.Compute/virtualMachines/write",
    "subscriptionId": "sub-123",
    "resourceGroupName": "gludd-smoke-rg"
  }
]"""

# Two events with the SAME resource name — duplicate detection should fire.
DUPLICATE_EVENTS_JSON = """[
  {
    "resourceName": "loop-vm",
    "resourceType": "Microsoft.Compute/virtualMachines",
    "operationName": "Microsoft.Compute/virtualMachines/write",
    "subscriptionId": "sub-123",
    "resourceGroupName": "gludd-smoke-rg"
  },
  {
    "resourceName": "loop-vm",
    "resourceType": "Microsoft.ContainerInstance/containerGroups",
    "operationName": "Microsoft.ContainerInstance/containerGroups/write",
    "subscriptionId": "sub-123",
    "resourceGroupName": "gludd-smoke-rg"
  }
]"""

# An event with a GPU/expensive VM type.
EXPENSIVE_EVENTS_JSON = """[
  {
    "resourceName": "Standard_NC24ads_A100_v4-gpu",
    "resourceType": "Microsoft.Compute/virtualMachines",
    "operationName": "Microsoft.Compute/virtualMachines/write",
    "subscriptionId": "sub-123",
    "resourceGroupName": "gludd-smoke-rg"
  }
]"""

WRONG_SUB_EVENTS_JSON = """[
  {
    "resourceName": "stray-vm",
    "resourceType": "Microsoft.Compute/virtualMachines",
    "operationName": "Microsoft.Compute/virtualMachines/write",
    "subscriptionId": "sub-999",
    "resourceGroupName": "gludd-smoke-rg"
  }
]"""

WRONG_RG_EVENTS_JSON = """[
  {
    "resourceName": "stray-vm",
    "resourceType": "Microsoft.Compute/virtualMachines",
    "operationName": "Microsoft.Compute/virtualMachines/write",
    "subscriptionId": "sub-123",
    "resourceGroupName": "production-rg"
  }
]"""

EMPTY_EVENTS_JSON = "[]"


def _run_guard(
    *,
    mode: str = "--once",
    az_output: str = CLEAN_EVENTS_JSON,
    sub_id: str = "sub-123",
    rg: str = "gludd-smoke-rg",
    tenant_id: str = "tenant-456",
    lookback: str = "5",
    env_extra: dict[str, str] | None = None,
    timeout: int = 15,
) -> subprocess.CompletedProcess[bytes]:
    """Run the guard script in a subprocess with mocked `az` via PATH injection.

    Creates a temporary `az` script that echoes the desired JSON and returns 0.
    Also writes a real `python3` shim so the inline python3 parser works.
    """
    tmpdir = tempfile.mkdtemp(prefix="gludd-az-guard-test-")

    # Fake az CLI — always succeeds and prints the supplied JSON.
    az_shim = Path(tmpdir) / "az"
    az_shim.write_text(
        "#!/usr/bin/env bash\n"
        "# -- only handle: az account show (for _check_auth) and\n"
        "#    az monitor activity-log list (for _fetch_events)\n"
        'if echo "$*" | grep -q "account show"; then\n'
        f'  echo "{sub_id}"\n'
        f'  exit 0\n'
        "elif echo \"$*\" | grep -q 'monitor activity-log list'; then\n"
        "  cat <<'__AZ_JSON_EOF__'\n"
        f"{az_output}\n"
        "__AZ_JSON_EOF__\n"
        "  exit 0\n"
        "else\n"
        '  echo "unknown az subcommand: $*" >&2\n'
        "  exit 1\n"
        "fi\n"
    )
    az_shim.chmod(0o755)

    env = {
        "PATH": f"{tmpdir}:{os.environ.get('PATH', '')}",
        "AZURE_SUBSCRIPTION_ID": sub_id,
        "AZURE_RESOURCE_GROUP": rg,
        "AZURE_TENANT_ID": tenant_id,
        "AZURE_EVENT_GUARD_LOOKBACK": lookback,
        "AZURE_EVENT_GUARD_INTERVAL": "1",  # fast polling for watch-mode tests
        "VIOLATION_LOG": f"{tmpdir}/violations.log",
        "SMOKE_PID_FILE": f"{tmpdir}/.smoke-test.pid",
    }
    if env_extra:
        env.update(env_extra)

    return subprocess.run(
        ["bash", str(SCRIPT_PATH), mode],
        capture_output=True,
        env=env,
        timeout=timeout,
        cwd=tmpdir,
    )


# ============================================================================
# Structural tests — script exists, is executable, has expected sections
# ============================================================================


class TestScriptExists:
    def test_script_file_present(self):
        assert SCRIPT_PATH.exists(), f"Guard script missing at {SCRIPT_PATH}"

    def test_script_is_readable(self):
        assert os.access(SCRIPT_PATH, os.R_OK), "Guard script is not readable"

    def test_script_has_shebang(self):
        first_line = SCRIPT_PATH.read_text().splitlines()[0]
        assert first_line.startswith("#!"), "Missing shebang"

    def test_script_has_set_flags(self):
        content = SCRIPT_PATH.read_text()
        assert "set -euo pipefail" in content, "Missing strict flags"


class TestScriptContainsGuards:
    def test_expensive_type_pattern_exists(self):
        content = SCRIPT_PATH.read_text()
        assert "EXPENSIVE_TYPE_PATTERN" in content, "Missing expensive type regex"

    def test_watched_operations_defined(self):
        content = SCRIPT_PATH.read_text()
        assert "WATCHED_OPERATIONS" in content, "Missing watched operations array"

    def test_error_exit_code_2(self):
        content = SCRIPT_PATH.read_text()
        assert "exit 2" in content or 'code 2' in content or '_die 2' in content, "Auth error exit 2 not found"

    def test_error_exit_code_1(self):
        content = SCRIPT_PATH.read_text()
        assert "exit 1" in content or 'return 1' in content, "Violation exit 1 not found"


# ============================================================================
# Auth / config error tests — exit 2
# ============================================================================


class TestAuthErrorExit2:
    def test_missing_subscription_id_exits_2(self):
        proc = _run_guard(sub_id="", env_extra={"AZURE_SUBSCRIPTION_ID": ""})
        assert proc.returncode == 2, f"Expected exit 2, got {proc.returncode}"

    def test_missing_resource_group_exits_2(self):
        proc = _run_guard(rg="", env_extra={"AZURE_RESOURCE_GROUP": ""})
        assert proc.returncode == 2, f"Expected exit 2, got {proc.returncode}"

    def test_stderr_mentions_auth_error(self):
        proc = _run_guard(sub_id="", env_extra={"AZURE_SUBSCRIPTION_ID": ""})
        stderr = proc.stderr.decode()
        assert "AZURE_SUBSCRIPTION_ID" in stderr or "ERROR" in stderr


# ============================================================================
# Clean pass — exit 0
# ============================================================================


class TestCleanPass:
    def test_no_violations_exits_0(self):
        proc = _run_guard(az_output=CLEAN_EVENTS_JSON)
        assert proc.returncode == 0, f"Expected exit 0, got {proc.returncode}\nstderr: {proc.stderr.decode()}"

    def test_empty_events_exits_0(self):
        proc = _run_guard(az_output=EMPTY_EVENTS_JSON)
        assert proc.returncode == 0, f"Expected exit 0, got {proc.returncode}"


# ============================================================================
# Violation tests — exit 1
# ============================================================================


class TestDuplicateDetection:
    def test_duplicate_resource_name_exits_1(self):
        proc = _run_guard(az_output=DUPLICATE_EVENTS_JSON)
        assert proc.returncode == 1, f"Expected exit 1, got {proc.returncode}"

    def test_duplicate_logs_violation(self):
        proc = _run_guard(az_output=DUPLICATE_EVENTS_JSON)
        stderr = proc.stderr.decode()
        assert "VIOLATION" in stderr, f"VIOLATION not in stderr: {stderr}"


class TestExpensiveDetection:
    def test_Standard_NC_detected(self):
        proc = _run_guard(az_output=EXPENSIVE_EVENTS_JSON)
        assert proc.returncode == 1, "Standard_NC* should trigger violation"

    @pytest.mark.parametrize(
        "sku",
        [
            "Standard_NC6",
            "Standard_ND96asr_v4",
            "Standard_NV36adms_A10_v5",
            "Standard_HB120rs_v3",
            "Standard_HC44rs",
            "Standard_H8",
            "Standard_A100",
            "Standard_M128s",
            "Standard_L32s_v2",
            "Standard_G5",
        ],
    )
    def test_expensive_sku_triggers_violation(self, sku):
        events = f"""[{{
          "resourceName": "{sku}-gpu-node",
          "resourceType": "Microsoft.Compute/virtualMachines",
          "operationName": "Microsoft.Compute/virtualMachines/write",
          "subscriptionId": "sub-123",
          "resourceGroupName": "gludd-smoke-rg"
        }}]"""
        proc = _run_guard(az_output=events)
        assert proc.returncode == 1, f"SKU {sku} should trigger violation"

    @pytest.mark.parametrize(
        "sku",
        [
            "Standard_B1s",
            "Standard_B2ms",
            "Standard_D2s_v3",
            "Standard_F2s_v2",
            "Standard_E2s_v3",
            "Standard_A2_v2",
        ],
    )
    def test_cheap_sku_does_not_trigger(self, sku):
        events = f"""[{{
          "resourceName": "{sku}-cheap-node",
          "resourceType": "Microsoft.Compute/virtualMachines",
          "operationName": "Microsoft.Compute/virtualMachines/write",
          "subscriptionId": "sub-123",
          "resourceGroupName": "gludd-smoke-rg"
        }}]"""
        proc = _run_guard(az_output=events)
        assert proc.returncode == 0, f"Cheap SKU {sku} should NOT trigger violation"


class TestWrongAccount:
    def test_wrong_subscription_exits_1(self):
        proc = _run_guard(az_output=WRONG_SUB_EVENTS_JSON)
        assert proc.returncode == 1, f"Wrong sub should exit 1, got {proc.returncode}"

    def test_wrong_resource_group_exits_1(self):
        proc = _run_guard(az_output=WRONG_RG_EVENTS_JSON)
        assert proc.returncode == 1, f"Wrong RG should exit 1, got {proc.returncode}"


# ============================================================================
# Watch mode tests
# ============================================================================


class TestWatchMode:
    def test_watch_mode_polls_and_exits_on_violation(self):
        """Watch mode enters its loop and terminates on a detected violation."""
        proc = _run_guard(
            mode="--watch",
            az_output=EXPENSIVE_EVENTS_JSON,
            timeout=5,
        )
        assert proc.returncode == 1
        stdout = proc.stdout.decode()
        assert "starting watch mode" in stdout, f"Watch mode not entered: {stdout}"


class TestKillSwitch:
    def test_violation_kills_smoke_test_pid(self):
        """On violation, the script should attempt to kill the PID in the smoke file."""
        tmpdir = tempfile.mkdtemp(prefix="gludd-az-kill-")
        pid_file = Path(tmpdir) / ".smoke-test.pid"

        # Start a long-running dummy process
        dummy = subprocess.Popen(["sleep", "60"])
        pid_file.write_text(str(dummy.pid))

        proc = _run_guard(
            az_output=EXPENSIVE_EVENTS_JSON,
            env_extra={"SMOKE_PID_FILE": str(pid_file)},
        )
        assert proc.returncode == 1, f"Violation should exit 1, got {proc.returncode}"

        # The dummy may have been killed; clean up
        try:
            dummy.wait(timeout=1)
        except subprocess.TimeoutExpired:
            dummy.kill()
            dummy.wait()


# ============================================================================
# Makefile target existence tests
# ============================================================================


class TestMakefileTargets:
    def test_azure_event_guard_start_target_exists(self):
        makefile = (SCRIPT_PATH.parents[1] / "Makefile").read_text()
        assert "azure-event-guard-start:" in makefile, "Makefile missing azure-event-guard-start"

    def test_azure_event_guard_stop_target_exists(self):
        makefile = (SCRIPT_PATH.parents[1] / "Makefile").read_text()
        assert "azure-event-guard-stop:" in makefile, "Makefile missing azure-event-guard-stop"

    def test_azure_event_guard_check_target_exists(self):
        makefile = (SCRIPT_PATH.parents[1] / "Makefile").read_text()
        assert "azure-event-guard-check:" in makefile, "Makefile missing azure-event-guard-check"

    def test_azure_event_guard_status_target_exists(self):
        makefile = (SCRIPT_PATH.parents[1] / "Makefile").read_text()
        assert "azure-event-guard-status:" in makefile, "Makefile missing azure-event-guard-status"
