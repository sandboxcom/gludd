"""Focused contracts for bounded FreeLLMAPI source inspection."""

from __future__ import annotations

import io
import json
import tarfile

from general_ludd.models.freellmapi_upstream_source import (
    FREELLMAPI_EXPECTED_EXPORTS,
    inspect_upstream_archive,
)


def _archive() -> bytes:
    license_text = b"""MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
THE SOFTWARE IS PROVIDED \"AS IS\"
"""
    package_lock = json.dumps(
        {"name": "@freellmapi/monorepo", "lockfileVersion": 3, "packages": {}}
    ).encode()
    scoring = "\n".join(
        f"export function {name}(): number {{ return 1; }}"
        for name in FREELLMAPI_EXPECTED_EXPORTS
    ).encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for path, content in (
            ("LICENSE", license_text),
            ("package-lock.json", package_lock),
            ("server/src/services/scoring.ts", scoring),
        ):
            info = tarfile.TarInfo(f"freellmapi-source/{path}")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return stream.getvalue()


def test_inspection_returns_only_required_validated_source_evidence() -> None:
    evidence = inspect_upstream_archive(_archive(), max_archive_bytes=64 * 1024 * 1024)

    assert evidence.license_bytes.startswith(b"MIT License")
    assert json.loads(evidence.package_lock_bytes)["lockfileVersion"] == 3
    assert evidence.symbols_present == list(FREELLMAPI_EXPECTED_EXPORTS)
    assert evidence.symbols_missing == []
    assert b"rateLimitFactor" in evidence.scoring_bytes
