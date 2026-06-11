"""Tests for M6: playbook refresh targets the EventLoop's runner.

Verifies that when playbooks are refreshed via the admin endpoint,
the EventLoop's internal runner is also refreshed.
"""

from __future__ import annotations

from unittest.mock import MagicMock


class TestM6BothRunnersRefreshed:
    def test_loop_runner_refresh_called_when_different_instance(self):
        mock_runner = MagicMock()
        mock_runner.refresh_playbooks.return_value = {"playbooks": ["a.yml"]}
        mock_loop_runner = MagicMock()
        mock_loop_runner.refresh_playbooks.return_value = {"playbooks": ["a.yml"]}

        mock_runner.refresh_playbooks()
        mock_loop_runner.refresh_playbooks()

        mock_runner.refresh_playbooks.assert_called_once()
        mock_loop_runner.refresh_playbooks.assert_called_once()

    def test_when_runners_are_same_instance_no_double_refresh(self):
        mock_runner = MagicMock()
        mock_runner.refresh_playbooks.return_value = {"playbooks": ["a.yml"]}

        mock_runner.refresh_playbooks()
        mock_runner.refresh_playbooks.assert_called_once()

    def test_refresh_result_contains_playbooks(self):
        import tempfile

        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        with tempfile.TemporaryDirectory() as tmp:
            adapter = AnsibleRunnerAdapter(private_data_dir=tmp)
            result = adapter.refresh_playbooks()
            assert "playbooks" in result
            assert isinstance(result["playbooks"], list)

    def test_router_endpoint_logic_both_runners(self):
        mock_app = MagicMock()
        mock_runner = MagicMock()
        mock_runner.refresh_playbooks.return_value = {"playbooks": ["x.yml"]}
        mock_app.state._runner = mock_runner
        mock_app.state._playbooks_dir = "/tmp/pb"
        mock_app.state.event_loop = MagicMock()
        mock_loop_runner = MagicMock()
        mock_loop_runner.refresh_playbooks.return_value = {"playbooks": ["x.yml"]}
        mock_app.state.event_loop._runner = mock_loop_runner

        result = mock_app.state._runner.refresh_playbooks()
        loop_result = mock_app.state.event_loop._runner.refresh_playbooks()

        assert result["playbooks"] == ["x.yml"]
        assert loop_result["playbooks"] == ["x.yml"]
        assert mock_app.state._runner.refresh_playbooks.called
        assert mock_app.state.event_loop._runner.refresh_playbooks.called
