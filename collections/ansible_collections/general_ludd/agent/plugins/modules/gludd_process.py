#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_process
  short_description: List, signal, and inspect daemon-managed processes
  description:
    - Talks to the general_ludd daemon's managed-process API so a playbook (or
      the model running a job) can enumerate the processes the daemon launched,
      inspect a live process's resource usage, or deliver a signal to one — all
      without ever calling C(kill)/C(ps) directly on the managed node.
    - C(action=list) calls C(GET /admin/processes) and returns the registry of
      managed processes under C(ansible_facts.gludd_process).
    - C(action=status) calls C(GET /admin/processes/{pid}/stats) and returns a
      live psutil snapshot for one process under
      C(ansible_facts.gludd_process.stats).
    - C(action=signal) calls C(POST /admin/processes/{pid}/signal) to deliver a
      signal (optionally to the whole process group). Signal delivery is a state
      change, so it reports C(changed=True) on success.
    - SECURITY — for C(action=signal) the signal name is validated client-side
      against a small allow-list before any request is sent (defence in depth;
      the daemon enforces the same set server-side). Disallowed signal names are
      rejected without contacting the daemon.
    - Read-only and check-mode safe for C(list)/C(status). In check mode a
      C(signal) request is B(not) sent — the module reports the change it would
      make (C(changed=True)) instead.
  options:
    action:
      description:
        - The operation to perform. C(list) enumerates managed processes,
          C(status) fetches a live stats snapshot for one C(pid), and C(signal)
          delivers a signal to one C(pid).
      type: str
      required: true
      choices: [list, signal, status]
    pid:
      description:
        - Target process id. Required (and must be C(> 0)) for C(action=signal)
          and C(action=status). Ignored for C(action=list).
      type: int
      default: 0
    signal:
      description:
        - Signal name to deliver when C(action=signal). Must be one of the
          allow-listed names (C(SIGTERM), C(SIGINT), C(SIGHUP), C(SIGQUIT),
          C(SIGUSR1), C(SIGUSR2), C(SIGKILL), C(SIGSTOP), C(SIGCONT)).
      type: str
      default: SIGTERM
    group:
      description:
        - When C(true) and C(action=signal), deliver the signal to the target's
          whole process group rather than the single process.
      type: bool
      default: false
    daemon_url:
      description: Base URL of the general_ludd daemon.
      type: str
      default: "http://localhost:8000"
    psk:
      description: Pre-shared key for daemon auth (Bearer / X-PSK).
      type: str
      no_log: true
      default: ""
    timeout:
      description: Per-request HTTP timeout in seconds.
      type: int
      default: 10

EXAMPLES:
  - name: List all daemon-managed processes
    general_ludd.agent.gludd_process:
      action: list
    register: managed

  - name: Show how many managed processes are alive
    ansible.builtin.debug:
      msg: "{{ ansible_facts.gludd_process.count }} managed processes"

  - name: Inspect one managed process
    general_ludd.agent.gludd_process:
      action: status
      pid: 4242

  - name: Gracefully terminate a managed process group
    general_ludd.agent.gludd_process:
      action: signal
      pid: 4242
      signal: SIGTERM
      group: true

RETURN:
  ansible_facts:
    description: Facts dict containing the C(gludd_process) result.
    type: dict
    returned: always
    contains:
      gludd_process:
        description: The process-API result for the requested action.
        type: dict
        returned: always
        contains:
          processes:
            description: List of managed-process records (C(action=list)).
            type: list
          count:
            description: Number of managed processes (C(action=list)).
            type: int
          stats:
            description: psutil snapshot for one process (C(action=status)).
            type: dict
  pid:
    description: The target pid for a C(signal)/C(status) action.
    type: int
    returned: when action is signal or status
  signal:
    description: The signal that was (or would be) delivered.
    type: str
    returned: when action is signal
  note:
    description: Human-readable note describing a check-mode no-op.
    type: str
    returned: when action is signal in check mode
