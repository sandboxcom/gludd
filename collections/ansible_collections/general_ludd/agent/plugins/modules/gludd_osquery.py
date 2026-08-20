#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: gludd_osquery
  short_description: Query live system state via osquery and inject rows as ansible_facts
  description:
    - Runs a B(read-only) C(SELECT) query against C(osqueryi --json) and returns
      the result rows under C(ansible_facts.gludd_osquery) so a playbook (or the
      model running a job) can branch on real system state — processes, users,
      mounts, network interfaces, installed packages, system_info, etc.
    - SECURITY — only C(SELECT) queries are permitted. The query is validated to
      start with C(SELECT) (after optional C(WITH ...) CTEs) and is rejected if it
      contains any mutating / side-effecting keyword
      (C(INSERT)/C(UPDATE)/C(DELETE)/C(DROP)/C(CREATE)/C(ALTER)/C(ATTACH)/
      C(DETACH)/C(PRAGMA)/C(REPLACE)/C(VACUUM)). osquery's virtual tables are
      mostly read-only, but this module refuses to even hand a write-shaped query
      to the binary.
    - The osquery binary is resolved from the daemon's filestore
      (C(binaries/osquery), downloaded on first use by the BinaryBootstrapper)
      when running in the same venv as the daemon; otherwise it falls back to an
      C(osqueryi) on the system C(PATH).
    - Runs the binary via an explicit argument list (never C(shell=True)).
    - Read-only and check-mode safe — it performs no writes. In check mode the
      query is validated and the binary located, but osquery is not executed.
  options:
    query:
      description: A single read-only osquery SQL C(SELECT) statement.
      type: str
      required: true
    timeout:
      description: Subprocess timeout in seconds for the osqueryi call.
      type: int
      default: 10
    osquery_path:
      description:
        - Explicit path to the C(osqueryi) binary. When unset the module resolves
          it from the daemon filestore, then from the system C(PATH).
      type: str
      default: ""
    daemon_url:
      description: Base URL of the daemon (reserved for future remote resolution).
      type: str
      default: "http://localhost:8000"
    psk:
      description: Pre-shared key for daemon auth.
      type: str
      no_log: true
      default: ""

EXAMPLES:
  - name: List running processes
    general_ludd.agent.gludd_osquery:
      query: "SELECT pid, name, state FROM processes LIMIT 20"
    register: procs

  - name: Branch on a process being present
    ansible.builtin.debug:
      msg: "nginx is running"
    when: >-
      ansible_facts.gludd_osquery.rows
      | selectattr('name', 'equalto', 'nginx') | list | length > 0

  - name: System info
    general_ludd.agent.gludd_osquery:
      query: "SELECT hostname, cpu_brand, physical_memory FROM system_info"

RETURN:
  ansible_facts:
    description: Facts dict containing the C(gludd_osquery) result.
    type: dict
    returned: always
    contains:
      gludd_osquery:
        description: The osquery result — C(rows), C(count), and the C(query) run.
        type: dict
        returned: always
        contains:
          rows:
            description: List of result rows (each a dict of column->value).
            type: list
          count:
            description: Number of rows returned.
            type: int
          query:
            description: The validated query that was executed.
            type: str
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.gludd import (
    error_result,
    ok_result,
)

# Keywords that must never appear in a query handed to osquery. Matched as whole
# words, case-insensitively, anywhere in the (comment-stripped) query.
_FORBIDDEN_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "CREATE",
    "ALTER",
    "ATTACH",
    "DETACH",
    "PRAGMA",
    "REPLACE",
    "VACUUM",
    "REINDEX",
    "TRIGGER",
)

# A query must START with SELECT, optionally preceded by one or more WITH-CTEs.
_SELECT_PREFIX_RE = re.compile(r"^\s*(WITH\b.*?\bSELECT\b|SELECT\b)", re.IGNORECASE | re.DOTALL)
_FORBIDDEN_RE = re.compile(
    r"\b(" + "|".join(_FORBIDDEN_KEYWORDS) + r")\b", re.IGNORECASE
)
# Strip /* ... */ and -- ... line comments so a forbidden keyword can't hide in
# a comment (or, conversely, so a benign comment can't trip the blacklist).
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


