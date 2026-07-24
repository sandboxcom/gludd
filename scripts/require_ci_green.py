import json, subprocess, sys

def verdict_for(sha, branch="development"):
    try:
        r = subprocess.run(
            ["gh", "run", "list", "--commit", sha, "--branch", branch,
             "-R", "sandboxcom/gludd", "--json", "conclusion,databaseId,status,headSha", "--limit", "3"],
            capture_output=True, text=True, timeout=10)
        runs = json.loads(r.stdout or "[]")
        if not runs:
            print(f"CI RED: no run found for SHA {sha}")
            return 1
        latest = runs[0]
        conc = latest.get("conclusion")
        rid = latest.get("databaseId", "?")
        if conc == "success":
            print(f"CI GREEN: sha={sha} run={rid}")
            return 0
        elif conc in ("cancelled", "skipped"):
            print(f"CI BYPASS: sha={sha} run={rid} conclusion={conc}")
            return 0
        elif conc in ("failure", "timed_out"):
            print(f"CI RED: sha={sha} run={rid} conclusion={conc}")
            return 1
        else:
            status = latest.get("status", "?")
            print(f"CI PENDING: sha={sha} run={rid} status={status}")
            return 2
    except Exception as e:
        print(f"CI ERROR: {e}")
        return 2

if __name__ == "__main__":
    sha = sys.argv[1] if len(sys.argv) > 1 else None
    branch = sys.argv[2] if len(sys.argv) > 2 else "development"
    if not sha:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        sha = r.stdout.strip()
    sys.exit(verdict_for(sha, branch))