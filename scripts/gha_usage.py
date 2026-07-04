#!/usr/bin/env python3
"""Print GitHub Actions usage summary: recent run durations, success rate."""

import subprocess, sys, datetime, json


def _gh(*args):
    """Run gh and return stdout, or empty string on failure."""
    try:
        return subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except Exception:
        return ""


def _check_private(repo: str) -> str:
    """Return 'PUBLIC' or 'PRIVATE' based on repo visibility."""
    out = _gh("api", f"repos/{repo}", "--jq", ".private")
    if out == "false":
        return "PUBLIC — unlimited minutes"
    elif out == "true":
        return "PRIVATE — limited minutes"
    return "UNKNOWN"


def main():
    repo = "sandboxcom/gludd"

    print("=== GitHub Actions Usage ===")

    vis = _check_private(repo)
    print(f"Repo: {repo} ({vis})")
    print("Recent runs (last 10):")

    # Fetch runs via API for structured data
    raw = _gh("api", f"repos/{repo}/actions/runs?per_page=10")
    if not raw:
        # Fallback: pipe-based approach
        raw = _gh(
            "api",
            f"repos/{repo}/actions/runs?per_page=10",
            "--jq",
            '.workflow_runs[] | "\(.conclusion // "RUNNING")|\(.id)|\(.created_at)|\(.updated_at)|\(.display_title)"',
        )
        if not raw:
            print("  No runs found or gh not configured")
            return
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
    else:
        # Use full JSON for reliability (titles may contain spaces/special chars)
        try:
            data = json.loads(raw)
            runs = data.get("workflow_runs", [])
        except json.JSONDecodeError:
            print("  Failed to parse API response")
            return
        lines = []
        for r in runs:
            conclusion = r.get("conclusion") or "RUNNING"
            rid = r["id"]
            created = r["created_at"]
            updated = r["updated_at"]
            title = r.get("display_title", "")
            lines.append(f"{conclusion}|{rid}|{created}|{updated}|{title}")

    if not lines:
        print("  No runs found")
        return

    total = len(lines)
    success = 0
    durations = []

    for l in lines:
        parts = l.split("|", 4)
        conclusion = parts[0]
        run_id = parts[1] if len(parts) > 1 else "?"
        created = parts[2] if len(parts) > 2 else ""
        updated = parts[3] if len(parts) > 3 else ""
        title = parts[4] if len(parts) > 4 else ""

        try:
            dt_c = datetime.datetime.fromisoformat(created.replace("Z", "+00:00"))
            dt_u = datetime.datetime.fromisoformat(updated.replace("Z", "+00:00"))
            dur = (dt_u - dt_c).total_seconds()
            dur_min = round(dur / 60)
            durations.append(dur)
            dur_str = f"{dur_min}min"
        except Exception:
            dur_str = "?"

        if conclusion == "success":
            success += 1

        print(f"  {conclusion.upper():<8} {run_id:<14} {dur_str:<6} {title}")

    if durations:
        avg_min = round(sum(durations) / len(durations) / 60)
        avg_str = f"{avg_min}min"
    else:
        avg_str = "N/A"

    if total:
        success_rate = f"{success}/{total} ({success * 100 // total}%)"
    else:
        success_rate = "0/0"

    print(f"Success rate: {success_rate}")
    print(f"Avg duration: {avg_str}")


if __name__ == "__main__":
    main()
