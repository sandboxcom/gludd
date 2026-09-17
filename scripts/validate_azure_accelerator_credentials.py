#!/usr/bin/env python3
"""Validate Azure accelerator JSON credentials without rendering their values."""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Sequence

from general_ludd.azure.accelerator_credential_store import (
    load_preserved_azure_accelerator_credentials as load_azure_accelerator_credentials,
)
from general_ludd.azure.accelerator_credentials import (
    AzureAcceleratorCredentialError,
)

SUCCESS_MARKER = (
    "AZURE_ACCELERATOR_AUTH_FILE_OK format=json private_mode=true "
    "owner=current subscription_match=true secret_output=false"
)
PLAN_MARKER = "AZURE_ACCELERATOR_AUTH_CHECK_PLAN secret_output=false"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Secret-safe validation for Azure CLI --json-auth output.",
        allow_abbrev=False,
    )
    parser.add_argument("--auth-file", required=True)
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument("--validate-only", action="store_true")
    return parser


def _validate_subscription_id(value: str) -> None:
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, AttributeError):
        canonical = ""
    if value != canonical:
        raise AzureAcceleratorCredentialError(
            "credential identifiers must be canonical UUID values"
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Validate a private credential file and emit fixed, secret-free markers."""

    args = _parser().parse_args(argv)
    try:
        if args.validate_only:
            _validate_subscription_id(args.subscription_id)
            print(PLAN_MARKER)
            return 0
        load_azure_accelerator_credentials(
            args.auth_file,
            expected_subscription_id=args.subscription_id,
        )
    except AzureAcceleratorCredentialError as exc:
        print(f"AZURE_ACCELERATOR_AUTH_FILE_INVALID reason={exc}", file=sys.stderr)
        return 2
    print(SUCCESS_MARKER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