def _strip_comments(query: str) -> str:
    no_block = _BLOCK_COMMENT_RE.sub(" ", query)
    return _LINE_COMMENT_RE.sub(" ", no_block)


def validate_select_only(query: str) -> str | None:
    """Return an error string if ``query`` is not a safe read-only SELECT, else None."""
    if not query or not query.strip():
        return "query is empty"
    stripped = _strip_comments(query).strip()
    if not stripped:
        return "query is empty after stripping comments"
    # Reject stacked statements (only one statement allowed). A single trailing
    # semicolon is fine; an internal one means a second statement.
    body = stripped.rstrip().rstrip(";").rstrip()
    if ";" in body:
        return "multiple statements are not permitted (single SELECT only)"
    if _FORBIDDEN_RE.search(body):
        match = _FORBIDDEN_RE.search(body)
        kw = match.group(1).upper() if match else "?"
        return f"forbidden keyword in query: {kw} (only read-only SELECT is allowed)"
    if not _SELECT_PREFIX_RE.match(body):
        return "query must be a SELECT statement (optionally with leading WITH CTEs)"
    return None


def resolve_osquery_binary(explicit: str = "", module: object | None = None) -> str | None:
    """Resolve the osqueryi binary path.

    Order: explicit managed-host path, then the managed host's system PATH.
    Returns ``None`` if no usable binary is found.
    """
    if explicit:
        if os.path.isfile(explicit) and os.access(explicit, os.X_OK):
            return explicit
        return None

    # Controller filestore binaries are deliberately not visible to managed
    # hosts. Roles provision osquery separately before this module runs.
    on_path = shutil.which("osqueryi")
    if on_path:
        return on_path
    return None


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            query=dict(type="str", required=True),
            timeout=dict(type="int", default=10),
            osquery_path=dict(type="str", default=""),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
        ),
        supports_check_mode=True,
    )

    query: str = module.params["query"]
    timeout: int = module.params["timeout"]
    explicit_path: str = module.params["osquery_path"]
    if explicit_path and os.path.isfile(explicit_path) and not os.access(explicit_path, os.X_OK):
        module.fail_json(
            **error_result(
                f"osquery binary exists but is not executable "
                f"(chmod +x {explicit_path})"
            )
        )
        return

    # 1. SELECT-only validation (always, before anything touches the binary).
    err = validate_select_only(query)
    if err is not None:
        module.fail_json(**error_result(f"query rejected: {err}"))
        return

    # 2. Resolve the binary.
    binary = resolve_osquery_binary(explicit_path, module=module)
    if binary is None:
        module.fail_json(
            **error_result(
                "osquery binary not found in the explicit managed-host path or PATH; "
                "provision osquery in the role environment before this task"
            )
        )
        return

    # 3. Check mode — validated + located, but do not run.
    if module.check_mode:
        module.exit_json(
            **ok_result(
                {
                    "ansible_facts": {
                        "gludd_osquery": {"rows": [], "count": 0, "query": query.strip()}
                    },
                    "osquery_path": binary,
                },
                changed=False,
            )
        )
        return

    # 4. Run osqueryi via an explicit arg list (never shell=True).
    try:
        proc = subprocess.run(
            [binary, "--json", query],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        module.fail_json(**error_result(f"osquery timed out after {timeout}s"))
        return
    except OSError as exc:
        module.fail_json(**error_result(f"failed to execute osquery: {exc}"))
        return

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        module.fail_json(
            **error_result(
                f"osquery exited {proc.returncode}: {stderr or 'no stderr'}"
            )
        )
        return

    # 5. Parse the JSON rows.
    raw = (proc.stdout or "").strip()
    if not raw:
        rows: list[Any] = []
    else:
        try:
            rows = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            module.fail_json(**error_result(f"failed to parse osquery JSON output: {exc}"))
            return
    if not isinstance(rows, list):
        module.fail_json(**error_result("unexpected osquery output (expected a JSON array of rows)"))
        return

    module.exit_json(
        **ok_result(
            {
                "ansible_facts": {
                    "gludd_osquery": {
                        "rows": rows,
                        "count": len(rows),
                        "query": query.strip(),
                    }
                }
            },
            changed=False,
        )
    )


if __name__ == "__main__":
    main()
