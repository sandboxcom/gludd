"""Second extended regression guard: all connector health() + query()
methods must not leak exception detail (batch 2 — 16 connectors)."""

from __future__ import annotations

import inspect

LEAK_PATTERNS = [
    'f"{type(exc).__name__}: {exc}"',
    'f"transport error: {exc}"',
    'f"health error: {exc}"',
    'f"health error: {exc!r}"',
    'f"client factory error: {exc!r}"',
    'f"request failed: {exc}"',
    'f"probe error: {type(exc).__name__}: {exc}"',
    'str(exc)',
]

CONNECTORS = [
    "gcp_observability",
    "gcp_asset_inventory",
    "aws_config_trail",
    "azure_devops",
    "azure_resource_graph",
    "gitlab_ci",
    "circleci",
    "buildkite",
    "travis",
    "argo_workflows",
    "okta",
    "entra_signin",
    "pagerduty",
    "opsgenie",
    "k8s_events",
    "syslog_file",
]


def _src(mod_name: str) -> str:
    module = __import__(f"general_ludd.connectors.{mod_name}", fromlist=[""])
    return inspect.getsource(module)


def test_no_health_exc_leak_remains() -> None:
    for mod_name in CONNECTORS:
        src = _src(mod_name)
        for pattern in LEAK_PATTERNS:
            assert pattern not in src, (
                f"{mod_name}: leak pattern {pattern!r} found in source"
            )


def test_each_health_logs_with_exc_info() -> None:
    for mod_name in CONNECTORS:
        src = _src(mod_name)
        assert "exc_info=True" in src, (
            f"{mod_name}: no exc_info=True in source"
        )


def test_generic_error_messages_in_health_dicts() -> None:
    for mod_name in CONNECTORS:
        src = _src(mod_name)
        assert "health check failed" in src, (
            f"{mod_name}: no 'health check failed' message in source"
        )
