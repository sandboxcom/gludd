"""Behavioral tests for gludd_agent_run._run_local (W6.8 gap coverage).

W6.8 decision: uses ToolCallLoop from execution.tool_loop in-process.
Prior coverage was 100% via static source-string checks — no actual execution.
These tests run _run_local with mocked dependencies and document the FIXED
behavior after W6.8 repairs (JobSpec missing fields + gateway sig mismatch).

Run: make test-iso TESTFILE='tests/unit/test_gludd_agent_run_behavioral.py'
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
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


def test_agent_module_explicitly_bundles_ansible_sentinel_dependency() -> None:
    """Keep Ansible's dynamically imported serializer dependency in module payloads."""
    module_path = Path(__file__).parents[2] / (
        "collections/ansible_collections/general_ludd/agent/plugins/modules/"
        "gludd_agent_run.py"
    )
    source = module_path.read_text(encoding="utf-8")

    assert "ansible.module_utils.common.sentinel" in source


def test_agent_module_bundles_ansible_serializer_internal_dependencies() -> None:
    """Ansible 2.19+ serializing a result needs private module-utils helpers."""
    module_path = Path(__file__).parents[2] / (
        "collections/ansible_collections/general_ludd/agent/plugins/modules/"
        "gludd_agent_run.py"
    )
    source = module_path.read_text(encoding="utf-8")

    for dependency in (
        "ansible.module_utils._internal",
        "_ambient_context",
        "_event_utils",
        "_messages",
        "_traceback",
        "ansible.module_utils.common import yaml",
    ):
        assert dependency in source


# ---------------------------------------------------------------------------
# Tests for _run_local behavioral contract
# ---------------------------------------------------------------------------

class TestRunLocalBehavioral:
    """The collection never imports or executes the Gludd core runtime locally."""

    def test_run_local_returns_error_result_structure(self):
        """The legacy helper fails closed without importing core implementation modules."""
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
        assert msg == "local agent execution is disabled; use the authenticated daemon"

    def test_mocked_gateway_cannot_reopen_local_execution(self):
        """A mocked in-process gateway cannot bypass the daemon-only boundary."""
        _run_local = _import_run_local()

        mock_gw = MagicMock()
        mock_gw.call_model.return_value = MagicMock(content="hello from gateway")

        with patch(
            "general_ludd.models.gateway.ModelGateway", return_value=mock_gw
        ) as gateway_cls:
            result = _run_local(
                prompt="test prompt",
                system_prompt="",
                model_profile=None,
                max_iterations=1,
            )

        assert result == {
            "failed": True,
            "changed": False,
            "msg": "local agent execution is disabled; use the authenticated daemon",
        }
        gateway_cls.assert_not_called()

    def test_in_process_gateway_is_never_called(self):
        """The collection helper never calls an in-process model gateway."""
        _run_local = _import_run_local()

        mock_gw = MagicMock()
        mock_gw.call_model.return_value = MagicMock(content="fixed response")

        with patch(
            "general_ludd.models.gateway.ModelGateway", return_value=mock_gw
        ) as gateway_cls:
            result = _run_local(
                prompt="patched prompt",
                system_prompt="system",
                model_profile=None,
                max_iterations=1,
            )

        assert result["failed"] is True
        assert result["changed"] is False
        gateway_cls.assert_not_called()
        mock_gw.call_model.assert_not_called()

    def test_full_core_mock_cannot_reopen_local_execution(self):
        """Mocked core dependencies cannot reopen local collection execution."""
        _run_local = _import_run_local()

        mock_gw = MagicMock()

        async def fake_run_with_tools(self_inner, job, system, user):
            return "hello from mock"

        with patch(
            "general_ludd.models.gateway.ModelGateway", return_value=mock_gw
        ) as gateway_cls, \
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

        assert result == {
            "failed": True,
            "changed": False,
            "msg": "local agent execution is disabled; use the authenticated daemon",
        }
        gateway_cls.assert_not_called()


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
