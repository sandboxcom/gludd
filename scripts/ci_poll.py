#!/usr/bin/env python3
"""Incremental CI poller — surfaces the first failing job immediately."""
import json
import os
import subprocess
import sys
import time

REPO = "sandboxcom/gludd"
FAIL_CONCLUSIONS = {"failure", "cancelled", "timed_out"}


def gh(*args: str) -> "dict | list | None":
    """Run gh and return parsed JSON, or None on error."""
    cmd = ["gh"] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print(f"[ci-poll] gh warning: {r.stderr.strip()[:200]}", file=sys.stderr)
            return None
        return json.loads(r.stdout)
    except Exception as e:
        print(f"[ci-poll] gh error: {e}", file=sys.stderr)
        return None


def fetch_job_log(run_id: str, job_id: int, job_name: str) -> str:
    """Fetch failed log for a job, save to /tmp, return file path."""
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in job_name)[:60]
    log_path = f"/tmp/ci-poll-{run_id}-{safe_name}.log"
    cmd = ["gh", "run", "view", "--job", str(job_id), "-R", REPO, "--log-failed"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        content = r.stdout or r.stderr or "(no output)"
        with open(log_path, "w") as f:
            f.write(content)
        return log_path
    except Exception as e:
        return f"(log fetch failed: {e})"


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: ci_poll.py <run-id>", file=sys.stderr)
        sys.exit(1)

    run_id = sys.argv[1]
    interval = int(os.environ.get("CI_POLL_INTERVAL", "15"))
    max_sec = int(os.environ.get("CI_POLL_MAX_SEC", "1800"))

    print(f"[ci-poll] run={run_id} repo={REPO} interval={interval}s max={max_sec}s")
    start = time.time()
    poll_n = 0

    while True:
        elapsed = int(time.time() - start)
        if elapsed >= max_sec:
            print(f"[ci-poll] TIMEOUT after {elapsed}s (CI_POLL_MAX_SEC={max_sec})")
            sys.exit(2)

        poll_n += 1
        data = gh("run", "view", run_id, "-R", REPO, "--json", "jobs,status,conclusion")

        if data is None:
            print(f"[poll #{poll_n} elapsed={elapsed}s] fetch failed, retrying...")
            time.sleep(interval)
            continue

        run_status = data.get("status", "unknown")
        run_conclusion = data.get("conclusion") or ""
        jobs = data.get("jobs", [])

        total = len(jobs)
        completed = sum(1 for j in jobs if j.get("status") == "completed")
        failed_jobs = [j for j in jobs if j.get("conclusion") in FAIL_CONCLUSIONS]

        print(
            f"[poll #{poll_n} elapsed={elapsed}s] run_status={run_status} "
            f"jobs: {completed}/{total} complete  {len(failed_jobs)} failed"
        )

        # Early exit on first failure
        if failed_jobs:
            job = failed_jobs[0]
            job_name = job.get("name", "unknown")
            job_id = job.get("databaseId") or job.get("id", 0)
            conclusion = job.get("conclusion", "failure")
            print("\n[ci-poll] FIRST FAILURE DETECTED")
            print(f"  job: {job_name}")
            print(f"  conclusion: {conclusion}")
            print(f"  job_id: {job_id}")
            print("  fetching failed log ...")
            log_path = fetch_job_log(run_id, job_id, job_name)
            # Print last ~40 lines
            try:
                with open(log_path) as f:
                    lines = f.readlines()
                tail = lines[-40:] if len(lines) > 40 else lines
                print(f"\n--- last {len(tail)} lines of failed log ---")
                print("".join(tail), end="")
                print("--- end ---")
            except Exception:
                pass
            print(f"\n[ci-poll] Full log saved: {log_path}")
            print(f"[ci-poll] RESULT=FAILED job='{job_name}' run={run_id}")
            sys.exit(1)

        # Whole run completed
        if run_status == "completed":
            if run_conclusion == "success":
                print(f"[ci-poll] ALL JOBS GREEN — run {run_id} completed successfully")
                sys.exit(0)
            else:
                # Run failed but no individual job flagged yet (edge case)
                print(f"[ci-poll] Run completed with conclusion={run_conclusion}")
                for j in jobs:
                    jc = j.get("conclusion", "?")
                    jn = j.get("name", "?")
                    if jc not in (None, "success", "skipped"):
                        print(f"  FAILED job: {jn} -> {jc}")
                print(f"[ci-poll] RESULT=FAILED run={run_id}")
                sys.exit(1)

        time.sleep(interval)


if __name__ == "__main__":
    main()
