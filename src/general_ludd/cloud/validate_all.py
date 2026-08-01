"""Offline cross-provider validation for generated least-privilege roles."""

from __future__ import annotations

from general_ludd.cloud.core import generate_cloud_role, validate_cloud_role

_PROVIDERS = ("azure", "aws", "gcp")
_GENERATED_STATUSES = {"ok", "generated_with_warnings"}


def validate_monitor_roles() -> int:
    """Generate and validate each provider's monitor role without cloud calls."""
    failed = False
    for provider in _PROVIDERS:
        generated = generate_cloud_role(provider, "monitor")
        generated_status = str(generated.get("status", "error"))
        role_definition = generated.get("role_definition")
        if generated_status not in _GENERATED_STATUSES or not isinstance(role_definition, dict):
            print(f"{provider} monitor: generated={generated_status} validated=skipped")
            failed = True
            continue

        validated = validate_cloud_role(provider, role_definition)
        validated_status = str(validated.get("status", "invalid"))
        print(f"{provider} monitor: generated={generated_status} validated={validated_status}")
        failed = failed or validated_status != "valid"

    return int(failed)


def main() -> int:
    """Run the cross-provider IAM generation smoke check."""
    return validate_monitor_roles()


if __name__ == "__main__":
    raise SystemExit(main())
