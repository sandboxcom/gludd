"""Tests for ``gludd test background`` CLI subcommand.

Covers parser registration + handler wiring for ``gludd test background``
(separate from the top-level ``gludd test-bg`` command).
"""
from __future__ import annotations

import argparse
import json
from typing import Any
from unittest.mock import MagicMock, patch

import general_ludd.cli as cli_mod


def _test_bg_subactions() -> argparse._SubParsersAction[argparse.ArgumentParser]:
    """Return the nested subparsers action for ``gludd test background``."""
    _parser, subcommand_map = cli_mod.build_parser()
    assert "test" in subcommand_map, "test not registered in subcommand_map"
    test_parser = subcommand_map["test"]
    for action in test_parser._subparsers._group_actions:  # type: ignore[union-attr]
        if isinstance(action, argparse._SubParsersAction):
            for act2 in action.choices["background"]._subparsers._group_actions:  # type: ignore[union-attr]
                if isinstance(act2, argparse._SubParsersAction):
                    return act2
    raise AssertionError("test background parser has no nested subparsers action")


# ── Parser registration + subcommand wiring ──────────────────────────────


class TestParserRegistration:
    def test_top_level_test_registered(self) -> None:
        _parser, subcommand_map = cli_mod.build_parser()
        assert "test" in subcommand_map

    def test_background_subcommand_registered(self) -> None:
        _parser, subcommand_map = cli_mod.build_parser()
        test_parser = subcommand_map["test"]
        for action in test_parser._subparsers._group_actions:  # type: ignore[union-attr]
            if isinstance(action, argparse._SubParsersAction):
                assert "background" in action.choices

    def test_all_five_subcommands_registered(self) -> None:
        sub_action = _test_bg_subactions()
        assert set(sub_action.choices) == {"launch", "status", "poll-all", "kill", "results"}

    def test_launch_wired_to_handler(self) -> None:
        sub_action = _test_bg_subactions()
        assert sub_action.choices["launch"].get_default("func") is cli_mod._cmd_testbg_launch

    def test_status_wired_to_handler(self) -> None:
        sub_action = _test_bg_subactions()
        assert sub_action.choices["status"].get_default("func") is cli_mod._cmd_testbg_status

    def test_poll_all_wired_to_handler(self) -> None:
        sub_action = _test_bg_subactions()
        assert sub_action.choices["poll-all"].get_default("func") is cli_mod._cmd_testbg_poll_all

    def test_kill_wired_to_handler(self) -> None:
        sub_action = _test_bg_subactions()
        assert sub_action.choices["kill"].get_default("func") is cli_mod._cmd_testbg_kill

    def test_results_wired_to_handler(self) -> None:
        sub_action = _test_bg_subactions()
        assert sub_action.choices["results"].get_default("func") is cli_mod._cmd_testbg_results


# ── Argument acceptance ───────────────────────────────────────────────────


class TestArgumentParsing:
    def test_launch_accepts_testfile_and_wait(self) -> None:
        sub_action = _test_bg_subactions()
        launch = sub_action.choices["launch"]
        ns = launch.parse_args(["tests/unit/test_foo.py"])
        assert ns.testfile == "tests/unit/test_foo.py"
        assert ns.wait is False
        ns2 = launch.parse_args(["tests/unit/test_foo.py", "--wait"])
        assert ns2.wait is True

    def test_status_accepts_testfile(self) -> None:
        sub_action = _test_bg_subactions()
        status = sub_action.choices["status"]
        ns = status.parse_args(["tests/unit/test_foo.py"])
        assert ns.testfile == "tests/unit/test_foo.py"

    def test_poll_all_accepts_no_args(self) -> None:
        _parser, subcommand_map = cli_mod.build_parser()
        test_p = subcommand_map["test"]
        for action in test_p._subparsers._group_actions:  # type: ignore[union-attr]
            if isinstance(action, argparse._SubParsersAction):
                bg = action.choices["background"]
                ns = bg.parse_args(["poll-all"])
                assert ns.testbg_command == "poll-all"
                return
        raise AssertionError("background subparser not found")

    def test_kill_accepts_testfile_and_force(self) -> None:
        sub_action = _test_bg_subactions()
        kill = sub_action.choices["kill"]
        ns = kill.parse_args(["tests/unit/test_foo.py"])
        assert ns.testfile == "tests/unit/test_foo.py"
        assert ns.force is False
        ns2 = kill.parse_args(["tests/unit/test_foo.py", "--force"])
        assert ns2.force is True

    def test_results_accepts_testfile(self) -> None:
        sub_action = _test_bg_subactions()
        results = sub_action.choices["results"]
        ns = results.parse_args(["tests/unit/test_foo.py"])
        assert ns.testfile == "tests/unit/test_foo.py"


