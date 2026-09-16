"""Censored failure-detail vocabulary for Container App lifecycle evidence."""

from __future__ import annotations

from general_ludd.infra.azure_containerapp_make_types import (
    LIVE_PROOF_RUNTIME_FAILURE_DETAILS,
)

LIVE_PROOF_FAILURE_DETAILS = frozenset(
    {
        "action",
        "arguments",
        "change_count",
        "configuration",
        "container",
        "cost_policy_action",
        "cost_policy_identity",
        "cuda_startup_command",
        "cuda_startup_command_executable",
        "cuda_startup_command_indented",
        "cuda_startup_command_mismatch",
        "cuda_startup_command_shape",
        "cuda_startup_command_wrapped",
        "environment_binding",
        "format",
        "image",
        "ingress",
        "network_restriction",
        "resource_identity",
        "resource_scope",
        "shape",
        *LIVE_PROOF_RUNTIME_FAILURE_DETAILS,
    }
)

__all__ = ["LIVE_PROOF_FAILURE_DETAILS"]
