#!/usr/bin/env python3
"""Live GitHub CI annotations poller — surfaces failure annotations as they appear."""
import json
import os
import subprocess
import sys
import time

REPO = "sandboxcom/gludd"
FAIL_LEVELS = {"failure", "error"}
FAIL_CONCLUSIONS = {"failure", "cancelled", "timed_out"}


def gh(*args: str) -> "dict | list | None":
    """Run gh and return parsed JSON, or None on error."""
    cmd = ["gh"] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            print(
                f"[ci-annotations] gh warning: {r.stderr.strip()[:200]}",
                file=sys.stderr,
            )
            return None
        return json.loads(r.stdout)
    except Exception as e:
        print(f"[ci-annotations] gh error: {e}", file=sys.stderr)
        return None


def fetch_jobs(run_id: str) -> "list[dict] | None":
    """Return the jobs list for the given run, or None on error."""
    data = gh("api", f"repos/{REPO}/actions/runs/{run_id}/jobs")
    if data is None:
        return None
    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        print(
            f"[ci-annotations] unexpected jobs payload type: {type(jobs)}",
            file=sys.stderr,
        )
        return None
    return jobs


def check_run_id_from_url(check_run_url: str) -> "str | None":
    """Extract the check_run_id (last path segment) from a check_run_url."""
    if not check_run_url:
        return None
    return check_run_url.rstrip("/").rsplit("/", 1)[-1]


def fetch_annotations(check_run_id: str) -> "list[dict] | None":
    """Fetch all annotations for a check-run (paginated)."""
    data = gh(
        "api",
        "--paginate",
        f"repos/{REPO}/check-runs/{check_run_id}/annotations",
    )
    if data is None:
        return None
    if not isinstance(data, list):
        print(
            f"[ci-annotations] unexpected annotations payload type: {type(data)}",
            file=sys.stderr,
        )
        return None
    return data


def ann_key(ann: dict) -> "tuple[str, int | None, str]":
    """De-dupe key for an annotation."""
    return (
        ann.get("path", ""),
        ann.get("start_line"),
        ann.get("message", ""),
    )


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: ci_annotations_poll.py <run-id>", file=sys.stderr)
        sys.exit(1)

    run_id = sys.argv[1]
    interval = int(os.environ.get("CI_ANN_INTERVAL", "45"))
    max_sec = int(os.environ.get("CI_ANN_MAX_SEC", "3600"))
    early_exit = os.environ.get("CI_ANN_EARLY_EXIT", "0") == "1"

    print(
        f"[ci-annotations] run={run_id} repo={REPO} "
        f"interval={interval}s max={max_sec}s early_exit={early_exit}"
    )
    start = time.time()
    poll_n = 0

    # Track seen annotation keys per check_run_id
    seen: "dict[str, set[tuple]]" = {}
    # Track resolved check_run_ids for job ids we've already looked up
    job_check_run: "dict[int, str]" = {}

    total_new = 0
    run_succeeded = False

    while True:
        elapsed = int(time.time() - start)
        if elapsed >= max_sec:
            print(
                f"[ci-annotations] TIMEOUT after {elapsed}s "
                f"(CI_ANN_MAX_SEC={max_sec}); total new annotations={total_new}"
            )
            sys.exit(2)

        poll_n += 1
        jobs = fetch_jobs(run_id)
        if jobs is None:
            print(
                f"[ci-annotations][poll #{poll_n} elapsed={elapsed}s] "
                "fetch failed, retrying next cycle",
                file=sys.stderr,
            )
            time.sleep(interval)
            continue

        total_jobs = len(jobs)
        completed_jobs = [j for j in jobs if j.get("status") == "completed"]
        in_progress_jobs = [j for j in jobs if j.get("status") != "completed"]

        print(
            f"[ci-annotations][poll #{poll_n} elapsed={elapsed}s] "
            f"jobs: {len(completed_jobs)}/{total_jobs} complete"
        )

        # Poll annotations for completed + in-progress jobs
        for job in jobs:
            job_id: int = job.get("id", 0)
            job_name: str = job.get("name", "unknown")
            job_status: str = job.get("status", "")
            check_run_url: str = job.get("check_run_url", "")

            # Resolve check_run_id (cache it)
            if job_id not in job_check_run:
                cr_id = check_run_id_from_url(check_run_url)
                if not cr_id:
                    print(
                        f"[ci-annotations] could not resolve check_run_id "
                        f"for job '{job_name}' (url={check_run_url!r})",
                        file=sys.stderr,
                    )
                    continue
                job_check_run[job_id] = cr_id
            cr_id = job_check_run[job_id]

            if cr_id not in seen:
                seen[cr_id] = set()

            annotations = fetch_annotations(cr_id)
            if annotations is None:
                print(
                    f"[ci-annotations] annotation fetch failed for job '{job_name}' "
                    f"(check_run_id={cr_id}), skipping",
                    file=sys.stderr,
                )
                continue

            for ann in annotations:
                level: str = ann.get("annotation_level", "")
                if level not in FAIL_LEVELS:
                    continue

                key = ann_key(ann)
                if key in seen[cr_id]:
                    continue
                seen[cr_id].add(key)

                path = ann.get("path", "?")
                line = ann.get("start_line", "?")
                msg = (ann.get("message") or "")[:200]
                print(f"[ann] {path}:{line} {level}: {msg}")
                total_new += 1

                if early_exit:
                    print(
                        f"[ci-annotations] CI_ANN_EARLY_EXIT=1 — "
                        f"exiting on first failure annotation"
                    )
                    sys.exit(1)

        # Check if all jobs are done
        all_done = len(in_progress_jobs) == 0 and total_jobs > 0
        if all_done:
            failed = [
                j for j in completed_jobs
                if j.get("conclusion") in FAIL_CONCLUSIONS
            ]
            run_succeeded = len(failed) == 0
            break

        time.sleep(interval)

    # Final summary
    elapsed = int(time.time() - start)
    print(
        f"\n[ci-annotations] DONE — elapsed={elapsed}s "
        f"total_new_failure_annotations={total_new}"
    )
    if run_succeeded:
        print(f"[ci-annotations] RESULT=SUCCESS run={run_id}")
        sys.exit(0)
    else:
        print(f"[ci-annotations] RESULT=FAILED run={run_id}")
        sys.exit(1)


if __name__ == "__main__":
    main()
