# Model Gateway Buffered Payload Limits (D-30 Phase 1)

## Status and boundary

D-30 phase 1 is implemented for buffered calls that pass through
`ModelGateway.call_model` and its retry/fallback/cache paths. The gateway rejects
oversized logical requests before provider construction or invocation, then
validates buffered provider responses and cache hits before any billing,
success metric, token-learning record, LangSmith trace, or cache write.

This phase deliberately does not claim the full D-30 control. Streaming chunk
count/bytes, stream duration and idle timeout, compressed-to-decompressed ratio,
prompt bytes introduced by provider-specific serialization, raw
`get_chat_model` runnables, and a cumulative budget shared across every fallback
hop remain open. Those controls require a cancellable streaming adapter rather
than pretending a post-buffer check can stop upstream allocation.

## Configuration

Each `ModelProfile` has positive integer limits:

| Field | Default | Enforced boundary |
|---|---:|---|
| `max_request_bytes` | 1 MiB | compact UTF-8 message JSON plus buffered tool/guided/extra-body JSON |
| `max_input_tokens` | 120,000 | trusted configured local counter, with fail-closed fallback |
| `max_response_bytes` | 4 MiB | retained UTF-8 text plus normalized tool-call JSON |
| `max_output_tokens` | 8,000 | consistent LangChain usage metadata, with fail-closed fallback |
| `max_tool_calls` | 64 | raw provider tool-call list, before normalization can drop malformed calls |

`ModelGateway(request_token_counter=...)` accepts a model-specific, local token
counter with the signature `(profile, messages) -> int`. The return is trusted
only when it is an exact non-boolean, non-negative integer. A counter failure or
invalid value does not disable the limit.

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
construction, profile fan-out, and broadcaster-log redaction. Its 23 tests pass;
the selected 297-test gateway regression set passes at 85.03% branch-inclusive
coverage for `gateway.py`. The existing fallback cost fixture now keeps its
synthetic 50-token response at the declared 50-token boundary instead of relying
on an invalid over-limit response.
