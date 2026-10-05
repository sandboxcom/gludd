#!/usr/bin/env python3
"""Ingest Azure CLI JSON into the protected immutable credential store."""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Sequence

from general_ludd.azure.accelerator_credential_store import (
    AzureAcceleratorCredentialStore,
    AzureCredentialArtifactError,
    durable_azure_accelerator_credential_path,
)
from general_ludd.azure.accelerator_credentials import (
    AzureAcceleratorCredentialError,
    read_azure_accelerator_credential_payload,
)

_MAX_INPUT_BYTES = 16 * 1024
PLAN_MARKER = (
    "AZURE_ACCELERATOR_AUTH_STORE_PLAN durable=true "
    "immutable_generation=true automatic_deletion=false secret_output=false"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--auth-file", default="")
    parser.add_argument("--source-file", default="")
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument("--validate-only", action="store_true")
    return parser


def _validate_subscription_id(value: str) -> None:
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, AttributeError):
        canonical = ""
    if value != canonical:
        raise AzureCredentialArtifactError(
            "credential identifiers must be canonical UUID values"
        )


def _read_stdin() -> bytes:
    payload = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    if len(payload) > _MAX_INPUT_BYTES:
        raise AzureCredentialArtifactError("credential input is too large")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    """Validate a destination, or install one secret without rendering it."""
    args = _parser().parse_args(argv)
    try:
        _validate_subscription_id(args.subscription_id)
        target = durable_azure_accelerator_credential_path(args.auth_file or None)
        if args.validate_only:
            print(PLAN_MARKER)
            return 0
        payload = (
            read_azure_accelerator_credential_payload(args.source_file)
            if args.source_file
            else _read_stdin()
        )
        store = AzureAcceleratorCredentialStore(
            root=target.parent,
            current_name=target.name,
        )
        receipt = store.install(
            payload,
            expected_subscription_id=args.subscription_id,
        )
    except (AzureAcceleratorCredentialError, AzureCredentialArtifactError) as exc:
        print(f"AZURE_ACCELERATOR_AUTH_STORE_INVALID reason={exc}", file=sys.stderr)
        return 2
    print(
        "AZURE_ACCELERATOR_AUTH_STORED "
        f"generation={receipt.generation_id} durable=true "
        "immutable_generation=true automatic_deletion=false secret_output=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