"""

from __future__ import annotations

from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    GluddClient,
    error_result,
    ok_result,
)

# Signal names this module will deliver. Defence-in-depth: the daemon enforces
# the same allow-list server-side. Kept as a set for O(1) membership checks.
ALLOWED_SIGNALS = frozenset(
    {
        "SIGTERM",
        "SIGINT",
        "SIGHUP",
        "SIGQUIT",
        "SIGUSR1",
        "SIGUSR2",
        "SIGKILL",
        "SIGSTOP",
        "SIGCONT",
    }
)


def _error_from_response(resp: dict[str, Any], default_msg: str) -> str:
    """Extract the daemon's error message from a GluddClient response dict.

    GluddClient surfaces transport errors as ``_error`` and HTTP status as
    ``_status``. Daemon error bodies typically carry ``detail`` or ``msg``.
    """
    transport_err = resp.get("_error")
    if transport_err:
        return f"daemon request failed: {transport_err}"
    detail = resp.get("detail") or resp.get("msg") or resp.get("error")
    status = resp.get("_status", 0)
    if detail:
        return f"daemon returned {status}: {detail}"
    return f"{default_msg} (status {status})"


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            action=dict(type="str", required=True, choices=["list", "signal", "status"]),
            pid=dict(type="int", default=0),
            signal=dict(type="str", default="SIGTERM"),
            group=dict(type="bool", default=False),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
            timeout=dict(type="int", default=10),
        ),
        supports_check_mode=True,
    )

    action: str = module.params["action"]
    pid: int = module.params["pid"]
    signal_name: str = module.params["signal"]
    group: bool = module.params["group"]
    daemon_url: str = module.params["daemon_url"]
    psk: str = module.params["psk"]
    timeout: int = module.params["timeout"]

    client = GluddClient(
        base_url=daemon_url,
        psk=psk,
        timeout=timeout,
    )

    # ------------------------------------------------------------------ list
    if action == "list":
        resp = client.get("/admin/processes")
        status = resp.get("_status", 0)
        if resp.get("_error") or status < 200 or status >= 300:
            module.fail_json(**error_result(_error_from_response(resp, "failed to list processes")))
            return
        processes = resp.get("processes", [])
        count = resp.get("count", len(processes) if isinstance(processes, list) else 0)
        module.exit_json(
            **ok_result(
                {
                    "ansible_facts": {
                        "gludd_process": {
                            "processes": processes,
                            "count": count,
                        }
                    }
                },
                changed=False,
            )
        )
        return

    # ---------------------------------------------------------------- status
    if action == "status":
        if pid <= 0:
            module.fail_json(**error_result("action=status requires a positive 'pid'"))
            return
        resp = client.get(f"/admin/processes/{pid}/stats")
        status = resp.get("_status", 0)
        if resp.get("_error") or status < 200 or status >= 300:
            module.fail_json(**error_result(_error_from_response(resp, f"failed to get stats for pid {pid}")))
            return
        stats = {k: v for k, v in resp.items() if not k.startswith("_")}
        module.exit_json(
            **ok_result(
                {
                    "ansible_facts": {"gludd_process": {"stats": stats}},
                    "pid": pid,
                },
                changed=False,
            )
        )
        return

    # ---------------------------------------------------------------- signal
    # action == "signal"
    if pid <= 0:
        module.fail_json(**error_result("action=signal requires a positive 'pid'"))
        return

    if signal_name not in ALLOWED_SIGNALS:
        allowed = ", ".join(sorted(ALLOWED_SIGNALS))
        module.fail_json(**error_result(f"signal '{signal_name}' is not permitted; allowed signals: {allowed}"))
        return

    # Check mode — describe the change, do NOT deliver the signal.
    # A signal request is mutating, so check mode reports changed=True to show
    # that a real run would alter process state.
    if module.check_mode:
        target = f"process group of {pid}" if group else str(pid)
        module.exit_json(
            **ok_result(
                {
                    "pid": pid,
                    "signal": signal_name,
                    "note": f"would send {signal_name} to {target}",
                },
                changed=True,
            )
        )
        return

    resp = client.post(
        f"/admin/processes/{pid}/signal",
        {"signal": signal_name, "group": group},
    )
    status = resp.get("_status", 0)
    if resp.get("_error") or status < 200 or status >= 300:
        module.fail_json(**error_result(_error_from_response(resp, f"failed to send {signal_name} to pid {pid}")))
        return

    module.exit_json(
        **ok_result(
            {
                "pid": pid,
                "signal": signal_name,
                "group": group,
            },
            changed=True,
        )
    )


if __name__ == "__main__":
    main()
