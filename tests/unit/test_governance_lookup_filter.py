"""Tests for bounded governance lookup command planning."""

from __future__ import annotations

from typing import Any

import pytest
from ansible.errors import AnsibleFilterError
from ansible_collections.general_ludd.governance.plugins.filter.governance_lookup import (
    FilterModule,
    build_governance_lookup_plan,
)


@pytest.mark.parametrize(
    ("profile", "variables", "expected_filename", "expected_tail"),
    (
        (
            "civic_service_finder",
            {
                "country": "US",
                "category": "health",
                "list_countries": False,
                "list_categories": False,
            },
            "civic_services.py",
            ["--country", "US", "--category", "health"],
        ),
        (
            "conflicts_treaties_lookup",
            {"country": "FR", "scope": "both"},
            "conflicts_treaties.py",
            ["--country", "FR", "--scope", "both"],
        ),
        (
            "decision_maker_lookup",
            {"country": "DE", "branch": "executive", "list_countries": False},
            "decision_makers.py",
            ["--country", "DE", "--branch", "executive"],
        ),
        (
            "info_classification_check",
            {"scheme": "data", "level": "restricted", "list_schemes": True},
            "info_classification.py",
            ["--scheme", "data", "--level", "restricted", "--list-schemes"],
        ),
        (
            "lookup_governing_body",
            {"country": "CA", "type": "federal", "list_countries": False},
            "governing_bodies.py",
            ["--country", "CA", "--type", "federal"],
        ),
        (
            "navigate_borders",
            {"country": "CH", "list_countries": False},
            "borders.py",
            ["--country", "CH"],
        ),
        (
            "tax_currency_info",
            {"country": "JP", "list_countries": False},
            "tax_currency_cli.py",
            ["--country", "JP"],
        ),
    ),
)
def test_build_plan_for_every_allowlisted_profile(
    profile: str,
    variables: dict[str, Any],
    expected_filename: str,
    expected_tail: list[str],
) -> None:
    prefix = profile
    role_vars = {
        f"{prefix}_enabled": True,
        f"{prefix}_output_dir": f"/tmp/{profile}",
        "ansible_python_interpreter": "/usr/bin/python3",
        **{f"{prefix}_{key}": value for key, value in variables.items()},
    }

    plan = build_governance_lookup_plan(profile, role_vars)

    assert plan["filename"] == expected_filename
    assert plan["result_fact"] == f"{profile}_result"
    assert plan["argv"][:2] == [
        "/usr/bin/python3",
        f"/tmp/{profile}/{expected_filename}",
    ]
    assert plan["argv"][2:] == expected_tail
    expected_support = ["tax_currency.py"] if profile == "tax_currency_info" else []
    assert plan["support_filenames"] == expected_support


@pytest.mark.parametrize(
    ("profile", "list_flag"),
    (
        ("civic_service_finder", "list_categories"),
        ("decision_maker_lookup", "list_countries"),
        ("lookup_governing_body", "list_countries"),
        ("navigate_borders", "list_countries"),
        ("tax_currency_info", "list_countries"),
    ),
)
def test_list_modes_do_not_emit_empty_country_values(
    profile: str,
    list_flag: str,
) -> None:
    plan = build_governance_lookup_plan(
        profile,
        {
            f"{profile}_enabled": "yes",
            f"{profile}_output_dir": "/tmp/governance",
            f"{profile}_country": None,
            f"{profile}_{list_flag}": True,
        },
    )

    assert "--country" not in plan["argv"]
    assert f"--{list_flag.replace('_', '-')}" in plan["argv"]


@pytest.mark.parametrize(
    ("profile", "variables"),
    (
        ("unknown", {}),
        ("navigate_borders", {"navigate_borders_enabled": False}),
        (
            "conflicts_treaties_lookup",
            {
                "conflicts_treaties_lookup_enabled": True,
                "conflicts_treaties_lookup_output_dir": "/tmp/out",
                "conflicts_treaties_lookup_country": "USA",
                "conflicts_treaties_lookup_scope": "both",
            },
        ),
        (
            "info_classification_check",
            {
                "info_classification_check_enabled": True,
                "info_classification_check_output_dir": "x" * 4097,
                "info_classification_check_scheme": "secret",
            },
        ),
    ),
)
def test_invalid_or_unbounded_requests_fail_closed(
    profile: str,
    variables: dict[str, Any],
) -> None:
    with pytest.raises(AnsibleFilterError):
        build_governance_lookup_plan(profile, variables)


def test_filter_module_exports_the_collection_filter() -> None:
    assert FilterModule().filters() == {
        "governance_lookup_plan": build_governance_lookup_plan
    }
