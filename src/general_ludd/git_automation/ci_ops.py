"""CI operations — verdict, cooldown, cancel, active-check.

Ports Makefile targets ci-verdict, ci-verdict-safe, ci-cancel, ci-cooldown-status,
ci-active into callable Python functions. Reuses the cooldown state file pattern
from ``scripts/ci_check_cooldown.py``.

For subprocess cost, all ``gh`` calls use short ``--json`` field lists and
``--limit 3`` (verdict) / ``--limit 10`` (active). Each call returns in <1s
when ``gh`` is reachable.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, cast

from general_ludd.security.state import project_state, secure_directory, secure_write_text

_COOLDOWN_SEC: int = int(os.environ.get("CI_CHECK_COOLDOWN_SEC", "600"))
_STATE_FILE: Path | None = None
_REPO: str = "sandboxcom/gludd"
_WORKFLOW: str = "Build and Release"

# ═══════════════════════════════════════════════════════════════════════════════
# internal helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _get_current_time() -> float:
    return time.time()


def _state_file() -> Path:
    if _STATE_FILE is not None:
        return _STATE_FILE
    configured = os.environ.get("GLUDD_CI_STATE_FILE")
    if configured:
        candidate = Path(configured).expanduser()
        secure_directory(candidate.parent)
        return candidate
    return project_state().path("ci", "check-state.json")


def _load_cooldown_state() -> dict[str, Any]:
    try:
        return cast(dict[str, Any], json.loads(_state_file().read_text()))
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "last_check_epoch": 0.0,
            "last_push_epoch": 0.0,
            "last_head_sha": "",
            "check_count": 0,
            "last_verdict": "",
            "last_verdict_epoch": 0.0,
        }


def _save_cooldown_state(state: dict[str, Any]) -> None:
    secure_write_text(_state_file(), json.dumps(state))


# ═══════════════════════════════════════════════════════════════════════════════
# ci-verdict
# ═══════════════════════════════════════════════════════════════════════════════


def _parse_gh_run_list(runs: list[dict[str, Any]]) -> dict[str, Any]:
    if not runs:
        return {"verdict": "UNKNOWN", "run_id": "", "headSha": ""}

    latest = runs[0]
    conc = latest.get("conclusion")
    status = latest.get("status", "")
    head_sha = latest.get("headSha", "")
    run_id = str(latest.get("databaseId", ""))

    if conc == "success" or conc == "skipped":
        return {
            "verdict": "GREEN",
            "run_id": run_id,
            "headSha": head_sha,
        }
    elif conc in ("failure", "cancelled", "timed_out"):
        return {
            "verdict": "RED",
            "run_id": run_id,
            "headSha": head_sha,
        }
    elif status in ("in_progress", "queued", "pending", "waiting", "requested"):
        return {
            "verdict": "PENDING",
            "run_id": run_id,
            "headSha": head_sha,
        }
    return {"verdict": "UNKNOWN", "run_id": run_id, "headSha": head_sha}


def ci_verdict(
    branch: str = "development", sha: str | None = None
) -> dict[str, Any]:
    try:
        commit = sha or _git_head_sha()
        result = subprocess.run(
            [
                "gh", "run", "list",
                "--commit", commit,
                "--branch", branch,
                "-R", _REPO,
                "--json", "conclusion,databaseId,status,headSha",
                "--limit", "3",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        runs: list[dict[str, Any]] = json.loads(result.stdout or "[]")
        parsed = _parse_gh_run_list(runs)
        if sha and parsed["headSha"] and parsed["headSha"] != sha:
            parsed["stale"] = True
        return parsed
    except (subprocess.CalledProcessError, json.JSONDecodeError, Exception):
        return {"verdict": "UNKNOWN", "run_id": "", "headSha": ""}


def _git_head_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, timeout=5
        ).strip()
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════════════════════════
# ci-verdict-safe + cooldown
# ═══════════════════════════════════════════════════════════════════════════════


def _remaining_cooldown_sec(state: dict[str, Any]) -> float:
    elapsed = _get_current_time() - float(state.get("last_check_epoch", 0.0))
    return max(0.0, _COOLDOWN_SEC - elapsed)


def ci_verdict_safe(
    branch: str = "development",
    sha: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    state = _load_cooldown_state()
    remaining = _remaining_cooldown_sec(state)
    if remaining > 0 and not force:
        last_verdict = state.get("last_verdict", "")
        return {
            "verdict": "COOLDOWN",
            "remaining_sec": remaining,
            "last_verdict": last_verdict,
            "check_count": state.get("check_count", 0),
        }
    result = ci_verdict(branch=branch, sha=sha)
    state["last_check_epoch"] = _get_current_time()
    state["last_head_sha"] = result.get("headSha", "")
    state["check_count"] = state.get("check_count", 0) + 1
    state["last_verdict"] = result["verdict"].lower()
    state["last_verdict_epoch"] = _get_current_time()
    _save_cooldown_state(state)
    return result


def ci_cooldown_status() -> dict[str, Any]:
    state = _load_cooldown_state()
    remaining = _remaining_cooldown_sec(state)
    return {
        "cooldown_active": remaining > 0,
        "remaining_sec": remaining,
        "check_count": state.get("check_count", 0),
        "last_check_epoch": state.get("last_check_epoch", 0.0),
        "last_verdict": state.get("last_verdict", ""),
        "last_verdict_epoch": state.get("last_verdict_epoch", 0.0),
        "last_head_sha": state.get("last_head_sha", ""),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ci-cancel
# ═══════════════════════════════════════════════════════════════════════════════


def ci_cancel(run_id: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["gh", "run", "cancel", run_id, "-R", _REPO],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return {"success": True, "run_id": run_id, "output": result.stdout.strip()}
        return {"success": False, "run_id": run_id, "output": result.stderr.strip()}
    except Exception as e:
        return {"success": False, "run_id": run_id, "output": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# ci-active
# ═══════════════════════════════════════════════════════════════════════════════


def ci_active(branch: str = "development") -> bool:
    try:
        result = subprocess.run(
            [
                "gh", "run", "list",
                "-R", _REPO,
                "--workflow", _WORKFLOW,
                "--branch", branch,
                "--json", "status",
                "--limit", "10",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        runs: list[dict[str, Any]] = json.loads(result.stdout or "[]")
        _active_statuses = {"in_progress", "queued", "pending", "waiting", "requested"}
        return any(run.get("status") in _active_statuses for run in runs)
    except Exception:
        return False
