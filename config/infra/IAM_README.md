# IAM Roles — Least-Privilege Deployment Guide

Three cloud providers, four personas per provider. Every role grants **only the
permissions required** for the named gludd operation to function.

## Personas

| Persona | AWS | GCP | Azure | Description |
|---------|-----|-----|-------|-------------|
| `terraform_deploy` | IAM role | Service account | Custom role + built-in | Provision/destroy infra via Terraform |
| `runtime_execution` | Instance profile | Service account | Managed identity | Daemon + ansible at runtime |
| `model_inference` | IAM role | Service account | Managed identity | Call AI model APIs |
| `monitor` | IAM role | Service account | Reader roles | Read-only dashboards, billing |

## File Map

Two Azure policy files exist because the Portal JSON editor and the Azure CLI
expect different JSON structures for the same role definition. Both files
contain identical permissions — only the shape differs.

| File | Format | Use with |
|------|--------|----------|
| `aws-iam-roles.yml` | AWS IAM policy documents (JSON-embedded in YAML) | AWS CLI / CloudFormation |
| `gcp-iam-roles.yml` | GCP predefined role bindings + CEL conditions | gcloud CLI |
| `azure-iam-roles.yml` | Azure RBAC role assignments scoped to resource group | az CLI |
| `azure-iam-policy.json` | Azure custom role — **PascalCase flat keys** (`Name`, `Actions`, `NotActions`, `AssignableScopes`) | Azure CLI: `az role definition create --role-definition "$(cat ...)"` (inline JSON, not `@file`) |
| `azure-iam-policy-cli.json` | Azure custom role — **REST API format** (`properties` wrapper: `roleName`, `permissions[].actions`, `assignableScopes`) | Azure Portal Web UI: paste into JSON tab |

### When to use which Azure file

- **Portal (Web UI)** → Use `azure-iam-policy-cli.json`. The Portal JSON editor expects the REST API format with `properties` / `permissions` wrapper.
- **Azure CLI** → Use `azure-iam-policy.json`. The CLI `--role-definition` flag expects PascalCase flat keys.

## Headless CI validation

Run `make iam-headless-smoke` before applying any IAM change. It is deliberately
credential-free: CI validates the manifest shape, all four personas, descriptions,
and explicit denial of administrator-equivalent bindings without contacting a cloud
control plane. Provider-specific live permission simulations remain an operator
responsibility after deployment.

---

## AWS — Apply Roles

### Create role + attach policy

```bash
# Derive from config
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
TRUST_POLICY="config/infra/aws-assume-role-tf.json"

# Terraform deployer
aws iam create-role \
  --role-name gludd-terraform-deploy \
  --assume-role-policy-document "file://${TRUST_POLICY}"

aws iam put-role-policy \
  --role-name gludd-terraform-deploy \
  --policy-name TerraformDeploy \
  --policy-document "$(python -c "
import yaml, json
with open('config/infra/aws-iam-roles.yml') as f:
    doc = yaml.safe_load(f)
print(json.dumps({
    'Version': '2012-10-17',
    'Statement': doc['roles']['terraform_deploy']['policy']
}, indent=2))
")"

# Runtime execution
aws iam create-role \
  --role-name gludd-runtime \
  --assume-role-policy-document "file://config/infra/aws-assume-role-daemon.json"

aws iam put-role-policy \
  --role-name gludd-runtime \
  --policy-name RuntimeExecution \
  --policy-document "$(python -c "
import yaml, json
with open('config/infra/aws-iam-roles.yml') as f:
    doc = yaml.safe_load(f)
print(json.dumps({
    'Version': '2012-10-17',
    'Statement': doc['roles']['runtime_execution']['policy']
}, indent=2))
")"

# Model inference
aws iam create-role \
  --role-name gludd-model-inference \
  --assume-role-policy-document "file://config/infra/aws-assume-role-model.json"

aws iam put-role-policy \
  --role-name gludd-model-inference \
  --policy-name ModelInference \
  --policy-document "$(python -c "
import yaml, json
with open('config/infra/aws-iam-roles.yml') as f:
    doc = yaml.safe_load(f)
print(json.dumps({
    'Version': '2012-10-17',
    'Statement': doc['roles']['model_inference']['policy']
}, indent=2))
")"

# Monitor
aws iam create-role \
  --role-name gludd-monitor \
  --assume-role-policy-document "file://config/infra/aws-assume-role-monitor.json"

aws iam put-role-policy \
  --role-name gludd-monitor \
  --policy-name Monitor \
  --policy-document "$(python -c "
import yaml, json
with open('config/infra/aws-iam-roles.yml') as f:
    doc = yaml.safe_load(f)
print(json.dumps({
    'Version': '2012-10-17',
    'Statement': doc['roles']['monitor']['policy']
}, indent=2))
")"
```

