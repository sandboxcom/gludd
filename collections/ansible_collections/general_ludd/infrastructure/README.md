# General Ludd infrastructure collection

Infrastructure discovery and lifecycle roles run through collection-owned
controller actions rather than generated source or managed-host Python glue.

| Surface | Purpose |
|---|---|
| `openapi_query` | Admit one digest-pinned OpenAPI 3.1 contract and execute one bounded GET/HEAD operation |
| `auto_register_service` | Role adapter for `openapi_query`; no source or registry mutation |
| `service_discovery` | Bounded service discovery through the native SearXNG batch action |
| `auto_retire_service` | Explicit retirement workflow |

`openapi_query` pins `openapi-core==0.23.1` and `jsonpointer==3.2.0` in the
controller execution environment. It accepts secrets only as environment
variable names, uses a collection-native DNS-pinned HTTPS transport, forbids
redirects, and validates responses before returning bounded records.
