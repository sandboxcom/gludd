# AWS Least-Privilege Access Guide for gludd

This guide defines the **minimal** AWS IAM permissions, credential delivery, and
environment configuration gludd needs for every AWS facility it connects to (or
is designed to connect to).

It is split into two IAM roles you keep **separate**:

| Role | Purpose | Grant to |
| --- | --- | --- |
| `gludd-observability-readonly` | Read logs, metrics, traces, and CI status. No mutations. | The agent runtime / any process running `AwsPipelineSource` and future read connectors. |
| `gludd-compute-deploy` | Provision and run model servers (vLLM / llama.cpp) on EC2/ECS/EKS. | A separate deploy identity only. Never attach to the read-only agent runtime. |

## What the code actually consumes today vs. what is prospective

Grep of `src/general_ludd/connectors/` shows exactly **one** AWS connector wired in:

- **`src/general_ludd/connectors/aws_pipeline.py`** — class `AwsPipelineSource`.
  It talks to two AWS services via boto3:
  - **AWS CodePipeline** — `list_pipeline_executions(pipelineName=...)`
    (method `AwsPipelineSource.query`).
  - **CloudWatch Logs** — `filter_log_events(logGroupName=..., startTime=...)`
    (method `AwsPipelineSource.fetch_logs`).

  boto3 is an **optional** dependency (guarded import at `aws_pipeline.py:124`).
  When absent, `health()` reports `detail: "boto3 unavailable"`.

- **No `*_env` secret field exists in this connector.** It does **not** read AWS
  credentials from config or from any `*_env` key. The default client factory
  (`_default_client_factory`, `aws_pipeline.py:116-127`) calls
  `boto3.client(service, region_name=self.region or None)` and relies entirely on
  boto3's **standard credential chain** (env vars → instance profile / IRSA →
  `~/.aws/credentials` → STS). The only AWS-specific input read from config is
  `region`.

- **CloudWatch Metrics, X-Ray (traces), CodeBuild, and EC2/ECS/EKS compute
  deploy are NOT implemented in code yet.** No `cloudwatch.py` (metrics),
  `xray.py`, `codebuild.py`, or compute-deploy module exists. They are included
  below as **prospective** least-privilege policy so the IAM scaffolding is ready
  before those connectors land. Each prospective facility is marked
  **(PROSPECTIVE)**. Model serving (vLLM / llama.cpp) is currently provider-
  agnostic via the LangChain gateway (`src/general_ludd/models/gateway.py`) with
  `local-inference` optional deps — there is no AWS compute-deploy wiring today.

> Principle: grant a permission only when a connector that uses it ships. The
> prospective statements below are kept in their own policy / statement so you
> can attach them incrementally.

---

## Facility 1 — CloudWatch Logs (read)

**Used by:** `AwsPipelineSource.fetch_logs` → `logs.filter_log_events`. (Live.)

**Minimal IAM actions:**

| Action | Why |
| --- | --- |
| `logs:FilterLogEvents` | The exact call gludd makes (`fetch_logs`). |
| `logs:GetLogEvents` | Single-stream reads (future / debugging). |
| `logs:DescribeLogGroups` | Discover/validate log group names. |
| `logs:DescribeLogStreams` | Enumerate streams within a group. |

Read-only. No `logs:PutLogEvents`, no `logs:CreateLogGroup`, no deletes.

---

## Facility 2 — CloudWatch Metrics (read) — (PROSPECTIVE)

**Used by:** a future metric source. Not yet in code.

**Minimal IAM actions:**

| Action | Why |
| --- | --- |
| `cloudwatch:GetMetricData` | Bulk metric reads (preferred API). |
| `cloudwatch:GetMetricStatistics` | Legacy per-metric reads. |
| `cloudwatch:ListMetrics` | Discover available metrics. |
| `cloudwatch:DescribeAlarms` | Read alarm state (optional). |

Read-only. No `cloudwatch:PutMetricData`, no `PutMetricAlarm`.

---

## Facility 3 — X-Ray / traces (read) — (PROSPECTIVE)

**Used by:** a future trace source. Not yet in code.

**Minimal IAM actions:**