# ── Handler behaviour (with mock BackgroundTestRunner) ────────────────────


def _make_runner() -> MagicMock:
    runner = MagicMock()
    runner.launch.return_value = {"phase": "running", "testfile": "x", "pid": 42}
    runner.status.return_value = {"phase": "completed", "testfile": "x", "alive": False}
    runner.poll_all.return_value = [{"phase": "completed", "testfile": "y"}]
    runner.kill.return_value = {"status": "terminated", "testfile": "x", "pid": 42}
    runner.results.return_value = {"phase": "completed", "complete": True, "passed": True}
    return runner


class TestLaunchHandler:
    def test_prints_json_to_stdout(self, capsys: Any) -> None:
        mock = _make_runner()
        ns = argparse.Namespace(testfile="tests/unit/test_foo.py", wait=False)
        with patch(
            "general_ludd.runner.background_test_runner.BackgroundTestRunner",
            return_value=mock,
        ):
            cli_mod._cmd_testbg_launch(ns)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["phase"] == "running"
        assert data["pid"] == 42
        mock.launch.assert_called_once_with("tests/unit/test_foo.py", wait=False)

    def test_passes_wait_flag(self, capsys: Any) -> None:
        mock = _make_runner()
        ns = argparse.Namespace(testfile="tests/unit/test_bar.py", wait=True)
        with patch(
            "general_ludd.runner.background_test_runner.BackgroundTestRunner",
            return_value=mock,
        ):
            cli_mod._cmd_testbg_launch(ns)
        mock.launch.assert_called_once_with("tests/unit/test_bar.py", wait=True)


class TestStatusHandler:
    def test_prints_json_to_stdout(self, capsys: Any) -> None:
        mock = _make_runner()
        ns = argparse.Namespace(testfile="tests/unit/test_foo.py")
        with patch(
            "general_ludd.runner.background_test_runner.BackgroundTestRunner",
            return_value=mock,
        ):
            cli_mod._cmd_testbg_status(ns)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["phase"] == "completed"
        mock.status.assert_called_once_with("tests/unit/test_foo.py")


class TestPollAllHandler:
    def test_prints_json_to_stdout(self, capsys: Any) -> None:
        mock = _make_runner()
        ns = argparse.Namespace()
        with patch(
            "general_ludd.runner.background_test_runner.BackgroundTestRunner",
            return_value=mock,
        ):
            cli_mod._cmd_testbg_poll_all(ns)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert data[0]["testfile"] == "y"
        mock.poll_all.assert_called_once()


class TestKillHandler:
    def test_prints_json_to_stdout(self, capsys: Any) -> None:
        mock = _make_runner()
        ns = argparse.Namespace(testfile="tests/unit/test_foo.py", force=False)
        with patch(
            "general_ludd.runner.background_test_runner.BackgroundTestRunner",
            return_value=mock,
        ):
            cli_mod._cmd_testbg_kill(ns)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["status"] == "terminated"
        mock.kill.assert_called_once_with("tests/unit/test_foo.py", force=False)

    def test_passes_force_flag(self, capsys: Any) -> None:
        mock = _make_runner()
        ns = argparse.Namespace(testfile="tests/unit/test_bar.py", force=True)
        with patch(
            "general_ludd.runner.background_test_runner.BackgroundTestRunner",
            return_value=mock,
        ):
            cli_mod._cmd_testbg_kill(ns)
        mock.kill.assert_called_once_with("tests/unit/test_bar.py", force=True)


class TestResultsHandler:
    def test_prints_json_to_stdout(self, capsys: Any) -> None:
        mock = _make_runner()
        ns = argparse.Namespace(testfile="tests/unit/test_foo.py")
        with patch(
            "general_ludd.runner.background_test_runner.BackgroundTestRunner",
            return_value=mock,
        ):
            cli_mod._cmd_testbg_results(ns)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["complete"] is True
        assert data["passed"] is True
        mock.results.assert_called_once_with("tests/unit/test_foo.py")
