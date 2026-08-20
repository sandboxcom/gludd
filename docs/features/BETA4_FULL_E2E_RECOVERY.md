# Beta4 Full-E2E Recovery

## Incident

The beta4 full-E2E run exposed two independent contract failures:

- the authentication test treated every public `GET /readyz` response as a
  readiness success, although the full harness intentionally keeps an
  automatically-created event loop unavailable until an explicit probe task is
  installed; and
- the Azure Container App materializer copied the vLLM module into its isolated
  Terraform root but left the cost-watchdog module outside that root, with an
  invalid `../../modules` reference. `terraform init` therefore failed before
  validation with an unreadable-module error.

## Runtime contracts

Authentication and readiness are separate signals. `/healthz`, `/readyz`,
`/docs`, and `/openapi.json` remain public even when protected routes fail
closed because no PSK is configured. `/readyz` returns 503 while the daemon
cannot accept work; that status is not an authentication denial. The regression
checks both sides at once: public paths never return the authentication error,
while a protected admin path does.

An Azure Container App validation directory is now self-contained. One ordered
module inventory drives both source localization and asset copying, so the vLLM
and cost-watchdog references cannot drift independently. Missing repository
assets fail before Terraform starts instead of silently producing a partial
root.

## Practitioner evidence

HashiCorp Terraform issue
[#23333](https://github.com/hashicorp/terraform/issues/23333), opened
2019-11-09, records years of practitioner reports around upward-traversing local
module paths and `Unreadable module directory`; maintainers confirmed that
root-local `./...` paths behaved correctly. Issue
[#22785](https://github.com/hashicorp/terraform/issues/22785), opened
2019-09-12 and still open when reviewed on 2026-08-20, includes a 2022 CI report
where a packaged module tree accidentally omitted nested modules. Both reports
support shipping a complete, root-local module graph rather than depending on
the source checkout's parent layout.

Kubernetes issue
[#122591](https://github.com/kubernetes/kubernetes/issues/122591), opened
2024-01-04 and still open when reviewed on 2026-08-20, documents the operational
difference between readiness and liveness: a failed probe correctly removes a
workload from service without necessarily implying process death. Long-lived
issue [#89898](https://github.com/kubernetes/kubernetes/issues/89898), opened
2020-04-06, records intermittent readiness failures under real resource and
connection pressure. Those reports reinforce that a 503 readiness signal must
not be reinterpreted as a failed public-authentication contract.

## Verification and resources

The focused matrix runs one deterministic authentication/readiness test in both
normal and `GLUDD_E2E_ACTIVE=1` modes, the Azure materialization unit family, and
the real state-free Terraform init/validate E2E. The tests use pytest-owned
temporary directories, two bounded workers, no cloud credentials, and no cloud
resource creation. Every copied module is repository-owned and contains no
state or secrets.

## ZDD and rollback

The change affects newly-created validation and deployment directories only.
Existing Terraform state and running deployments are untouched, so rollout is
zero-downtime. Rollback is a code-only revert: no state migration is required.
During a mixed-version rollout, old daemon processes continue using their
already-materialized directories while new processes produce the complete
root-local module graph.
