import sys
from unittest.mock import MagicMock, patch

from general_ludd.cli import main


def _parse(args: list[str]) -> object:
    with patch.object(sys, "argv", ["gludd", *args]):
        main()
    return True


class TestScoresCommand:
    def test_scores_calls_daemon(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"scores": [{"prompt_profile_id": "p1", "composite_score": 0.9}]},
            )
            _parse(["scores"])
            out = capsys.readouterr().out
            assert "composite_score" in out

    def test_scores_with_task_type_filter(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"scores": []},
            )
            _parse(["scores", "--task-type", "bug_fix"])
            call_args = mock_get.call_args
            assert call_args[1]["params"]["task_type"] == "bug_fix"

    def test_scores_with_custom_daemon_url(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"scores": []},
            )
            _parse(["scores", "--daemon-url", "http://other:9000"])
            url = mock_get.call_args[0][0]
            assert "http://other:9000" in url

    def test_scores_handles_error(self, capsys):
        with patch("httpx.get") as mock_get, patch("sys.exit") as mock_exit:
            mock_get.return_value = MagicMock(status_code=500, text="fail")
            _parse(["scores"])
            mock_exit.assert_called_with(1)

    def test_scores_handles_exception(self, capsys):
        with patch("httpx.get", side_effect=Exception("conn refused")), patch("sys.exit") as mock_exit:
            _parse(["scores"])
            mock_exit.assert_called_with(1)


class TestLeaderboardCommand:
    def test_leaderboard_prints_table(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    "leaderboard": [
                        {
                            "prompt_profile_id": "aider",
                            "model_profile_id": "gpt4",
                            "composite_score": 0.92,
                            "avg_cost_usd": 0.015,
                            "sample_count": 10,
                            "task_type": "feature",
                        }
                    ]
                },
            )
            _parse(["leaderboard"])
            out = capsys.readouterr().out
            assert "aider" in out
            assert "0.92" in out

    def test_leaderboard_empty_data(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"leaderboard": []},
            )
            _parse(["leaderboard"])
            out = capsys.readouterr().out
            assert "No benchmark data" in out

    def test_leaderboard_with_task_type(self, capsys):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"leaderboard": []},
            )
            _parse(["leaderboard", "--task-type", "bug_fix"])
            call_args = mock_get.call_args
            assert call_args[1]["params"]["task_type"] == "bug_fix"

    def test_leaderboard_handles_error(self, capsys):
        with patch("httpx.get") as mock_get, patch("sys.exit") as mock_exit:
            mock_get.return_value = MagicMock(status_code=503, text="unavailable")
            _parse(["leaderboard"])
            mock_exit.assert_called_with(1)

    def test_leaderboard_handles_exception(self, capsys):
        with patch("httpx.get", side_effect=Exception("timeout")), patch("sys.exit") as mock_exit:
            _parse(["leaderboard"])
            mock_exit.assert_called_with(1)
