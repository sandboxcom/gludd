"""Generate least-privilege cloud roles from learned provider templates."""

from __future__ import annotations

from typing import Any

from general_ludd.cloud.role_pruning import (
    _aws_action_matches as _aws_action_matches,
)
from general_ludd.cloud.role_pruning import (
    _azure_action_matches as _azure_action_matches,
)
from general_ludd.cloud.role_pruning import (
    prune_by_resource_types as _prune_by_resource_types,
)

ROLE_TEMPLATES: dict[str, dict[str, dict[str, Any]]] = {
    "azure": {
        "terraform_deploy": {
            "Name": "custom-terraform-deploy",
            "Description": (
                "Least-privilege role for Terraform infrastructure deployments. "
                "Grants resource-group-scoped CRUD across compute, network, storage, "
                "ACR, App, OperationalInsights, and ManagedIdentity providers."
            ),
            "Actions": [
                "Microsoft.Resources/subscriptions/resourceGroups/*",
                "Microsoft.Compute/*",
                "Microsoft.Network/*",
                "Microsoft.Storage/*",
                "Microsoft.ContainerRegistry/*",
                "Microsoft.App/*",
                "Microsoft.OperationalInsights/*",
                "Microsoft.ManagedIdentity/*",
            ],
            "NotActions": [
                "Microsoft.Compute/virtualMachines/runCommand/action",
                "Microsoft.Authorization/roleAssignments/write",
                "Microsoft.Authorization/roleAssignments/delete",
                "Microsoft.Authorization/roleDefinitions/write",
                "Microsoft.Authorization/roleDefinitions/delete",
            ],
            "AssignableScopes": ["/subscriptions/{subscription_id}"],
            "DataActions": [],
            "NotDataActions": [],
        },
        "runtime_execution": {
            "Name": "custom-runtime-execution",
            "Description": (
                "Least-privilege role for container-app runtime execution. "
                "Grants read access to ACR images, managed identity read, and "
                "log-analytics query ability."
            ),
            "Actions": [
                "Microsoft.ContainerRegistry/registries/pull/read",
                "Microsoft.ManagedIdentity/userAssignedIdentities/read",
                "Microsoft.OperationalInsights/workspaces/query/action",
                "Microsoft.App/containerApps/*",
            ],
            "NotActions": [
                "Microsoft.Compute/virtualMachines/runCommand/action",
                "Microsoft.Authorization/roleAssignments/write",
            ],
            "AssignableScopes": ["/subscriptions/{subscription_id}"],
            "DataActions": [],
            "NotDataActions": [],
        },
        "model_inference": {
            "Name": "custom-model-inference",
            "Description": (
                "Least-privilege role for model-inference workloads.  Read-only "
                "access to storage blobs, ACR pull, and log-analytics query."
            ),
            "Actions": [
                "Microsoft.Storage/storageAccounts/blobServices/read",
                "Microsoft.ContainerRegistry/registries/pull/read",
                "Microsoft.OperationalInsights/workspaces/query/action",
            ],
            "NotActions": [
                "Microsoft.Compute/virtualMachines/runCommand/action",
                "Microsoft.Authorization/roleAssignments/write",
            ],
            "AssignableScopes": ["/subscriptions/{subscription_id}"],
            "DataActions": [],
            "NotDataActions": [],
        },
        "monitor": {
            "Name": "custom-monitor",
            "Description": (
                "Least-privilege role for monitoring and log-analysis. "
                "Read-only access to metrics, logs, alerts, and diagnostic settings."
            ),
            "Actions": [
                "Microsoft.OperationalInsights/workspaces/read",
                "Microsoft.OperationalInsights/workspaces/query/action",
                "Microsoft.Insights/metrics/read",
                "Microsoft.Insights/diagnosticSettings/read",
                "Microsoft.Insights/alertRules/read",
            ],
            "NotActions": [
                "Microsoft.Authorization/roleAssignments/write",
                "Microsoft.Authorization/roleAssignments/delete",
            ],
            "AssignableScopes": ["/subscriptions/{subscription_id}"],
            "DataActions": [],
            "NotDataActions": [],
        },
    },
    "aws": {
        "terraform_deploy": {
            "role_name": "terraform-deploy",
            "description": (
                "IAM role for Terraform infrastructure provisioning. Grants "
                "broad CRUD across EC2, ECS, ECR, VPC, IAM, S3, CloudWatch, "
                "Lambda, and Route53 with least-privilege constraints."
            ),
            "policy": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "ec2:*",
                        "ecs:*",
                        "ecr:*",
                        "vpc:*",
                        "elasticloadbalancing:*",
                        "s3:*",
                        "iam:GetRole",
                        "iam:GetPolicy",
                        "iam:GetInstanceProfile",
                        "iam:CreateRole",
                        "iam:CreatePolicy",
                        "iam:AttachRolePolicy",
                        "iam:ListRoles",
                        "logs:*",
                        "lambda:*",
                        "route53:*",
                        "cloudwatch:*",
                    ],
                    "Resource": "*",
                },
                {
                    "Effect": "Allow",
                    "Action": ["iam:PassRole"],
                    "Resource": ["arn:aws:iam::*:role/ecsTaskExecutionRole"],
                    "Condition": {"StringEquals": {"iam:PassedToService": "ecs-tasks.amazonaws.com"}},
                },
            ],
        },
        "runtime_execution": {
            "role_name": "runtime-execution",
            "description": (
                "Least-privilege IAM role for container-runtime execution. "
                "Grants ECR pull, S3 read, CloudWatch logs write, and "
                "explicitly denies all IAM and EC2 security-group mutations."
            ),
            "policy": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "ecr:GetAuthorizationToken",
                        "ecr:BatchCheckLayerAvailability",
                        "ecr:GetDownloadUrlForLayer",
                        "ecr:BatchGetImage",
                        "s3:GetObject",
                        "s3:ListBucket",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                    ],
                    "Resource": "*",
                },
                {
                    "Effect": "Deny",
                    "Action": [
                        "iam:*",
                        "ec2:AuthorizeSecurityGroupIngress",
                        "ec2:AuthorizeSecurityGroupEgress",
                        "ec2:CreateSecurityGroup",
                    ],
                    "Resource": "*",
                },
            ],
        },
        "model_inference": {
            "role_name": "model-inference",
            "description": (
                "Least-privilege IAM role for model-inference workloads. "
                "Read-only S3 + ECR access, explicitly denies IAM and EC2 mutations."
            ),
            "policy": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject",
                        "s3:ListBucket",
                        "ecr:GetAuthorizationToken",
                        "ecr:BatchCheckLayerAvailability",
                        "ecr:GetDownloadUrlForLayer",
                        "ecr:BatchGetImage",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                    ],
                    "Resource": "*",
                },
                {
                    "Effect": "Deny",
                    "Action": [
                        "iam:PassRole",
                        "ec2:RunInstances",
                        "ec2:CreateVolume",
                    ],
                    "Resource": "*",
                },
            ],
        },
        "monitor": {
            "role_name": "monitor",
            "description": (
                "Least-privilege IAM role for monitoring and observability. "
                "Read-only CloudWatch, logs, and X-Ray access."
            ),
            "policy": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "cloudwatch:GetMetricData",
                        "cloudwatch:GetMetricStatistics",
                        "cloudwatch:ListMetrics",
                        "cloudwatch:DescribeAlarms",
                        "logs:DescribeLogGroups",
                        "logs:DescribeLogStreams",
                        "logs:GetLogEvents",
                        "logs:FilterLogEvents",
                        "xray:GetTraceSummaries",
                        "xray:BatchGetTraces",
                    ],
                    "Resource": "*",
                },
                {
                    "Effect": "Deny",
                    "Action": ["iam:CreateUser", "iam:PassRole"],
                    "Resource": "*",
                },
            ],
        },
    },
    "gcp": {
        "terraform_deploy": {
            "role_name": "terraform-deploy",
            "description": (
                "Least-privilege custom IAM role for Terraform infrastructure "
                "deployments on GCP. Grants broad CRUD across compute, storage, "
                "networking, and IAM (service-account scoped)."
            ),
            "bindings": [
                {
                    "role": "roles/compute.admin",
                    "members": ["serviceAccount:terraform@{project_id}.iam.gserviceaccount.com"],
                },
                {
                    "role": "roles/storage.admin",
                    "members": ["serviceAccount:terraform@{project_id}.iam.gserviceaccount.com"],
                },
                {
                    "role": "roles/iam.serviceAccountAdmin",
                    "members": ["serviceAccount:terraform@{project_id}.iam.gserviceaccount.com"],
                    "condition": {
                        "title": "only_sa",
                        "expression": "resource.name.startsWith('projects/-/serviceAccounts/terraform-')",
                    },
                },
            ],
        },
        "runtime_execution": {
            "role_name": "runtime-execution",
            "description": (
                "Least-privilege custom IAM role for container-runtime execution "
                "on GCP. Grants artifact-registry read, storage object read, "
                "and logging write."
            ),
            "bindings": [
                {
                    "role": "roles/artifactregistry.reader",
                    "members": ["serviceAccount:runtime@{project_id}.iam.gserviceaccount.com"],
                },
                {
                    "role": "roles/storage.objectViewer",
                    "members": ["serviceAccount:runtime@{project_id}.iam.gserviceaccount.com"],
                },
                {
                    "role": "roles/logging.logWriter",
                    "members": ["serviceAccount:runtime@{project_id}.iam.gserviceaccount.com"],
                },
                {
                    "effect": "deny",
                    "role": "custom/deny-metadata",
                    "permissions": [
                        "compute.instances.setMetadata",
                        "compute.instances.setServiceAccount",
                        "iam.serviceAccounts.setIamPolicy",
                        "iam.serviceAccounts.actAs",
                    ],
                },
            ],
        },
        "model_inference": {
            "role_name": "model-inference",
            "description": (
                "Least-privilege custom IAM role for model-inference workloads "
                "on GCP. Read-only storage object access and logging write."
            ),
            "bindings": [
                {
                    "role": "roles/storage.objectViewer",
                    "members": ["serviceAccount:inference@{project_id}.iam.gserviceaccount.com"],
                },
                {
                    "role": "roles/logging.logWriter",
                    "members": ["serviceAccount:inference@{project_id}.iam.gserviceaccount.com"],
                },
                {
                    "effect": "deny",
                    "role": "custom/deny-metadata",
                    "permissions": [
                        "compute.instances.setMetadata",
                        "iam.serviceAccounts.actAs",
                    ],
                },
            ],
        },
        "monitor": {
            "role_name": "monitor",
            "description": (
                "Least-privilege custom IAM role for GCP monitoring. Read-only monitoring, logging, and trace access."
            ),
            "bindings": [
                {
                    "role": "roles/monitoring.viewer",
                    "members": ["serviceAccount:monitor@{project_id}.iam.gserviceaccount.com"],
                },
                {
                    "role": "roles/logging.viewer",
                    "members": ["serviceAccount:monitor@{project_id}.iam.gserviceaccount.com"],
                },
                {
                    "role": "roles/cloudtrace.user",
                    "members": ["serviceAccount:monitor@{project_id}.iam.gserviceaccount.com"],
                },
                {
                    "effect": "deny",
                    "role": "custom/deny-iam-mutations",
                    "permissions": [
                        "iam.serviceAccounts.create",
                        "iam.serviceAccountKeys.create",
                    ],
                },
            ],
        },
    },
}


def generate_role_from_template(provider: str, persona: str, resource_types: list[str] | None = None) -> dict[str, Any]:
    """Generate a cloud IAM role definition from a provider template.

    Returns a dict with ``status``, ``role_definition``, and ``warnings``.
    If *resource_types* is provided, unused actions are pruned (best-effort).
    """
    provider_templates = ROLE_TEMPLATES.get(provider)
    if provider_templates is None:
        return {
            "status": "error",
            "role_definition": {},
            "warnings": [f"Unknown provider {provider!r}. Supported: azure, aws, gcp"],
        }

    template = provider_templates.get(persona)
    if template is None:
        known = sorted(provider_templates.keys())
        return {
            "status": "error",
            "role_definition": {},
            "warnings": [f"Unknown persona {persona!r} for {provider}. Known: {known}"],
        }

    role_def = dict(template)

    if resource_types:
        role_def, pruned_warnings = _prune_by_resource_types(provider, role_def, resource_types)
    else:
        pruned_warnings = []

    return {
        "status": "ok",
        "role_definition": role_def,
        "warnings": pruned_warnings,
    }


__all__ = [
    "ROLE_TEMPLATES",
    "generate_role_from_template",
]
