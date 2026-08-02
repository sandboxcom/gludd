# Deployment fix-loop capability boundary

## Decision

Every state-changing deployment fix-loop route requires the exact
`admin:deploy:write` capability:

- `POST /admin/deployments/suggest-fix` may spend model budget and parks a new
  proposal;
- `POST /admin/deployments/fixes/{fix_id}/approve` authorizes a configuration
  change and may invoke the redeploy hook; and
- `POST /admin/deployments/fixes/{fix_id}/reject` changes the proposal's durable
  decision state.

Authentication and authorization remain separate. The daemon PSK middleware
validates the bearer secret and attaches the canonical full-admin
`PermissionSpec`; the route dependency then checks its capability. A bare router,
missing request spec, insufficient spec, or explicit deny receives 403 before
the proposal manager, model gateway, or redeploy hook runs. Tests never disable
the dependency: they install a least-privilege request spec matching the daemon
middleware contract.

The PSK is an intentional break-glass full-admin credential, so its canonical
spec includes `admin:deploy:write`. More restricted human, agent, or STS specs
remain configurable and can omit or explicitly deny that grant. Denials continue
to override positive grants through `check_capability`.

## ZDD configuration delivery

Permission changes SHALL use immutable, versioned specs. New workers must load
and validate the replacement spec before they accept traffic; each request keeps
the spec snapshot selected at authentication; old workers drain their in-flight
requests before shutdown. A deployment must not mutate the capability list
attached to an already-authorized request. Rollback selects the prior validated
spec for replacement workers instead of editing live request state.

## Operator evidence

- [FastAPI discussion #9477](https://github.com/fastapi/fastapi/discussions/9477)
  shows the established middleware pattern of placing authenticated identity on
  `request.state` for downstream dependencies. Gludd tests the same composition
  instead of calling a protected route with an impossible empty state.
- [FastAPI discussion #3958](https://github.com/fastapi/fastapi/discussions/3958)
  records long-lived authentication and application-state lifecycle concerns.
  Gludd therefore creates one proposal manager per app and one permission
  snapshot per request rather than relying on mutable module-global auth state.

## Regression contract

`tests/routers/test_deployments_fix_loop.py` proves all three mutations fail
closed without a spec and that a least-privilege operator can complete the
workflow. `tests/unit/test_security_capability_guard.py` pins the canonical PSK
grant. `tests/e2e/test_e2e_security_auth.py` exercises the real daemon middleware
with a bearer PSK through suggestion plus approve/reject decisions.