| Action | Why |
| --- | --- |
| `xray:GetTraceSummaries` | List traces in a time window. |
| `xray:BatchGetTraces` | Fetch full trace segment documents. |
| `xray:GetServiceGraph` | Service-map topology (optional). |
| `xray:GetTraceGraph` | Per-trace graph (optional). |

Read-only. No `xray:PutTraceSegments`, no `PutTelemetryRecords`.

---

## Facility 4 — CodePipeline / CodeBuild (CI, read)

**Used by:** `AwsPipelineSource.query` → `codepipeline.list_pipeline_executions`
(CodePipeline is live). CodeBuild is **(PROSPECTIVE)** — no `codebuild.py` yet.

**Minimal IAM actions:**

| Action | Why |
| --- | --- |
| `codepipeline:ListPipelineExecutions` | The exact call gludd makes (`query`). |
| `codepipeline:GetPipelineExecution` | Detail for a single execution. |
| `codepipeline:GetPipelineState` | Current stage/action state. |
| `codepipeline:ListPipelines` | Discover pipeline names. |
| `codebuild:BatchGetBuilds` *(prospective)* | Build detail by id. |
| `codebuild:ListBuilds` *(prospective)* | List recent builds. |
| `codebuild:ListBuildsForProject` *(prospective)* | Builds for one project. |
| `codebuild:BatchGetProjects` *(prospective)* | Project config detail. |

Read-only. No `StartPipelineExecution`, no `StartBuild`, no mutations.

---

## Facility 5 — Compute deploy: EC2 / ECS / EKS (vLLM / llama.cpp serving) — (PROSPECTIVE)

**Used by:** a future deploy path that provisions model servers. Not in code today
(model serving is provider-agnostic via the LangChain gateway). Kept in its own
**separate** role, `gludd-compute-deploy`.

**Minimal IAM actions (pick the subset for your launch target):**

EC2 (self-managed instances):

| Action | Why |
| --- | --- |
| `ec2:RunInstances` | Launch a serving instance. |
| `ec2:TerminateInstances` | Tear it down. |
| `ec2:DescribeInstances` / `ec2:DescribeInstanceStatus` | Poll readiness. |
| `ec2:CreateTags` | Tag for ownership/cost. |
| `ec2:DescribeImages` / `ec2:DescribeSubnets` / `ec2:DescribeSecurityGroups` | Resolve launch inputs. |

ECS (Fargate / EC2 tasks):

| Action | Why |
| --- | --- |
| `ecs:RegisterTaskDefinition` | Define the serving container. |
| `ecs:RunTask` / `ecs:StartTask` | Start serving tasks. |
| `ecs:StopTask` | Stop them. |
| `ecs:CreateService` / `ecs:UpdateService` / `ecs:DeleteService` | Long-running serving service. |
| `ecs:DescribeTasks` / `ecs:DescribeServices` / `ecs:ListTasks` | Poll status. |

EKS (Kubernetes-hosted):

| Action | Why |
| --- | --- |
| `eks:DescribeCluster` | Resolve cluster endpoint/CA for kubeconfig. |
| `eks:ListClusters` | Discover clusters. |
| `eks:AccessKubernetesApi` *(if using EKS access entries)* | API access. |

**Required for all three:** `iam:PassRole`, scoped to **only** the task/instance
execution role ARN the serving workload assumes — never `Resource: "*"`. This is
the single most-abused deploy permission; keep it pinned.

No `iam:CreateRole`, no `iam:AttachRolePolicy`, no `*:*` admin.

---

## Ready-to-paste IAM policy JSON

### Policy A — `gludd-observability-readonly`

