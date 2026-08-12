# Model Gateway Payload Limits (D-30 Phases 1–3)

## Status and boundary

D-30 phases 1–2 are implemented for buffered calls that pass through
`ModelGateway.call_model` and its retry/fallback/cache paths. The gateway rejects
oversized logical requests before provider construction or invocation, then
validates buffered provider responses and cache hits before any billing,
success metric, token-learning record, LangSmith trace, or cache write.
Retries and fallback hops share one request-scoped cumulative ledger, so walking
the chain cannot reset byte, token, tool-call, or provider-attempt ceilings.

Phase 3 adds `ModelGateway.call_model_stream`. It applies the buffered request
gate before provider construction, then enforces bytes, conservative tokens,
chunks, absolute duration, inter-chunk idle time, tool fragments, and
compressed-to-decoded ratio while the provider iterator is live. Every terminal
path closes that iterator. Billing, health success, metrics, token learning, and
tracing occur only after clean exhaustion; streaming deliberately bypasses the
buffered response cache because a partially delivered stream is not atomic.

This phase does not claim full D-30 completion. Raw `get_chat_model` runnables,
the separate HTTP chat client, asynchronous streams, streaming retry/fallback
chains, provider-specific serialized prompt bytes, and cancellation of an
already-running buffered invocation remain open. The stream ratio is exact when
an adapter injects wire-byte counts. If a provider advertises a non-identity
encoding without those counts, Gludd rejects it; if LangChain exposes neither
encoding nor wire bytes, this layer can only treat the decoded chunk as identity.

## Configuration

Each `ModelProfile` has positive integer limits:

| Field | Default | Enforced boundary |
|---|---:|---|
| `max_request_bytes` | 1 MiB | compact UTF-8 message JSON plus buffered tool/guided/extra-body JSON |
| `max_input_tokens` | 120,000 | trusted configured local counter, with fail-closed fallback |
| `max_response_bytes` | 4 MiB | retained UTF-8 text plus normalized tool-call JSON |
| `max_output_tokens` | 8,000 | consistent LangChain usage metadata, with fail-closed fallback |
| `max_tool_calls` | 64 | raw provider tool-call list, before normalization can drop malformed calls |
| `max_cumulative_request_bytes` | 1 MiB | outbound logical request bytes summed across provider attempts |
| `max_cumulative_input_tokens` | 120,000 | outbound input tokens summed across provider attempts |
| `max_cumulative_response_bytes` | 4 MiB | buffered response bytes summed across retries and fallbacks |
| `max_cumulative_output_tokens` | 8,000 | validated/fail-closed output tokens summed across responses |
| `max_cumulative_tool_calls` | 64 | raw tool calls summed before normalization across responses |
| `max_provider_attempts` | 16 | provider constructions/invocations admitted for one logical request |
| `max_stream_bytes` | 4 MiB | decoded UTF-8 text and serialized tool fragments while the iterator is live |
| `max_stream_tokens` | 8,000 | validated streamed usage when available, otherwise one token per retained UTF-8 byte |
| `max_stream_chunks` | 8,192 | provider chunks admitted before upstream cancellation |
| `max_stream_seconds` | 300 | monotonic absolute lifetime plus provider transport read bound |
| `max_stream_idle_seconds` | 60 | monotonic inter-chunk gap plus provider transport read bound |
| `max_stream_decompression_ratio` | 100 | cumulative decoded bytes divided by injected compressed wire bytes |

`ModelGateway(request_token_counter=...)` accepts a model-specific, local token
counter with the signature `(profile, messages) -> int`. The return is trusted
only when it is an exact non-boolean, non-negative integer. A counter failure or
invalid value does not disable the limit.

`ModelGateway(stream_wire_byte_counter=...)` accepts an adapter-local counter
with signature `(chunk) -> int`. It must return the exact non-negative compressed
wire bytes represented by that chunk. Invalid counts reject the stream. Without
the callback, identity streams use decoded bytes as wire bytes (ratio 1), while a
provider-declared gzip/Brotli/other encoding rejects because the true ratio is
unavailable. The configured ratio is never inferred from Python object size.

The initiating profile owns the cumulative limits for the lifetime of the
logical request. When an explicitly named primary is absent, the first
configured profile in the explicit fallback list is the policy anchor; if no
such profile exists, the request fails before provider construction. This keeps
the established missing-primary recovery path without permitting an unconfigured
name to create an unmetered ledger. Every fallback still enforces its own
ordinary per-attempt profile limits, but it cannot replace or enlarge the policy
anchor's ledger. All cumulative settings are positive integers and flow through
the existing `ModelProfile` loading, dynamic-profile event, hook, and worker
broadcast paths; no parallel configuration framework is introduced.