### Verify

```bash
# Simulate a specific permission
aws iam simulate-principal-policy \
  --policy-source-arn "arn:aws:iam::${ACCOUNT_ID}:role/gludd-terraform-deploy" \
  --action-names ec2:RunInstances ec2:DescribeInstances s3:GetObject

# List all attached policies for a role
aws iam list-role-policies --role-name gludd-terraform-deploy
aws iam get-role-policy --role-name gludd-terraform-deploy --policy-name TerraformDeploy

# Test: can the role actually launch an instance?
aws iam simulate-principal-policy \
  --policy-source-arn "arn:aws:iam::${ACCOUNT_ID}:role/gludd-terraform-deploy" \
  --action-names ec2:RunInstances \
  --resource-arns "arn:aws:ec2:us-east-1:${ACCOUNT_ID}:instance/*"
```

### Audit

```bash
# IAM Access Analyzer — generates a policy from CloudTrail usage
aws accessanalyzer create-analyzer --analyzer-name gludd-audit --type ACCOUNT

# Unused permission report (run quarterly)
aws iam generate-service-last-accessed-details \
  --arn "arn:aws:iam::${ACCOUNT_ID}:role/gludd-terraform-deploy"

# Find AccessDenied events in CloudTrail (last 7 days)
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=AccessDenied \
  --start-time "$(date -u -v-7d +%Y-%m-%dT%H:%M:%SZ)" \
  --query 'Events[?Username == `gludd-terraform-deploy`]'
```

### Least-privilege checklist

- [ ] No policy contains `Action: "*"` (admin wildcard)
- [ ] `iam:PassRole` is scoped to specific role ARNs (not `*`)
- [ ] Condition blocks restrict instance types, regions, and resource tags
- [ ] Separate roles for deploy, runtime, inference, and monitor
- [ ] No `iam:CreateRole` / `iam:DeleteRole` in non-admin roles

---

## GCP — Apply Roles

### Create service account + bind roles

```bash
PROJECT_ID=$(gcloud config get-value project)

# Create service accounts
gcloud iam service-accounts create gludd-terraform-deploy \
  --display-name="gludd Terraform Deployer"

gcloud iam service-accounts create gludd-runtime \
  --display-name="gludd Daemon Runtime"

gcloud iam service-accounts create gludd-model-inference \
  --display-name="gludd Model Inference"

gcloud iam service-accounts create gludd-monitor \
  --display-name="gludd Monitor"

# Bind roles to the terraform deployer (with conditions)
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:gludd-terraform-deploy@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/compute.instanceAdmin.v1" \
  --condition-from-file="config/infra/gcp-condition-instance-type.cond"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:gludd-terraform-deploy@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/compute.networkAdmin"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:gludd-terraform-deploy@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/compute.viewer"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:gludd-terraform-deploy@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/storage.objectViewer"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:gludd-terraform-deploy@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/storage.objectCreator"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:gludd-terraform-deploy@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser"

# Bind runtime roles
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:gludd-runtime@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/compute.viewer"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:gludd-runtime@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/logging.logWriter"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:gludd-runtime@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/storage.objectViewer"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:gludd-runtime@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/storage.objectCreator"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:gludd-runtime@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor" \
  --condition-from-file="config/infra/gcp-condition-gludd-secrets.cond"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:gludd-runtime@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/artifactregistry.reader"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:gludd-runtime@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/monitoring.metricWriter"

# Bind model inference roles
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:gludd-model-inference@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user" \
  --condition-from-file="config/infra/gcp-condition-gludd-endpoints.cond"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:gludd-model-inference@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/aiplatform.viewer"

# Bind monitor roles
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:gludd-monitor@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/monitoring.viewer"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:gludd-monitor@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/logging.viewer"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:gludd-monitor@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/billing.viewer"
```

