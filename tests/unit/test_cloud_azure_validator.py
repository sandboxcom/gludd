"""Tests for Azure validator — azure_generate_portal_json."""

from __future__ import annotations

from general_ludd.cloud.azure_validator import azure_generate_portal_json


class TestAzureGeneratePortalJson:
    def test_full_cli_json_to_portal_format(self):
        cli_json = {
            "Name": "Monitor Reader",
            "Description": "Read-only monitoring",
            "AssignableScopes": ["/subscriptions/abc"],
            "Actions": ["Microsoft.Compute/*/read"],
            "NotActions": ["Microsoft.Compute/virtualMachines/delete"],
            "DataActions": ["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"],
            "NotDataActions": [],
        }
        result = azure_generate_portal_json(cli_json)
        props = result["properties"]

        assert props["roleName"] == "Monitor Reader"
        assert props["description"] == "Read-only monitoring"
        assert props["assignableScopes"] == ["/subscriptions/abc"]

        perms = props["permissions"]
        assert len(perms) == 1
        assert perms[0]["actions"] == ["Microsoft.Compute/*/read"]
        assert perms[0]["notActions"] == ["Microsoft.Compute/virtualMachines/delete"]
        assert perms[0]["dataActions"] == ["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"]
        assert perms[0]["notDataActions"] == []

    def test_empty_cli_json(self):
        result = azure_generate_portal_json({})
        props = result["properties"]

        assert props["roleName"] == ""
        assert props["description"] == ""
        assert props["assignableScopes"] == []
        assert props["permissions"] == [
            {
                "actions": [],
                "notActions": [],
                "dataActions": [],
                "notDataActions": [],
            }
        ]

    def test_partial_cli_json(self):
        result = azure_generate_portal_json({"Name": "Reader Only"})
        props = result["properties"]

        assert props["roleName"] == "Reader Only"
        assert props["description"] == ""
        assert props["assignableScopes"] == []
