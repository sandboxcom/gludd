# Secure outbound URL fetching

Gludd's migrated public HTTP call sites route requests through
`general_ludd.security.url_fetch`. The module is their single policy boundary:
callers declare the schemes, hosts, response-size cap, whole-operation deadline,
DNS deadline, and redirect limit that their workflow needs. A request that is
not explicitly allowed fails before network I/O.

The DAST startup health probe is the sole exception in this migration. Its host
is constructed as `127.0.0.1` by Gludd rather than accepted from a caller, so it
uses an HTTPX client with redirects and environment proxies disabled. Sending
that probe through the public fetcher would weaken the public fetcher's rule
that private and loopback destinations are never allowed. Azure retail-pricing
code is separately owned and intentionally outside this change.

## Why this mechanism

The transport is
[`safehttpx.AsyncSecureTransport`](https://github.com/gradio-app/safehttpx),
version 0.1.7 or later in the 0.1 line. The project was created from Trail of
Bits' Gradio 5 security audit and is used by Gradio for attacker-influenced
URLs. Its transport connects to a vetted IP while retaining the original host
for HTTP `Host` and TLS SNI, closing the validation-to-connection DNS-rebinding
window. Gludd already depended on HTTPX, so `safehttpx` adds the security
transport without introducing another general-purpose HTTP stack.

Upstream distributes safehttpx under
[Apache-2.0](https://github.com/gradio-app/safehttpx/blob/main/LICENSE). That
permissive license is compatible with Gludd's MIT distribution; its provenance
and retention requirement are recorded in `THIRD_PARTY_LICENSES.md`, and the
resolved version and hashes are locked in `uv.lock` and emitted into the
CycloneDX SBOM.

The package's convenience `get()` is intentionally not called directly. It is
GET-only and does not supply Gludd's response cap, total deadline, host policy,
or redirect-chain policy. Gludd wraps the mature pinned transport with those
application-specific controls rather than implementing another socket layer.
The underlying client runs with `trust_env=False`; an ambient proxy would
otherwise replace the vetted destination with a different connection path.

## Enforced policy

For the initial URL and every redirect hop, the fetcher:

1. parses the URL with HTTPX's normalized, IDNA-aware URL type;
2. rejects embedded usernames/passwords and non-absolute URLs;
3. requires the scheme and normalized hostname to match the caller's explicit
   allowlists (`*` means any otherwise-safe public host; `*.example.com` does
   not include the parent domain);
4. rejects loopback, RFC 1918/ULA private, link-local, multicast, reserved,
   non-global, and cloud-metadata literals and names;
5. resolves every address, rejects the entire answer set if any destination is
   unsafe, and passes only the vetted IP to `safehttpx`'s pinned transport;
6. disables automatic redirects, resolves relative `Location` values, and
   repeats all validation before the next request;
7. strips authorization, cookie, and proxy-authorization headers on an
   explicitly allowed cross-origin redirect;
8. streams the response and aborts when either declared `Content-Length` or
   bytes actually received exceed the cap; and
9. applies one deadline to DNS, every redirect, connection, and body reading.

Callers should choose the smallest policy that preserves their API contract:

```python
result = secure_fetch(
    "https://api.example.com/v1/items",
    headers={"Accept": "application/json"},
    policy=FetchPolicy(
        allowed_hosts=frozenset({"api.example.com"}),
        allowed_schemes=frozenset({"https"}),
        max_bytes=2 * 1024 * 1024,
        timeout_seconds=15,
        max_redirects=2,
    ),
)
```

Do not derive a wildcard policy merely to make a failing integration pass.
Host allowlists may be derived from an already validated integration base URL,
but redirects should normally remain on that same host. Do not allow private
hosts through the public fetcher; use a purpose-built internal client whose
destination is constructed by trusted code.

## Guidance and persistent field evidence

The normative design follows the
[OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html): prefer allowlists,
limit schemes to HTTP(S), inspect every A and AAAA result, account for DNS
pinning/rebinding, and disable automatic redirects so validated input cannot be
bypassed. The
[HTTPX client documentation](https://www.python-httpx.org/api/#client)
documents explicit redirect, timeout, transport, and environment-trust controls;
these are all set rather than inherited from process defaults.

User reports show that these are durable engineering problems, not theoretical
edge cases:

- A 2019
  [AskNetsec SSRF field report](https://www.reddit.com/r/AskNetsec/comments/cprxoy/further_exploiting_of_blind_ssrflfi/)
  describes alternate schemes, encoded loopback forms, and DNS rebinding in a
  real blind-SSRF investigation. That history supports a positive scheme list
  and normalized IP classification rather than string deny rules.
- A 2022
  [AskNetsec implementation question](https://www.reddit.com/r/AskNetsec/comments/zemvnu/ssrf_protection_in_java_spring/)
  independently arrives at the need to resolve every DNS record and reject
  private, localhost, and link-local answers when a domain allowlist is not
  possible. The same concern remained unresolved enough to recur years later,
  which is why Gludd centralizes and tests it once.
- A 2025
  [web-development discussion](https://www.reddit.com/r/webdev/comments/1ii0jdr/owasp_recommends_disabling_http_redirections_i/)
  records developers being surprised that a public URL can redirect a server
  fetch into an internal target. Gludd follows redirects only manually and
  re-runs the full policy at each hop.

The regression suite in `tests/unit/test_url_fetch_security.py` covers pinned
DNS destinations, rebinding/private answers, redirect revalidation, metadata
redirects, credential-bearing URLs, response-size limits, and whole-operation
timeouts. Bandit B310 is also rerun after call-site migrations; suppressions are
not an accepted remediation.

Webhook retry, body, header, and timeout unit tests explicitly replace DNS
pinning with a deterministic public-host result. Those tests do not own DNS
behavior, and must not depend on ambient resolver or network availability in a
developer shell or GitHub-hosted runner. The separate webhook rebinding and SSRF
suites retain the real boundary contract: delivery calls `resolve_and_pin`,
private or unresolved answers fail closed, and redirects remain disabled.