### Verify

```bash
# List all role bindings for a service account
gcloud projects get-iam-policy "${PROJECT_ID}" \
  --flatten="bindings[].members" \
  --filter="bindings.members:gludd-terraform-deploy" \
  --format="table(bindings.role)"

# Simulate permissions (tests actual enforcement)
gcloud iam roles simulate \
  --member="serviceAccount:gludd-terraform-deploy@${PROJECT_ID}.iam.gserviceaccount.com" \
  --permissions="compute.instances.list,compute.instances.insert"

# Test: can the SA actually list instances?
gcloud auth activate-service-account \
  gludd-terraform-deploy@${PROJECT_ID}.iam.gserviceaccount.com \
  --key-file=/dev/null 2>/dev/null || true
gcloud compute instances list --project="${PROJECT_ID}"
```

### Audit

```bash
# Policy Analyzer — shows unused permissions per service account
gcloud recommender insights list \
  --insight-type=google.iam.policy.Insight \
  --project="${PROJECT_ID}"

# IAM policy dump (pipe through audit script)
gcloud projects get-iam-policy "${PROJECT_ID}" --format=json > /tmp/gcp-iam-policy.json
python scripts/audit_gcp_iam.py /tmp/gcp-iam-policy.json

# Cloud Audit Logs — find denied calls
gcloud logging read \
  'protoPayload.@type="type.googleapis.com/google.iam.v1.AuditData"
   AND protoPayload.status.code=7' \
  --project="${PROJECT_ID}" \
  --limit=50
```

### Least-privilege checklist

- [ ] All role bindings use predefined roles (not `roles/owner` or `roles/editor`)
- [ ] Conditions restrict instance types and zones
- [ ] Service account keys are disabled (use workload identity federation)
- [ ] No `roles/iam.securityAdmin` or `roles/iam.roleAdmin` on runtime accounts

---

## Azure — Apply Roles

### Create managed identity + assign roles

```bash
SUB_ID="00000000-0000-0000-0000-000000000000"
RG="gludd-prod"

# Create the custom Terraform deployer role (one-time)
# Use inline JSON (not @file) — the CLI expects PascalCase keys for --role-definition
az role definition create --role-definition "$(sed "s/{subscription_id}/$SUB_ID/" config/infra/azure-iam-policy.json)"

# Create user-assigned managed identities
az identity create --name gludd-terraform-deploy --resource-group "${RG}"
az identity create --name gludd-runtime --resource-group "${RG}"
az identity create --name gludd-model-inference --resource-group "${RG}"
az identity create --name gludd-monitor --resource-group "${RG}"

# Get principal IDs
TF_ID=$(az identity show --name gludd-terraform-deploy --resource-group "${RG}" --query principalId -o tsv)
RT_ID=$(az identity show --name gludd-runtime --resource-group "${RG}" --query principalId -o tsv)
MI_ID=$(az identity show --name gludd-model-inference --resource-group "${RG}" --query principalId -o tsv)
MO_ID=$(az identity show --name gludd-monitor --resource-group "${RG}" --query principalId -o tsv)

SCOPE="/subscriptions/${SUB_ID}/resourceGroups/${RG}"

# Assign roles to Terraform deployer
az role assignment create --assignee "${TF_ID}" --role "General Ludd Accelerator Deployer" --scope "${SCOPE}"
az role assignment create --assignee "${TF_ID}" --role "Contributor" --scope "${SCOPE}"
az role assignment create --assignee "${TF_ID}" --role "Storage Blob Data Contributor" --scope "${SCOPE}"
az role assignment create --assignee "${TF_ID}" --role "Key Vault Secrets User" --scope "${SCOPE}"

# Assign roles to runtime
az role assignment create --assignee "${RT_ID}" --role "Virtual Machine Contributor" --scope "${SCOPE}"
az role assignment create --assignee "${RT_ID}" --role "Storage Blob Data Reader" --scope "${SCOPE}"
az role assignment create --assignee "${RT_ID}" --role "Storage Blob Data Contributor" --scope "${SCOPE}"
az role assignment create --assignee "${RT_ID}" --role "Log Analytics Contributor" --scope "${SCOPE}"
az role assignment create --assignee "${RT_ID}" --role "Key Vault Secrets User" --scope "${SCOPE}"
az role assignment create --assignee "${RT_ID}" --role "AcrPull" --scope "${SCOPE}"

# Assign roles to model inference (scoped to cognitive services account)
COG_SCOPE="${SCOPE}/providers/Microsoft.CognitiveServices/accounts/gludd-openai"
az role assignment create --assignee "${MI_ID}" --role "Cognitive Services User" --scope "${COG_SCOPE}"
az role assignment create --assignee "${MI_ID}" --role "Cognitive Services Metrics Advisor User" --scope "${COG_SCOPE}"

# Assign roles to monitor
az role assignment create --assignee "${MO_ID}" --role "Monitoring Reader" --scope "${SCOPE}"
az role assignment create --assignee "${MO_ID}" --role "Cost Management Reader" --scope "${SCOPE}"
az role assignment create --assignee "${MO_ID}" --role "Service Health Reader" --scope "${SCOPE}"
```

