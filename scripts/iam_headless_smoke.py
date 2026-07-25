"""Credential-free smoke validation for checked-in IAM role definitions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

REQUIRED_PERSONAS = frozenset({"terraform_deploy", "runtime_execution", "model_inference", "monitor"})
PROVIDER_FILES = {
    "aws": "aws-iam-roles.yml",
    "gcp": "gcp-iam-roles.yml",
    "azure": "azure-iam-roles.yml",
}
FORBIDDEN_BINDINGS = frozenset({"roles/owner", "roles/editor", "roles/viewer", "Owner", "User Access Administrator"})


def _load_roles(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("roles"), dict):
        raise ValueError("missing top-level roles mapping")
    return document["roles"]


def _validate_provider(provider: str, roles: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    missing = sorted(REQUIRED_PERSONAS - roles.keys())
    if missing:
        violations.append(f"{provider}: missing personas: {', '.join(missing)}")

    for name, role in roles.items():
        if not isinstance(role, dict):
            violations.append(f"{provider}:{name}: role definition must be a mapping")
            continue
        if len(str(role.get("description", "")).strip()) < 20:
            violations.append(f"{provider}:{name}: description must be at least 20 characters")
        if provider == "aws":
            for statement in role.get("policy", []):
                for action in statement.get("Action", []):
                    if action == "*" or action == "*:*":
                        violations.append(f"{provider}:{name}: admin wildcard action is forbidden")
        else:
            for binding in role.get("roles", role.get("role_definitions", [])):
                if binding in FORBIDDEN_BINDINGS:
                    violations.append(f"{provider}:{name}: forbidden admin binding {binding}")
    return violations


def run_smoke(infra_dir: Path) -> dict[str, Any]:
    """Validate IAM manifests without cloud credentials or provider CLIs."""
    providers: dict[str, int] = {}
    violations: list[str] = []
    for provider, filename in PROVIDER_FILES.items():
        path = infra_dir / filename
        if not path.exists():
            violations.append(f"{provider}: missing manifest {filename}")
            continue
        try:
            roles = _load_roles(path)
        except (OSError, ValueError, yaml.YAMLError) as error:
            violations.append(f"{provider}: invalid manifest: {error}")
            continue
        providers[provider] = len(roles)
        violations.extend(_validate_provider(provider, roles))
    return {"mode": "headless", "ok": not violations, "providers": providers, "violations": violations}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--infra-dir", type=Path, default=Path("config/infra"))
    args = parser.parse_args()
    report = run_smoke(args.infra_dir)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
