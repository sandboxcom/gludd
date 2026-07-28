package hottentot.iam

import future.keywords.if

# ============================================================================
# AWS tests (9)
# ============================================================================

test_aws_valid_policy_passes if {
    aws_least_privilege_valid with input as {
        "aws_policy": {"statements": [
            {"sid": "Ec2Describe", "effect": "Allow", "action": ["ec2:DescribeInstances"], "resource": ["arn:aws:ec2:*:*:*"]},
            {"sid": "Ec2Run", "effect": "Allow", "action": ["ec2:RunInstances"], "resource": ["arn:aws:ec2:*:*:instance/*"]},
        ]}
    }
}

test_aws_wildcard_action_denied if {
    violation := deny_aws_wildcard_action with input as {
        "aws_policy": {"statements": [
            {"sid": "BadStatement", "effect": "Allow", "action": ["ec2:*"], "resource": ["arn:aws:ec2:*:*:*"]},
        ]}
    }
    count(violation) == 1
}

test_aws_wildcard_resource_denied if {
    violation := deny_aws_wildcard_resource with input as {
        "aws_policy": {"statements": [
            {"sid": "BadResource", "effect": "Allow", "action": ["ec2:RunInstances"], "resource": ["*"]},
        ]}
    }
    count(violation) == 1
}

test_aws_describe_wildcard_resource_required if {
    aws_least_privilege_valid with input as {
        "aws_policy": {"statements": [
            {
                "sid": "Ec2Describe",
                "effect": "Allow",
                "action": ["ec2:DescribeInstances", "ec2:DescribeVolumes"],
                "resource": ["*"],
            },
        ]}
    }
}

test_aws_mixed_wildcard_resource_denied if {
    violation := deny_aws_wildcard_resource with input as {
        "aws_policy": {"statements": [
            {
                "sid": "MixedActions",
                "effect": "Allow",
                "action": ["ec2:DescribeInstances", "ec2:RunInstances"],
                "resource": ["*"],
            },
        ]}
    }
    count(violation) == 1
}

test_aws_iam_escalation_denied if {
    violation := deny_aws_iam_escalation with input as {
        "aws_policy": {"statements": [
            {"sid": "Escalation", "effect": "Allow", "action": ["iam:CreateUser"], "resource": ["*"]},
        ]}
    }
    count(violation) == 1
}

test_aws_multiple_violations_invalidates if {
    not aws_least_privilege_valid with input as {
        "aws_policy": {"statements": [
            {"sid": "S1", "effect": "Allow", "action": ["ec2:*"], "resource": ["*"]},
            {"sid": "S2", "effect": "Allow", "action": ["iam:CreateRole"], "resource": ["*"]},
        ]}
    }
}

test_aws_deny_statement_ignored if {
    aws_least_privilege_valid with input as {
        "aws_policy": {"statements": [
            {"sid": "DenyEscalation", "effect": "Deny", "action": ["iam:*"], "resource": ["*"]},
            {"sid": "AllowEc2", "effect": "Allow", "action": ["ec2:RunInstances"], "resource": ["arn:aws:ec2:*:*:*"]},
        ]}
    }
}

test_aws_allow_must_not_contain_escalation if {
    violation := deny_aws_iam_escalation with input as {
        "aws_policy": {"statements": [
            {"sid": "PassRole", "effect": "Allow", "action": ["iam:AttachRolePolicy", "iam:PassRole"], "resource": ["arn:aws:iam::*:role/gludd-*"]},
        ]}
    }
    count(violation) == 1
}

# ============================================================================
# Azure tests (8)
# ============================================================================

test_azure_scoped_roles_pass if {
    azure_least_privilege_valid with input as {
        "azure_role_assignments": [
            {
                "role_definition_name": "General Ludd Accelerator Deployer",
                "scope": "/subscriptions/sub-123",
            },
        ]
    }
}

test_azure_accelerator_role_subscription_scope_passes if {
    azure_least_privilege_valid with input as {
        "azure_role_assignments": [{
            "role_definition_name": "General Ludd Accelerator Deployer",
            "scope": "/subscriptions/sub-123",
        }]
    }
}

test_azure_missing_scope_denied if {
    violation := deny_azure_missing_scope with input as {
        "azure_role_assignments": [{
            "role_definition_name": "General Ludd Accelerator Deployer",
        }]
    }
    count(violation) == 1
}

test_azure_contributor_denied if {
    violation := deny_azure_forbidden_role with input as {
        "azure_role_assignments": [
            {"role_definition_name": "Virtual Machine Contributor"},
            {"role_definition_name": "Contributor"},
        ]
    }
    count(violation) == 1
}

