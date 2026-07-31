"""Cloud IAM expert package — unified role generation and validation across
Azure, AWS, and GCP.

Exports the public API: generate_cloud_role, validate_cloud_role,
CROSS_PROVIDER_PATTERNS, dataclass contracts, and provider-specific
validators.
"""

from __future__ import annotations

from general_ludd.cloud.aws_validator import AWS_REQUIRED_DENIALS, validate_aws_role
from general_ludd.cloud.azure_validator import (
    azure_generate_portal_json,
    generate_role_definition,
    validate_action_string,
    validate_against_azure_schema,
)
from general_ludd.cloud.contracts import (
    CloudFunction,
    CloudRoleDefinition,
    PersonaRoleMap,
    ValidationResult,
)
from general_ludd.cloud.core import (
    CROSS_PROVIDER_PATTERNS,
    generate_cloud_role,
    validate_cloud_role,
)
from general_ludd.cloud.gcp_validator import GCP_REQUIRED_DENIALS, validate_gcp_role
from general_ludd.cloud.role_generator import (
    ROLE_TEMPLATES,
    generate_role_from_template,
)

__all__ = [
    "AWS_REQUIRED_DENIALS",
    "CROSS_PROVIDER_PATTERNS",
    "GCP_REQUIRED_DENIALS",
    "ROLE_TEMPLATES",
    "CloudFunction",
    "CloudRoleDefinition",
    "PersonaRoleMap",
    "ValidationResult",
    "azure_generate_portal_json",
    "generate_cloud_role",
    "generate_role_definition",
    "generate_role_from_template",
    "validate_action_string",
    "validate_against_azure_schema",
    "validate_aws_role",
    "validate_cloud_role",
    "validate_gcp_role",
]