Covers Facilities 1-4 (read-only). Narrow each `Resource` from `"*"` to your real
ARNs where indicated by the comment under each `Sid`.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CloudWatchLogsRead",
      "Effect": "Allow",
      "Action": [
        "logs:FilterLogEvents",
        "logs:GetLogEvents",
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams"
      ],
      "Resource": "*"
    },
    {
      "Sid": "CloudWatchMetricsRead",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:GetMetricData",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:ListMetrics",
        "cloudwatch:DescribeAlarms"
      ],
      "Resource": "*"
    },
    {
      "Sid": "XRayTracesRead",
      "Effect": "Allow",
      "Action": [
        "xray:GetTraceSummaries",
        "xray:BatchGetTraces",
        "xray:GetServiceGraph",
        "xray:GetTraceGraph"
      ],
      "Resource": "*"
    },
    {
      "Sid": "CodePipelineRead",
      "Effect": "Allow",
      "Action": [
        "codepipeline:ListPipelineExecutions",
        "codepipeline:GetPipelineExecution",
        "codepipeline:GetPipelineState",
        "codepipeline:ListPipelines"
      ],
      "Resource": "*"
    },
    {
      "Sid": "CodeBuildRead",
      "Effect": "Allow",
      "Action": [
        "codebuild:BatchGetBuilds",
        "codebuild:ListBuilds",
        "codebuild:ListBuildsForProject",
        "codebuild:BatchGetProjects"
      ],
      "Resource": "*"
    }
  ]
}
```

**Where to narrow the ARNs:**

- `CloudWatchLogsRead` → scope to your log groups:
  `arn:aws:logs:<region>:<acct>:log-group:<your-group>:*`.
  `logs:DescribeLogGroups` requires `*`; split it into its own statement if you
  want the rest scoped.
- `CloudWatchMetricsRead` → CloudWatch metric `Get*`/`ListMetrics` do not support
  resource-level ARNs; constrain by a `cloudwatch:namespace` condition key if
  needed.
- `XRayTracesRead` → X-Ray read actions are account/region-wide; leave `*` but
  pin the policy to a single region via an `aws:RequestedRegion` condition.
- `CodePipelineRead` → scope to specific pipeline ARNs:
  `arn:aws:codepipeline:<region>:<acct>:<pipeline-name>`. `ListPipelines`
  requires `*`; split it out to scope the rest.
- `CodeBuildRead` → scope to project ARNs:
  `arn:aws:codebuild:<region>:<acct>:project/<project-name>`.

> The metrics, X-Ray, and CodeBuild statements are for **prospective** connectors.
> If you want to grant only what ships today, keep only `CloudWatchLogsRead` and
> `CodePipelineRead`.

Optional region-pinning condition you can add to any statement:

```json
"Condition": { "StringEquals": { "aws:RequestedRegion": "us-east-1" } }
```

### Policy B — `gludd-compute-deploy`

Covers Facility 5. **(PROSPECTIVE)** — attach only when the deploy path ships,
and only to a dedicated deploy identity.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "Ec2ServerLifecycle",
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances",
        "ec2:TerminateInstances",
        "ec2:DescribeInstances",
        "ec2:DescribeInstanceStatus",
        "ec2:CreateTags",
        "ec2:DescribeImages",
        "ec2:DescribeSubnets",
        "ec2:DescribeSecurityGroups"
      ],
      "Resource": "*"
    },
    {
      "Sid": "EcsServerLifecycle",
      "Effect": "Allow",
      "Action": [
        "ecs:RegisterTaskDefinition",
        "ecs:RunTask",
        "ecs:StartTask",
        "ecs:StopTask",
        "ecs:CreateService",
        "ecs:UpdateService",
        "ecs:DeleteService",
        "ecs:DescribeTasks",
        "ecs:DescribeServices",
        "ecs:ListTasks"
      ],
      "Resource": "*"
    },
    {
      "Sid": "EksClusterAccess",
      "Effect": "Allow",
      "Action": [
        "eks:DescribeCluster",
        "eks:ListClusters",
        "eks:AccessKubernetesApi"
      ],
      "Resource": "*"
    },
    {
      "Sid": "PassExecutionRoleOnly",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::<acct>:role/gludd-model-server-exec-role",
      "Condition": {
        "StringEquals": {
          "iam:PassedToService": [
            "ec2.amazonaws.com",
            "ecs-tasks.amazonaws.com"
          ]
        }
      }
    }
  ]
}
```

**Where to narrow the ARNs:**

- `Ec2ServerLifecycle` → scope `RunInstances`/`TerminateInstances` with tag
  conditions, e.g. `"Condition": {"StringEquals": {"aws:ResourceTag/owner":
  "gludd"}}`, and pin `Resource` to specific subnet/SG/AMI ARNs.