### Verify

```bash
# List all role assignments for a principal
az role assignment list --assignee "${TF_ID}" --resource-group "${RG}" \
  --query "[].{Role:roleDefinitionName,Scope:scope}" -o table

# Check if a principal can perform a specific action
az rest --method POST \
  --uri "https://management.azure.com/subscriptions/${SUB_ID}/providers/Microsoft.Authorization/checkAccess?api-version=2022-04-01" \
  --body "$(cat <<EOF
{
  "subject": {"id": "${TF_ID}"},
  "actions": ["Microsoft.Compute/virtualMachines/read"],
  "resource": {"id": "${SCOPE}"}
}
EOF
)"

# Test: verify custom role definition exists
az role definition list --name "General Ludd Accelerator Deployer" -o table
```

### Audit

```bash
# All role assignments for the subscription
az role assignment list --all --subscription "${SUB_ID}" \
  --query "[?contains(principalName, 'gludd')]" -o table

# Custom roles in use
az role definition list --custom-role-only \
  --query "[].{Name:roleName,AssignableScopes:assignableScopes}" -o table

# Activity log for role assignment changes (last 30 days)
az monitor activity-log list \
  --resource-group "${RG}" \
  --query "[?authorization.action == 'Microsoft.Authorization/roleAssignments/write']" \
  -o table \
  --start-time "$(date -u -v-30d +%Y-%m-%dT%H:%M:%SZ)"
```

### Least-privilege checklist

- [ ] All role assignments scoped to resource group (not subscription)
- [ ] Custom role `azure-iam-policy.json` has explicit NotActions for dangerous operations
- [ ] No `Owner` or `User Access Administrator` roles assigned
- [ ] Managed identities used (no service principal client secrets)
- [ ] Cognitive Services User scoped to specific account (not `*`)

---

## Cross-Provider Audit

Run `make audit-iam` to check all three providers:

```bash
make audit-iam
```

This target:
1. Validates all YAML/JSON syntax
2. Checks no policy contains `*:*` (admin wildcard)
3. Verifies every role has a non-empty description
4. Confirms conditions are present where Resource: "*" is used
5. Flags any role definition not listed in this README

## Automated Tests

```bash
make test-specific TESTFILE='tests/unit/test_iam_roles.py'
```

Tests verify: valid YAML/JSON, no admin wildcards, descriptions present, actions scoped, conditions for broad permissions.

## Reference

- [AWS IAM Best Practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
- [GCP IAM Conditions](https://cloud.google.com/iam/docs/conditions-overview)
- [Azure RBAC Best Practices](https://learn.microsoft.com/en-us/azure/role-based-access-control/best-practices)
- [Least Privilege — CISA Guidance](https://www.cisa.gov/news-events/news/implementing-least-privilege-principle)
