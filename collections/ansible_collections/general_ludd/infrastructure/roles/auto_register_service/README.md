# OpenAPI service registration

`auto_register_service` admits one digest-pinned, controller-local OpenAPI 3.1
contract and either validates or executes one explicit read-only operation. It
does not generate source, edit provider registries, create pricing sidecars, or
start a service. A local-only `openapi-core` adapter validates the selected
request and response schemas; `jsonpointer` selects bounded records; Gludd's
DNS-pinned `secure_fetch` transport performs exactly one HTTPS request without
redirects.

## Variables

| Variable | Default | Contract |
|---|---:|---|
| `auto_register_service_root` | `""` | Absolute controller root confining the contract |
| `auto_register_service_spec_path` | `""` | Regular file below the root |
| `auto_register_service_spec_sha256` | `""` | Required lowercase SHA-256 digest |
| `auto_register_service_base_url` | `""` | Explicit HTTPS origin and optional path prefix |
| `auto_register_service_allowed_host` | `""` | Exact DNS host; no wildcard |
| `auto_register_service_operation_id` | `""` | Unique GET or HEAD operation |
| `auto_register_service_parameters` | `{}` | At most 32 bounded scalar parameters |
| `auto_register_service_secret_env` | `{}` | Header names mapped to environment-variable names |
| `auto_register_service_records_pointer` | `""` | JSON Pointer resolving to the returned record list |
| `auto_register_service_timeout_seconds` | `15.0` | Positive total bound, at most 30 seconds |
| `auto_register_service_max_response_bytes` | `1048576` | Positive cap, at most 4 MiB |
| `auto_register_service_max_records` | `100` | Positive cap, at most 1,000 |
| `auto_register_service_validate_only` | `false` | Validate without opening transport |

Secrets are never accepted as task values. For example,
`Authorization: INVENTORY_API_AUTHORIZATION` reads the header value from that
named controller environment variable and never returns it. Controlled
transport headers and case-insensitive collisions are rejected before DNS.

## ZDD rollout and rollback

1. **Canary:** deploy the digest-addressed controller image and run with
   `auto_register_service_validate_only: true`; compare the admitted digest and
   operation identity with the active image.
2. **Shift:** enable execution for one inventory consumer. Both images remain
   compatible because the contract and operation identity are explicit and the
   query is read-only.
3. **Drain:** stop dispatching new tasks to the old image and allow its bounded
   request (at most 30 seconds) to finish. There is no listener, worker, cache,
   generated module, or persistent registry to migrate.
4. **Rollback:** route tasks to the prior digest-addressed image and keep the
   same contract digest. If the upstream contract or response fails validation,
   leave execution disabled and repair forward; no source cleanup is required.

See `docs/features/NATIVE_OPENAPI_SERVICE_REGISTRATION.md` for upstream issue
evidence and the complete trust boundary.
