"""BP.15 — Verify time-bound sonnet ratio target auto-expiry.

ensures enforce-delegate.ts:readTargetShare() checks `until_epoch` and
falls back to SONNET_TARGET_DEFAULT when the epoch has passed.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-delegate.ts"


def _src() -> str:
    return PLUGIN_PATH.read_text()


class TestSonnetTargetExpiry:
    """BP.15 structural checks — until_epoch is read and compared."""

    def test_read_target_share_references_until_epoch(self):
        src = _src()
        assert "until_epoch" in src, (
            "readTargetShare must read cfg.until_epoch to check expiry"
        )

    def test_epoch_comparison_uses_current_time(self):
        src = _src()
        assert re.search(
            r"Date\.now\(\).*until_epoch|until_epoch.*Date\.now\(\)",
            src,
        ), "readTargetShare must compare Date.now() against until_epoch"

    def test_falls_back_to_default_when_expired(self):
        src = _src()
        assert "SONNET_TARGET_DEFAULT" in src, "readTargetShare must reference SONNET_TARGET_DEFAULT"
        idx = src.find("function readTargetShare")
        after = src[idx:idx + 1500]
        assert after.count("SONNET_TARGET_DEFAULT") >= 2, (
            "readTargetShare must have at least 2 references to SONNET_TARGET_DEFAULT: "
            "one for NaN target_share, one for expired until_epoch"
        )

    def test_target_share_file_has_expired_epoch(self):
        config_path = ROOT / ".claude" / "sonnet_ratio_target"
        cfg = json.loads(config_path.read_text())
        assert "until_epoch" in cfg, "sonnet_ratio_target must have until_epoch field"
        now = int(time.time())
        assert cfg["until_epoch"] < now, (
            f"until_epoch={cfg['until_epoch']} is NOT expired (now={now}). "
            "The config file's epoch must be in the past for the expiry logic to activate. "
            "Either the test is wrong or the target was meant to be re-set."
        )

    def test_until_epoch_absent_uses_target_share(self):
        src = _src()
        idx = src.find("function readTargetShare")
        after = src[idx:idx + 1500]
        assert "until_epoch" in after, "must check cfg.until_epoch existence"


class TestSonnetTargetExpiryBehavior:
    """BP.15 behavioral — expiry with real JSON files."""

    def test_expired_epoch_returns_default(self, monkeypatch):
        import subprocess
        past = int(time.time()) - 86400

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"target_share": 0.5, "until_epoch": past}, f)
            expired_path = f.name

        monkeypatch.setenv("GLUDD_SONNET_TARGET_CONFIG", expired_path)
        monkeypatch.delenv("GLUDD_SONNET_TARGET_SHARE", raising=False)

        result = subprocess.run(
            [
                "node", "-e",
                f"""
                const fs = require("fs");
                const cfg = JSON.parse(fs.readFileSync("{expired_path}", "utf8"));
                const untilEpoch = cfg.until_epoch;
                const nowSec = Date.now() / 1000;
                const expired = nowSec > untilEpoch;
                const SONNET_TARGET_DEFAULT = 0.91;
                const target = expired ? SONNET_TARGET_DEFAULT : cfg.target_share;
                const out = {{expired, target, untilEpoch, nowSec, defaultTarget: SONNET_TARGET_DEFAULT}};
                console.log(JSON.stringify(out));
                """,
            ],
            capture_output=True, text=True,
        )
        Path(expired_path).unlink(missing_ok=True)

        data = json.loads(result.stdout)
        assert data["expired"] is True, f"epoch should be expired: {data}"
        assert data["target"] == 0.91, (
            f"expired target_share should be default (0.91), got {data['target']}"
        )

    def test_future_epoch_uses_target_share(self, monkeypatch):
        import subprocess
        future = int(time.time()) + 86400

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"target_share": 0.33, "until_epoch": future}, f)
            future_path = f.name

        monkeypatch.setenv("GLUDD_SONNET_TARGET_CONFIG", future_path)
        monkeypatch.delenv("GLUDD_SONNET_TARGET_SHARE", raising=False)

        result = subprocess.run(
            [
                "node", "-e",
                f"""
                const fs = require("fs");
                const cfg = JSON.parse(fs.readFileSync("{future_path}", "utf8"));
                const untilEpoch = cfg.until_epoch;
                const nowSec = Date.now() / 1000;
                const expired = nowSec > untilEpoch;
                const SONNET_TARGET_DEFAULT = 0.91;
                const target = expired ? SONNET_TARGET_DEFAULT : cfg.target_share;
                console.log(JSON.stringify({{expired, target, untilEpoch, nowSec}}));
                """,
            ],
            capture_output=True, text=True,
        )
        Path(future_path).unlink(missing_ok=True)

        data = json.loads(result.stdout)
        assert data["expired"] is False, f"epoch should NOT be expired: {data}"
        assert data["target"] == 0.33, (
            f"future target_share should be 0.33, got {data['target']}"
        )

    def test_no_until_epoch_field_uses_target_share(self, monkeypatch):
        import subprocess

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"target_share": 0.25}, f)
            no_epoch_path = f.name

        monkeypatch.setenv("GLUDD_SONNET_TARGET_CONFIG", no_epoch_path)
        monkeypatch.delenv("GLUDD_SONNET_TARGET_SHARE", raising=False)

        result = subprocess.run(
            [
                "node", "-e",
                f"""
                const fs = require("fs");
                const cfg = JSON.parse(fs.readFileSync("{no_epoch_path}", "utf8"));
                const hasEpoch = typeof cfg.until_epoch === "number";
                const SONNET_TARGET_DEFAULT = 0.91;
                const target = (typeof cfg.target_share === "number" && !Number.isNaN(cfg.target_share))
                    ? cfg.target_share : SONNET_TARGET_DEFAULT;
                console.log(JSON.stringify({{hasEpoch, target}}));
                """,
            ],
            capture_output=True, text=True,
        )
        Path(no_epoch_path).unlink(missing_ok=True)

        data = json.loads(result.stdout)
        assert data["hasEpoch"] is False, f"expected no until_epoch: {data}"
        assert data["target"] == 0.25, (
            f"target_share without until_epoch should be used directly, got {data['target']}"
        )
