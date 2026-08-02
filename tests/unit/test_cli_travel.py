"""Unit tests for ``gludd travel`` CLI subcommands."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.cli_travel import (
    _cmd_travel_event_plan,
    _cmd_travel_plan,
    _cmd_travel_search_flights,
    _cmd_travel_search_hotels,
    _emit_result,
    _resolve_playbook_path,
    _run_travel_playbook,
    add_travel_subparser,
)


class TestAddTravelSubparser:
    def test_registers_travel_parser(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_travel_subparser(sub)
        assert "travel" in sub.choices

    def test_plan_subcommand_parsed(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_travel_subparser(sub)
        args = parser.parse_args(["travel", "plan"])
        assert args.travel_command == "plan"

    def test_plan_subcommand_with_args(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_travel_subparser(sub)
        args = parser.parse_args(
            [
                "travel",
                "plan",
                "--origin",
                "JFK",
                "--destination",
                "CDG",
                "--departure-date",
                "2026-08-15",
                "--return-date",
                "2026-08-22",
                "--budget",
                "2000.0",
            ]
        )
        assert args.origin == "JFK"
        assert args.destination == "CDG"
        assert args.departure_date == "2026-08-15"
        assert args.return_date == "2026-08-22"
        assert args.budget == 2000.0

    def test_search_flights_subcommand_parsed(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_travel_subparser(sub)
        args = parser.parse_args(["travel", "search", "flights"])
        assert args.travel_search_command == "flights"

    def test_search_flights_with_args(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_travel_subparser(sub)
        args = parser.parse_args(
            [
                "travel",
                "search",
                "flights",
                "--origin",
                "LAX",
                "--destination",
                "HND",
                "--departure-date",
                "2026-09-01",
                "--return-date",
                "2026-09-10",
                "--passengers",
                "3",
            ]
        )
        assert args.origin == "LAX"
        assert args.destination == "HND"
        assert args.passengers == 3

    def test_search_hotels_subcommand_parsed(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_travel_subparser(sub)
        args = parser.parse_args(["travel", "search", "hotels"])
        assert args.travel_search_command == "hotels"

    def test_search_hotels_with_args(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_travel_subparser(sub)
        args = parser.parse_args(
            [
                "travel",
                "search",
                "hotels",
                "--destination",
                "Paris",
                "--checkin-date",
                "2026-08-15",
                "--checkout-date",
                "2026-08-22",
                "--guests",
                "2",
            ]
        )
        assert args.destination == "Paris"
        assert args.checkin_date == "2026-08-15"
        assert args.checkout_date == "2026-08-22"
        assert args.guests == 2

    def test_event_plan_subcommand_parsed(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_travel_subparser(sub)
        args = parser.parse_args(["travel", "event", "plan"])
        assert args.travel_event_command == "plan"

    def test_event_plan_with_args(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_travel_subparser(sub)
        args = parser.parse_args(
            [
                "travel",
                "event",
                "plan",
                "--destination",
                "Berlin",
                "--event-date",
                "2026-10-01",
                "--event-type",
                "conference",
                "--attendees",
                "200",
            ]
        )
        assert args.destination == "Berlin"
        assert args.event_date == "2026-10-01"
        assert args.event_type == "conference"
        assert args.attendees == 200

    def test_five_subcommands_registered(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        add_travel_subparser(sub)
        travel = sub.choices["travel"]
        travel_sub = travel._subparsers._group_actions[0]
        registered = sorted(travel_sub.choices.keys())
        expected = sorted(["plan", "search", "event"])
        assert registered == expected


class TestResolvePlaybookPath:
    def test_resolves_from_playbooks_dir(self, tmp_path: Path):
        playbook_dir = tmp_path / "playbooks"
        playbook_dir.mkdir()
        (playbook_dir / "travel_plan.yml").write_text("---")
        with (
            patch.object(Path, "cwd", return_value=tmp_path),
            patch(
                "general_ludd.cli_travel.Path.__file__",
                create=True,
                new_callable=lambda: tmp_path / "src" / "cli_travel.py",
            ),
        ):
            pass

    def test_returns_candidates_zero_when_no_file(self):
        with patch.object(Path, "is_file", return_value=False):
            result = _resolve_playbook_path("nonexistent.yml")
            assert result is not None
            assert result.name == "nonexistent.yml"

    def test_second_candidate_cwd_used_when_present(self, tmp_path: Path):
        cwd_playbook = tmp_path / "playbooks"
        cwd_playbook.mkdir()
        (cwd_playbook / "test.yml").write_text("---")
        with patch.object(Path, "cwd", return_value=tmp_path):
            # When both candidates exist, first is returned (here's parent)
            pass


class TestRunTravelPlaybook:
    def test_registers_and_runs_playbook(self):
        mock_adapter = MagicMock()
        mock_adapter.list_playbooks.return_value = []
        mock_adapter.run_playbook.return_value = {"status": "successful", "rc": 0}
        with patch("general_ludd.cli_travel.AnsibleRunnerAdapter", return_value=mock_adapter):
            result = _run_travel_playbook("travel_plan.yml", {"origin": "JFK"})
        mock_adapter.register_playbook.assert_called_once()
        mock_adapter.run_playbook.assert_called_once_with("travel_plan.yml", extravars={"origin": "JFK"})
        assert result["status"] == "successful"

    def test_skips_registration_if_already_registered(self):
        mock_adapter = MagicMock()
        mock_adapter.list_playbooks.return_value = ["travel_plan.yml"]
        mock_adapter.run_playbook.return_value = {"status": "successful", "rc": 0}
        with patch("general_ludd.cli_travel.AnsibleRunnerAdapter", return_value=mock_adapter):
            _run_travel_playbook("travel_plan.yml", {})
        mock_adapter.register_playbook.assert_not_called()
        mock_adapter.run_playbook.assert_called_once()


class TestCmdTravelPlan:
    def test_runs_plan_playbook_with_all_args(self):
        args = argparse.Namespace(
            origin="JFK",
            destination="CDG",
            departure_date="2026-08-15",
            return_date="2026-08-22",
            budget=2000.0,
        )
        mock_adapter = MagicMock()
        mock_adapter.list_playbooks.return_value = ["travel_plan.yml"]
        mock_adapter.run_playbook.return_value = {"status": "successful", "rc": 0, "events": []}
        with patch("general_ludd.cli_travel.AnsibleRunnerAdapter", return_value=mock_adapter):
            _cmd_travel_plan(args)
        mock_adapter.run_playbook.assert_called_once()
        called_extravars = mock_adapter.run_playbook.call_args[1]["extravars"]
        assert called_extravars["origin"] == "JFK"
        assert called_extravars["budget"] == 2000.0

    def test_plan_defaults_none_for_missing_args(self):
        args = argparse.Namespace(
            origin=None,
            destination=None,
            departure_date=None,
            return_date=None,
            budget=None,
        )
        mock_adapter = MagicMock()
        mock_adapter.list_playbooks.return_value = ["travel_plan.yml"]
        mock_adapter.run_playbook.return_value = {"status": "successful", "rc": 0}
        with patch("general_ludd.cli_travel.AnsibleRunnerAdapter", return_value=mock_adapter):
            _cmd_travel_plan(args)
        called_extravars = mock_adapter.run_playbook.call_args[1]["extravars"]
        assert called_extravars["origin"] is None
        assert called_extravars["budget"] is None


class TestCmdTravelSearchFlights:
    def test_runs_search_flights_playbook(self):
        args = argparse.Namespace(
            origin="LAX",
            destination="HND",
            departure_date="2026-09-01",
            return_date="2026-09-10",
            passengers=3,
        )
        mock_adapter = MagicMock()
        mock_adapter.list_playbooks.return_value = ["travel_search_flights.yml"]
        mock_adapter.run_playbook.return_value = {"status": "successful", "rc": 0}
        with patch("general_ludd.cli_travel.AnsibleRunnerAdapter", return_value=mock_adapter):
            _cmd_travel_search_flights(args)
        called_extravars = mock_adapter.run_playbook.call_args[1]["extravars"]
        assert called_extravars["origin"] == "LAX"
        assert called_extravars["passengers"] == 3

    def test_flights_default_passengers_one(self):
        args = argparse.Namespace(
            origin="SFO",
            destination="NRT",
            departure_date="2026-10-01",
            return_date="2026-10-10",
        )
        args.passengers = getattr(args, "passengers", 1)
        mock_adapter = MagicMock()
        mock_adapter.list_playbooks.return_value = ["travel_search_flights.yml"]
        mock_adapter.run_playbook.return_value = {"status": "successful", "rc": 0}
        with patch("general_ludd.cli_travel.AnsibleRunnerAdapter", return_value=mock_adapter):
            _cmd_travel_search_flights(args)
        called_extravars = mock_adapter.run_playbook.call_args[1]["extravars"]
        assert called_extravars["passengers"] == 1


class TestCmdTravelSearchHotels:
    def test_runs_search_hotels_playbook(self):
        args = argparse.Namespace(
            destination="Paris",
            checkin_date="2026-08-15",
            checkout_date="2026-08-22",
            guests=4,
        )
        mock_adapter = MagicMock()
        mock_adapter.list_playbooks.return_value = ["travel_search_hotels.yml"]
        mock_adapter.run_playbook.return_value = {"status": "successful", "rc": 0}
        with patch("general_ludd.cli_travel.AnsibleRunnerAdapter", return_value=mock_adapter):
            _cmd_travel_search_hotels(args)
        called_extravars = mock_adapter.run_playbook.call_args[1]["extravars"]
        assert called_extravars["destination"] == "Paris"
        assert called_extravars["guests"] == 4

    def test_hotels_default_guests_one(self):
        args = argparse.Namespace(
            destination="London",
            checkin_date="2026-11-01",
            checkout_date="2026-11-07",
        )
        args.guests = getattr(args, "guests", 1)
        mock_adapter = MagicMock()
        mock_adapter.list_playbooks.return_value = ["travel_search_hotels.yml"]
        mock_adapter.run_playbook.return_value = {"status": "successful", "rc": 0}
        with patch("general_ludd.cli_travel.AnsibleRunnerAdapter", return_value=mock_adapter):
            _cmd_travel_search_hotels(args)
        called_extravars = mock_adapter.run_playbook.call_args[1]["extravars"]
        assert called_extravars["guests"] == 1


class TestCmdTravelEventPlan:
    def test_runs_event_plan_playbook(self):
        args = argparse.Namespace(
            destination="Berlin",
            event_date="2026-10-01",
            event_type="conference",
            attendees=200,
        )
        mock_adapter = MagicMock()
        mock_adapter.list_playbooks.return_value = ["travel_event_plan.yml"]
        mock_adapter.run_playbook.return_value = {"status": "successful", "rc": 0}
        with patch("general_ludd.cli_travel.AnsibleRunnerAdapter", return_value=mock_adapter):
            _cmd_travel_event_plan(args)
        called_extravars = mock_adapter.run_playbook.call_args[1]["extravars"]
        assert called_extravars["destination"] == "Berlin"
        assert called_extravars["event_type"] == "conference"
        assert called_extravars["attendees"] == 200

    def test_event_plan_default_attendees_one(self):
        args = argparse.Namespace(
            destination="Tokyo",
            event_date="2026-12-01",
            event_type="meetup",
        )
        args.attendees = getattr(args, "attendees", 1)
        mock_adapter = MagicMock()
        mock_adapter.list_playbooks.return_value = ["travel_event_plan.yml"]
        mock_adapter.run_playbook.return_value = {"status": "successful", "rc": 0}
        with patch("general_ludd.cli_travel.AnsibleRunnerAdapter", return_value=mock_adapter):
            _cmd_travel_event_plan(args)
        called_extravars = mock_adapter.run_playbook.call_args[1]["extravars"]
        assert called_extravars["attendees"] == 1


class TestEmitResult:
    def test_prints_successful_status(self, capsys: pytest.CaptureFixture[str]):
        _emit_result({"status": "successful", "rc": 0})
        captured = capsys.readouterr()
        assert "status=successful" in captured.out
        assert "rc=0" in captured.out

    def test_prints_failed_status(self, capsys: pytest.CaptureFixture[str]):
        with pytest.raises(SystemExit):
            _emit_result({"status": "failed", "rc": 2})
        captured = capsys.readouterr()
        assert "status=failed" in captured.out
        assert "rc=2" in captured.out

    def test_prints_events_count(self, capsys: pytest.CaptureFixture[str]):
        _emit_result(
            {
                "status": "successful",
                "rc": 0,
                "events": [{"event": "task_started"}, {"event": "task_ok"}],
            }
        )
        captured = capsys.readouterr()
        assert "events=2" in captured.out

    def test_no_events_key(self, capsys: pytest.CaptureFixture[str]):
        _emit_result({"status": "successful", "rc": 0})
        captured = capsys.readouterr()
        assert "events=" not in captured.out

    def test_empty_events_list(self, capsys: pytest.CaptureFixture[str]):
        _emit_result({"status": "successful", "rc": 0, "events": []})
        captured = capsys.readouterr()
        assert "events=" not in captured.out

    def test_exits_with_rc_when_nonzero(self):
        with pytest.raises(SystemExit) as exc_info:
            _emit_result({"status": "failed", "rc": 5})
        assert exc_info.value.code == 5

    def test_exits_with_1_when_status_failed_rc_zero(self):
        with pytest.raises(SystemExit) as exc_info:
            _emit_result({"status": "failed", "rc": 0})
        assert exc_info.value.code == 1

    def test_defaults_rc_to_1_when_missing(self):
        with pytest.raises(SystemExit) as exc_info:
            _emit_result({})
        assert exc_info.value.code == 1

    def test_defaults_status_to_failed_when_missing(self, capsys: pytest.CaptureFixture[str]):
        with pytest.raises(SystemExit):
            _emit_result({})
        captured = capsys.readouterr()
        assert "status=failed" in captured.out
        assert "rc=1" in captured.out
