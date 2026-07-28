"""Runtime contract for OpenCode's background gate-refresh singleflight."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHARED = (ROOT / ".opencode" / "lib" / "shared.ts").as_uri()


def _run_probe(workspace: Path, lease: Path) -> dict[str, object]:
    gate = workspace / ".gate-status"
    gate.write_text("=== GATE: PASSED ===\n", encoding="utf-8")
    stale = time.time() - 600
    os.utime(gate, (stale, stale))
    code = f"""
import {{ spawnGateRefreshIfStale }} from {SHARED!r}
let count = 0
const fakeSpawn = (_command, _args, options) => {{
  count += 1
  if (!options.detached || options.stdio !== "ignore") throw new Error("unsafe options")
  return {{ pid: process.pid, unref() {{}} }}
}}
const first = spawnGateRefreshIfStale({str(workspace)!r}, fakeSpawn)
const second = spawnGateRefreshIfStale({str(workspace)!r}, fakeSpawn)
console.log(JSON.stringify({{ first, second, count }}))
"""
    env = os.environ.copy()
    env["GLUDD_GATE_REFRESH_AUTOSPAWN"] = "1"
    env["GLUDD_GATE_REFRESH_LEASE_PATH"] = str(lease)
    proc = subprocess.run(
        [
            "node",
            "--experimental-strip-types",
            "--input-type=module",
            "-e",
            code,
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_gate_refresh_autospawn_is_singleflight(tmp_path: Path) -> None:
    result = _run_probe(tmp_path, tmp_path / "refresh-lease.json")

    assert result == {"first": True, "second": False, "count": 1}


def test_gate_refresh_autospawn_recovers_dead_lease(tmp_path: Path) -> None:
    lease = tmp_path / "refresh-lease.json"
    lease.write_text(
        json.dumps({"pid": 999_999_999, "started_at": 1}),
        encoding="utf-8",
    )

    result = _run_probe(tmp_path, lease)

    assert result == {"first": True, "second": False, "count": 1}
