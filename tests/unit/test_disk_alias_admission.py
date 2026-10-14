"""Exercise legacy disk aliases against the real canonical owners, without cleanup."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.check_make_target_contract import _stanzas

ROOT = Path(__file__).resolve().parents[2]
LEGACY_OPTIONS = (
    "GLUDD_DISK_THRESHOLD",
    "GLUDD_DISK_CLEANUP_LOG",
    "DISK_GUARD_UV_LOCK_TIMEOUT",
    "DISK_GUARD_UV_MAX_SECONDS",
    "DISK_GUARD_UV_HEARTBEAT_SECONDS",
)

# This fixture invokes the existing owners with fake disk inputs and a cleanup
# spy. No project cache, worktree, process, or disk capacity is modified.
OWNER_FIXTURE = """
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

root = Path(os.environ["GLUDD_DISK_ALIAS_SOURCE_ROOT"])
sys.path.insert(0, str(root))
if "scripts" in sys.modules:
    sys.modules["scripts"].__path__ = [str(root / "scripts")]
from scripts import check_disk_usage

inputs = json.loads(os.environ["GLUDD_DISK_ALIAS_INPUTS"])
def measure(*args, **kwargs):
    mode = inputs.get("measurement", "valid")
    if mode == "unavailable":
        raise subprocess.CalledProcessError(5, ["df"])
    if mode == "malformed":
        return "df: no measurements\\n"
    header = "Filesystem 1024-blocks Used Available Capacity Mounted on\\n"
    return header + "fixture 100 96 4 " + str(inputs.get("disk_pct", 96)) + "% /fixture\\n"

def scratch():
    if inputs.get("registry_error"):
        raise check_disk_usage.WorktreeRegistryError("fixture registry unavailable")
    return (inputs.get("scratch_mb", 0.0), [])

with (
    patch.object(check_disk_usage.subprocess, "check_output", measure),
    patch.object(check_disk_usage, "_gludd_tmp_inspection", scratch),
):
    if Path(__file__).name == "check_disk_usage.py":
        print("canonical_owner=check_disk_usage interpreter=" + sys.executable, flush=True)
        raise SystemExit(check_disk_usage.cli(sys.argv[1:]))
    from scripts import automatic_disk_cleanup
    print("canonical_owner=automatic_disk_cleanup interpreter=" + sys.executable, flush=True)
    original = automatic_disk_cleanup.run_preflight
    def cleanup(**kwargs):
        print("cleanup_spy=" + json.dumps(kwargs, sort_keys=True), flush=True)
        errors = tuple(inputs.get("cleanup_errors", []))
        return automatic_disk_cleanup.CleanupResult((), ("protected active ownership",), errors)
    def preflight(**kwargs):
        kwargs["inspect_usage"] = automatic_disk_cleanup.inspect_usage
        return original(**kwargs)
    with (
        patch.object(automatic_disk_cleanup, "_automatic_cleanup", cleanup),
        patch.object(automatic_disk_cleanup, "run_preflight", preflight),
    ):
        raise SystemExit(automatic_disk_cleanup.main(sys.argv[1:]))
