# Model Gateway Buffered Payload Limits (D-30 Phases 1–2)

## Status and boundary

D-30 phases 1–2 are implemented for buffered calls that pass through
`ModelGateway.call_model` and its retry/fallback/cache paths. The gateway rejects
oversized logical requests before provider construction or invocation, then
validates buffered provider responses and cache hits before any billing,
success metric, token-learning record, LangSmith trace, or cache write.
Retries and fallback hops share one request-scoped cumulative ledger, so walking
the chain cannot reset byte, token, tool-call, or provider-attempt ceilings.

These phases deliberately do not claim the full D-30 control. Streaming chunk
count/bytes, stream duration and idle timeout, compressed-to-decompressed ratio,
prompt bytes introduced by provider-specific serialization, raw
`get_chat_model` runnables, and cancellation of an already-running buffered
provider invocation remain open. Those controls require a cancellable streaming
adapter rather than pretending a post-buffer check can stop upstream allocation.

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

`ModelGateway(request_token_counter=...)` accepts a model-specific, local token
counter with the signature `(profile, messages) -> int`. The return is trusted
only when it is an exact non-boolean, non-negative integer. A counter failure or
invalid value does not disable the limit.

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
  decompression ratio and streaming cancellation remain explicit later work,
  not a false claim in this buffered phase:
  [HTTPX discussion #2123](https://github.com/encode/httpx/discussions/2123).
- HTTPX's long-lived streaming discussion also shows response bodies must be
  read/closed at the correct lifecycle point when hooks raise. The next phase
  must close/cancel upstream in the streaming adapter itself:
  [HTTPX discussion #1856](https://github.com/encode/httpx/discussions/1856).

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
