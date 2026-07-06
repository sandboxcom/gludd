"""Tests for ``gludd project init`` — thin CLI wrapper over the
``general_ludd.agent.project_init`` ansible role.

The Python CLI does only arg parsing + role invocation. These tests pin that
contract: the CLI invokes ``AnsibleRunnerAdapter.run_playbook`` with
``project_init.yml`` and the right extra_vars; CLI rejects missing
``--namespace`` before invoking the role; failures propagate; the role's
debug summary is surfaced to stdout.
"""

from __future__ import annotations

import argparse
import io
from contextlib import redirect_stderr, redirect_stdout
from typing import Any
from unittest.mock import patch

import pytest

from general_ludd.cli import build_parser
from general_ludd.cli_project_init import _cmd_project_init

_SUCCESS_RESULT: dict[str, Any] = {
    "status": "successful",
    "rc": 0,
    "events": [
        {
            "event_data": {
                "task": "Report scaffold summary",
                "res": {
                    "msg": (
                        "Scaffolded project collection at: "
                        ".gludd/collections/ansible_collections/acme/project | "
                        "FQCN prefix: acme.project.<role_or_module>"
                    )
                },
            }
        }
    ],
}


class _StubAdapter:
    """Stand-in for AnsibleRunnerAdapter that records run_playbook calls."""

    def __init__(self, *, result: dict[str, Any] | None = None) -> None:
        self.run_calls: list[tuple[str, dict[str, Any]]] = []
        self._result = result or _SUCCESS_RESULT
        self._registered: list[str] = []

    def list_playbooks(self) -> list[str]:
        return list(self._registered)

    def register_playbook(self, name: str, path: str) -> None:
        self._registered.append(name)

    def run_playbook(
        self,
        playbook: str,
        private_data_dir: str | None = None,
        extravars: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        **runner_kwargs: Any,
    ) -> dict[str, Any]:
        self.run_calls.append((playbook, dict(extravars or {})))
        return self._result


def _ns(**kwargs: Any) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def _run_init(
    project_dir: str,
    namespace: str,
    collection: str = "project",
    force: bool = False,
    stub: _StubAdapter | None = None,
) -> str:
    args = _ns(
        project_dir=project_dir,
        namespace=namespace,
        collection=collection,
        force=force,
    )
    buf = io.StringIO()
    s = stub or _StubAdapter()
    with patch(
        "general_ludd.ansible.runner.AnsibleRunnerAdapter",
        return_value=s,
    ), redirect_stdout(buf):
        _cmd_project_init(args)
    return buf.getvalue()


def test_init_invokes_ansible_role() -> None:
    stub = _StubAdapter()
    _run_init("/tmp/proj", namespace="acme", stub=stub)
    assert stub.run_calls, "run_playbook was never invoked"
    playbook, _ = stub.run_calls[0]
    assert playbook == "project_init.yml"


def test_init_passes_namespace_collection_force_as_extra_vars() -> None:
    stub = _StubAdapter()
    _run_init(
        "/tmp/proj",
        namespace="acme",
        collection="platform",
        force=True,
        stub=stub,
    )
    _, extra_vars = stub.run_calls[0]
    assert extra_vars["collection_namespace"] == "acme"
    assert extra_vars["collection_name"] == "platform"
    assert extra_vars["force"] is True
    assert extra_vars["project_dir"] == "/tmp/proj"


def test_init_propagates_role_failure() -> None:
    failed = _StubAdapter(
        result={"status": "failed", "rc": 1, "events": [], "error": "boom"}
    )
    with pytest.raises(SystemExit) as excinfo:
        _run_init("/tmp/proj", namespace="acme", stub=failed)
    assert excinfo.value.code == 1


def test_init_prints_role_output_summary() -> None:
    out = _run_init("/tmp/proj", namespace="acme")
    assert "Scaffolded project collection" in out
    assert "acme.project" in out


def test_init_namespace_required_still_enforced_at_cli_layer() -> None:
    parser, _ = build_parser()
    err = io.StringIO()
    with redirect_stderr(err), pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["project", "init"])
    assert excinfo.value.code == 2
    assert "--namespace is required" in err.getvalue() or "required" in err.getvalue()


def test_init_default_collection_name_is_project() -> None:
    stub = _StubAdapter()
    _run_init("/tmp/proj", namespace="acme", stub=stub)
    _, extra_vars = stub.run_calls[0]
    assert extra_vars["collection_name"] == "project"
