"""Unit tests for CLI subcommand: ``gludd spec-quality``."""

from __future__ import annotations

import json
from argparse import ArgumentParser

import pytest

from general_ludd.cli_spec_quality import (
    _cmd_audit,
    _cmd_check,
    _cmd_rules,
    _cmd_scan,
    _parse_entries_arg,
    add_spec_quality_subparser,
)


def _spec_quality_parser() -> ArgumentParser:
    """Build a parser with the spec-quality subcommand registered."""
    parser = ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    add_spec_quality_subparser(sub)
    return parser


class TestAddSpecQualitySubparser:
    def test_subcommand_added(self) -> None:
        parser = _spec_quality_parser()
        args, _ = parser.parse_known_args(["spec-quality", "audit", "--entries", "[]"])
        assert args.command == "spec-quality"

    def test_audit_parses_entries_and_json(self) -> None:
        parser = _spec_quality_parser()
        args, _ = parser.parse_known_args(["spec-quality", "audit", "--entries", "[]", "--json"])
        assert args.json is True
        assert args.entries == "[]"

    def test_audit_no_json_defaults_false(self) -> None:
        parser = _spec_quality_parser()
        args, _ = parser.parse_known_args(["spec-quality", "audit", "--entries", "[]"])
        assert args.json is False

    def test_check_parses_positional_args(self) -> None:
        parser = _spec_quality_parser()
        args, _ = parser.parse_known_args(["spec-quality", "check", "AA001", "spec body here"])
        assert args.spec_id == "AA001"
        assert args.body == "spec body here"

    def test_add_subparser_yields_all_exported(self) -> None:
        from general_ludd import cli_spec_quality

        assert "add_spec_quality_subparser" in cli_spec_quality.__all__

    def test_audit_sets_func_default(self) -> None:
        parser = _spec_quality_parser()
        args, _ = parser.parse_known_args(["spec-quality", "audit", "--entries", "[]"])
        assert callable(args.func)
        assert args.func is _cmd_audit

    def test_check_sets_func_default(self) -> None:
        parser = _spec_quality_parser()
        args, _ = parser.parse_known_args(["spec-quality", "check", "AA001", "body"])
        assert callable(args.func)
        assert args.func is _cmd_check


class TestParseEntriesArg:
    def test_parse_valid_json_array(self) -> None:
        ns = type("NS", (), {"entries": '[{"spec_id":"AA001","body":"hello"}]'})()
        result = _parse_entries_arg(ns)
        assert len(result) == 1
        assert result[0]["spec_id"] == "AA001"
        assert result[0]["body"] == "hello"

    def test_parse_empty_entries(self) -> None:
        ns = type("NS", (), {"entries": None})()
        result = _parse_entries_arg(ns)
        assert result == []

    def test_parse_missing_entries_attr(self) -> None:
        ns = type("NS", (), {})()
        result = _parse_entries_arg(ns)
        assert result == []

    def test_parse_invalid_json_exits(self) -> None:
        ns = type("NS", (), {"entries": "not json"})()
        with pytest.raises(SystemExit) as exc_info:
            _parse_entries_arg(ns)
        assert exc_info.value.code == 2

    def test_parse_ignores_non_dict_items(self) -> None:
        ns = type("NS", (), {"entries": '[{"spec_id":"AA001","body":"x"}, "string", 42]'})()
        result = _parse_entries_arg(ns)
        assert len(result) == 1
        assert result[0]["spec_id"] == "AA001"


class TestCmdAudit:
    def _make_valid_entry(self) -> list[dict[str, object]]:
        return [{"spec_id": "AA001", "body": "**Enforcement:** `make lint`\n**Behavior:** Blocks on error. exit 1"}]

    def test_audit_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        entries_json = json.dumps(self._make_valid_entry())
        ns = type("NS", (), {"entries": entries_json, "json": True})()
        _cmd_audit(ns)
        out = capsys.readouterr().out
        result = json.loads(out)
        assert "total_findings" in result
        assert "error_count" in result
        assert isinstance(result["findings"], list)

    def test_audit_text_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        entries_json = json.dumps(self._make_valid_entry())
        ns = type("NS", (), {"entries": entries_json, "json": False})()
        _cmd_audit(ns)
        out = capsys.readouterr().out
        assert "Total findings:" in out
        assert "Errors:" in out
        assert "Warnings:" in out

    def test_audit_has_errors_causes_exit_1(self, capsys: pytest.CaptureFixture[str]) -> None:
        entry = [{"spec_id": "AA001", "body": "no enforcement field here"}]
        entries_json = json.dumps(entry)
        ns = type("NS", (), {"entries": entries_json, "json": True})()
        with pytest.raises(SystemExit) as exc_info:
            _cmd_audit(ns)
        assert exc_info.value.code == 1

    def test_audit_empty_entries_exits_0(self, capsys: pytest.CaptureFixture[str]) -> None:
        ns = type("NS", (), {"entries": "[]", "json": True})()
        _cmd_audit(ns)
        out = capsys.readouterr().out
        result = json.loads(out)
        assert result["total_findings"] == 0
        assert result["has_errors"] is False


