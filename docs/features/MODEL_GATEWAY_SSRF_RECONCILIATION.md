# Model gateway SSRF reconciliation

## Scope and ancestry

Candidate `7a4acd900cfb00eab67160f606c3a0e054bf45cd` is not an ancestor of the
beta4 development base, but its C6 behavior is already present through ancestor
`3c37e31ee49847cbe098441127de66d38edfbfb6`. The current gateway retains the
three intended controls: caller `base_url` and `api_key` kwargs cannot replace
alias-owned values, provider calls receive a bounded HTTPX timeout, and rejected
alias URLs are redacted. The C6 regression module is also present on development.

The old candidate must therefore not be cherry-picked. Its source hunks would
replay stale line context over a substantially evolved gateway, and its tests
would duplicate an existing module. Reconciliation is a focused follow-up on the
current source: preserve the alias authority boundary and make the timeout
resource boundary consistent across every gateway construction path.

## Security and resource contract

- Credentials and remote endpoints come only from the project-scoped secrets
  resolver. Callers may not replace either value through provider kwargs.
- Non-local alias URLs are HTTPS-only and rejected before provider construction
  when their literal host is private, loopback, link-local, reserved, metadata,
  malformed, or otherwise non-global.
- Gateway-owned connect/read/write/pool deadlines cannot be removed or enlarged
  through either `request_timeout` or `timeout` kwargs. This bounds unreachable
  endpoints and stalled provider reads without spawning background processes.
- Rejection messages identify the profile but never echo the resolved secret URL
  or internal address.

These checks fail closed before invoking a provider. They do not mutate durable
state, create a migration, change a public schema, or add a daemon.

## DNS rebinding and mature tooling

Literal URL validation alone does not close DNS rebinding: a public hostname can
resolve differently between validation and connection. Gludd already selected
[`safehttpx`](https://pypi.org/project/safehttpx/) 0.1.7, created after the Trail
of Bits Gradio 5 audit, and uses its pinned-IP transport in
`general_ludd.security.url_fetch`. That transport preserves the original HTTP
Host and TLS SNI while connecting to the vetted address. The gateway must not
grow a second home-made URL scanner or socket transport.

The provider boundary is different from the fetch boundary: LangChain provider
objects own their clients, and not every approved provider accepts the same
custom-client parameters. LangChain documents `http_client` as sync-only unless
`http_async_client` is also supplied. Therefore this reconciliation does not
pretend that a preflight DNS lookup is pinning; a check-then-connect lookup would
retain the race. DNS transport pinning belongs in one provider-client integration
that reuses a mature transport for both sync and async calls and owns both client
lifecycles. Until that integration is available, configured API-base aliases are
an operator-controlled trust boundary and caller overrides remain denied.

Persistent user reports support that conservative boundary:

- HTTPX users have reported since 2021 that a custom transport can silently take
  ownership of TLS and other client settings, so transport injection must be
  explicit and tested:
  <https://github.com/encode/httpx/discussions/1867>.
- LangChain users reported in 2024 that custom sync/async HTTP clients were wired
  to the wrong OpenAI client type; the repair required coordinated upstream
  changes rather than an application-local shim:
  <https://github.com/langchain-ai/langchain/issues/19116>.
- The long-lived field reports collected in
  [`docs/security/url-fetching.md`](../security/url-fetching.md) document encoded
  loopback targets, DNS rebinding, private DNS answers, and redirect pivots.

## Zero-downtime delivery and rollback

The repair is process-local and backward-compatible for legitimate callers.
Deploy new workers beside old workers, route new work to the new pool, and drain
old model calls. Existing in-flight calls keep their original client and timeout;
new calls receive the consistent bounded timeout. No database, queue, cache, or
secret migration is required.

Rollback is a source revert followed by the same drain-first worker replacement.
It restores the previous timeout behavior without data reversal. Rollback must
not restore caller endpoint or credential overrides, raw SSRF error URLs, or an
unbounded provider deadline.