"""


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class DiskAliasAdmissionTests(unittest.TestCase):
    """Fail-closed native compatibility tests with cleanup replaced by a spy."""

    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory(prefix="gludd-disk-alias-test-")
        self.addCleanup(self.scratch.cleanup)
        self.fixture = Path(self.scratch.name)
        scripts = self.fixture / "scripts"
        scripts.mkdir()
        (scripts / "disk-guard.sh").write_text(_source("scripts/disk-guard.sh"), encoding="utf-8")
        for name in ("check_disk_usage.py", "automatic_disk_cleanup.py"):
            (scripts / name).write_text(OWNER_FIXTURE, encoding="utf-8")
        (scripts / "__init__.py").write_text("", encoding="utf-8")
        make_source = _source("make/20-recovery-and-git.mk")
        (self.fixture / "Makefile").write_text(
            "SYSTEM_PYTHON := "
            + sys.executable
            + "\n"
            + make_source
            + "\n"
            + _stanzas(_source("make/50-development-and-guardrails.mk"))["disk-reclaim"]
            + "\ntest-files:\n\t@printf '%s\\n' 'validation_owner=test-files'\n",
            encoding="utf-8",
        )
        tools = self.fixture / "tools"
        tools.mkdir()
        (tools / "df").write_text(
            "#!/bin/sh\n"
            'if [ "$GLUDD_DISK_ALIAS_MEASUREMENT" = unavailable ]; then exit 5; fi\n'
            'if [ "$GLUDD_DISK_ALIAS_MEASUREMENT" = malformed ]; then printf "bad\\n"; exit 0; fi\n'
            'printf "Filesystem 1024-blocks Used Available Capacity Mounted on\\n"\n'
            'printf "fixture 100 96 4 %s%% /fixture\\n" "$GLUDD_DISK_ALIAS_PCT"\n',
            encoding="utf-8",
        )
        (tools / "df").chmod(0o700)
        # The red run must never execute the old cleanup engine, including when
        # an assertion fails. These sentinels cannot modify any project data.
        for tool in ("rm", "pip3", "uv", "find", "pgrep"):
            sentinel = tools / tool
            sentinel.write_text(
                "#!/bin/sh\nprintf '%s\\n' 'UNSAFE_LEGACY_TOOL_CALLED' >&2\nexit 99\n", encoding="utf-8"
            )
            sentinel.chmod(0o700)
        self.environment = dict(os.environ)
        for option in (*LEGACY_OPTIONS, "MAKEFLAGS", "MFLAGS", "MAKELEVEL"):
            self.environment.pop(option, None)
        self.environment.update(
            GLUDD_DISK_ALIAS_SOURCE_ROOT=str(ROOT),
            GLUDD_DISK_ALIAS_INPUTS=json.dumps({"disk_pct": 96}),
            GLUDD_DISK_ALIAS_PCT="96",
            GLUDD_DISK_ALIAS_MEASUREMENT="valid",
            SYSTEM_PYTHON=sys.executable,
            PATH=str(tools) + os.pathsep + os.environ.get("PATH", ""),
            PYTHONDONTWRITEBYTECODE="1",
        )

    def _run(
        self,
        *,
        mode: str = "check",
        target: str = "",
        inputs: dict[str, object] | None = None,
        options: dict[str, str] | None = None,
        extra: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        env = dict(self.environment)
        values = {"disk_pct": 96, **(inputs or {})}
        env["GLUDD_DISK_ALIAS_INPUTS"] = json.dumps(values)
        env["GLUDD_DISK_ALIAS_PCT"] = str(values["disk_pct"])
        env["GLUDD_DISK_ALIAS_MEASUREMENT"] = str(values.get("measurement", "valid"))
        env.update(options or {})
        if target:
            command = [shutil.which("make") or "make", "--no-print-directory", target, *extra]
        else:
            command = ["bash", str(self.fixture / "scripts/disk-guard.sh"), mode, *extra]
        result = subprocess.run(
            command, cwd=self.fixture, env=env, capture_output=True, text=True, timeout=15, check=False
        )
        self.assertNotIn("UNSAFE_LEGACY_TOOL_CALLED", result.stdout + result.stderr)
        return result

    def test_user_authorized_95_percent_limit_is_shared_by_all_aliases(self) -> None:
        for disk_pct in (91, 95):
            for mode, target in (("check", ""), ("guard", ""),
                                 ("check", "disk-check"), ("guard", "disk-guard"),
                                 ("guard", "check-disk"), ("guard", "disk-reclaim")):
                with self.subTest(disk_pct=disk_pct, mode=mode, target=target):
                    result = self._run(mode=mode, target=target, inputs={"disk_pct": disk_pct})
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertNotIn("cleanup_spy=", result.stdout)
                    self.assertIn("canonical_owner=", result.stdout)

    def test_disk_check_blocks_96_at_authorized_95_limit(self) -> None:
        result = self._run(target="disk-check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("disk usage 96.0% > 95% limit", result.stderr)

    def test_shell_check_uses_same_limit(self) -> None:
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("disk usage 96.0% > 95% limit", result.stderr)

    def test_authorized_boundary_95_is_healthy(self) -> None:
        result = self._run(inputs={"disk_pct": 95})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("disk = 95.0% (<= 95)", result.stdout)

    def test_read_only_check_also_enforces_scratch_limit(self) -> None:
        result = self._run(inputs={"disk_pct": 80, "scratch_mb": 101})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("scratch 101.0 MB > 100 MB limit", result.stderr)
        self.assertNotIn("cleanup_spy=", result.stdout)

    def test_measurement_failure_is_not_zero_usage(self) -> None:
        result = self._run(inputs={"measurement": "unavailable"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("disk usage inspection failed", result.stderr)

    def test_malformed_measurement_fails_closed(self) -> None:
        result = self._run(inputs={"measurement": "malformed"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("malformed df output", result.stderr)

    def test_invalid_percentages_fail_closed(self) -> None:
        for value in ("nan", "inf", -1, 101):
            with self.subTest(value=value):
                result = self._run(inputs={"disk_pct": value})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("percentage out of range", result.stderr)

    def test_registry_failure_is_not_healthy_scratch(self) -> None:
        result = self._run(inputs={"disk_pct": 80, "registry_error": True})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("worktree registry unavailable", result.stderr)

    def test_disk_guard_delegates_to_cleanup_owner_and_keeps_refusal(self) -> None:
        result = self._run(target="disk-guard")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("canonical_owner=automatic_disk_cleanup", result.stdout)
        self.assertIn("protected active ownership", result.stdout)
        self.assertIn("reason=no-progress", result.stderr)

    def test_reclaim_alias_preserves_canonical_admission_and_validation(self) -> None:
        result = self._run(target="disk-reclaim")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("canonical_owner=automatic_disk_cleanup", result.stdout)
        self.assertIn("reason=no-progress", result.stderr)
        plan = self._run(target="disk-reclaim", extra=(
            "DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=1", "DISK_CLEANUP_PREFLIGHT_DRY_RUN=0",
            "DISK_CLEANUP_RECEIPT_GRACE_SECONDS=1800",
        ))
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertIn("validation_owner=test-files", plan.stdout)
        self.assertNotIn("cleanup_spy=", plan.stdout)

    def test_guard_measurement_failure_prevents_cleanup(self) -> None:
        result = self._run(mode="guard", inputs={"measurement": "unavailable"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("phase=inspect status=failed", result.stderr)
        self.assertNotIn("cleanup_spy=", result.stdout)

    def test_guard_preserves_dry_run_and_receipt_grace(self) -> None:
        result = self._run(
            mode="guard",
            options={
                "DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY": "0",
                "DISK_CLEANUP_PREFLIGHT_DRY_RUN": "1",
                "DISK_CLEANUP_RECEIPT_GRACE_SECONDS": "3600",
            },
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('cleanup_spy={"dry_run": true, "receipt_grace_seconds": 3600}', result.stdout)

    def test_cleanup_errors_cannot_admit_capacity(self) -> None:
        result = self._run(mode="guard", inputs={"cleanup_errors": ["ownership lookup unavailable"]})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reason=cleanup-errors", result.stderr)

    def test_invalid_dry_run_value_is_rejected(self) -> None:
        result = self._run(mode="guard", options={"DISK_CLEANUP_PREFLIGHT_DRY_RUN": "2"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Usage: make disk-cleanup-preflight", result.stdout)
        self.assertNotIn("cleanup_spy=", result.stdout)

    def test_empty_explicit_preflight_settings_are_not_defaulted(self) -> None:
        for option in (
            "DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY",
            "DISK_CLEANUP_PREFLIGHT_DRY_RUN",
            "DISK_CLEANUP_RECEIPT_GRACE_SECONDS",
        ):
            with self.subTest(option=option):
                result = self._run(mode="guard", inputs={"disk_pct": 80}, options={option: ""})
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("cleanup_spy=", result.stdout)

    def test_guard_recipe_forwards_all_documented_preflight_settings(self) -> None:
        recipe = _source("make/20-recovery-and-git.mk").split("\ndisk-guard:\n", 1)[1].split("\ndisk-check:\n", 1)[0]
        for option in (
            "DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY",
            "DISK_CLEANUP_PREFLIGHT_DRY_RUN",
            "DISK_CLEANUP_RECEIPT_GRACE_SECONDS",
        ):
            self.assertIn(option + '="$(' + option + ')"', recipe)

    def test_receipt_floor_is_not_weakened(self) -> None:
        result = self._run(mode="guard", options={"DISK_CLEANUP_RECEIPT_GRACE_SECONDS": "1799"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("receipt grace must be at least 1800 seconds", result.stderr)
        self.assertNotIn("cleanup_spy=", result.stdout)

    def test_validate_only_uses_existing_test_owner(self) -> None:
        result = self._run(mode="guard", options={"DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("validation_owner=test-files", result.stdout)
        self.assertNotIn("cleanup_spy=", result.stdout)

    def test_unsupported_legacy_knobs_are_rejected(self) -> None:
        for option in LEGACY_OPTIONS:
            with self.subTest(option=option):
                result = self._run(options={option: "95"})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(option + " is unsupported", result.stderr)

    def test_unknown_modes_do_not_authorize_cleanup(self) -> None:
        result = self._run(mode="unknown")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported disk guard mode", result.stderr)

    def test_extra_arguments_are_rejected(self) -> None:
        result = self._run(extra=(str(self.fixture), "unexpected"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stderr)

    def test_alternate_volume_does_not_replace_repository_scope(self) -> None:
        result = self._run(extra=(str(self.fixture / "tools"),))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("repository root", result.stderr)

    def test_legacy_mode_aliases_and_root_argument_remain_supported(self) -> None:
        for mode in ("check", "disk-check", "guard", "disk-guard"):
            with self.subTest(mode=mode):
                result = self._run(mode=mode, inputs={"disk_pct": 80}, extra=(str(self.fixture),))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("canonical_owner=", result.stdout)

    def test_entrypoint_has_no_duplicate_measurement_or_cleanup_engine(self) -> None:
        source = _source("scripts/disk-guard.sh")
        for forbidden in (
            "pip3 cache purge",
            "rm -rf",
            "__pycache__",
            "df -Pk",
            "uv cache prune",
            "get_usage_pct",
            "GLUDD_NODE_CACHE_DIRS",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertIn("scripts/check_disk_usage.py", source)
        self.assertIn("disk-cleanup-preflight", source)

    def test_native_interpreter_is_forwarded_without_project_sync(self) -> None:
        for target in ("disk-check", "disk-guard"):
            with self.subTest(target=target):
                result = self._run(target=target, inputs={"disk_pct": 80})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("interpreter=" + sys.executable, result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
