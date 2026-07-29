"""Isolated process entrypoint for the Ansible ``gludd_agent_run`` module.

Ansible executes collection modules from an AnsiballZ zip payload. Importing
the controller application in that process mixes the payload's partial
``ansible.module_utils`` package with the controller's full Ansible package.
Run the controller-only agent loop in a fresh interpreter so the module process
can always serialize its result with the payload's own Ansible runtime.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from typing import Any


def run_local(
    prompt: str,
    system_prompt: str,
    model_profile: str | None,
    max_iterations: int,
) -> dict[str, Any]:
    """Run ``ToolCallLoop`` in a controller-only interpreter."""
    try:
        from general_ludd.execution.tool_loop import ToolCallLoop
        from general_ludd.models.gateway import ModelGateway
        from general_ludd.schemas.job import JobSpec

        gateway = ModelGateway()
        loop_runner = ToolCallLoop(
            model_gateway=gateway,
            max_iterations=max_iterations,
        )
        job = JobSpec(
            job_id=str(uuid.uuid4()),
            todo_id="agent-run",
            playbook="noop.yml",
            queue="default",
            prompt_text=prompt,
            model_profile=model_profile or "",
        )
        answer = asyncio.run(
            loop_runner.run_with_tools(job, system_prompt, prompt)
        )
        return {
            "failed": False,
            "changed": False,
            "answer": answer,
            "tool_calls": [],
            "usage": {},
            "iterations": 1,
        }
    except ImportError as exc:
        return {
            "failed": True,
            "changed": False,
            "msg": f"general_ludd not importable for local run: {exc}",
        }
    except Exception as exc:
        return {
            "failed": True,
            "changed": False,
            "msg": f"local agent run failed: {exc}",
        }


def main() -> int:
    """Read one JSON request from stdin and write one JSON result to stdout."""
    try:
        request = json.load(sys.stdin)
        result = run_local(
            prompt=str(request["prompt"]),
            system_prompt=str(request["system_prompt"]),
            model_profile=(
                str(request["model_profile"])
                if request.get("model_profile")
                else None
            ),
            max_iterations=int(request["max_iterations"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        result = {
            "failed": True,
            "changed": False,
            "msg": f"invalid local agent request: {exc}",
        }
    json.dump(result, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
