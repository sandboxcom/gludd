"""Verify enhanced status endpoint output matches the CLI format."""
import asyncio
import json

from general_ludd.cli import _gather_offline_status, _format_offline_status

print("=== OFFLINE STATUS ===")
info = _gather_offline_status()
_format_offline_status(info)

print()
print("=== RAW binary_versions ===")
print(json.dumps(info.get("binary_versions", {}), indent=2))
print()
print("=== RAW filestore_binaries ===")
print(json.dumps(info.get("filestore_binaries", []), indent=2))
