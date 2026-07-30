"""Behavioral tests for gludd_agent_run._run_local (W6.8 gap coverage).

W6.8 decision: uses ToolCallLoop from execution.tool_loop in-process.
Prior coverage was 100% via static source-string checks — no actual execution.
These tests run _run_local with mocked dependencies and document the FIXED
behavior after W6.8 repairs (JobSpec missing fields + gateway sig mismatch).

Run: make test-iso TESTFILE='tests/unit/test_gludd_agent_run_behavioral.py'
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import helpers — _run_local lives in an Ansible module which imports
# AnsibleModule at the top level. We mock that away before import.
# ---------------------------------------------------------------------------

def _import_run_local():
    """Import _run_local from gludd_agent_run, mocking AnsibleModule."""
    module_path = os.path.join(
        os.path.dirname(__file__),
        "../../collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_agent_run.py",
    )
    module_path = os.path.abspath(module_path)

    ansible_mock = MagicMock()
    gludd_utils_mock = MagicMock()
    gludd_utils_mock.ok_result = lambda data, changed=False: {
        "failed": False, "changed": changed, **data
    }
    gludd_utils_mock.error_result = lambda msg, **extra: {
        "failed": True, "changed": False, "msg": msg, **extra
    }

    with mock.patch.dict(sys.modules, {
        "ansible": ansible_mock,
        "ansible.module_utils": ansible_mock.module_utils,
        "ansible.module_utils.basic": ansible_mock.module_utils.basic,
        "ansible_collections": MagicMock(),
        "ansible_collections.general_ludd": MagicMock(),
        "ansible_collections.general_ludd.agent": MagicMock(),
        "ansible_collections.general_ludd.agent.plugins": MagicMock(),
        "ansible_collections.general_ludd.agent.plugins.module_utils": MagicMock(),
        "ansible_collections.general_ludd.agent.plugins.module_utils.gludd": gludd_utils_mock,
    }):
        import importlib.util
        spec = importlib.util.spec_from_file_location("gludd_agent_run", module_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod._run_local


def _import_agent_run_module():
    """Import the complete module with its Ansible dependencies isolated."""
    module_path = os.path.join(
        os.path.dirname(__file__),
        "../../collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_agent_run.py",
    )
    module_path = os.path.abspath(module_path)

    ansible_mock = MagicMock()
    gludd_utils_mock = MagicMock()
    gludd_utils_mock.ok_result = lambda data, changed=False: {
        "failed": False, "changed": changed, **data
    }
    gludd_utils_mock.error_result = lambda msg, **extra: {
        "failed": True, "changed": False, "msg": msg, **extra
    }

    with mock.patch.dict(sys.modules, {
        "ansible": ansible_mock,
        "ansible.module_utils": ansible_mock.module_utils,
        "ansible.module_utils.basic": ansible_mock.module_utils.basic,
        "ansible_collections": MagicMock(),
        "ansible_collections.general_ludd": MagicMock(),
        "ansible_collections.general_ludd.agent": MagicMock(),
        "ansible_collections.general_ludd.agent.plugins": MagicMock(),
        "ansible_collections.general_ludd.agent.plugins.module_utils": MagicMock(),
        "ansible_collections.general_ludd.agent.plugins.module_utils.gludd": gludd_utils_mock,
    }):
        import importlib.util
        spec = importlib.util.spec_from_file_location("gludd_agent_run_module", module_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


# ---------------------------------------------------------------------------
# Tests for _run_local behavioral contract
# ---------------------------------------------------------------------------

class TestRunLocalBehavioral:
    """W6.8: behavioral coverage for _run_local — actual execution, not source inspection."""

    def test_run_local_returns_error_result_structure(self):
        """_run_local error path returns failed=True, changed=False, non-empty msg.

        Forces the ImportError branch deterministically (general_ludd.execution.tool_loop
        made unimportable) and asserts the ACTUAL error-result VALUES, not just dict shape.
        """
        blocked_modules = {
            "general_ludd.execution.tool_loop": None,
            "general_ludd.models.gateway": None,
            "general_ludd.schemas.job": None,
        }
        with mock.patch.dict(sys.modules, blocked_modules):
            _run_local = _import_run_local()
            result = _run_local(
                prompt="hello",
                system_prompt="you are helpful",
                model_profile=None,
                max_iterations=1,
            )

        assert isinstance(result, dict), "must return a dict"
        assert result["failed"] is True, (
            f"Expected failed=True on ImportError path, got {result.get('failed')!r}. Full: {result}"
        )
        assert result.get("changed") is False, (
            f"Expected changed=False on error path, got {result.get('changed')!r}. Full: {result}"
        )
        msg = result.get("msg", "")
        assert isinstance(msg, str) and msg, f"Expected non-empty 'msg', got {msg!r}"
        assert "not importable" in msg, (
            f"Expected ImportError message, got {msg!r} — the error path changed; update this test."
        )

    def test_run_local_jobspec_now_valid(self):
        """FIX VERIFIED: _run_local now builds a valid JobSpec (job_id/playbook/queue provided).

        After the BUG 2 fix, JobSpec no longer raises ValidationError.
        The call falls through to the model gateway, which fails because no
        real API key is present — but the error is NOT a ValidationError.
        The result may succeed (with mocked gw) or fail with a different error.
        """
        _run_local = _import_run_local()

        mock_gw = MagicMock()
        mock_gw.call_model.return_value = MagicMock(content="hello from gateway")

        with patch("general_ludd.models.gateway.ModelGateway", return_value=mock_gw):
            result = _run_local(
                prompt="test prompt",
                system_prompt="",
                model_profile=None,
                max_iterations=1,
            )

        # With mocked gateway, the call should now succeed (no ValidationError)
        assert result["failed"] is False, (
            f"Expected failed=False after JobSpec fix with mocked gateway, got: {result}"
        )
        assert result.get("answer") is not None, (
            f"Expected 'answer' in result, got: {result}"
        )

    def test_run_local_with_patched_jobspec_gateway_called_correctly(self):
        """FIX VERIFIED: _call_model now passes profile_id + messages to gateway.

        After the BUG 1 fix, ToolCallLoop._call_model calls:
            gateway.call_model(profile_id, messages=[...])
        NOT the old broken form: gateway.call_model(system_prompt=..., user_prompt=...)
        """
        _run_local = _import_run_local()

        mock_gw = MagicMock()
        mock_gw.call_model.return_value = MagicMock(content="fixed response")

        with patch("general_ludd.models.gateway.ModelGateway", return_value=mock_gw):
            _run_local(
                prompt="patched prompt",
                system_prompt="system",
                model_profile=None,
                max_iterations=1,
            )

        assert mock_gw.call_model.called, "gateway.call_model must be called"
        call_args = mock_gw.call_model.call_args
        # After fix: profile_id is passed as first positional arg
        pos_args = call_args.args if hasattr(call_args, "args") else call_args[0]
        kwargs = call_args.kwargs if hasattr(call_args, "kwargs") else call_args[1]
        assert len(pos_args) >= 1, (
            f"Expected profile_id as positional arg, got args={pos_args}, kwargs={kwargs}"
        )
        # profile_id should be "default" (model_profile=None falls back)
        assert pos_args[0] == "default", (
            f"Expected profile_id='default', got: {pos_args[0]}"
        )
        # messages kwarg must be present
        assert "messages" in kwargs, (
            f"Expected 'messages' kwarg in gateway call, got: {kwargs}"
        )
        # Old broken kwargs must NOT be present
        assert "system_prompt" not in kwargs, (
            f"Old broken 'system_prompt' kwarg still present: {kwargs}"
        )
        assert "user_prompt" not in kwargs, (
            f"Old broken 'user_prompt' kwarg still present: {kwargs}"
        )

    def test_run_local_with_full_mock_succeeds(self):
        """_run_local succeeds when ALL dependencies are mocked correctly.

        Verifies the happy path now that both bugs are fixed. Patches ModelGateway
        and ToolCallLoop.run_with_tools to bypass real API calls.
        """
        _run_local = _import_run_local()

        mock_gw = MagicMock()

        async def fake_run_with_tools(self_inner, job, system, user):
            return "hello from mock"

        with patch("general_ludd.models.gateway.ModelGateway", return_value=mock_gw), \
             patch(
                 "general_ludd.execution.tool_loop.ToolCallLoop.run_with_tools",
                 new=fake_run_with_tools,
             ):
            result = _run_local(
                prompt="my prompt",
                system_prompt="my system",
                model_profile="default",
                max_iterations=5,
            )

        assert result.get("failed") is False, (
            f"Expected success when all deps mocked, got: {result}"
        )
        assert result.get("answer") == "hello from mock", (
            f"Expected answer='hello from mock', got: {result}"
        )


class TestRunLocalIsolation:
    """The Ansible module process must never import the controller application."""

    def test_isolated_runner_uses_fresh_python_process(self):
        mod = _import_agent_run_module()
        expected = {
            "failed": False,
            "changed": False,
            "answer": "isolated answer",
            "tool_calls": [],
            "usage": {},
            "iterations": 1,
        }
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(expected),
            stderr="",
        )

        with patch.object(mod.subprocess, "run", return_value=completed) as run:
            result = mod._run_local_isolated(
                prompt="hello",
                system_prompt="system",
                model_profile="profile",
                max_iterations=3,
                timeout=17,
            )

        assert result == expected
        command = run.call_args.args[0]
        assert command == [
            sys.executable,
            "-m",
            "general_ludd.execution.local_agent_runner",
        ]
        request = json.loads(run.call_args.kwargs["input"])
        assert request == {
            "prompt": "hello",
            "system_prompt": "system",
            "model_profile": "profile",
            "max_iterations": 3,
        }
        assert run.call_args.kwargs["timeout"] == 17

    def test_isolated_runner_timeout_is_a_safe_fallback_result(self):
        mod = _import_agent_run_module()
        timeout = subprocess.TimeoutExpired(cmd=["python"], timeout=17)

        with patch.object(mod.subprocess, "run", side_effect=timeout):
            result = mod._run_local_isolated(
                prompt="hello",
                system_prompt="system",
                model_profile=None,
                max_iterations=3,
                timeout=17,
            )

        assert result == {
            "failed": True,
            "changed": False,
            "msg": "local agent run timed out after 17 seconds",
        }

    def test_isolated_runner_rejects_non_json_output(self):
        mod = _import_agent_run_module()
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="controller log without a result",
            stderr="",
        )

        with patch.object(mod.subprocess, "run", return_value=completed):
            result = mod._run_local_isolated(
                prompt="hello",
                system_prompt="system",
                model_profile=None,
                max_iterations=3,
                timeout=17,
            )

        assert result == {
            "failed": True,
            "changed": False,
            "msg": "local agent runner returned invalid JSON",
        }

    def test_main_calls_isolated_runner_not_in_process_runner(self):
        mod = _import_agent_run_module()
        fake_module = SimpleNamespace(
            params={
                "prompt": "hello",
                "system_prompt": "",
                "tools": [],
                "max_iterations": 3,
                "model_profile": "",
                "daemon_url": "http://localhost:8000",
                "psk": "",
                "timeout": 17,
            },
            check_mode=False,
            exit_json=MagicMock(),
            fail_json=MagicMock(),
        )
        local_result = {
            "failed": False,
            "changed": False,
            "answer": "isolated answer",
            "tool_calls": [],
            "usage": {},
            "iterations": 1,
        }

        with (
            patch.object(mod, "AnsibleModule", return_value=fake_module),
            patch.object(mod, "_run_local", side_effect=AssertionError(
                "controller application imported inside AnsiballZ process"
            )),
            patch.object(mod, "_run_local_isolated", return_value=local_result),
        ):
            mod.main()

        fake_module.exit_json.assert_called_once_with(**local_result)
        fake_module.fail_json.assert_not_called()


class TestLocalAgentRunnerCLI:
    """The subprocess protocol is one JSON request and one JSON response."""

    def test_main_dispatches_json_request(self):
        from general_ludd.execution import local_agent_runner

        expected = {
            "failed": False,
            "changed": False,
            "answer": "answer",
            "tool_calls": [],
            "usage": {},
            "iterations": 1,
        }
        request = {
            "prompt": "hello",
            "system_prompt": "system",
            "model_profile": "profile",
            "max_iterations": 4,
        }
        stdout = io.StringIO()
        with (
            patch.object(
                local_agent_runner,
                "run_local",
                return_value=expected,
            ) as run_local,
            patch.object(
                local_agent_runner.sys,
                "stdin",
                io.StringIO(json.dumps(request)),
            ),
            patch.object(local_agent_runner.sys, "stdout", stdout),
        ):
            return_code = local_agent_runner.main()

        assert return_code == 0
        assert json.loads(stdout.getvalue()) == expected
        run_local.assert_called_once_with(
            prompt="hello",
            system_prompt="system",
            model_profile="profile",
            max_iterations=4,
        )

    def test_main_returns_structured_error_for_invalid_request(self):
        from general_ludd.execution import local_agent_runner

        stdout = io.StringIO()
        with (
            patch.object(
                local_agent_runner.sys,
                "stdin",
                io.StringIO("{}"),
            ),
            patch.object(local_agent_runner.sys, "stdout", stdout),
        ):
            return_code = local_agent_runner.main()

        result = json.loads(stdout.getvalue())
        assert return_code == 0
        assert result["failed"] is True
        assert result["changed"] is False
        assert result["msg"].startswith("invalid local agent request:")


# ---------------------------------------------------------------------------
# Direct ToolCallLoop tests (toolless path, mcp_client=None)
# ---------------------------------------------------------------------------

class TestToolCallLoopToolless:
    """Verify ToolCallLoop toolless path (mcp_client=None) calls gateway correctly."""

    def test_run_with_tools_no_mcp_calls_gateway(self):
        """FIX VERIFIED: run_with_tools calls gateway.call_model(profile_id, messages=...)."""
        from general_ludd.execution.tool_loop import ToolCallLoop
        from general_ludd.schemas.job import JobSpec

        mock_gw = MagicMock()
        mock_gw.call_model.return_value = MagicMock(content="mock response")

        loop = ToolCallLoop(model_gateway=mock_gw, mcp_client=None)
        assert loop.is_available() is False, "is_available() must be False when mcp_client=None"

        job = JobSpec(
            job_id="test-job",
            todo_id="t1",
            playbook="test.yml",
            queue="default",
            prompt_text="hello",
        )

        asyncio.run(loop.run_with_tools(job, "system", "user prompt"))

        assert mock_gw.call_model.called, "gateway.call_model must be called in toolless path"

        call_args = mock_gw.call_model.call_args
        pos_args = call_args.args if hasattr(call_args, "args") else call_args[0]
        kwargs = call_args.kwargs if hasattr(call_args, "kwargs") else call_args[1]

        # FIX VERIFIED: profile_id now passed as first positional arg
        assert len(pos_args) >= 1, (
            f"Expected profile_id as positional arg, got: args={pos_args}, kwargs={kwargs}"
        )
        assert pos_args[0] == "default", (
            f"Expected profile_id='default' (job has no model_profile), got: {pos_args[0]}"
        )
        # messages kwarg must be present
        assert "messages" in kwargs, (
            f"Expected 'messages' kwarg, got: {kwargs}"
        )
        messages = kwargs["messages"]
        assert isinstance(messages, list), f"messages must be a list, got: {type(messages)}"
        roles = [m["role"] for m in messages]
        assert "system" in roles, f"Expected system message, got roles: {roles}"
        assert "user" in roles, f"Expected user message, got roles: {roles}"

        # Old broken kwargs must NOT be present
        assert "system_prompt" not in kwargs, (
            f"Old broken 'system_prompt' kwarg still present after fix: {kwargs}"
        )
        assert "user_prompt" not in kwargs, (
            f"Old broken 'user_prompt' kwarg still present after fix: {kwargs}"
        )

    def test_toolless_with_real_gateway_raises_valueerror_not_typeerror(self):
        """FIX VERIFIED: real gateway now gets correct args; raises ValueError (no profile).

        After the BUG 1 fix, the real ModelGateway no longer gets TypeError from
        wrong kwargs. Instead it raises ValueError because profile 'default' is
        not registered (no profiles configured in this test). This proves the
        signature mismatch is resolved.
        """
        from general_ludd.execution.tool_loop import ToolCallLoop
        from general_ludd.models.gateway import ModelGateway
        from general_ludd.schemas.job import JobSpec

        gw = ModelGateway()  # no profiles registered
        loop = ToolCallLoop(model_gateway=gw, mcp_client=None)
        job = JobSpec(
            job_id="test-job",
            todo_id="t1",
            playbook="test.yml",
            queue="default",
            prompt_text="hello",
        )

        # After fix: TypeError is gone; ValueError raised because 'default' profile
        # is not registered in this uninitialized gateway.
        with pytest.raises((ValueError, Exception)) as exc_info:
            asyncio.run(loop.run_with_tools(job, "system", "user prompt"))

        err = str(exc_info.value)
        # Must NOT be a TypeError about wrong kwargs
        assert not isinstance(exc_info.value, TypeError), (
            f"Got TypeError — BUG 1 is still present: {err}"
        )
        # Should be ValueError about missing profile
        assert "default" in err or "Profile" in err or "profile" in err, (
            f"Expected ValueError about missing profile, got: {err}"
        )