- `EcsServerLifecycle` → scope `Resource` to your cluster/service/task-definition
  ARNs: `arn:aws:ecs:<region>:<acct>:cluster/<cluster>` etc.
- `EksClusterAccess` → scope to a single cluster ARN:
  `arn:aws:eks:<region>:<acct>:cluster/<cluster-name>`.
- `PassExecutionRoleOnly` → **must** name exactly the one execution role the
  serving workload assumes. Replace `<acct>` and the role name. Never `*`.

---

## Where / how to apply

### Console (IAM > Policies)

1. **IAM → Policies → Create policy → JSON tab.** Paste Policy A. Replace
   placeholder ARNs. **Next**, name it `gludd-observability-readonly`,
   **Create policy**.
2. Repeat for Policy B → name `gludd-compute-deploy`.
3. **IAM → Roles → Create role.** Choose the trusted entity (see credential
   delivery below — typically *Web identity* for IRSA, or *AWS service → EC2*).
4. Attach `gludd-observability-readonly` to the **agent runtime role** and
   `gludd-compute-deploy` to a **separate deploy role**. Do not combine.

### CLI

```bash
# Create the two managed policies
aws iam create-policy \
  --policy-name gludd-observability-readonly \
  --policy-document file://gludd-observability-readonly.json

aws iam create-policy \
  --policy-name gludd-compute-deploy \
  --policy-document file://gludd-compute-deploy.json

# Create the read-only agent role (example: EC2 trust; swap trust policy for IRSA)
aws iam create-role \
  --role-name gludd-agent-readonly \
  --assume-role-policy-document file://trust-ec2.json

# Attach the read-only policy to the agent role
aws iam attach-role-policy \
  --role-name gludd-agent-readonly \
  --policy-arn arn:aws:iam::<acct>:policy/gludd-observability-readonly

# Separate deploy role gets the deploy policy
aws iam create-role \
  --role-name gludd-deployer \
  --assume-role-policy-document file://trust-deploy.json
aws iam attach-role-policy \
  --role-name gludd-deployer \
  --policy-arn arn:aws:iam::<acct>:policy/gludd-compute-deploy
```

Example `trust-ec2.json` (EC2 instance-profile trust):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "ec2.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
```

---

## Credential delivery — three options, ranked

gludd's connector reads **no** credentials itself — `AwsPipelineSource` uses
boto3's standard credential chain. So you only need to make credentials
discoverable by boto3 in the runtime environment. Ranked best-to-last:

### (a) IRSA / EKS OIDC — **preferred**

Use when gludd runs as a pod on **EKS**. Bind a Kubernetes ServiceAccount to the
IAM role via the cluster's OIDC provider; boto3 picks up the projected token
automatically (no long-lived secret on disk, auto-rotated).

```bash
aws eks describe-cluster --name <cluster> \
  --query "cluster.identity.oidc.issuer" --output text
# Create role with web-identity trust to that OIDC issuer + SA condition, then:
kubectl annotate serviceaccount gludd-agent \
  eks.amazonaws.com/role-arn=arn:aws:iam::<acct>:role/gludd-agent-readonly
```

### (b) EC2 / ECS instance (or task) profile — **good**

Use when gludd runs on a plain **EC2 instance** or **ECS task** outside EKS.
Attach the role via an instance profile (EC2) or task role (ECS); boto3 reads
the instance/container metadata service. No secret material handled by gludd.

```bash
aws iam create-instance-profile --instance-profile-name gludd-agent-readonly
aws iam add-role-to-instance-profile \
  --instance-profile-name gludd-agent-readonly \
  --role-name gludd-agent-readonly
