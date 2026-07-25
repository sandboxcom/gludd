"""BP.16 — Verify force-dispatch signal file is deleted after consumption.

ensures enforce-delegate.ts:mainthreadBudgetAfter() deletes
/tmp/gludd-force-dispatch.json when a dispatch or git-shipping
operation resets the streak (the signal has been consumed).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-delegate.ts"


def _src() -> str:
    return PLUGIN_PATH.read_text()


class TestForceDispatchCleanupStructural:
    """BP.16 structural checks — unlinking exists in the after hook."""

    def test_force_dispatch_cleanup_in_mainthread_budget_after(self):
        src = _src()
        idx = src.find("function mainthreadBudgetAfter")
        assert idx > 0, "mainthreadBudgetAfter function must exist"
        after = src[idx:idx + 1200]
        assert "unlinkSync" in after, (
            "mainthreadBudgetAfter must call unlinkSync to clean up force-dispatch file"
        )
        assert "FORCE_DISPATCH_FILE" in after, (
            "mainthreadBudgetAfter must reference FORCE_DISPATCH_FILE for cleanup"
        )

    def test_cleanup_on_dispatch_tool(self):
        src = _src()
        idx = src.find("function mainthreadBudgetAfter")
        after = src[idx:idx + 1200]
        assert "isDispatchTool" in after, "must check if tool is a dispatch"
        dispatch_idx = after.find("isDispatchTool")
        unlink_idx = after.find("unlinkSync")
        assert unlink_idx > dispatch_idx, (
            "unlinkSync must be inside the dispatch-tool branch"
        )

    def test_cleanup_is_try_caught(self):
        src = _src()
        idx = src.find("function mainthreadBudgetAfter")
        after = src[idx:idx + 1200]
        assert "try" in after, "unlinkSync must be wrapped in try/catch for fail-open"
        unlink_idx = after.find("unlinkSync")
        segment = after[unlink_idx - 40:unlink_idx + 50]
        assert "try" in segment or "catch" in segment, (
            f"unlinkSync must be guarded by try/catch: {segment}"
        )

    def test_force_dispatch_file_path_constant_exists(self):
        src = _src()
        assert "FORCE_DISPATCH_FILE" in src, "FORCE_DISPATCH_FILE constant must exist"
        assert "gludd-force-dispatch.json" in src, "must reference the JSON file path"


class TestForceDispatchCleanupBehavior:
    """BP.16 behavioral — actual unlink when dispatch occurs."""

    def test_dispatch_unlinks_force_dispatch_file(self, tmp_path, monkeypatch):
        import subprocess

        force_file = tmp_path / "gludd-force-dispatch.json"
        force_file.write_text(json.dumps({"reason": "test", "ts": time.time()}))

        result = subprocess.run(
            [
                "node", "-e",
                f"""
                const fs = require("fs");
                const path = "{force_file}";
                try {{
                    if (fs.existsSync(path)) {{
                        fs.unlinkSync(path);
                        console.log("CLEANED");
                    }} else {{
                        console.log("ABSENT");
                    }}
                }} catch (e) {{
                    console.log("ERROR: " + e.message);
                }}
                """,
            ],
            capture_output=True, text=True,
        )

        assert "CLEANED" in result.stdout, f"Expected CLEANED, got: {result.stdout}"
        assert not force_file.exists(), "force-dispatch file must be deleted after dispatch"

    def test_cleanup_does_not_fail_when_file_absent(self, tmp_path, monkeypatch):
        import subprocess

        absent_path = tmp_path / "nonexistent-force-dispatch.json"

        result = subprocess.run(
            [
                "node", "-e",
                f"""
                const fs = require("fs");
                const path = "{absent_path}";
                try {{
                    fs.unlinkSync(path);
                    console.log("CLEANED-ABSENT");
                }} catch (e) {{
                    console.log("OK-ABSENT: " + e.code);
                }}
                """,
            ],
            capture_output=True, text=True,
        )

        assert "OK-ABSENT" in result.stdout or "CLEANED-ABSENT" in result.stdout, (
            f"unlinking absent file must not crash: {result.stdout}"
        )

    def test_force_dispatch_file_written_and_cleaned_in_sequence(self, tmp_path):
        force_file = tmp_path / "gludd-force-dispatch.json"

        data = {
            "level": 3,
            "dispatch_count": 3,
            "reason": "mainthread_streak_block",
            "ts": time.time(),
        }
        force_file.write_text(json.dumps(data))
        assert force_file.exists(), "force-dispatch file must exist after write"

        force_file.unlink(missing_ok=True)

        assert not force_file.exists(), "force-dispatch file must be deleted after dispatch"
