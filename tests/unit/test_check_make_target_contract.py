"""Branch-complete tests for the Make target contract checker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from scripts.check_make_target_contract import (
    _stanzas,
    load_contract,
    main,
    validate_contract,
)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"targets": {}},
        {"wrong": []},
    ],
)
def test_load_contract_rejects_non_list_schema(tmp_path: Path, payload: Any) -> None:
    """The top-level contract schema fails closed."""
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="targets list"):
        load_contract(path)


def test_stanza_parser_flushes_on_comments_targets_and_eof() -> None:
    """Every target is retained across comments and the final EOF boundary."""
    makefile = (
        "first:\n"
        "\t@echo first\n"
        "# separator\n"
        "second: dependency\n"
        "  @echo second\n"
        "\n"
        "third:\n"
        "\t@echo third"
    )

    stanzas = _stanzas(makefile)

    assert set(stanzas) == {"first", "second", "third"}
    assert "@echo first" in stanzas["first"]
    assert "@echo second" in stanzas["second"]
    assert "@echo third" in stanzas["third"]


def test_validate_contract_reports_every_malformed_entry_class(tmp_path: Path) -> None:
    """Bad contract entries produce stable actionable diagnostics."""
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        "valid:\n"
        "\t@echo \"  valid VAR=$(VAR) TOKEN\"\n"
        "\n"
        "nohelp:\n"
        "\t@echo quiet\n"
        "\n"
        "badvars:\n"
        "\t@echo \"  badvars\"\n"
        "\n"
        "badbehavior:\n"
        "\t@echo \"  badbehavior\"\n"
        "\n"
        "missingref:\n"
        "\t@echo \"  missingref\"\n"
        "\n"
        "_internal:\n"
        "\t@echo internal\n",
        encoding="utf-8",
    )
    contract: dict[str, Any] = {
        "targets": [
            "not-an-object",
            {},
            {"name": "absent", "make_variables": [], "behavior": "make absent"},
            {"name": "valid", "make_variables": ["VAR"], "behavior": "make valid VAR=value"},
            {"name": "valid", "make_variables": ["VAR"], "behavior": "make valid"},
            {"name": "nohelp", "make_variables": [], "behavior": "make nohelp"},
            {"name": "badvars", "make_variables": "VAR", "behavior": "make badvars"},
            {"name": "badbehavior", "make_variables": [], "behavior": 3},
            {
                "name": "missingref",
                "make_variables": [7, "MISSING"],
                "behavior": "make missingref",
                "environment_variables": [8, "TOKEN"],
            },
            {"name": "_internal", "make_variables": [], "behavior": "make _internal"},
        ]
    }

    errors = validate_contract(makefile, contract)
    joined = "\n".join(errors)

    for expected in (
        "target entry must be an object",
        "target entry is missing a name",
        "absent: target is missing from Makefile",
        "valid: duplicated in contract",
        "valid: behavior does not demonstrate VAR",
        "nohelp: target is missing from make help",
        "badvars: make_variables must be a list",
        "badbehavior: behavior must start with 'make badbehavior'",
        "missingref: variable names must be strings",
        "missingref: Makefile does not reference MISSING",
        "missingref: behavior does not demonstrate MISSING",
        "missingref: behavior does not demonstrate 8",
        "missingref: behavior does not demonstrate TOKEN",
    ):
        assert expected in joined
    assert "_internal: target is missing from make help" not in joined


def test_main_reports_contract_parse_and_validation_failures(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI errors remain bounded and return nonzero."""
    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("{", encoding="utf-8")

    assert main([str(invalid_json)]) == 1
    assert "make-target-contract: ERROR" in capsys.readouterr().out

    missing_target = tmp_path / "missing.json"
    missing_target.write_text(
        json.dumps(
            {
                "targets": [
                    {
                        "name": "definitely-absent",
                        "make_variables": [],
                        "behavior": "make definitely-absent",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert main([str(missing_target)]) == 1
    output = capsys.readouterr().out
    assert "make-target-contract: FAIL" in output
    assert "target is missing from Makefile" in output


def test_main_accepts_the_repository_contract(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The real contract is the positive CLI boundary."""
    root = Path(__file__).resolve().parents[2]
    contract = root / "config" / "make_target_contract.json"

    assert main([str(contract)]) == 0
    assert "make-target-contract: PASS" in capsys.readouterr().out