# Attach the instance profile at launch (--iam-instance-profile)
```

### (c) IAM user access key — **last resort**

Use only for **local dev** or environments with no instance identity. Long-lived
secret; rotate frequently and scope to the read-only policy only.

```bash
aws iam create-access-key --user-name gludd-dev
# Then export AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in the runtime env.
```

> Never attach `gludd-compute-deploy` to an IAM user access key used by the agent
> runtime.

---

## Exact keys / URLs / env vars gludd needs

`AwsPipelineSource` reads `region` from its connector **config** (not from an env
var) and otherwise defers to boto3. boto3's standard env vars supply credentials
and region. There are **no `*_env` secret fields** in the AWS connector.

| Env var / key | What it is | How to obtain | Least-priv role it maps to |
| --- | --- | --- | --- |
| `region` (connector config key, `aws_pipeline.py:102`) | AWS region for CodePipeline + Logs clients. Passed as `region_name` to `boto3.client`. | Set in the source/connector config (YAML/dict), e.g. `us-east-1`. | n/a (not a secret) |
| `pipeline` (connector config key) | CodePipeline name `query()` lists executions for. | Name of your pipeline. | `gludd-observability-readonly` |
| `log_group` (connector config key) | Default CloudWatch log group for `fetch_logs`. | Your log group name. | `gludd-observability-readonly` |
| `AWS_REGION` | boto3/AWS region (used when `region` config is empty → `region_name=None`). | Same region as above. | n/a |
| `AWS_DEFAULT_REGION` | Fallback region for boto3 if `AWS_REGION` unset. | Same. | n/a |
| `AWS_ACCESS_KEY_ID` | Access key id (credential-chain option **(c)** only). | `aws iam create-access-key`. | `gludd-observability-readonly` |
| `AWS_SECRET_ACCESS_KEY` | Secret access key (option **(c)** only). | Same create-access-key call. | `gludd-observability-readonly` |
| `AWS_SESSION_TOKEN` | Session token for temporary STS creds (assumed-role / SSO). | `aws sts assume-role` / SSO login. | the role being assumed |
| `AWS_PROFILE` | Named profile in `~/.aws/credentials` (local dev). | `aws configure --profile gludd`. | maps to whatever the profile's creds grant |
| `AWS_WEB_IDENTITY_TOKEN_FILE` + `AWS_ROLE_ARN` | IRSA token file + role (option **(a)**). | Injected automatically by EKS pod identity webhook. | `gludd-agent-readonly` |

For IRSA (option a) and instance profile (option b) you set **none** of the
`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` vars — boto3 resolves credentials
from the OIDC token / metadata service.

> Note: gludd's own observability config (`ObservabilityConfig` in
> `src/general_ludd/config/user_config.py`) exposes `otel_endpoint` and
> `service_name` under the `GLUDD_OBSERVABILITY` prefix (env prefix `GLUDD_`).
> Those are for OpenTelemetry export, **not** AWS endpoints, and require no AWS
> IAM permission.

---

## Verification

After attaching each policy, confirm every permission with a **read-only** call.
These mutate nothing.

CloudWatch Logs (Facility 1, live):

```bash
aws logs describe-log-groups --max-items 1
aws logs filter-log-events --log-group-name <your-group> --limit 1
```

CloudWatch Metrics (Facility 2, prospective):

```bash
aws cloudwatch list-metrics --max-items 1
aws cloudwatch get-metric-data \
  --metric-data-queries '[{"Id":"m1","MetricStat":{"Metric":{"Namespace":"AWS/EC2","MetricName":"CPUUtilization"},"Period":300,"Stat":"Average"}}]' \
  --start-time "$(date -u -d '-10 min' +%FT%TZ)" --end-time "$(date -u +%FT%TZ)"
```

X-Ray (Facility 3, prospective):

```bash
aws xray get-trace-summaries \
  --start-time "$(date -u -d '-5 min' +%s)" --end-time "$(date -u +%s)"
```

CodePipeline (Facility 4, live):

```bash
aws codepipeline list-pipelines
aws codepipeline list-pipeline-executions --pipeline-name <your-pipeline>
```

CodeBuild (Facility 4, prospective):

```bash
aws codebuild list-builds
```

Compute deploy (Facility 5, prospective — describe-only, launches nothing):

```bash
aws ec2 describe-instances --max-items 1
aws ecs list-clusters
aws eks list-clusters
# Confirm PassRole is scoped (should succeed only for the one exec role):
aws iam get-role --role-name gludd-model-server-exec-role
```

Confirm the *active* identity boto3/gludd will use:

```bash
aws sts get-caller-identity
```

If `get-caller-identity` returns the expected role ARN and the read-only calls
above succeed while no mutating call is permitted, the least-privilege setup is
correct.
