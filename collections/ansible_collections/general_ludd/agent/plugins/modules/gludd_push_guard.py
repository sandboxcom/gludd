#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_push_guard
  short_description: Enforce push-rate guard via force-push bypass tracking
  description:
    - Wraps the ForcePushTracker (scripts/push_rate_guard.py) as an idempotent
      Ansible module.
    - Tracks consecutive GLUDD_FORCE_PUSH bypasses in a JSON state file and
      rejects further bypasses when the configured C(max_bypasses) threshold
      is exceeded within C(window_hours).
    - Supports three states: C(check) to query whether a bypass is allowed,
      C(record) to persist a bypass event, and C(reset) to clear the counter
      (normal push).
    - Check-mode safe — C(check) and C(record) report what would change
      without mutating the state file.
  options:
    state:
      description:
        - C(check) — query whether a force-push bypass is currently permitted.
          Returns C(bypass_allowed) boolean plus C(current_count) and
          C(max_bypasses).  Never modifies the state file.
        - C(record) — persist a force-push bypass event after a successful
          force-push.  Increments the counter.
        - C(reset) — clear the bypass counter (called after a normal,
          non-force push).
      type: str
      choices: [check, record, reset]
      default: check
    state_file:
      description:
        - Path to the JSON state file that holds bypass tracking data.
        - Defaults to C(.gate-logs/force-push-track.json) relative to the
          playbook working directory when not set.
      type: str
      default: ".gate-logs/force-push-track.json"
    max_bypasses:
      description:
        - Maximum number of consecutive force-push bypasses permitted within
          C(window_hours) before C(bypass_allowed) becomes false.
      type: int
      default: 5
    window_hours:
      description:
        - Rolling window (in hours) within which bypass entries count toward
          the limit.  Entries older than the window are automatically purged.
      type: float
      default: 12.0
  notes:
    - This module does NOT require a running daemon — it operates on a local
      state file only.
    - C(check) and C(record) in check mode report C(changed=true) when they
      WOULD mutate state, but do not actually write.
    - Uses the ForcePushTracker class from C(scripts/push_rate_guard.py).

EXAMPLES:
  - name: Check if a force-push bypass is currently allowed
    general_ludd.agent.gludd_push_guard:
      state: check
      max_bypasses: 3
    register: guard

  - name: Block push playbook when bypass is denied
    ansible.builtin.fail:
      msg: "Force-push blocked: {{ guard.current_count }} bypasses already"
    when: not guard.bypass_allowed

  - name: Record a successful force-push bypass
    general_ludd.agent.gludd_push_guard:
      state: record

  - name: Reset the bypass counter after a normal push
    general_ludd.agent.gludd_push_guard:
      state: reset
      state_file: "/var/tmp/my-force-track.json"

RETURN:
  bypass_allowed:
    description:
      - Whether a force-push bypass is currently permitted.
      - Only returned when C(state=check).
    type: bool
    returned: when state is 'check'
  current_count:
    description:
      - Number of bypass entries currently within the window.
    type: int
    returned: when state is 'check'
  max_bypasses:
    description:
      - Configured maximum consecutive bypasses before blocking.
    type: int
    returned: when state is 'check'
  window_hours:
    description:
      - Configured rolling window in hours.
    type: float
    returned: when state is 'check'
  state_file:
    description:
      - Path to the JSON state file used for tracking.
    type: str
    returned: always
  recorded:
    description:
      - Whether a bypass was recorded in this invocation.
      - Only returned when C(state=record).
    type: bool
    returned: when state is 'record'
  reset:
    description:
      - Whether the counter was reset in this invocation.
      - Only returned when C(state=reset).
    type: bool
    returned: when state is 'reset'
"""

from __future__ import annotations

from pathlib import Path

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.push_rate_guard import (
    ForcePushTracker,
)


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            state=dict(
                type="str",
                default="check",
                choices=["check", "record", "reset"],
            ),
            state_file=dict(
                type="str",
                default=".gate-logs/force-push-track.json",
            ),
            max_bypasses=dict(type="int", default=5),
            window_hours=dict(type="float", default=12.0),
        ),
        supports_check_mode=True,
    )

    state: str = module.params["state"]
    state_file: str = module.params["state_file"]
    max_bypasses: int = module.params["max_bypasses"]
    window_hours: float = module.params["window_hours"]

    tracker = ForcePushTracker(
        state_file=Path(state_file),
        max_bypasses=max_bypasses,
        window_hours=window_hours,
    )

    if state == "check":
        bypass_allowed = tracker.is_bypass_allowed()
        current_count = tracker.count
        module.exit_json(
            failed=False,
            changed=False,
            bypass_allowed=bypass_allowed,
            current_count=current_count,
            max_bypasses=max_bypasses,
            window_hours=window_hours,
            state_file=state_file,
        )

    elif state == "record":
        if module.check_mode:
            module.exit_json(
                failed=False,
                changed=True,
                recorded=True,
                state_file=state_file,
            )
            return

        tracker.record_bypass()
        module.exit_json(
            failed=False,
            changed=True,
            recorded=True,
            state_file=state_file,
        )

    elif state == "reset":
        if module.check_mode:
            module.exit_json(
                failed=False,
                changed=True,
                reset=True,
                state_file=state_file,
            )
            return

        tracker.record_normal_push()
        module.exit_json(
            failed=False,
            changed=True,
            reset=True,
            state_file=state_file,
        )


if __name__ == "__main__":
    main()