## Accounting and fail-closed behavior

Byte accounting uses `ensure_ascii=False`, compact separators, stable key order,
and `len(encoded.encode("utf-8"))`; Python character count and escaped-JSON
length are never substituted. Request accounting includes the message envelope
and the structured request bodies Gludd explicitly forwards (`tools`,
`extra_body`, and guided-generation fields). Response accounting includes both
text and normalized tool-call arguments, so a single allowed call cannot hide an
unbounded argument payload.

For request tokens, an operator-supplied model tokenizer is the accurate path.
When none is configured—or it fails—the gateway conservatively counts one token
per retained UTF-8 byte. This intentionally overestimates common byte-level BPE
tokenizers and can reject a safe request; it never silently substitutes the
usual four-characters-per-token heuristic in a security decision. Structured
tool/guided bodies are conservatively added by byte even when a configured
message counter is present.

For responses, the gateway trusts LangChain-standard `input_tokens`,
`output_tokens`, and `total_tokens` only when all three are exact non-negative
integers and `total_tokens == input_tokens + output_tokens`. Missing, boolean,
negative, floating, or internally inconsistent metadata falls back to one token
per retained UTF-8 response byte. This validates metadata structure and
consistency; the configured provider remains the trust boundary for a
well-formed but dishonest count.

Rejections raise `PayloadLimitError`, whose typed fields are `profile_id`,
`stage`, `dimension`, `actual`, `limit`, `source`, and `count_source`. Its message
contains only those bounded scalars—never prompt text, response text, tool
arguments, credentials, or raw provider objects. The exception is explicitly
propagated through fallback walkers so input rejection cannot be converted into
calls to additional providers.

Profile-change fan-out also treats worker-broadcaster exception text as
untrusted: failures log only the bounded add/remove action, never the exception
body, which may contain credentials or model-response fragments.

Cache hits are revalidated against the profile's current limits. An oversized
or stale entry is neither returned nor rewritten. Provider responses are checked
before `record_spend`, health success, metrics, token tracker, LangSmith, and
cache set; therefore rejection cannot bill, trace, meter, learn, persist, or
recache the payload.

Before each cache-miss provider hop, the ledger atomically reserves one provider
attempt plus that hop's request bytes and tokens. A failed invocation retains
that reservation because the outbound work already happened; exhaustion rejects
the next retry/fallback before provider lookup, credential resolution, client
construction, or invocation. Each buffered provider result is then atomically
reserved after per-profile validation and before the empty-response retry gate or
any billing/observability side effect. Cache hits reserve only returned response
dimensions because they send no provider request and consume no provider attempt.
An over-limit reservation changes none of the ledger counters.

For streaming, the configured stream byte/token ceilings are intersected with
the ordinary response limits and request-wide cumulative limits. The online
counter therefore cannot widen an existing profile policy. Missing or malformed
stream usage metadata is counted conservatively as one output token per decoded
UTF-8 byte because many providers emit authoritative usage only in the terminal
chunk. A caller that stops consuming closes the upstream iterator and records no
success or spend. A limit breach closes upstream before the typed error escapes
and never reads or writes the cache.

Duration and idle checks use a monotonic clock at chunk boundaries. The provider
constructor also receives a read timeout equal to the stricter duration/idle
limit, so a provider that stops yielding cannot hold the caller indefinitely
between boundary checks. The adapter does not log upstream chunks or include them
in `StreamLimitError`; its diagnostics remain bounded scalar fields. Idle is the
more specific diagnosis when both deadlines expire at a chunk boundary, while an
absolute-duration check after clean iterator exhaustion catches a provider that
returns its last chunk only after the overall deadline.

Request-wide exhaustion raises `CumulativePayloadLimitError`, a typed subclass of
`PayloadLimitError`, with the same payload-free bounded scalar fields and
`count_source="request_wide_cumulative"`. Existing payload-error propagation thus
still hard-stops retry/fallback walkers, while callers can distinguish a
cumulative cancellation from a single-hop limit.

## ZDD and multi-worker boundary

The ledger is private to a logical request, has atomic reservations, and is
passed explicitly through the retry and fallback walkers. It is neither a
process-global counter nor shared between tenants, requests, Gunicorn workers, or
xdist workers. A dynamic profile replacement therefore affects newly admitted
requests while an in-flight request completes under its immutable initiating
limits; no restart or partially applied cross-worker counter reset is required.
Existing model add/remove events still carry every limit to worker broadcasters,
which preserves the project's zero-downtime profile-update path.

## Existing-facility research and decisions

