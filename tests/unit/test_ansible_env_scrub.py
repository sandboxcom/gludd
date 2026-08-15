"""Regression: playbook env must exclude secrets from the parent process."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

try:
    from general_ludd.ansible.core_runner import CoreAnsibleRunner
except ImportError:
    pytest.skip("ansible-core not installed", allow_module_level=True)


def _make_runner() -> CoreAnsibleRunner:
    return CoreAnsibleRunner()


@pytest.fixture(autouse=True)
def _skip_real_ansible_plugin_loader():
    with patch("ansible.plugins.loader.init_plugin_loader", return_value=None):
        yield


class TestPlaybookEnvScrub:
    """PlaybookExecutor.run() must not see secrets in os.environ."""

    def test_secret_keys_stripped_from_playbook_env(self) -> None:
        """ZAI_API_KEY, GLUDD_AUTH_PSK, AWS_SECRET_ACCESS_KEY must not reach pb_exec.run()."""
        captured: list[dict[str, str]] = []

        def fake_run() -> int:
            # Capture the env visible to the playbook at run time.
            captured.append(dict(os.environ))
            return 0

        runner = _make_runner()
        mock_pb_exec = MagicMock()
        mock_pb_exec.run.side_effect = fake_run
        mock_pb_exec._tqm._callback_plugins = []

        secrets = {
            "ZAI_API_KEY": "super-secret-zai",
            "GLUDD_AUTH_PSK": "super-secret-psk",
            "AWS_SECRET_ACCESS_KEY": "super-secret-aws",
        }

        with patch.dict(os.environ, secrets, clear=False), patch(
            "ansible.executor.playbook_executor.PlaybookExecutor",
            return_value=mock_pb_exec,
        ):
            runner._execute_with_core(
                playbook_path="/dev/null",
                inventory=["localhost,"],
            )

        assert captured, "pb_exec.run() was never called"
        env_at_run = captured[0]
        assert "ZAI_API_KEY" not in env_at_run, "ZAI_API_KEY leaked into playbook env"
        assert "GLUDD_AUTH_PSK" not in env_at_run, "GLUDD_AUTH_PSK leaked into playbook env"
        assert "AWS_SECRET_ACCESS_KEY" not in env_at_run, (
            "AWS_SECRET_ACCESS_KEY leaked into playbook env"
        )

    def test_env_restored_after_run(self) -> None:
        """os.environ must be fully restored after _execute_with_core returns."""
        runner = _make_runner()
        mock_pb_exec = MagicMock()
        mock_pb_exec.run.return_value = 0
        mock_pb_exec._tqm._callback_plugins = []

        sentinel = {"ZAI_API_KEY": "restore-check-value", "HOME": "/test-home"}

        with patch.dict(os.environ, sentinel, clear=False):
            with patch(
                "ansible.executor.playbook_executor.PlaybookExecutor",
                return_value=mock_pb_exec,
            ):
                runner._execute_with_core(
                    playbook_path="/dev/null",
                    inventory=["localhost,"],
                )
            # After the call, the secret must be back (we're inside patch.dict).
            assert os.environ.get("ZAI_API_KEY") == "restore-check-value"
            assert os.environ.get("HOME") == "/test-home"

    def test_extra_env_reaches_playbook(self) -> None:
        """Non-secret extra_env overrides are visible inside pb_exec.run()."""
        captured: list[dict[str, str]] = []

        def fake_run() -> int:
            captured.append(dict(os.environ))
            return 0

        runner = _make_runner()
        mock_pb_exec = MagicMock()
        mock_pb_exec.run.side_effect = fake_run
        mock_pb_exec._tqm._callback_plugins = []

        with patch(
            "ansible.executor.playbook_executor.PlaybookExecutor",
            return_value=mock_pb_exec,
        ):
            runner._execute_with_core(
                playbook_path="/dev/null",
                inventory=["localhost,"],
                extra_env={"ANSIBLE_FORCE_COLOR": "0", "MY_ROLE_VAR": "hello"},
            )

        assert captured, "pb_exec.run() was never called"
        assert captured[0].get("ANSIBLE_FORCE_COLOR") == "0"
        assert captured[0].get("MY_ROLE_VAR") == "hello"

    def test_runner_adapter_no_global_env_mutation(self) -> None:
        """AnsibleRunnerAdapter.run_playbook must not mutate os.environ."""
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        adapter = AnsibleRunnerAdapter(registry={"noop.yml": "/dev/null"})

        pre_env = dict(os.environ)

        mock_result = MagicMock()
        mock_result.model_dump.return_value = {"status": "successful", "rc": 0, "events": []}

        with patch.object(
            adapter._core_runner,
            "run_playbook",
            return_value=mock_result,
        ) as mock_run:
            adapter.run_playbook(
                "noop.yml",
                env={"INJECTED_VAR": "should-not-pollute-os-environ"},
            )
            # Verify extra_env (not os.environ mutation) was passed to core runner.
            call_kwargs = mock_run.call_args
            assert call_kwargs is not None
            called_extra_env = call_kwargs.kwargs.get("extra_env") or (
                call_kwargs.args[5] if len(call_kwargs.args) > 5 else None
            )
            assert called_extra_env is not None, "extra_env not passed to core runner"
            assert "INJECTED_VAR" in called_extra_env

        post_env = dict(os.environ)
        assert pre_env == post_env, "os.environ was mutated by runner adapter"

    def test_gludd_playbook_timeout_reaches_playbook_env(self) -> None:
        """GLUDD_PLAYBOOK_TIMEOUT set in parent must be visible inside pb_exec.run(),
        while secret vars (ZAI_API_KEY, GLUDD_AUTH_PSK) remain stripped."""
        captured: list[dict[str, str]] = []

        def fake_run() -> int:
            captured.append(dict(os.environ))
            return 0

        runner = _make_runner()
        mock_pb_exec = MagicMock()
        mock_pb_exec.run.side_effect = fake_run
        mock_pb_exec._tqm._callback_plugins = []

        parent_env = {
            "GLUDD_PLAYBOOK_TIMEOUT": "120",
            "ZAI_API_KEY": "should-be-stripped",
            "GLUDD_AUTH_PSK": "also-stripped",
        }

        with patch.dict(os.environ, parent_env, clear=False), patch(
            "ansible.executor.playbook_executor.PlaybookExecutor",
            return_value=mock_pb_exec,
        ):
            runner._execute_with_core(
                playbook_path="/dev/null",
                inventory=["localhost,"],
            )

        assert captured, "pb_exec.run() was never called"
        env_at_run = captured[0]
        # Regression: GLUDD_PLAYBOOK_TIMEOUT must pass through the allowlist.
        assert env_at_run.get("GLUDD_PLAYBOOK_TIMEOUT") == "120", (
            "GLUDD_PLAYBOOK_TIMEOUT was stripped from the playbook env "
            "(regression: it is required so the forked child can read the timeout)"
        )
        # Secrets must still be absent.
        assert "ZAI_API_KEY" not in env_at_run, "ZAI_API_KEY leaked into playbook env"
        assert "GLUDD_AUTH_PSK" not in env_at_run, "GLUDD_AUTH_PSK leaked into playbook env"
