# Azure accelerator runtime protocols

Status: implemented for the Azure SDK structural boundary with focused runtime
and repository protocol-audit coverage. Full gate evidence is tracked in
`TASKS.md` as S83.18.

## Purpose

`general_ludd.infra.azure_accelerator` accepts the Azure Compute SDK through
six narrow `Protocol` interfaces rather than importing concrete SDK client
types into its read-only preflight logic. The boundary covers SKU records, usage
records, their operation groups, and the compute client that owns them.

These interfaces are now `@runtime_checkable`. Callers and health checks can
reject an object that lacks the required SDK surface before beginning a quota
or SKU query, while static type checking retains the same structural contract.

## Behavioral contract

1. `AzureResourceSku`, `AzureUsageName`, `AzureUsage`,
   `AzureResourceSkuOperations`, `AzureUsageOperations`, and
   `AzureComputeClient` support `isinstance` structural checks.
2. Checks validate member presence only. They do not validate attribute value
   types, method signatures, Azure credentials, permissions, or network health.
3. The preflight remains read-only: it lists resource SKUs and usage quota and
   never creates, updates, or deletes an Azure resource.
4. Missing SKU/quota data still fails closed through the existing preflight
   blockers. Runtime protocol checks do not replace response validation.
5. Fake clients used in tests implement the same shape without inheriting from
   Azure SDK classes, preserving dependency isolation.

## Security and resource boundaries

Runtime protocol checks must not be treated as authorization or input
validation. Azure identity, subscription, region, SKU restrictions, and quota
responses continue through their existing fail-closed checks. The protocols
expose no credential fields and perform no I/O by themselves.

The checks are shallow and bounded to six small interfaces. They add no workers,
ports, files, or persistent state.

## Zero-downtime deployment

The decorators change runtime introspection metadata only. Existing SDK clients
and structural fakes remain compatible, so old and new application workers can
overlap during a rolling deployment. There is no schema or configuration
migration. Rollback consists of restoring the previous module.

## Practitioner evidence

The Python typing community discussed the exact semantics of
`isinstance(obj, RuntimeProtocol)`, including data attributes, properties,
descriptor evaluation, compatibility with static narrowing, and the fact that
runtime protocols check member presence rather than value types. That report
motivates the narrow use here and the explicit limitation above:

- [python/typing issue #1363](https://github.com/python/typing/issues/1363)

Gludd therefore decorates only the SDK boundary protocols that it intentionally
checks at runtime, and retains independent validation for all returned values.

## Verification

- `tests/unit/test_azure_accelerator.py` proves SDK-shaped objects satisfy all
  six runtime protocols and preserves existing SKU/quota behavior.
- `tests/unit/test_abc_protocol_audit_deep.py` requires at least half of
  production protocols to be runtime-checkable and verifies every decorated
  protocol has meaningful members.
- Focused source coverage and the repository gate retain the global 85% and
  individual-file 75% coverage floors.
