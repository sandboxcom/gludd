"""Pipeline status — local gate + remote CI in one view."""
import json
import os
import subprocess
import sys
import time

REPO = "sandboxcom/gludd"


def local_gate():
    gate = ".gate-status"
    pidf = ".gate-background.pid"
    if not os.path.exists(gate):
        print("( no gate status file )")
        return
    print("=== LOCAL GATE ===")
    with open(gate) as f:
        print(f.read(), end="")
    if os.path.exists(pidf):
        with open(pidf) as f:
            pid = f.read().strip()
        if pid:
            try:
                os.kill(int(pid), 0)
                age = int(time.time()) - int(os.stat(gate).st_mtime)
                if age > 120:
                    print(f"  STALLED: .not updated for {age} seconds")
            except (OSError, ValueError):
                print(f"  DEAD: pid={pid} no longer running")


def remote_ci():
    print()
    print("=== REMOTE CI (development) ===")
    try:
        r = subprocess.run(
            ["gh", "run", "list", "--branch", "development", "--limit", "1",
             "-R", REPO, "--json", "conclusion,databaseId,headSha,status"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            print("  (gh CLI unavailable)")
            return
        runs = json.loads(r.stdout or "[]")
        if not runs:
            print("  IDLE (no recent CI runs)")
            return
        run = runs[0]
        conc = run.get("conclusion", "pending")
        stat = run.get("status", "?")
        rid = run.get("databaseId", "?")
        sha = (run.get("headSha", "") or "")[:12]
        if conc:
            print(f"  CI {conc.upper()}: run {rid} (sha={sha})")
        else:
            print(f"  CI PENDING: run {rid} (sha={sha}) status={stat}")
    except Exception:
        print("  (CI check failed)")


if __name__ == "__main__":
    local_gate()
    remote_ci()