class TestCmdCheck:
    def _valid_body(self) -> str:
        return "**Enforcement:** `make lint`\n**Behavior:** Blocks on error. exit 1"

    def test_check_pass_no_findings(self, capsys: pytest.CaptureFixture[str]) -> None:
        ns = type("NS", (), {"spec_id": "AA001", "body": self._valid_body(), "json": False})()
        _cmd_check(ns)
        out = capsys.readouterr().out
        assert "PASS:" in out
        assert "no findings" in out

    def test_check_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        ns = type("NS", (), {"spec_id": "AA001", "body": self._valid_body(), "json": True})()
        _cmd_check(ns)
        out = capsys.readouterr().out
        result = json.loads(out)
        assert result["spec_id"] == "AA001"
        assert result["total_findings"] == 0
        assert result["has_errors"] is False

    def test_check_fail_on_error_finding(self, capsys: pytest.CaptureFixture[str]) -> None:
        ns = type("NS", (), {"spec_id": "AA002", "body": "no enforcement", "json": False})()
        with pytest.raises(SystemExit) as exc_info:
            _cmd_check(ns)
        assert exc_info.value.code == 1

    def test_check_text_output_on_fail(self, capsys: pytest.CaptureFixture[str]) -> None:
        ns = type("NS", (), {"spec_id": "AA002", "body": "no enforcement", "json": False})()
        with pytest.raises(SystemExit):
            _cmd_check(ns)
        out = capsys.readouterr().out
        assert "FAIL:" in out


class TestCmdScan:
    def test_scan_text_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        ns = type("NS", (), {"json": False, "entries": None})()
        _cmd_scan(ns)
        out = capsys.readouterr().out
        assert "Codebase scan:" in out
        assert "Errors:" in out
        assert "Warnings:" in out

    def test_scan_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        ns = type("NS", (), {"json": True, "entries": None})()
        _cmd_scan(ns)
        out = capsys.readouterr().out
        result = json.loads(out)
        assert "total_findings" in result
        assert "error_count" in result

    def test_scan_json_has_expected_keys(self, capsys: pytest.CaptureFixture[str]) -> None:
        ns = type("NS", (), {"json": True, "entries": None})()
        _cmd_scan(ns)
        out = capsys.readouterr().out
        result = json.loads(out)
        for key in (
            "total_findings",
            "error_count",
            "warning_count",
            "info_count",
            "has_errors",
            "unique_specs_checked",
            "findings",
        ):
            assert key in result

    def test_scan_exits_0_when_no_errors(self) -> None:
        ns = type("NS", (), {"json": True, "entries": None})()
        _cmd_scan(ns)


class TestCmdRules:
    def test_rules_text_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        ns = type("NS", (), {"json": False})()
        _cmd_rules(ns)
        out = capsys.readouterr().out
        assert "Registered audit rules" in out
        assert "[R001]" in out
        assert "Enforcement Present" in out

    def test_rules_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        ns = type("NS", (), {"json": True})()
        _cmd_rules(ns)
        out = capsys.readouterr().out
        result = json.loads(out)
        assert "count" in result
        assert result["count"] >= 1
        assert "rules" in result
        rule = result["rules"][0]
        assert "rule_id" in rule
        assert "name" in rule
        assert "severity" in rule
        assert "active" in rule

    def test_rules_five_rules_registered(self, capsys: pytest.CaptureFixture[str]) -> None:
        ns = type("NS", (), {"json": True})()
        _cmd_rules(ns)
        result = json.loads(capsys.readouterr().out)
        assert result["count"] == 5
