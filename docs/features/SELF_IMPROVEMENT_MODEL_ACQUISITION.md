# Self-Improvement Model Acquisition Authentication

Status: implemented on the self-improvement feature branch.

## Decision

Managed self-improvement keeps public Hugging Face models usable without an
operator login. A network operation selects exactly one explicit mode:

- `anonymous_public`: neither `HF_TOKEN` nor the compatibility
  `HUGGING_FACE_HUB_TOKEN` variable contains a token. Gludd passes
  `token=False` to the locked Hub client so a token saved elsewhere on the
  machine is not attached implicitly.
- `explicit_env_token`: one of those environment variables contains a token.
  Gludd forwards the exact value to the Hub API without placing it in an event,
  process argument, ownership manifest, diagnostic, or log message.
  `HF_TOKEN` has precedence.

The default policy permits both modes. Operators that require every network
request to be authenticated set
`GLUDD_SELF_IMPROVE_HF_TOKEN_REQUIRED=true`. Accepted true values are
`1`, `true`, `yes`, and `on`; false values are `0`, `false`, `no`,
`off`, and the empty value. Any other value fails manager construction.

Strict mode is an admission policy, not a download workaround. If no explicit
environment token is present, Gludd emits the typed anonymous selection and a
typed validation failure, then stops before revision metadata or model bytes
can contact the Hub.

## Observable lifecycle

Each cache-miss Hub operation has one correlation identity and this order:

1. `revision_resolution_started` or `download_started`;
2. exactly one `anonymous_public` or `explicit_env_token` event whose
   `auth_mode` has the same typed value;
3. zero or more bounded progress heartbeats;
4. one completed or failed event.

A full cold acquisition normally performs two Hub operations, immutable
revision resolution and artifact transfer. It therefore emits one auth-mode
event for each operation. This is deliberate: the event proves the choice
immediately before the specific external call rather than implying that one
earlier choice governed later I/O.

Events contain only hashed repository/model correlation keys, immutable
revision when known, elapsed time, the enum mode, and a bounded failure class.
They never contain the credential or raw repository identifier.

An owned cache hit follows a narrower path. Gludd revalidates its manifest,
contained path, byte size, and digest, emits `cache_hit`, and creates the
lease. It does not select auth, call a token provider, resolve a Hub revision,
construct a downloader, or start an acquisition worker. Strict mode therefore
does not make an already admitted artifact unusable while the network is
unavailable or the token is intentionally absent.

## Credential and process boundary

Gludd does not serialize a credential into the arguments sent to its bounded
spawn worker. The parent observes only whether an explicit environment value
exists. The child inherits the process environment and reads the credential
at the last responsible moment:

- revision resolution constructs `HfApi(token=<environment value>)`, or
  `HfApi(token=False)` when absent;
- GGUF transfer constructs the existing `ModelDownloader`, which forwards the
  same explicit environment value or `False` to `hf_hub_download`.

The worker still has the existing finite deadline, process name, termination,
join, and partial-transfer cleanup. Missing strict auth fails before worker
creation. No authentication daemon, login subprocess, token cache, or
additional cleanup task is introduced.

## Upstream warnings are evidence

Anonymous public access and silent access are different contracts. The locked
`huggingface_hub==1.17.0` can complete a public request while the Hub returns
an `X-HF-Warning` explaining unauthenticated rate limits. Gludd does not:

- set `HF_HUB_VERBOSITY` to hide that message;
- install a warning or logging filter around Hub calls;
- redirect the Hub HTTP client;
- rewrite the server warning into a success claim; or
- retry with an ambient saved credential.

The upstream message remains visible so an operator can decide whether higher
authenticated rate limits are worth configuring. It is informational for a
successful public download, not evidence that Gludd requires a token.

## Zero-downtime deployment and rollback

The policy is read when a new `ModelLeaseManager` is constructed. Existing
workers and leases finish under the mode they already selected. New cache hits
continue without auth or network I/O. This avoids stopping the Gludd daemon or
invalidating a model merely to change authentication policy.

To roll back strict admission without changing cached bytes, unset
`GLUDD_SELF_IMPROVE_HF_TOKEN_REQUIRED` or set it to `false`, then let the
next manager use the default policy. To roll back authenticated acquisition,
remove the token from the daemon's environment before its next managed
operation. Neither rollback deletes manifests, revisions, or leases.

Code rollback is also data-compatible: the acquisition event adds an optional
field and does not change the ownership-manifest schema. Older consumers can
ignore the new event field, and existing cache entries remain valid.

## Resource and failure guarantees

- Cache reuse performs no Hub/auth operation and starts no child process.
- A strict missing-token refusal starts no child process and transfers zero
  bytes.
- A cold permitted operation retains the existing 600-second default deadline,
  observable heartbeats, deterministic process namespace, and always-joined
  cleanup.
- Authentication events cannot leak repository names or token values.
- A failing event sink is disabled without changing acquisition ownership.
- Quota, reserve, lease, digest, and partial-transfer rules are unchanged.
- The dedicated cache root is expanded and resolved before it reaches
  `huggingface_hub`; Gludd never passes a literal tilde path to the client.

## Verification

The focused tests cover:

- `token=False` for public revision and GGUF paths;
- exact forwarding from each supported environment variable;
- typed and secret-free mode events before injected Hub operations;
- strict refusal before both revision resolution and download;
- strict-policy parsing and invalid-value refusal;
- cold-worker argument inspection proving that no token is serialized;
- owned cache reuse with zero revision, downloader, and auth-mode activity;
- upstream warning visibility; and
- the pre-existing deadline, heartbeat, lease, quota, integrity, and cleanup
  matrix under `-W error`.

Coverage is measured with branch coverage. The project gate remains responsible
for the repository-wide 85 percent aggregate and 75 percent per-file minimums.

## Evidence and practitioner reports

Official evidence:

- The [Hugging Face authentication quickstart](https://huggingface.co/docs/huggingface_hub/en/quick-start#authentication)
  documents implicit saved-token use, `HF_TOKEN` precedence, and explicit
  method parameters.
- The [official header builder source](https://github.com/huggingface/huggingface_hub/blob/main/src/huggingface_hub/utils/_headers.py)
  defines a string token as explicit auth and `token=False` as the supported
  way to omit the authorization header.
- The [official HTTP warning source](https://github.com/huggingface/huggingface_hub/blob/main/src/huggingface_hub/utils/_http.py)
  turns server `X-HF-Warning` headers into visible logger warnings.
- The project lock records `huggingface_hub==1.17.0`, so this contract is
  tested against the actual dependency boundary rather than an assumed older
  client.

Practitioner evidence:

- [huggingface_hub issue 4292](https://github.com/huggingface/huggingface_hub/issues/4292)
  reproduces the locked 1.17 client on macOS. Its trace records
  `authenticated: False`, the server's unauthenticated-request warning, and a
  failure caused by a literal tilde in an explicit cache path. Gludd preserves
  the warning and resolves its owned cache path before calling the client.
- The long-running
  [Hugging Face forum report about newly visible unauthenticated warnings](https://discuss.huggingface.co/t/embeddings-position-ids-unexpected-warning-started-showing/173102)
  shows public model downloads remaining functional while users see the
  rate-limit warning, including duplicate-looking output through layered
  logging. That is why Gludd emits its own single typed mode event but leaves
  the upstream message untouched rather than trying to silence vendor output.
