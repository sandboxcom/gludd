# MCP Registry and Launch Security

Status: implemented and fail-closed as of 2026-08-14.

## Reconciliation decision

This feature reconciles five historical MCP tips without weakening the current
admission or dispatch gates.

| Tip | Decision and surviving behavior |
| --- | --- |
| `c2f3fe8233fa` | The registry remains keyed by `(server_id, tool_name)`, but its proposal to admit cross-server duplicate names is superseded by the current collision gate. |
| `b7bb04724cc0` | Reusable trim/non-empty behavior survives via Pydantic `Annotated` and `StringConstraints`, not a custom decorator factory. The tool-name domain gate remains separate and stricter. |
| `3dc41a77266b` | Composite-key lookup/removal behavior survives. Its collision-relaxing source and tests are superseded; ambiguous state is still defended against if legacy/private state bypasses admission. |
| `4c3cca13088e` | Directory-redirection and inline flag-value smuggling are rejected before process creation, while the newer exact-pin validation for `--package` and `-p` remains intact. |
| `eecfff616dd8` | The call-tool server-binding gate survives, but removing collision-admission tests is superseded. Both gates remain regression-tested. |

The MCP specification scopes tool-name uniqueness to one server and recommends
disambiguation in aggregating clients. Gludd intentionally applies a stricter
policy until its model-facing namespace always includes a trusted server
identity. That avoids a second server turning an existing unqualified model
tool call into an ambiguous or attacker-selected route. The internal composite
key remains necessary for pinned lookup, server removal, and defense-in-depth
handling of legacy state. See the
[MCP tool-name guidance](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/tools.mdx#tool-names).

## Mature primitives and community evidence

Pydantic already provides the reusable schema primitive this feature needs.
Its official documentation recommends `Annotated` with `StringConstraints`
for reusable string constraints and exposes whitespace, minimum-length, and
pattern constraints in generated JSON Schema. Gludd uses that primitive rather
than maintaining a validator-decorator factory. See
[Pydantic StringConstraints](https://docs.pydantic.dev/latest/api/types/#pydantic.types.StringConstraints)
and
[Pydantic reusable annotated validators](https://docs.pydantic.dev/latest/concepts/validators/#using-the-annotated-pattern).

Two long-lived Pydantic community discussions informed the choice. The
[empty-string discussion #2687](https://github.com/pydantic/pydantic/discussions/2687)
has accumulated migration approaches for more than five years, while
[pattern discussion #7278](https://github.com/pydantic/pydantic/discussions/7278)
documents the easily missed need to anchor a Pydantic v2 regex. Gludd therefore
uses the built-in trim/minimum-length metadata and retains its explicitly
anchored, domain-specific tool-name check.

The command is already an argv list passed to `asyncio.create_subprocess_exec`;
re-parsing it with a shell or `shlex` would add the wrong abstraction. The
remaining work is launcher policy. Official
[npx documentation](https://docs.npmjs.com/cli/commands/npx/)
states that options before the package are interpreted by npx, that any npm
configuration value may be provided, and that `--package` can select fetched
code. Official
[uv tool-run documentation](https://docs.astral.sh/uv/reference/cli/#uv-tool-run)
documents `--directory`, `--project`, `--config-file`, and `--from` as inputs
that can change discovery or the installed package. Gludd parses the small
security-relevant option surface, validates package-bearing options with the
existing exact-pin gate, and rejects resolution redirects.

The long-lived npm user report
[`npx` workspace resolution #6765](https://github.com/npm/cli/issues/6765),
opened in 2023, shows that workspace/context flags can select a different local
package version than users expect. This is why a later pinned-looking
positional argument is not sufficient evidence after a directory, prefix, or
workspace redirect.

## Security and observability

Validation runs before subprocess creation. Invalid commands fail with
`MCPTransportError` naming the launcher and rejected flag category. Known
package-bearing flags are parsed first and must carry an exact version; they do
not trip the generic inline-value guard. Benign value-free flags continue to
work. The registry rejects a duplicate unqualified name before modifying either
the composite store or the per-server index, and `call_tool` independently
checks the `(server_id, tool_name)` binding before transport dispatch.

These are policy errors, not retryable transport failures. Operators should fix
the MCP configuration and restart only the affected server/client worker. No
secret value is needed to diagnose the category.

## ZDD, resources, and rollback

The change is zero-downtime deployable: it has no schema migration, persistent
state rewrite, port change, or daemon. Existing processes continue serving
while new workers load the constraints. A rolling restart may reject an unsafe
configuration that an old worker accepted; this is the intended fail-closed
transition. Remove or replace that configuration before retrying the worker.

Validation is linear in the already-bounded argv length and constant-space per
argument. It performs no network, filesystem, package-manager, or subprocess
work. The Pydantic constraints are compiled with the model schema at import and
add no background process. Tests use only in-process validators, so they do not
consume launcher or daemon slots.

Rollback is a single code revision with no data restoration. Rolling back
re-enables previously accepted unsafe flags, so the safer operational rollback
is to correct the configuration and roll forward. If code rollback is required,
first disable affected MCP entries, roll back workers gradually, and retain the
registry collision and call-tool binding gates.

## Verification map

- `tests/unit/test_mcp_validators.py` pins reusable schema-visible constraints
  and the stronger tool-name gate.
- `tests/unit/test_mcp_transport_flag_smuggle.py` pins redirect/smuggling
  rejection and benign/package-flag compatibility.
- `tests/unit/test_mcp_registry.py` pins composite storage and strict collision
  admission.
- `tests/unit/test_tool_loop_routing.py` pins fail-closed ambiguous-state routing.
- `tests/unit/test_mcp_registry_gate.py` pins collision admission and per-server
  call-tool dispatch.
