"""Per-cloud-service data retention / deletion notices.

The retention facts below summarize the **publicly documented** data-retention
behaviour of each cloud / model service after an account or API key is deleted.
They are advisory copy for display to humans (CLI + router) — not a legal
contract. Update the text when a provider publishes a new policy.

Services covered (the set the harness dispatches against):
    DeepSeek, OpenAI, Z.AI, AWS, GCP, Azure.
"""

from __future__ import annotations

from collections.abc import Mapping

# Canonical service identifiers (lowercase, hyphen-free for easy matching).
SUPPORTED_SERVICES: frozenset[str] = frozenset(
    {"deepseek", "openai", "zai", "aws", "gcp", "azure"}
)

# Canonical display name per service key.
_DISPLAY_NAME: dict[str, str] = {
    "deepseek": "DeepSeek",
    "openai": "OpenAI",
    "zai": "Z.AI",
    "aws": "AWS",
    "gcp": "GCP",
    "azure": "Azure",
}

# Retention policy text per service. Kept short and human-readable; mentions
# retention period (or "best effort") so the caller can show it verbatim.
_POLICIES: dict[str, str] = {
    "deepseek": (
        "DeepSeek retains API request logs and generated outputs for up to "
        "30 days after account/API-key deletion for abuse-monitoring and "
        "legal compliance, then permanently removes them. Inputs submitted "
        "via the API are not used for model training."
    ),
    "openai": (
        "OpenAI retains API request metadata and outputs for 30 days by "
        "default (extended retention is configurable per-project). After "
        "account deletion, customer content is purged within 30 days. API "
        "inputs are not used for training."
    ),
    "zai": (
        "Z.AI retains API request logs and account metadata for up to 30 "
        "days after deletion for security and fraud-detection purposes. "
        "Conversation data is deleted within 30 days of account closure; "
        "inputs are not used for model training."
    ),
    "aws": (
        "AWS does not delete your underlying resources (S3 objects, "
        "CloudWatch logs, DynamoDB rows) when an IAM user or root account "
        "is deleted — those resources persist until you delete them "
        "explicitly. Account closure triggers a 90-day post-closure window "
        "after which all remaining data in the account is permanently "
        "deleted. CloudTrail logs are retained as configured by the trail."
    ),
    "gcp": (
        "GCP retains certain billing and audit logs (Cloud Audit Logs, "
        "billing exports) for a regulated period after project deletion "
        "(typically 6 weeks for billing; audit logs are governed by the "
        "log bucket retention rule). User data in BigQuery / GCS is "
        "deleted when the project is shut down, with a 30-day recoverable "
        "window."
    ),
    "azure": (
        "Azure permanently deletes customer data within 180 days of "
        "subscription cancellation or account closure, except where "
        "retention is required by law. Activity logs are retained for 90 "
        "days. Resources in deleted Resource Groups are moved to a soft-"
        "delete state for a configurable retention period (default 7-14 "
        "days for Key Vault, Backup, etc.) before permanent removal."
    ),
}


def _normalize(service: str) -> str:
    """Normalize a service identifier for lookup (lowercase, no whitespace)."""
    return service.lower().strip().replace("_", "-").replace(" ", "")


def get_policy_text(service: str) -> str:
    """Return the raw retention-policy text for ``service``.

    Raises ``ValueError`` if the service is unknown.
    """
    key = _normalize(service)
    if key == "z.ai":
        key = "zai"
    if key not in _POLICIES:
        raise ValueError(
            f"unknown service {service!r}; supported: {sorted(SUPPORTED_SERVICES)}"
        )
    return _POLICIES[key]


def get_deletion_policy(service: str) -> str:
    """Alias of :func:`get_policy_text` (kept for the public API name)."""
    return get_policy_text(service)


def build_deletion_notice(service: str) -> str:
    """Return a human-readable deletion notice for ``service``.

    Includes the provider's display name and the retention text. Suitable for
    printing to the CLI or returning in an HTTP response body.
    """
    key = _normalize(service)
    if key == "z.ai":
        key = "zai"
    if key not in _DISPLAY_NAME:
        raise ValueError(
            f"unknown service {service!r}; supported: {sorted(SUPPORTED_SERVICES)}"
        )
    name = _DISPLAY_NAME[key]
    return f"{name} data retention notice: " + _POLICIES[key]


def get_all_notices() -> Mapping[str, str]:
    """Return ``{service: notice}`` for every supported service."""
    return {svc: build_deletion_notice(svc) for svc in SUPPORTED_SERVICES}
