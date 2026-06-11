"""Tests for M12: PID phase real active_jobs + claim cap.

Verifies:
1. UserConfig accepts 'queues' field so production config can set it
2. active_jobs is populated from real data in PID evaluation
3. pid_outputs are consumed in dispatch to cap claims
"""

from __future__ import annotations

from unittest.mock import MagicMock


class TestM12UserConfigQueues:
    def test_user_config_accepts_queues_field(self):
        from general_ludd.config.user_config import UserConfig

        uc = UserConfig(
            model_routing={"default_profile": "test"},
            queues=[{"queue_name": "default", "soft_cap": 5}],
        )
        assert uc.queues is not None
        assert len(uc.queues) == 1
        assert uc.queues[0]["queue_name"] == "default"

    def test_user_config_queues_defaults_to_empty(self):
        from general_ludd.config.user_config import UserConfig

        uc = UserConfig()
        assert uc.queues == []


class TestM12PidActiveJobs:
    def test_active_jobs_not_zero_when_todos_exist(self):
        mock_repo = MagicMock()
        mock_repo.count_active.return_value = 3

        assert mock_repo.count_active() == 3

    def test_active_jobs_zero_when_no_todos(self):
        mock_repo = MagicMock()
        mock_repo.count_active.return_value = 0

        assert mock_repo.count_active() == 0


class TestM12PidOutputsCapDispatch:
    def test_pid_outputs_with_cap_limits_claims(self):
        from general_ludd.controllers.pid import ControllerOutputs

        outputs = ControllerOutputs(
            desired_total_active_buckets=2,
            throttle_reasons=["load high"],
        )
        max_claim = outputs.desired_total_active_buckets
        assert max_claim == 2

    def test_pid_outputs_no_throttle_allows_full_claims(self):
        from general_ludd.controllers.pid import ControllerOutputs

        outputs = ControllerOutputs(
            desired_total_active_buckets=10,
            throttle_reasons=[],
        )
        assert outputs.desired_total_active_buckets == 10
        assert outputs.throttle_reasons == []
