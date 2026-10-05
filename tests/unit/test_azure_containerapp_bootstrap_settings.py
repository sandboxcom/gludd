"""Tests for exact Azure bootstrap configuration parsing."""

import pytest

from general_ludd.self_improve.azure_containerapp_bootstrap_settings import (
    parse_azure_containerapp_bootstrap_settings,
)


def test_settings_parser_rejects_partial_auth_schema() -> None:
    """A partial configuration must fail before credentials or compute exist."""
    with pytest.raises(ValueError, match="exact schema"):
        parse_azure_containerapp_bootstrap_settings(
            {
                "schema_version": 1,
                "enabled": True,
                "auth_file": "/private/azure-auth.json",
            }
        )