- LangChain defines `UsageMetadata` as the cross-model standard containing
  input, output, and total token counts. Gludd consumes this standard shape but
  adds strict integer and sum checks before treating it as trustworthy:
  [LangChain `UsageMetadata` reference](https://reference.langchain.com/python/langchain-core/messages/ai/UsageMetadata).
- LangChain's official `count_tokens_approximately` explicitly says it may not
  match a model and recommends model-specific tokenizers for accurate counts.
  It is useful for trimming, but not a hard security limit:
  [LangChain approximate-token reference](https://reference.langchain.com/python/langchain-core/messages/utils/count_tokens_approximately).
- `ChatOpenAI.get_num_tokens_from_messages` uses model-specific `tiktoken`
  encodings but documents approximations and unsupported tool-schema details.
  A local adapter may use it for a matching model; Gludd does not assume it is
  correct for every provider:
  [LangChain OpenAI implementation](https://github.com/langchain-ai/langchain/blob/master/libs/partners/openai/langchain_openai/chat_models/base.py).
- A long-running LangChain AWS issue records Anthropic removing a local
  `count_tokens` API and Bedrock lacking the replacement, demonstrating why a
  universal provider counter cannot be assumed and why a conservative fallback
  is required:
  [langchain-aws issue #314](https://github.com/langchain-ai/langchain-aws/issues/314).
- A 2023 LangChain user discussion notes that character-sized chunks vary in
  tokens and recommends an LLM/model token function when the model boundary
  matters:
  [langchain issue #2026](https://github.com/langchain-ai/langchain/issues/2026).
- HTTPX maintainers explain that byte iteration is over decompressed content
  while raw iteration is one-shot compressed wire data. That distinction is why
  decompression ratio needs either raw wire counters or an identity-encoding
  policy rather than decoded object size:
  [HTTPX discussion #2123](https://github.com/encode/httpx/discussions/2123).
- HTTPX's long-lived streaming discussion also shows response bodies must be
  read/closed at the correct lifecycle point when hooks raise. The bounded
  generator therefore owns and closes the provider iterator in `finally`:
  [HTTPX discussion #1856](https://github.com/encode/httpx/discussions/1856).
- A LangChain user issue documents streaming endpoints that omit or return
  incompatible usage metadata, including provider-specific differences even
  when `stream_usage` is requested. That long-lived interoperability gap is why
  Gludd enforces a local byte-conservative token count until metadata is valid:
  [LangChain issue #30786](https://github.com/langchain-ai/langchain/issues/30786).
- A LangGraph user report shows cancellation can leave streamed state different
  from the last persisted checkpoint. Gludd consequently does not cache or mark
  a stream successful until clean exhaustion, and early consumer close is a
  cancellation rather than a partial success:
  [LangGraph issue #5672](https://github.com/langchain-ai/langgraph/issues/5672).
- An OpenAI Python user asked for early stream interruption specifically to stop
  generation and avoid spending tokens after the answer is no longer needed.
  Gludd exposes generator close as that cancellation boundary and guarantees the
  provider iterator is closed from `finally`:
  [openai-python issue #969](https://github.com/openai/openai-python/issues/969).
- A long-lived OpenAI Python practitioner report describes streaming failures at
  a repeatable five-minute boundary while a longer non-streaming timeout did not
  fire. This is why Gludd enforces its own monotonic absolute stream lifetime in
  addition to transport read timeouts, including after the iterator's last item:
  [openai-python issue #399](https://github.com/openai/openai-python/issues/399).

## Verification

`tests/unit/test_model_gateway_payload_limits.py` covers exact multibyte UTF-8
boundaries, structured tool-schema bytes, trusted and failing token counters,
metadata validation, tool-argument bytes, raw tool-call count, cache hits,
side-effect suppression, fail-closed fallback propagation, guarded raw-model
construction, profile fan-out, broadcaster-log redaction, and all six cumulative
dimensions independently across both retry and fallback chains. Its 35 tests
pass, and the current 143-test retry, fallback, health, router, circuit-breaker,
S3, and payload-limit regression slice passes. The regression slice also covers
the explicit missing-primary recovery contract and enabled metered profiles with
non-zero pricing; neither route can bypass the cumulative ledger or fail-closed
cost validation.

`tests/unit/test_model_gateway_stream_limits.py` covers all six new profile
settings, byte/token/chunk/time/idle/ratio breaches, missing compressed-wire
telemetry, payload-free errors, prompt upstream closure, early consumer close,
side-effect suppression, and success accounting after exhaustion. The live Z.AI
stream suite now consumes this gateway entry point instead of bypassing it with a
raw provider object. Live execution remains credential-gated and is not evidence
for this local phase until its dedicated target runs in an authorized session.
