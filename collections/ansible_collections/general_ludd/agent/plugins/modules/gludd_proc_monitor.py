#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_proc_monitor
  short_description: Report resource utilization, I/O and locks for gludd-managed processes
  description:
    - Queries the daemon's managed-process stats API and returns per-process
      resource utilization under C(ansible_facts.gludd_proc_monitor) so a
      playbook (or the model running a job) can branch on real process health —
      CPU, memory (RSS/VMS), I/O counters, open file descriptors, thread count,
      context switches, open files and held locks.
    - When C(pid) is C(0) (the default) the module enumerates every
      gludd-managed process via C(GET /admin/processes) and then fetches stats
      for each B(alive) process. A process that exits mid-scan (its per-pid stats
      call returns C(404)) is skipped rather than failing the whole task.
    - When C(pid) is greater than C(0) only that single process's stats are
      returned.
    - B(Read-only) and B(check-mode safe) — it performs no writes. In check mode
      no daemon call is made and an empty fact set is returned (mirroring
      C(gludd_osquery)).
    - Talks to the daemon over HTTP via the shared C(GluddClient) (stdlib
      C(urllib) only — no third-party deps in the managed-node venv).
  options:
    pid:
      description:
        - PID of a single gludd-managed process to monitor. When C(0) (the
          default) B(all) managed processes are monitored.
      type: int
      default: 0
    daemon_url:
      description: Base URL of the daemon.
      type: str
      default: "http://localhost:8000"
    psk:
      description: Pre-shared key for daemon auth.
      type: str
      no_log: true
      default: ""
    timeout:
      description: Per-request timeout in seconds for daemon calls.
      type: int
      default: 10

EXAMPLES:
  - name: Monitor all gludd-managed processes
    general_ludd.agent.gludd_proc_monitor:
    register: procs

  - name: Branch on a process using too much memory
    ansible.builtin.debug:
      msg: "a managed process exceeds 1 GiB RSS"
    when: >-
      ansible_facts.gludd_proc_monitor.processes
      | selectattr('memory.rss', 'gt', 1073741824) | list | length > 0

  - name: Monitor a single process by pid
    general_ludd.agent.gludd_proc_monitor:
      pid: 4242

RETURN:
  ansible_facts:
    description: Facts dict containing the C(gludd_proc_monitor) result.
    type: dict
    returned: always
    contains:
      gludd_proc_monitor:
        description: The managed-process monitor result — C(processes) and C(count).
        type: dict
        returned: always
        contains:
          processes:
            description:
              - List of per-process stats dicts. Each contains C(pid),
                C(cpu_percent), C(memory) (C(rss)/C(vms)), C(io)
                (C(read_bytes)/C(write_bytes)/C(read_count)/C(write_count) or
                C(null)), C(num_fds), C(num_threads), C(num_ctx_switches),
                C(status), C(open_files) and C(locks).
            type: list
          count:
            description: Number of process stats dicts returned.
            type: int
"""

from __future__ import annotations

from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    error_result,
    ok_result,
)


def _is_error(resp: dict[str, Any]) -> bool:
    """A transport error from GluddClient surfaces as a `_error` key."""
    return "_error" in resp


def _status(resp: dict[str, Any]) -> int:
    """HTTP status code stamped onto a GluddClient response (0 for transport error)."""
    return int(resp.get("_status", 0) or 0)


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            pid=dict(type="int", default=0),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=10),
        ),
        supports_check_mode=True,
    )

    pid: int = module.params["pid"]
    daemon_url: str = module.params["daemon_url"]
    psk: str = module.params["psk"]
    timeout: int = module.params["timeout"]

    # Check mode — read-only module, but mirror gludd_osquery: do not touch the
    # daemon, return an empty fact set.
    if module.check_mode:
        module.exit_json(
            **ok_result(
                {"ansible_facts": {"gludd_proc_monitor": {"processes": [], "count": 0}}},
                changed=False,
            )
        )
        return

    client = GluddClient(
        base_url=daemon_url,
        psk=psk,
        timeout=timeout,
    )

    if pid > 0:
        resp = client.get(f"/admin/processes/{pid}/stats")
        if _is_error(resp):
            module.fail_json(**error_result(f"failed to reach daemon for process {pid} stats: {resp.get('_error')}"))
            return
        status = _status(resp)
        if status < 200 or status >= 300:
            detail = resp.get("detail") or resp.get("_raw") or "no detail"
            module.fail_json(**error_result(f"daemon returned HTTP {status} for process {pid} stats: {detail}"))
            return
        stats = {k: v for k, v in resp.items() if not k.startswith("_")}
        module.exit_json(
            **ok_result(
                {"ansible_facts": {"gludd_proc_monitor": {"processes": [stats], "count": 1}}},
                changed=False,
            )
        )
        return

    # pid == 0 -> enumerate all managed processes, then fetch each alive one's stats.
    listing = client.get("/admin/processes")
    if _is_error(listing):
        module.fail_json(**error_result(f"failed to reach daemon for process list: {listing.get('_error')}"))
        return
    status = _status(listing)
    if status < 200 or status >= 300:
        detail = listing.get("detail") or listing.get("_raw") or "no detail"
        module.fail_json(**error_result(f"daemon returned HTTP {status} for process list: {detail}"))
        return

    procs = listing.get("processes") or []
    if not isinstance(procs, list):
        module.fail_json(**error_result("unexpected daemon output (expected 'processes' to be a list)"))
        return

    collected: list[dict[str, Any]] = []
    for entry in procs:
        if not isinstance(entry, dict):
            continue
        if not entry.get("alive", False):
            continue
        epid = entry.get("pid")
        if epid is None:
            continue
        stats_resp = client.get(f"/admin/processes/{epid}/stats")
        if _is_error(stats_resp):
            module.fail_json(
                **error_result(f"failed to reach daemon for process {epid} stats: {stats_resp.get('_error')}")
            )
            return
        sstatus = _status(stats_resp)
        # Tolerate a process that exited mid-scan (404) — skip, don't fail.
        if sstatus == 404:
            continue
        if sstatus < 200 or sstatus >= 300:
            detail = stats_resp.get("detail") or stats_resp.get("_raw") or "no detail"
            module.fail_json(**error_result(f"daemon returned HTTP {sstatus} for process {epid} stats: {detail}"))
            return
        collected.append({k: v for k, v in stats_resp.items() if not k.startswith("_")})

    module.exit_json(
        **ok_result(
            {
                "ansible_facts": {
                    "gludd_proc_monitor": {
                        "processes": collected,
                        "count": len(collected),
                    }
                }
            },
            changed=False,
        )
    )


if __name__ == "__main__":
    main()