test_azure_owner_denied if {
    violation := deny_azure_forbidden_role with input as {
        "azure_role_assignments": [
            {"role_definition_name": "Owner"},
        ]
    }
    count(violation) == 1
}

test_azure_user_access_admin_denied if {
    violation := deny_azure_forbidden_role with input as {
        "azure_role_assignments": [
            {"role_definition_name": "User Access Administrator"},
        ]
    }
    count(violation) == 1
}

test_azure_multiple_forbidden_detected if {
    violation := deny_azure_forbidden_role with input as {
        "azure_role_assignments": [
            {"role_definition_name": "Contributor"},
            {"role_definition_name": "Owner"},
        ]
    }
    count(violation) == 2
}

test_azure_empty_assignments_pass if {
    azure_least_privilege_valid with input as {
        "azure_role_assignments": []
    }
}

# ============================================================================
# GCP tests (8)
# ============================================================================

test_gcp_scoped_roles_pass if {
    gcp_least_privilege_valid with input as {
        "gcp_role_bindings": [
            {"role": "roles/compute.instanceAdmin.v1"},
            {"role": "roles/compute.securityAdmin"},
            {"role": "roles/iam.serviceAccountUser"},
            {"role": "roles/logging.logWriter"},
        ]
    }
}

test_gcp_owner_role_denied if {
    violation := deny_gcp_forbidden_role with input as {
        "gcp_role_bindings": [
            {"role": "roles/owner"},
        ]
    }
    count(violation) == 1
}

test_gcp_editor_role_denied if {
    violation := deny_gcp_forbidden_role with input as {
        "gcp_role_bindings": [
            {"role": "roles/editor"},
        ]
    }
    count(violation) == 1
}

test_gcp_wildcard_permission_denied if {
    violation := deny_gcp_wildcard_permission with input as {
        "gcp_role_bindings": [
            {"role": "roles/custom.gludd", "permissions": ["compute.*"]},
        ]
    }
    count(violation) == 1
}

test_gcp_bound_role_no_permissions_passes if {
    gcp_least_privilege_valid with input as {
        "gcp_role_bindings": [
            {"role": "roles/compute.instanceAdmin.v1"},
        ]
    }
}

test_gcp_wildcard_partial_denied if {
    violation := deny_gcp_wildcard_permission with input as {
        "gcp_role_bindings": [
            {"role": "roles/custom.gludd", "permissions": ["compute.instances.*"]},
        ]
    }
    count(violation) == 1
}

test_gcp_no_permissions_field_passes if {
    gcp_least_privilege_valid with input as {
        "gcp_role_bindings": [
            {"role": "roles/compute.instanceAdmin.v1"},
        ]
    }
}

test_gcp_empty_permissions_passes if {
    gcp_least_privilege_valid with input as {
        "gcp_role_bindings": [
            {"role": "roles/custom.gludd", "permissions": []},
        ]
    }
}

# ============================================================================
# Cross-cloud aggregate tests (4)
# ============================================================================

test_all_clouds_valid_passes if {
    all_clouds_least_privilege_valid with input as {
        "aws_policy": {"statements": [
            {"sid": "Ec2Run", "effect": "Allow", "action": ["ec2:RunInstances"], "resource": ["arn:aws:ec2:*:*:*"]},
        ]},
        "azure_role_assignments": [
            {
                "role_definition_name": "General Ludd Accelerator Deployer",
                "scope": "/subscriptions/sub-123",
            },
        ],
        "gcp_role_bindings": [
            {"role": "roles/compute.instanceAdmin.v1"},
        ],
    }
}

test_all_clouds_fails_when_aws_invalid if {
    not all_clouds_least_privilege_valid with input as {
        "aws_policy": {"statements": [
            {"sid": "Bad", "effect": "Allow", "action": ["ec2:*"], "resource": ["*"]},
        ]},
        "azure_role_assignments": [],
        "gcp_role_bindings": [],
    }
}

test_all_clouds_fails_when_azure_invalid if {
    not all_clouds_least_privilege_valid with input as {
        "aws_policy": {"statements": [
            {"sid": "Ec2Run", "effect": "Allow", "action": ["ec2:RunInstances"], "resource": ["arn:aws:ec2:*:*:*"]},
        ]},
        "azure_role_assignments": [
            {"role_definition_name": "Owner"},
        ],
        "gcp_role_bindings": [],
    }
}

test_all_clouds_fails_when_gcp_invalid if {
    not all_clouds_least_privilege_valid with input as {
        "aws_policy": {"statements": [
            {"sid": "Ec2Run", "effect": "Allow", "action": ["ec2:RunInstances"], "resource": ["arn:aws:ec2:*:*:*"]},
        ]},
        "azure_role_assignments": [],
        "gcp_role_bindings": [
            {"role": "roles/owner"},
        ],
    }
}
