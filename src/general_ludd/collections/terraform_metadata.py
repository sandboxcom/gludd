"""Small deterministic parsers for imported Terraform collection metadata."""

from __future__ import annotations

import re

_VARIABLE_RE = re.compile(r'^\s*variable\s+"([^"]+)"\s*\{', re.MULTILINE)
_TFVARS_KEY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
_VERSION_RE = re.compile(r'version\s*=\s*"(?P<v>[^"]+)"')
_REQUIRED_PROVIDERS_HEADER_RE = re.compile(r"required_providers\s*\{")
_PROVIDER_NAME_RE = re.compile(r"([A-Za-z0-9_-]+)\s*=\s*\{")


def parse_variable_names(text: str) -> list[str]:
    """Extract variable names from a Terraform variables document."""
    return _VARIABLE_RE.findall(text)


def parse_tfvars_keys(text: str) -> set[str]:
    """Extract top-level assignment keys from a tfvars document."""
    keys: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            continue
        match = _TFVARS_KEY_RE.match(line)
        if match:
            keys.add(match.group(1))
    return keys


def parse_required_providers(text: str) -> dict[str, str]:
    """Parse required-provider blocks into provider-to-version mappings."""
    providers: dict[str, str] = {}
    for header in _REQUIRED_PROVIDERS_HEADER_RE.finditer(text):
        start = header.end()
        depth = 1
        index = start
        while index < len(text) and depth > 0:
            character = text[index]
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
            index += 1
        block_body = text[start : index - 1]
        for name, entry in iter_provider_entries(block_body):
            match = _VERSION_RE.search(entry)
            if match:
                providers[name] = match.group("v")
    return providers


def iter_provider_entries(body: str) -> list[tuple[str, str]]:
    """Return provider names and their balanced configuration bodies."""
    entries: list[tuple[str, str]] = []
    for match in _PROVIDER_NAME_RE.finditer(body):
        name = match.group(1)
        start = match.end()
        depth = 1
        index = start
        while index < len(body) and depth > 0:
            character = body[index]
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
            index += 1
        entries.append((name, body[start : index - 1]))
    return entries


def is_floating_version(version: str) -> bool:
    """Return whether a constraint permits unplanned provider upgrades."""
    normalized = version.strip()
    if normalized.startswith("~>") or normalized.startswith("="):
        return False
    return bool(normalized)


__all__ = [
    "is_floating_version",
    "iter_provider_entries",
    "parse_required_providers",
    "parse_tfvars_keys",
    "parse_variable_names",
]
