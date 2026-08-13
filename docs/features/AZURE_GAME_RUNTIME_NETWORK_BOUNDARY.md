# Azure Game Runtime Network Boundary

## Contract

Before provisioning any billable Azure game runtime, Gludd resolves the runner's
allowed IPv4 CIDR. An explicit `AZURE_ALLOWED_CIDR` remains authoritative.
Automatic discovery accepts only a syntactically valid, globally reachable IPv4
address and narrows it to a single-host `/32`. HTTP failures, malformed
responses, IPv6, private, shared, reserved, documentation, and other non-global
ranges fail closed before spend.

## Runtime adapter boundary

The `DeploymentController` protocol is runtime-checkable because the runtime
accepts injected deployment adapters in tests and operator integrations. That
check proves only the presence of `deploy` and `destroy`; authorization,
credentials, spend limits, and returned instance validation remain explicit
runtime responsibilities.

## Security and ZDD

The guard limits the temporary ingress rule to the observed public runner and
does not broaden access when discovery is ambiguous. Existing deployments are
unchanged until preflight succeeds; failed preflight allocates nothing, so
rollback is simply retaining the current environment. Operators behind NAT,
proxies, or restricted egress can provide the explicit CIDR without changing
the discovery policy.

## Observability and compatibility

Failures distinguish discovery errors from non-global results and tell the
operator to set `AZURE_ALLOWED_CIDR`. Tests use a truly global address for the
success path and separately pin RFC 1918 and TEST-NET rejection. This avoids
depending on historical `ipaddress` classifications while remaining compatible
with Python releases that follow the current IANA special-purpose registries.

## Practitioner evidence

CPython's long-running [special-address classification discussion](https://github.com/python/cpython/issues/61602)
records years of operator confusion over private, forwardable, and
globally reachable ranges, and the later registry-alignment work. Gludd uses
`is_global` at this spend boundary and treats documentation ranges such as
`203.0.113.0/24` as rejection fixtures, never as successful public discovery.

## Verification

- `tests/unit/test_cloud_azure_game_runtime.py` covers explicit configuration,
  public discovery, private and documentation ranges, malformed responses, and
  HTTP failures.
- The full gate proves the behavior alongside Azure IAM, provisioning, and
  release checks.
